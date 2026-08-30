"""Sandboxed shell command execution.

The "let the agent run shell" axis is the place where the gap between
a toy agent and a production agent is widest. ``exec_shell`` is **not**
raw ``subprocess.Popen``. It's wrapped in a platform-appropriate
sandbox: macOS Seatbelt, Linux ``bwrap`` / ``unshare``, Windows Job
Object + restricted token. The harness still gets the same
``stdout``/``stderr`` interface, but a misbehaving model cannot
``rm -rf /``, exfiltrate to a proxy, or escape the workspace.

This module does **not** ship a turnkey kernel sandbox — that's a deep
platform integration each user has to install. Instead it provides:

  1. A common ``SandboxPolicy`` data class.
  2. ``SandboxRunner`` — a process executor that always enforces the
     soft constraints (cwd lock, env allow-list, deny-network env hints,
     output size cap, wall-clock timeout, kill-tree on cancel).
  3. A pluggable ``Backend`` interface so a real bwrap/Seatbelt/Job
     Object backend can be wired in by the caller without touching
     the runner itself.
  4. A no-op ``DirectBackend`` that runs subprocess directly. This remains
     the local-development default — soft constraints still apply. Shared /
     commercial mode selects a hard backend and fails closed when none exists.

The contract: even with the no-op backend, **a sandbox-aware caller
gets observable behavior** (timeout, output cap, env scrubbing,
blocked-network hints). Switching to a real backend later is a
configuration change, not an API change.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

_logger = logging.getLogger(__name__)


def inference_domains() -> tuple[str, ...]:
    """Hostnames of the LLM inference endpoints the agent must always reach.

    Mirrors Claude Desktop's "allowed egress hosts" default: a network-denied
    sandbox still lets the agent talk to the model API. Sources, in order:

      1. ``custom_models.json`` — every entry's ``base_url`` host (the
         operator-declared model endpoints).
      2. ``ANTHROPIC_BASE_URL`` / ``ANTHROPIC_API_URL`` env override host.

    Hosts are lowercased and deduplicated. A network-*allowed* sandbox ignores
    this list entirely (everything is reachable); it only matters when
    ``allow_network=False``.
    """
    from urllib.parse import urlparse

    hosts: list[str] = []
    try:
        from runtime.platform.models.custom_model_flags import read_custom_models

        models = read_custom_models()
        if isinstance(models, dict):
            for entry in models.values():
                if not isinstance(entry, dict):
                    continue
                base = entry.get("base_url")
                if isinstance(base, str) and base.strip():
                    host = (urlparse(base.strip()).hostname or "").strip().lower()
                    if host and host not in hosts:
                        hosts.append(host)
    except Exception:  # noqa: BLE001 - inference-domain discovery is best-effort
        pass
    for env_key in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL"):
        raw = os.environ.get(env_key)
        if isinstance(raw, str) and raw.strip():
            host = (urlparse(raw.strip()).hostname or "").strip().lower()
            if host and host not in hosts:
                hosts.append(host)
    return tuple(hosts)


# Common dev-tool egress domains, pre-bundled so a network-"common" sandbox
# lets the agent install packages / clone repos without the user having to
# hand-maintain a host allowlist (Claude Desktop ships a similar preset in its
# "Allowed egress hosts"). Mirrors (npmmirror / tuna / aliyun) are included so
# CN users get the same zero-config experience. Everything outside
# inference + this preset stays blocked by the dead proxy.
DEFAULT_EGRESS_DOMAINS: tuple[str, ...] = (
    # npm / frontend tooling
    "registry.npmjs.org",
    "registry.npmmirror.com",
    "yarnpkg.com",
    "registry.yarnpkg.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
    # pip / python
    "pypi.org",
    "files.pythonhosted.org",
    "pypi.tuna.tsinghua.edu.cn",
    "mirrors.aliyun.com",
    # git
    "github.com",
    "codeload.github.com",
    "raw.githubusercontent.com",
    "gitee.com",
    # apt / system packages
    "archive.ubuntu.com",
    "security.ubuntu.com",
    # rust
    "crates.io",
    "index.crates.io",
    "static.crates.io",
    # go
    "proxy.golang.org",
    "goproxy.cn",
    # other dev tools
    "playwright.download.prss.microsoft.com",
    "cdn.playwright.dev",
    "repo1.maven.org",
    "central.sonatype.com",
)


def default_egress_domains() -> tuple[str, ...]:
    """The pre-bundled dev-tool host allowlist for the "common domains"
    network tier (see ``SandboxPolicy.egress_allow_common``)."""
    return DEFAULT_EGRESS_DOMAINS


SandboxMode = Literal["read-only", "workspace-write", "danger-full-access"]
"""File-effect tier for a confined process (dsh vocabulary)."""

SandboxEnforcement = Literal["full", "partial", "none"]
"""How completely a backend governs the promised file effects.

* ``full`` — every file effect in the tier is governed (Landlock/bwrap on
  a current ABI, or a backend that fails closed on gaps).
* ``partial`` — only a subset is governed (macOS Seatbelt scopes reads
  loosely; older Landlock ABIs).
* ``none`` — no kernel-level file-effect confinement (DirectBackend).
"""


_COMMERCIAL_DEPLOYMENT_MODES = frozenset(
    {
        "commercial",
        "production",
        "shared",
        "server",
    }
)


# Default env keys allowed through to the child. Anything else is
# stripped — keeps the tested model from accidentally inheriting a
# user's API keys / OAuth tokens. If you need more, override via
# ``SandboxPolicy.allowed_env``.
_BASE_ALLOWED_ENV = (
    "PATH",
    "HOME",
    "USERPROFILE",
    "LANG",
    "LC_ALL",
    "TZ",
    "TEMP",
    "TMP",
    "TMPDIR",
    "SystemRoot",
    "ComSpec",
    "PATHEXT",
)
_EXTRA_ENV_ALLOWED_EXACT = frozenset(
    {
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "NODE_PATH",
        "RUST_LOG",
        "TERM",
        "COLORTERM",
    }
)
_EXTRA_ENV_ALLOWED_PREFIXES = ("OCT_", "ECHO_", "PYTEST_")
_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "ACCESS_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE_KEY",
)


@dataclass(frozen=True)
class SandboxPolicy:
    """Knobs the runner enforces for every command."""

    workspace: Path
    """Allowed cwd. The runner refuses cwd= outside this tree."""

    allow_network: bool = False
    """If False, set ``no_proxy=*`` etc. so common HTTP libs short-circuit."""

    inference_domains: tuple[str, ...] = ()
    """Hostnames that stay reachable even when ``allow_network`` is False.

    Mirrors Claude Desktop's "allowed egress hosts" behaviour: a network-
    denied sandbox still lets the agent reach the LLM inference endpoint(s)
    (the model API), otherwise a sandboxed embedded engine would be unable to
    call the model at all. When non-empty and network is
    denied, ``no_proxy`` is set to these domains (direct connect) while the
    HTTP(S) proxy still points at the dead short-circuit address, so every
    other host is blocked.
    """

    egress_allow_common: bool = False
    """When network is denied, additionally allow the pre-bundled dev-tool
    domains (``DEFAULT_EGRESS_DOMAINS`` — npm / pip / git / apt / rust / go
    registries and CN mirrors). This is the "common domains" tier of the
    network setting: the agent can install packages and clone repos, but
    everything outside inference + the preset stays blocked. Users never
    hand-maintain this list.
    """

    timeout_s: float = 60.0
    """Wall-clock cap. 0 disables (don't do that with untrusted models)."""

    max_output_bytes: int = 256 * 1024
    """Truncate stdout+stderr beyond this. Avoids OOM on chatty commands."""

    allowed_env: tuple[str, ...] = field(default_factory=lambda: _BASE_ALLOWED_ENV)

    extra_env: Mapping[str, str] = field(default_factory=dict)
    """Extra entries to inject (e.g. project-specific PYTHONPATH)."""

    mode: SandboxMode = "workspace-write"
    """File-effect tier, dsh-style:

    * ``read-only`` — deny writes outside the required sinks (``/dev/null``
      plus backend temp areas). The agent can read and execute but cannot
      change the host.
    * ``workspace-write`` — allow writes under the workspace and the
      backend-defined temp area (the historical default).
    * ``danger-full-access`` — no file-effect confinement (soft constraints
      still apply).
    """

    additional_write_roots: tuple[Path, ...] = ()
    """Exact non-workspace directories a persistent sandboxed service may write.

    Most command executions need only ``workspace`` and must leave this empty.
    Long-lived sidecars can require private state outside the checked-out tree;
    each such directory is validated and mounted/rule-scoped independently so
    callers never widen authority to a common parent merely for convenience.
    """

    def __post_init__(self) -> None:
        workspace = self.workspace.expanduser().resolve(strict=False)
        protected_roots = tuple(
            path.resolve(strict=False)
            for path in (
                Path("/usr"),
                Path("/bin"),
                Path("/sbin"),
                Path("/lib"),
                Path("/lib64"),
                Path("/etc"),
                Path("/dev"),
                Path("/proc"),
                Path("/sys"),
            )
            if path.exists()
        )
        normalized: list[Path] = []
        for raw_root in self.additional_write_roots:
            candidate = Path(raw_root).expanduser()
            if not candidate.is_absolute():
                raise SandboxViolation("additional write roots must be absolute")
            if candidate.is_symlink():
                raise SandboxViolation(f"additional write root cannot be a symlink: {candidate}")
            try:
                root = candidate.resolve(strict=True)
            except OSError as exc:
                raise SandboxViolation(
                    f"additional write root does not exist: {candidate}"
                ) from exc
            if not root.is_dir():
                raise SandboxViolation(f"additional write root is not a directory: {root}")
            if root.parent == root:
                raise SandboxViolation("filesystem root cannot be an additional write root")
            if root == workspace or root in workspace.parents or workspace in root.parents:
                raise SandboxViolation(
                    f"additional write roots must not overlap the workspace: {root}"
                )
            if any(
                root == protected or root in protected.parents or protected in root.parents
                for protected in protected_roots
            ):
                raise SandboxViolation(
                    f"additional write root must not overlap a system directory: {root}"
                )
            if any(root in existing.parents or existing in root.parents for existing in normalized):
                raise SandboxViolation(
                    f"additional write roots must be exact non-overlapping directories: {root}"
                )
            if root not in normalized:
                normalized.append(root)
        object.__setattr__(self, "additional_write_roots", tuple(normalized))

    def env_for(self) -> dict[str, str]:
        env = {k: os.environ[k] for k in self.allowed_env if k in os.environ}
        allowed = set(self.allowed_env)
        for key, value in self.extra_env.items():
            key_s = str(key)
            if _sandbox_extra_env_key_allowed(key_s, allowed):
                env[key_s] = str(value)
        workspace = self.workspace.expanduser().resolve(strict=False)
        home_dir = _ensure_workspace_env_dir(workspace, ".echo-home")
        tmp_dir = _ensure_workspace_env_dir(workspace, ".echo-tmp")
        cache_dir = _ensure_workspace_env_dir(workspace, ".echo-cache")
        config_dir = _ensure_workspace_env_dir(workspace, ".echo-config")
        data_dir = _ensure_workspace_env_dir(workspace, ".echo-data")
        env.update(
            {
                "HOME": str(home_dir),
                "USERPROFILE": str(home_dir),
                "TMPDIR": str(tmp_dir),
                "TMP": str(tmp_dir),
                "TEMP": str(tmp_dir),
                "XDG_CACHE_HOME": str(cache_dir),
                "XDG_CONFIG_HOME": str(config_dir),
                "XDG_DATA_HOME": str(data_dir),
            }
        )
        if not self.allow_network:
            # Hint to popular HTTP libs to give up immediately. This is
            # not a substitute for kernel-level network namespace, but
            # it covers casual ``urllib.request.urlopen`` and pip
            # without backend support.
            env["no_proxy"] = "*"
            env["NO_PROXY"] = "*"
            env["http_proxy"] = "http://127.0.0.1:1"
            env["https_proxy"] = "http://127.0.0.1:1"
            # Model inference endpoints stay reachable even when the
            # sandbox is network-denied (Claude Desktop parity), and the
            # "common domains" tier additionally pre-allows dev-tool hosts.
            # ``no_proxy`` enumerates them so HTTP libs connect directly;
            # everything else still goes through the dead proxy and fails.
            allowed = [d.strip() for d in self.inference_domains if d.strip()]
            if self.egress_allow_common:
                allowed.extend(d.strip() for d in DEFAULT_EGRESS_DOMAINS if d.strip())
            unique: list[str] = []
            seen: set[str] = set()
            for domain in allowed:
                if domain and domain not in seen:
                    seen.add(domain)
                    unique.append(domain)
            if unique:
                joined = ",".join(unique)
                env["no_proxy"] = joined
                env["NO_PROXY"] = joined
        return env


def _ensure_workspace_env_dir(workspace: Path, name: str) -> Path:
    if not workspace.is_dir():
        return workspace
    path = workspace / name
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        return workspace


def _sandbox_extra_env_key_allowed(key: str, base_allowed: set[str]) -> bool:
    clean = str(key or "").strip()
    if not clean:
        return False
    upper = clean.upper()
    if upper in base_allowed:
        return True
    if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
        return False
    if upper in _EXTRA_ENV_ALLOWED_EXACT:
        return True
    return any(upper.startswith(prefix) for prefix in _EXTRA_ENV_ALLOWED_PREFIXES)


def _append_capped_utf8_output(
    sink: list[str],
    chunk: str,
    *,
    cap_bytes: int,
    size: list[int],
) -> tuple[str, bool]:
    if size[0] >= cap_bytes:
        return "", True
    chunk_bytes = chunk.encode("utf-8", errors="replace")
    remaining = cap_bytes - size[0]
    if len(chunk_bytes) <= remaining:
        size[0] += len(chunk_bytes)
        sink.append(chunk)
        return chunk, False
    if remaining <= 0:
        size[0] = cap_bytes
        return "", True
    truncated = chunk_bytes[:remaining].decode("utf-8", errors="ignore")
    size[0] = cap_bytes
    if truncated:
        sink.append(truncated)
    return truncated, True


@dataclass(frozen=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool
    timed_out: bool
    killed: bool = False


class Backend(Protocol):
    """Plug point for platform-specific sandbox launchers.

    Implementations rewrite the ``argv``/``env``/``cwd`` to invoke the
    actual command under their isolation primitive (e.g. ``bwrap --
    <argv>`` on Linux). The default ``DirectBackend`` returns the input
    unchanged.
    """

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]: ...

    def enforcement(self, policy: SandboxPolicy) -> SandboxEnforcement:
        """Report how completely this backend governs the policy tier.

        ``full`` / ``partial`` only mean anything for confined tiers
        (``read-only`` / ``workspace-write``); ``danger-full-access``
        consumers bypass confinement entirely.
        """
        ...


@dataclass(frozen=True)
class BackendChoice:
    """Resolved process sandbox backend for one command."""

    backend: Backend
    name: str
    hard: bool
    strict: bool = False
    needs_approval: bool = False


@dataclass(frozen=True)
class DirectBackend:
    """No isolation primitive applied — soft constraints only.

    Used by default. Replaceable by a caller who has bubblewrap /
    sandbox-exec / Job Object configured.
    """

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]:
        return argv, env, cwd

    def enforcement(self, policy: SandboxPolicy) -> SandboxEnforcement:
        return "none"


@dataclass(frozen=True)
class BubblewrapBackend:
    """Linux hard sandbox backend using ``bwrap``.

    The workspace is bind-mounted read/write at its original absolute
    path so callers can keep using host paths. System directories are
    mounted read-only; home directories and unrelated project folders
    are not mounted unless they are the workspace.
    """

    executable: str = "bwrap"

    def enforcement(self, policy: SandboxPolicy) -> SandboxEnforcement:
        return "full"

    @staticmethod
    def available() -> bool:
        return shutil.which("bwrap") is not None

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]:
        workspace = policy.workspace.expanduser().resolve()
        run_cwd = cwd.expanduser().resolve()
        try:
            run_cwd.relative_to(workspace)
        except ValueError as exc:
            raise SandboxViolation(f"cwd {run_cwd} escapes workspace {workspace}") from exc

        bwrap = shutil.which(self.executable)
        if not bwrap:
            raise SandboxViolation("bubblewrap sandbox requested but bwrap is not installed")

        wrapped: list[str] = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup",
        ]
        if not policy.allow_network:
            wrapped.append("--unshare-net")

        system_mounts: list[Path] = []
        for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
            if Path(path).exists():
                wrapped.extend(["--ro-bind", path, path])
                system_mounts.append(Path(path).resolve(strict=False))
        if Path("/dev").exists():
            wrapped.extend(["--dev", "/dev"])
        if Path("/proc").exists():
            wrapped.extend(["--proc", "/proc"])
        wrapped.extend(["--tmpfs", "/tmp"])  # nosec B108 — bwrap tmpfs mount arg, not a temp file

        mount_roots = _unique_paths([workspace, *policy.additional_write_roots])
        namespace_parents: list[Path] = []
        for mount_root in mount_roots:
            for parent in reversed(mount_root.parents):
                if parent.parent == parent:
                    continue
                if any(parent == system or system in parent.parents for system in system_mounts):
                    continue
                if parent not in namespace_parents:
                    namespace_parents.append(parent)
        for parent in namespace_parents:
            # /tmp already exists as the private tmpfs mounted above; its
            # descendants still need explicit namespace directories.
            if parent != Path("/tmp"):
                wrapped.extend(["--dir", str(parent)])

        workspace_flag = "--ro-bind" if policy.mode == "read-only" else "--bind"
        mount_specs = [
            (workspace, workspace_flag),
            *[(root, "--bind") for root in policy.additional_write_roots],
        ]
        # A deeper exact root must be mounted after its parent so it cannot be
        # hidden by a later broad bind.  This is especially relevant to a
        # thread state root plus its task-scoped child.
        for source, flag in sorted(mount_specs, key=lambda item: len(item[0].parts)):
            wrapped.extend([flag, str(source), str(source)])
        wrapped.extend(["--chdir", str(run_cwd), "--"])
        wrapped.extend(argv)
        return wrapped, env, run_cwd


@dataclass(frozen=True)
class SeatbeltBackend:
    """macOS hard write/network sandbox using ``sandbox-exec``.

    Seatbelt profiles that fully confine reads tend to break language
    runtimes without a curated framework allow-list, so this backend is
    intentionally scoped as a hard write/network guard: it allows reads
    needed to launch tooling, denies network unless explicitly allowed,
    and restricts writes to the workspace plus temporary directories.
    """

    executable: str = "sandbox-exec"

    def enforcement(self, policy: SandboxPolicy) -> SandboxEnforcement:
        # Reads are intentionally unconfined (full read confinement breaks
        # language runtimes without a curated framework allow-list), so the
        # write/network guard is only a partial enforcement of the tier.
        return "partial"

    @staticmethod
    def available() -> bool:
        return shutil.which("sandbox-exec") is not None and sys.platform == "darwin"

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]:
        workspace = policy.workspace.expanduser().resolve()
        run_cwd = cwd.expanduser().resolve()
        try:
            run_cwd.relative_to(workspace)
        except ValueError as exc:
            raise SandboxViolation(f"cwd {run_cwd} escapes workspace {workspace}") from exc

        sandbox_exec = shutil.which(self.executable)
        if not sandbox_exec:
            raise SandboxViolation("seatbelt sandbox requested but sandbox-exec is not installed")

        if policy.mode == "read-only":
            write_subpaths = [Path("/dev/null"), *policy.additional_write_roots]
        else:
            write_subpaths = [
                workspace,
                Path("/dev/null"),
                Path("/tmp"),  # nosec B108 — sandbox write-allow rule target, not a temp file
                Path("/private/tmp"),  # nosec B108 — sandbox write-allow rule target, not a temp file
                Path("/var/tmp"),  # nosec B108 — sandbox write-allow rule target, not a temp file
                Path(os.environ.get("TMPDIR", "/tmp")).expanduser().resolve(strict=False),  # nosec B108 — sandbox write-allow rule target
                *policy.additional_write_roots,
            ]
        write_rules = "\n".join(
            f'  (subpath "{_sbpl_escape(str(path))}")' for path in _unique_paths(write_subpaths)
        )
        network_rule = "(allow network*)" if policy.allow_network else "(deny network*)"
        profile = (
            "(version 1)\n"
            "(deny default)\n"
            "(allow process*)\n"
            "(allow signal (target same-sandbox))\n"
            "(allow sysctl-read)\n"
            "(allow mach-lookup)\n"
            "(allow file-read*)\n"
            f"(allow file-write*\n{write_rules})\n"
            f"{network_rule}\n"
        )
        return [sandbox_exec, "-p", profile, *argv], env, run_cwd


_LANDLOCK_WRAPPER = r"""
# Landlock confinement wrapper (generated by LandlockBackend).
#
# Applies a deny-by-default filesystem ruleset through the Landlock LSM
# syscalls (kernel >= 5.13), then execs the real command. Reads and
# execution of the whole tree stay allowed (language runtimes need them);
# writes are allowed only under the paths in ECHO_LANDLOCK_SPEC, plus
# the /dev/null sink the shells require.

import ctypes
import json
import os
import sys

libc = ctypes.CDLL(None, use_errno=True)
SYS_LANDLOCK_CREATE_RULESET = 444
SYS_LANDLOCK_ADD_RULE = 445
SYS_LANDLOCK_RESTRICT_SELF = 446
LANDLOCK_RULE_PATH_BENEATH = 1

# linux/landlock.h access rights (ABI v1 bits; ABI v2 adds REFER,
# ABI v3 adds TRUNCATE - both handled below).
EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13
TRUNCATE = 1 << 14

_READ = EXECUTE | READ_FILE | READ_DIR
_WRITE = (
    WRITE_FILE
    | REMOVE_DIR
    | REMOVE_FILE
    | MAKE_CHAR
    | MAKE_DIR
    | MAKE_REG
    | MAKE_SOCK
    | MAKE_FIFO
    | MAKE_BLOCK
    | MAKE_SYM
    | REFER
    | TRUNCATE
)


class _Attr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _RuleBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


attr = _Attr(_READ | _WRITE)
ruleset_fd = libc.syscall(
    SYS_LANDLOCK_CREATE_RULESET, ctypes.byref(attr), ctypes.sizeof(attr), 0
)
if ruleset_fd < 0:
    raise OSError(ctypes.get_errno(), "landlock_create_ruleset")


def add_path_beneath(path, allowed):
    try:
        parent_fd = os.open(path, os.O_PATH)
    except OSError:
        return  # path absent at confine time; not a failure
    try:
        rule = _RuleBeneath(allowed, parent_fd)
        rc = libc.syscall(
            SYS_LANDLOCK_ADD_RULE,
            ruleset_fd,
            LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(rule),
            0,
        )
        if rc != 0:
            raise OSError(ctypes.get_errno(), "landlock_add_rule(" + path + ")")
    finally:
        os.close(parent_fd)


add_path_beneath("/", _READ)
add_path_beneath("/dev/null", WRITE_FILE | TRUNCATE)
for path in json.loads(os.environ["ECHO_LANDLOCK_SPEC"])["write_paths"]:
    add_path_beneath(path, _READ | _WRITE)

rc = libc.syscall(SYS_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
if rc != 0:
    raise OSError(ctypes.get_errno(), "landlock_restrict_self")

if "--" in sys.argv:
    argv = sys.argv[sys.argv.index("--") + 1 :]
else:
    argv = sys.argv[1:]
os.execvpe(argv[0], argv, os.environ)
"""


def _landlock_kernel_available() -> bool:
    """True when the running Linux kernel exposes Landlock (>= 5.13)."""

    if not sys.platform.startswith("linux"):
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    match = re.match(r"(\d+)\.(\d+)", release)
    if not match:
        return False
    major, minor = (int(part) for part in match.groups())
    return (major, minor) >= (5, 13)


@dataclass(frozen=True)
class LandlockBackend:
    """Linux kernel-native sandbox backend using the Landlock LSM.

    Unlike ``bwrap`` this needs no setuid helper or user namespaces — the
    ruleset is applied by the process itself before exec. Read/execute of
    the whole tree stays allowed; writes are confined to the workspace plus
    the runner's temp dirs (``workspace-write``) or to ``/dev/null`` only
    (``read-only``). Network is outside Landlock's vocabulary, matching the
    dsh sandbox seam (soft network hints still apply via the policy).
    """

    executable: str = sys.executable

    @staticmethod
    def available() -> bool:
        return _landlock_kernel_available()

    def enforcement(self, policy: SandboxPolicy) -> SandboxEnforcement:
        # Reads are intentionally unconfined and network is outside
        # Landlock's vocabulary, so only the write confinement of the tier
        # is actually enforced — reporting "full" would mislead operators
        # into believing the process is fully confined (it can still read
        # any file and exfiltrate over a bare socket). Same criterion as
        # SeatbeltBackend, which reports "partial" for the same reason.
        return "partial"

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]:
        workspace = policy.workspace.expanduser().resolve()
        run_cwd = cwd.expanduser().resolve()
        try:
            run_cwd.relative_to(workspace)
        except ValueError as exc:
            raise SandboxViolation(f"cwd {run_cwd} escapes workspace {workspace}") from exc
        if not _landlock_kernel_available():
            raise SandboxViolation(
                "landlock sandbox requested but the kernel does not expose Landlock (>= 5.13)"
            )

        write_paths: list[str] = [str(path) for path in policy.additional_write_roots]
        if policy.mode != "read-only":
            write_paths.append(str(workspace))
            for key in (
                "HOME",
                "TMPDIR",
                "TMP",
                "TEMP",
                "XDG_CACHE_HOME",
                "XDG_CONFIG_HOME",
                "XDG_DATA_HOME",
            ):
                value = env.get(key)
                if value and value not in write_paths:
                    write_paths.append(value)

        wrapped_env = dict(env)
        wrapped_env["ECHO_LANDLOCK_SPEC"] = json.dumps({"write_paths": write_paths})
        wrapped: list[str] = [self.executable, "-c", _LANDLOCK_WRAPPER, "--", *argv]
        return wrapped, wrapped_env, run_cwd


class SandboxViolation(Exception):
    """Raised when a request would escape the policy.

    Distinct from a non-zero exit code — the command did not even run.
    Caller should surface this as ``status=rejected`` so the planner
    distinguishes "not allowed" from "ran and failed".
    """


def commercial_execution_mode() -> bool:
    """Return whether the process is running in a shared/commercial mode.

    This is deliberately an explicit deployment switch.  Local desktop and
    test callers keep the historical soft fallback unless they opt into a
    strict process sandbox themselves.  A commercial deployment must opt in
    with ``ECHO_DEPLOYMENT_MODE=commercial`` (``production``/``shared``
    are accepted aliases) so an operator cannot accidentally expose a
    network-facing service with the local development execution contract.
    """

    raw = os.environ.get("ECHO_DEPLOYMENT_MODE", "").strip().lower()
    return raw in _COMMERCIAL_DEPLOYMENT_MODES


def process_sandbox_required() -> bool:
    """Whether a high-risk process caller must provide a hard sandbox.

    Explicit hard backend requests are also treated as required.  This lets
    ``ECHO_PROCESS_SANDBOX=strict`` fail closed even for a local operator,
    while leaving the default local ``auto`` behavior backwards compatible.
    """

    raw = os.environ.get("ECHO_PROCESS_SANDBOX", "").strip().lower()
    return commercial_execution_mode() or raw in {
        "strict",
        "bwrap",
        "bubblewrap",
        "seatbelt",
        "sandbox-exec",
    }


def effective_process_sandbox_mode() -> str:
    """Resolve the backend mode with a commercial fail-closed override."""

    raw = os.environ.get("ECHO_PROCESS_SANDBOX", "").strip().lower()
    if commercial_execution_mode():
        # A commercial deployment may select a specific hard backend, but
        # cannot downgrade itself to ``soft``/``direct``/``auto``.
        if raw in {"bwrap", "bubblewrap", "seatbelt", "sandbox-exec", "strict"}:
            return raw
        return "strict"
    return raw or "auto"


def select_process_backend(mode: str | None = None) -> BackendChoice:
    """Resolve the process sandbox backend from env/config.

    ``ECHO_PROCESS_SANDBOX`` values:

    * ``auto`` (default): use the best available hard backend; fall back
      to soft if none is installed.
    * ``soft`` / ``direct`` / ``off``: direct subprocess with
      the soft policy already enforced by callers.
    * ``strict``: use a hard backend; reject execution if unavailable.
    * ``bwrap`` / ``bubblewrap`` / ``seatbelt``: require that backend.
    * ``landlock``: require the Linux Landlock backend (kernel >= 5.13).
    """

    raw = (mode or os.environ.get("ECHO_PROCESS_SANDBOX") or "auto").strip().lower()
    if raw in {"", "soft", "direct", "off", "false", "0"}:
        return BackendChoice(DirectBackend(), "direct", hard=False)

    if raw in {"bwrap", "bubblewrap"}:
        if BubblewrapBackend.available():
            return BackendChoice(BubblewrapBackend(), "bwrap", hard=True, strict=True)
        raise SandboxViolation("bwrap sandbox requested but bwrap is not installed")

    if raw in {"seatbelt", "sandbox-exec"}:
        if SeatbeltBackend.available():
            return BackendChoice(SeatbeltBackend(), "seatbelt", hard=True, strict=True)
        raise SandboxViolation(
            "seatbelt sandbox requested but sandbox-exec is not available on this host"
        )

    if raw == "landlock":
        if LandlockBackend.available():
            return BackendChoice(LandlockBackend(), "landlock", hard=True, strict=True)
        raise SandboxViolation(
            "landlock sandbox requested but the kernel does not expose Landlock (>= 5.13)"
        )

    if raw in {"auto", "strict"}:
        strict = raw == "strict"
        if sys.platform.startswith("linux") and BubblewrapBackend.available():
            return BackendChoice(BubblewrapBackend(), "bwrap", hard=True, strict=strict)
        if sys.platform.startswith("linux") and LandlockBackend.available():
            return BackendChoice(LandlockBackend(), "landlock", hard=True, strict=strict)
        if SeatbeltBackend.available():
            return BackendChoice(SeatbeltBackend(), "seatbelt", hard=True, strict=strict)
        if strict:
            raise SandboxViolation(
                "strict process sandbox requested but no hard backend is available "
                "(install bwrap on Linux, rely on Landlock >= 5.13, or use "
                "sandbox-exec on macOS)"
            )
        _warn_soft_fallback_once()
        # A hard sandbox was requested but none is available. Rather than
        # silently running with soft constraints only, flag this choice so
        # execution entry points gate the direct path behind an authorization
        # prompt (explicit human consent for unconfined execution).
        return BackendChoice(DirectBackend(), "direct", hard=False, needs_approval=True)

    raise SandboxViolation(f"unknown process sandbox mode: {raw}")


_soft_fallback_warned = False
_soft_fallback_warn_lock = threading.Lock()


def _warn_soft_fallback_once() -> None:
    """Loudly note the auto→direct soft fallback — exactly once per process.

    Before this existed the fallback was silent: on a Linux host without
    ``bwrap`` every agent command ran with soft constraints only, and
    the only way to notice was reading the execution-policy metadata.
    Windows has no hard backend yet, so the warning there points at the
    tracking gap rather than an install hint.
    """
    global _soft_fallback_warned
    with _soft_fallback_warn_lock:
        if _soft_fallback_warned:
            return
        _soft_fallback_warned = True
    if sys.platform.startswith("linux"):
        hint = "install bubblewrap (bwrap) for kernel-level isolation"
    elif sys.platform == "darwin":
        hint = "sandbox-exec is unavailable on this host"
    else:
        hint = "no hard sandbox backend exists for this platform yet"
    _logger.warning(
        "process sandbox: no hard backend available — falling back to soft "
        "constraints only (cwd lock + env allowlist + output cap). To get "
        "kernel-level isolation: %s. Set ECHO_PROCESS_SANDBOX=strict to "
        "refuse execution instead.",
        hint,
    )


def _sbpl_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


# ═══════════════════════════════════════════════════════════
# Resolved posture — single source of truth for the execution
# contract.  ``select_process_backend`` above stays as the cheap,
# pure resolver (used by embedders and tests); production entry
# points call ``resolved_process_backend`` so the whole process
# shares ONE verified backend instead of each exec path re-deriving
# one (and silently downgrading).
# ═══════════════════════════════════════════════════════════

_PROBE_COMMAND = ("echo", "echo-sandbox-probe")
_probe_cache: dict[tuple[str, str], bool] = {}
_probe_lock = threading.Lock()

_resolved_choice: BackendChoice | None = None
_resolved_mode: str | None = None
_resolved_lock = threading.Lock()


@dataclass(frozen=True)
class ProcessSandboxPosture:
    """The isolation posture the process resolved at boot."""

    mode: str
    backend: str
    hard: bool
    enforcement: str


def _hard_backend_candidates(mode: str) -> list[tuple[Backend, str]]:
    """Priority-ordered (backend, name) hard candidates for ``mode``."""
    if mode in {"bwrap", "bubblewrap"}:
        return [(BubblewrapBackend(), "bwrap")]
    if mode in {"seatbelt", "sandbox-exec"}:
        return [(SeatbeltBackend(), "seatbelt")]
    if mode == "landlock":
        return [(LandlockBackend(), "landlock")]
    # auto / strict
    out: list[tuple[Backend, str]] = []
    if sys.platform.startswith("linux"):
        out.append((BubblewrapBackend(), "bwrap"))
        out.append((LandlockBackend(), "landlock"))
    out.append((SeatbeltBackend(), "seatbelt"))
    return out


def probe_backend_runs(backend: Backend, *, timeout_s: float = 8.0) -> bool:
    """Run one harmless command through ``backend`` and report whether it runs.

    Unlike ``available()`` — which only checks for the binary — this actually
    applies the isolation primitive and confirms a subprocess survives. A
    backend that is present-but-broken (e.g. a deprecated ``sandbox-exec``
    that no longer applies its profile, or ``bwrap`` on a host where user
    namespaces are blocked) fails here and is treated as unavailable by
    :func:`resolve_process_backend`.

    Results are cached per backend-class + platform, so the probe runs at
    most once per process per backend.
    """
    key = (type(backend).__name__, sys.platform)
    with _probe_lock:
        if key in _probe_cache:
            return _probe_cache[key]
    ok = _probe_uncached(backend, timeout_s=timeout_s)
    with _probe_lock:
        _probe_cache[key] = ok
    return ok


def _probe_uncached(backend: Backend, *, timeout_s: float) -> bool:
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="echo-sandbox-probe-") as td:
            workspace = Path(td)
            policy = SandboxPolicy(workspace=workspace, timeout_s=timeout_s)
            argv, env, cwd = backend.transform(
                list(_PROBE_COMMAND),
                policy.env_for(),
                workspace,
                policy,
            )
            result = subprocess.run(
                argv,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                timeout=timeout_s,
                text=True,
            )
            return result.returncode == 0
    except Exception:
        return False


def _set_resolved(mode: str, choice: BackendChoice) -> BackendChoice:
    global _resolved_choice, _resolved_mode
    with _resolved_lock:
        _resolved_choice = choice
        _resolved_mode = mode
    return choice


def resolve_process_backend(mode: str | None = None) -> BackendChoice:
    """Resolve the process sandbox backend once, verified by a real probe.

    Single source of truth for the execution contract; :func:`select_process_backend`
    is the cheap, pure resolver, while this adds the once-per-process probe and
    caches the result keyed by the resolved mode.

    Resolution rules:

    * ``soft``/``direct``/``off`` → ``DirectBackend``.
    * explicit hard backend (``bwrap``/``seatbelt``/``landlock``) → must be
      available AND survive a real probe, else :class:`SandboxViolation`.
    * ``strict`` → first available+probed hard backend, else raises.
    * ``auto`` → first available+probed hard backend; if none, falls back to
      ``DirectBackend`` WITH a loud once-per-process consent warning — never a
      silent downgrade.
    """
    raw = (mode or os.environ.get("ECHO_PROCESS_SANDBOX") or "auto").strip().lower()
    if raw in {"", "soft", "direct", "off", "false", "0"}:
        return _set_resolved(raw or "soft", BackendChoice(DirectBackend(), "direct", hard=False))

    with _resolved_lock:
        if _resolved_choice is not None and _resolved_mode == raw:
            return _resolved_choice

    if raw not in {
        "auto",
        "strict",
        "bwrap",
        "bubblewrap",
        "seatbelt",
        "sandbox-exec",
        "landlock",
    }:
        raise SandboxViolation(f"unknown process sandbox mode: {raw}")

    for backend, name in _hard_backend_candidates(raw):
        if backend.available() and probe_backend_runs(backend):
            return _set_resolved(
                raw, BackendChoice(backend, name, hard=True, strict=raw == "strict")
            )

    if raw == "auto":
        # Never a silent downgrade: a hard backend is present-but-broken or
        # none exists. Loudly degrade to soft and let the caller/operator see it.
        _warn_soft_fallback_once()
        return _set_resolved(raw, BackendChoice(DirectBackend(), "direct", hard=False))

    raise SandboxViolation(
        f"process sandbox mode '{raw}' has no usable hard backend "
        "(install bwrap on Linux, rely on Landlock >= 5.13, use sandbox-exec "
        "on macOS, or set ECHO_PROCESS_SANDBOX=auto to allow a soft fallback "
        "with an explicit warning)"
    )


def resolved_process_backend(mode: str | None = None) -> BackendChoice:
    """Return the process-wide resolved backend, resolving on first use.

    Once the startup gate or the first exec path resolves the posture, every
    later call with the same mode returns that cached backend, so no call site
    can silently run weaker than what the process resolved at boot.
    """
    raw = (mode or os.environ.get("ECHO_PROCESS_SANDBOX") or "auto").strip().lower()
    if raw in {"", "soft", "direct", "off", "false", "0"}:
        return BackendChoice(DirectBackend(), "direct", hard=False)
    with _resolved_lock:
        if _resolved_choice is not None and _resolved_mode == raw:
            return _resolved_choice
    return resolve_process_backend(raw)


def resolved_process_sandbox_posture(mode: str | None = None) -> ProcessSandboxPosture:
    """Authoritative isolation posture of this process (resolves on first use)."""
    effective = mode or effective_process_sandbox_mode()
    choice = resolved_process_backend(effective)
    policy = SandboxPolicy(workspace=Path.cwd())
    return ProcessSandboxPosture(
        mode=effective,
        backend=choice.name,
        hard=choice.hard,
        enforcement=choice.backend.enforcement(policy),
    )


def _reset_process_backend_cache() -> None:
    """Drop the resolved posture and probe cache (test isolation / hot reload)."""
    global _resolved_choice, _resolved_mode
    with _resolved_lock:
        _resolved_choice = None
        _resolved_mode = None
    with _probe_lock:
        _probe_cache.clear()


def _unique_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


class SandboxRunner:
    def __init__(self, policy: SandboxPolicy, *, backend: Backend | None = None) -> None:
        self.policy = policy
        self.backend = backend or DirectBackend()
        if isinstance(self.backend, DirectBackend):
            _logger.warning(
                "SandboxRunner is using DirectBackend — no kernel-level isolation "
                "is applied. A misbehaving process can still damage the host "
                "(e.g. rm -rf, exfiltration). Install bwrap (Linux), sandbox-exec "
                "(macOS), or configure ContainerSandbox for real isolation."
            )

    def run(
        self,
        argv: Iterable[str] | str,
        *,
        cwd: Path | str | None = None,
        stdin_text: str | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> SandboxResult:
        """Run a command under the policy. Returns a :class:`SandboxResult`.

        ``argv`` may be a list (preferred) or a string — strings are
        split with :func:`shlex.split` so users get the obvious behaviour.
        ``on_output`` is called with each chunk of decoded stdout/stderr
        as it arrives; the runner *also* keeps the aggregated text for
        the result. Use ``on_output`` for streaming UIs.
        """
        cmd_list = list(_normalise_argv(argv))
        if not cmd_list:
            raise SandboxViolation("empty command")

        run_cwd = self._resolve_cwd(cwd)
        env = self.policy.env_for()
        cmd_list, env, run_cwd = self.backend.transform(cmd_list, env, run_cwd, self.policy)

        started_at = time.monotonic()
        from runtime.platform.process.tree import (
            process_group_kwargs,
            terminate_process_tree,
        )

        try:
            proc = subprocess.Popen(
                cmd_list,
                cwd=str(run_cwd),
                env=env,
                stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **process_group_kwargs(),
            )
        except FileNotFoundError as exc:
            raise SandboxViolation(f"executable not found: {exc.filename}") from exc
        except OSError as exc:
            raise SandboxViolation(f"failed to start process: {exc}") from exc

        out_chunks: list[str] = []
        err_chunks: list[str] = []
        truncated = False
        size_lock = threading.Lock()
        cap_bytes = max(0, int(self.policy.max_output_bytes))
        size = [0]

        def reader(stream: object, sink: list[str], tag: str) -> None:
            nonlocal truncated
            for raw_line in iter(stream.readline, ""):
                if not raw_line:
                    break
                with size_lock:
                    raw_line, was_truncated = _append_capped_utf8_output(
                        sink,
                        raw_line,
                        cap_bytes=cap_bytes,
                        size=size,
                    )
                    truncated = truncated or was_truncated
                if raw_line and on_output is not None:
                    try:
                        on_output(raw_line)
                    except Exception as cb_err:  # noqa: BLE001
                        _logger.debug("sandbox on_output raised: %s", cb_err)

        out_thread = threading.Thread(
            target=reader, args=(proc.stdout, out_chunks, "out"), daemon=True
        )
        err_thread = threading.Thread(
            target=reader, args=(proc.stderr, err_chunks, "err"), daemon=True
        )
        out_thread.start()
        err_thread.start()

        if stdin_text is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except OSError:  # noqa: BLE001 — stdin write best-effort
                pass

        timed_out = False
        killed = False
        timeout = self.policy.timeout_s if self.policy.timeout_s > 0 else None
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            killed = True
            terminate_process_tree(proc)
            exit_code = proc.returncode if proc.returncode is not None else -1

        out_thread.join(timeout=1.0)
        err_thread.join(timeout=1.0)

        return SandboxResult(
            exit_code=exit_code,
            stdout="".join(out_chunks),
            stderr="".join(err_chunks),
            duration_ms=int((time.monotonic() - started_at) * 1000),
            truncated=truncated,
            timed_out=timed_out,
            killed=killed,
        )

    def _resolve_cwd(self, cwd: Path | str | None) -> Path:
        ws = self.policy.workspace.expanduser().resolve(strict=False)
        if not ws.is_dir():
            raise SandboxViolation(f"workspace is not a directory: {ws}")
        if cwd is None:
            return ws
        candidate = Path(cwd).expanduser().resolve(strict=False)
        try:
            candidate.relative_to(ws)
        except ValueError as exc:
            raise SandboxViolation(f"cwd {candidate} escapes workspace {ws}") from exc
        if not candidate.is_dir():
            raise SandboxViolation(f"cwd is not a directory: {candidate}")
        return candidate


def _normalise_argv(argv: Iterable[str] | str) -> list[str]:
    if isinstance(argv, str):
        return shlex.split(argv, posix=(sys.platform != "win32"))
    return [str(a) for a in argv]


__all__ = [
    "Backend",
    "BackendChoice",
    "BubblewrapBackend",
    "DirectBackend",
    "LandlockBackend",
    "SandboxEnforcement",
    "SandboxMode",
    "SandboxPolicy",
    "SandboxResult",
    "SandboxRunner",
    "SandboxViolation",
    "SeatbeltBackend",
    "select_process_backend",
]
