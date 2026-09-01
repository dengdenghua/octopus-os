"""Fail-closed isolation gate for trusted hidden fixture verifiers.

The verifier source is evaluator-owned, but importing a candidate module runs
candidate-controlled top-level code.  Consequently a hidden verifier is an
untrusted-code runner and must not inherit the evaluator's filesystem or
network authority.

Seatbelt and a plain bubblewrap namespace can demonstrate useful permission
boundaries, but neither proves bounded output, CPU, memory, process/descriptor
counts, scratch bytes/inodes, kill-tree completeness, and immutable runtime
content.  They therefore remain diagnostics only.  Until an external runner
meets the complete contract below, candidate code is never executed and the
trial is infrastructure-invalid rather than an engine failure.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

_START_MARKER = "__ECHO_HIDDEN_VERIFIER_STARTED_v1__"
BROWSER_UNAVAILABLE_EXIT = 77
HARDENED_RUNNER_ENV = "ECHO_HARDENED_VERIFIER_RUNNER"
HARDENED_RUNNER_CONTRACT: dict[str, Any] = {
    "schema": "echo.hardened_verifier_runner.v2",
    "required": {
        "filesystem": "workspace-readonly,scratch-only-write",
        "network": "none-including-loopback",
        "environment": "minimal-allowlist",
        "output": "bounded-bytes",
        "descriptors": "closed-snapshot-and-bounded",
        "scratch": "bounded-bytes-and-inodes",
        "resources": "bounded-cpu-memory-pids-fds",
        "termination": "kernel-enforced-tree-kill",
        "runtime": "immutable-or-content-digest-bound",
        "probe": "adversarial-per-invocation",
    },
    "linux_required": "cgroup-v2(memory,pids,cpu)+cgroup.kill",
    "builtin_backends_authorized": False,
}
_TRUSTED_CONTROLLER_WRAPPERS: dict[tuple[str, str], str] = {
    (
        "verify_concurrent_cache.py",
        "04a40eae98cfbed206b275ef3acbb160f795f9c71fc8900489e2c3083603c5c8",
    ): "coding.concurrent-cache",
    (
        "verify_path_boundary.py",
        "2895c1481b03933308279090f6c112c5d75fc5c50c4fa53e7e96f083170adc44",
    ): "coding.path-boundary",
}
_BOOTSTRAP = r"""
import os
import shutil
import sys

sys.dont_write_bytecode = True
source = sys.stdin.read()
code = compile(source, "<echo-hidden-verifier>", "exec")
source_workspace, shadow_workspace, *verifier_args = sys.argv[1:]
print("__ECHO_HIDDEN_VERIFIER_STARTED_v1__", flush=True)
shutil.copytree(source_workspace, shadow_workspace, symlinks=True)
os.chdir(shadow_workspace)
sys.argv = ["<echo-hidden-verifier>", *verifier_args]
namespace = {"__name__": "__main__", "__file__": "<echo-hidden-verifier>"}
exec(code, namespace, namespace)
""".lstrip()

_PROBE_SOURCE = r"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

outside_read, symlink_read, original_write, outside_write, port = sys.argv[1:]
checks = {}
for key, path in (("outside_read", outside_read), ("symlink_read", symlink_read)):
    try:
        Path(path).read_text(encoding="utf-8")
    except Exception:
        checks[key] = False
    else:
        checks[key] = True
try:
    Path(original_write).write_text("source workspace modified", encoding="utf-8")
except Exception:
    checks["original_workspace_write"] = False
else:
    checks["original_workspace_write"] = True
try:
    Path(outside_write).write_text("sandbox escaped", encoding="utf-8")
except Exception:
    checks["outside_write"] = False
else:
    checks["outside_write"] = True
try:
    connection = socket.create_connection(("127.0.0.1", int(port)), timeout=0.25)
except Exception:
    checks["loopback_network"] = False
else:
    checks["loopback_network"] = True
    connection.close()
checks["inherited_secret"] = os.environ.get("ECHO_SANDBOX_PROBE_SECRET")
print(json.dumps(checks, sort_keys=True))
""".lstrip()


class FixtureInfrastructureError(RuntimeError):
    """The evaluator cannot safely produce a valid fixture verdict."""


@dataclass(frozen=True)
class VerifierProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class _PathIdentity:
    label: str
    path: str
    resolved_path: str
    path_device: int
    path_inode: int
    path_mode: int
    device: int
    inode: int
    mode: int

    def assert_unchanged(self) -> None:
        candidate = Path(self.path)
        try:
            path_observed = candidate.lstat()
            resolved = candidate.resolve(strict=True)
            observed = resolved.stat()
        except OSError as exc:
            raise FixtureInfrastructureError(
                f"hidden verifier runtime path became unavailable: {candidate}: {exc}"
            ) from exc
        if (
            str(resolved) != self.resolved_path
            or path_observed.st_dev != self.path_device
            or path_observed.st_ino != self.path_inode
            or path_observed.st_mode != self.path_mode
            or observed.st_dev != self.device
            or observed.st_ino != self.inode
            or observed.st_mode != self.mode
        ):
            raise FixtureInfrastructureError(
                f"hidden verifier runtime path identity drifted: {candidate}"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "path": self.path,
            "resolved_path": self.resolved_path,
            "path_device": self.path_device,
            "path_inode": self.path_inode,
            "path_mode": self.path_mode,
            "device": self.device,
            "inode": self.inode,
            "mode": self.mode,
        }


@dataclass(frozen=True)
class _PermissionProbeSandbox:
    backend: Literal["bubblewrap", "seatbelt"]
    executable: str
    python_executable: str
    python_version: str
    python_prefix: str
    python_base_prefix: str
    runtime_paths: tuple[_PathIdentity, ...]
    sandbox_identity: _PathIdentity

    def command(
        self,
        *,
        source_workspace: Path,
        scratch: Path,
        shadow_workspace: Path,
        verifier_args: Sequence[str],
    ) -> list[str]:
        self.assert_runtime_unchanged(
            source_workspace=source_workspace,
            scratch=scratch,
        )
        python_command = [
            self.python_executable,
            "-I",
            "-c",
            _BOOTSTRAP,
            str(source_workspace),
            str(shadow_workspace),
            *verifier_args,
        ]
        if self.backend == "bubblewrap":
            return _bubblewrap_command(
                self.executable,
                python_command,
                source_workspace=source_workspace,
                scratch=scratch,
                runtime_roots=self.runtime_roots,
            )
        return [
            self.executable,
            "-p",
            _seatbelt_profile(
                source_workspace=source_workspace,
                scratch=scratch,
                runtime_roots=self.runtime_roots,
            ),
            *python_command,
        ]

    def diagnostics(self) -> dict[str, Any]:
        return {
            "schema": "echo.hidden_verifier_permission_diagnostic.v1",
            "ownership": "evaluator",
            "backend": self.backend,
            "authorization": False,
            "coverage": "partial_permissions_only",
            "missing_guarantees": [
                "bounded_output",
                "bounded_cpu_memory_pids_fds",
                "bounded_scratch_bytes_inodes",
                "kernel_tree_kill",
                "immutable_runtime_content",
            ],
            "sandbox_executable": self.executable,
            "sandbox_executable_identity": self.sandbox_identity.to_dict(),
            "interpreter_path": self.python_executable,
            "python_version": self.python_version,
            "python_prefix": self.python_prefix,
            "python_base_prefix": self.python_base_prefix,
            "runtime_paths": [identity.to_dict() for identity in self.runtime_paths],
            "verifier_source_transport": "stdin",
            "workspace_source_access": "read_only",
            "writable_scope": "per_invocation_evaluator_scratch",
            "network": "denied_including_loopback",
            "environment": "minimal_allowlist",
            "environment_keys": sorted(_minimal_env(Path("/tmp/echo-provenance")).keys()),
            "bootstrap_sha256": sha256(_BOOTSTRAP.encode("utf-8")).hexdigest(),
            "probe": {
                "outside_read_denied": True,
                "symlink_escape_read_denied": True,
                "original_workspace_write_denied": True,
                "outside_write_denied": True,
                "loopback_network_denied": True,
                "host_secret_scrubbed": True,
            },
        }

    @property
    def runtime_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        for identity in self.runtime_paths:
            for candidate in (Path(identity.path), Path(identity.resolved_path)):
                root = candidate if candidate.is_dir() else candidate.parent
                if root not in roots:
                    roots.append(root)
        return tuple(roots)

    def assert_runtime_unchanged(self, *, source_workspace: Path, scratch: Path) -> None:
        self.sandbox_identity.assert_unchanged()
        for identity in self.runtime_paths:
            identity.assert_unchanged()
        for root in self.runtime_roots:
            if _paths_overlap(root, source_workspace) or _paths_overlap(root, scratch):
                raise FixtureInfrastructureError(
                    "hidden verifier runtime roots must not overlap a trial workspace or "
                    f"evaluator scratch: {root}"
                )


def verifier_sandbox_provenance() -> dict[str, Any]:
    """Return only a live, root-attested Linux runner's provenance."""

    return dict(_configured_hardened_runner().provenance())


def _hardened_runner_unavailable_message() -> str:
    configured = os.environ.get(HARDENED_RUNNER_ENV)
    configured_note = (
        f"; {HARDENED_RUNNER_ENV} is set but this platform cannot authorize execution"
        if configured
        else f"; {HARDENED_RUNNER_ENV} is not configured"
    )
    return (
        "hidden verifier infrastructure is invalid: built-in Seatbelt/bubblewrap "
        "provide permission diagnostics only and do not prove resource/output/scratch/"
        "kill-tree/runtime-content bounds" + configured_note
    )


def verifier_permission_diagnostics() -> dict[str, Any]:
    """Run non-authorizing filesystem/network probes for operator diagnostics."""

    return dict(_resolve_permission_probe_sandbox().diagnostics())


def run_hidden_verifier(
    *,
    verifier_source: str | Path,
    argument_templates: Sequence[str],
    workspace: str | Path,
    timeout_seconds: float,
    infrastructure_exit_codes: frozenset[int] = frozenset(),
    expected_source_sha256: str | None = None,
) -> VerifierProcessResult:
    """Dispatch only exact coding wrappers to the root-owned trusted controller."""

    del infrastructure_exit_codes
    runner = _configured_hardened_runner()
    if expected_source_sha256 is None:
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: expected source digest is required"
        )
    source_path = Path(verifier_source)
    source, source_identity = _read_bounded_wrapper_source(source_path)
    observed_sha256 = sha256(source).hexdigest()
    if observed_sha256 != expected_source_sha256:
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: wrapper source digest changed"
        )
    case_id = _TRUSTED_CONTROLLER_WRAPPERS.get((source_path.name, observed_sha256))
    if case_id is None or tuple(argument_templates) != ("{workspace}",):
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: no attested controller mapping "
            "authorizes this wrapper"
        )
    try:
        completed = runner.run_trusted_controller(
            case_id=case_id,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        from benchmarks.linux_hardened_verifier import LinuxRunnerInfrastructureError

        if isinstance(exc, LinuxRunnerInfrastructureError):
            raise FixtureInfrastructureError(
                f"hidden verifier infrastructure is invalid: {exc}"
            ) from exc
        raise
    try:
        stdout = completed.stdout.decode("utf-8", errors="strict")
        stderr = completed.stderr.decode("utf-8", errors="replace")
    except UnicodeDecodeError as exc:
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: trusted controller emitted invalid UTF-8"
        ) from exc
    if completed.timed_out or completed.returncode != 0:
        detail = stderr.strip() or stdout.strip() or f"exit {completed.returncode}"
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: trusted controller failed: "
            + detail[-4000:]
        )
    source_post, source_post_identity = _read_bounded_wrapper_source(source_path)
    if (
        source_post_identity != source_identity
        or sha256(source_post).hexdigest() != observed_sha256
    ):
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: wrapper source drifted during execution"
        )
    return VerifierProcessResult(
        returncode=0,
        stdout=stdout,
        stderr=stderr,
        timed_out=False,
    )


def _read_bounded_wrapper_source(path: Path) -> tuple[bytes, tuple[int, ...]]:
    try:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("not a non-symlink regular file")
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            before = os.fstat(descriptor)
            source = bytearray()
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                source.extend(chunk)
                if len(source) > 1024 * 1024:
                    raise FixtureInfrastructureError(
                        "hidden verifier infrastructure is invalid: wrapper source is oversized"
                    )
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except FixtureInfrastructureError:
        raise
    except OSError as exc:
        raise FixtureInfrastructureError(
            f"hidden verifier infrastructure is invalid: wrapper source is unsafe: {exc}"
        ) from exc
    path_identity = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if path_identity != before_identity or before_identity != after_identity:
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: wrapper source drifted while read"
        )
    return bytes(source), before_identity


def _configured_hardened_runner() -> Any:
    if not sys.platform.startswith("linux"):
        raise FixtureInfrastructureError(_hardened_runner_unavailable_message())
    configured = os.environ.get(HARDENED_RUNNER_ENV, "").strip()
    if not configured:
        raise FixtureInfrastructureError(_hardened_runner_unavailable_message())
    try:
        from benchmarks.linux_hardened_verifier import (
            LinuxHardenedVerifierRunner,
        )

        return LinuxHardenedVerifierRunner.from_config(configured)
    except Exception as exc:
        if isinstance(exc, FixtureInfrastructureError):
            raise
        detail = str(exc)
        if not detail:
            detail = type(exc).__name__
        raise FixtureInfrastructureError(
            "hidden verifier infrastructure is invalid: hardened runner attestation rejected: "
            + detail
        ) from exc


@lru_cache(maxsize=1)
def _resolve_permission_probe_sandbox() -> _PermissionProbeSandbox:
    if os.name == "nt":
        raise FixtureInfrastructureError(
            "hidden verifier permission diagnostics are unavailable on Windows; "
            "the required symlink-escape probe cannot be run"
        )
    python = _benchmark_python()
    if sys.platform.startswith("linux"):
        bwrap = shutil.which("bwrap")
        if not bwrap:
            raise FixtureInfrastructureError(
                "hidden verifier sandbox is unavailable: bubblewrap is required on Linux"
            )
        candidate = _PermissionProbeSandbox(
            backend="bubblewrap",
            executable=str(Path(bwrap).resolve()),
            python_executable=python["executable"],
            python_version=python["version"],
            python_prefix=python["prefix"],
            python_base_prefix=python["base_prefix"],
            runtime_paths=python["runtime_paths"],
            sandbox_identity=_path_identity("sandbox_executable", Path(bwrap)),
        )
    elif sys.platform == "darwin":
        sandbox_exec = Path("/usr/bin/sandbox-exec")
        if not sandbox_exec.is_file():
            raise FixtureInfrastructureError(
                "hidden verifier sandbox is unavailable: sandbox-exec is missing"
            )
        candidate = _PermissionProbeSandbox(
            backend="seatbelt",
            executable=str(sandbox_exec),
            python_executable=python["executable"],
            python_version=python["version"],
            python_prefix=python["prefix"],
            python_base_prefix=python["base_prefix"],
            runtime_paths=python["runtime_paths"],
            sandbox_identity=_path_identity("sandbox_executable", sandbox_exec),
        )
    else:
        raise FixtureInfrastructureError(
            f"hidden verifier sandbox is unavailable on platform {sys.platform!r}"
        )
    _probe_permission_boundaries(candidate)
    return candidate


def _benchmark_python() -> dict[str, Any]:
    launcher = Path(os.path.abspath(sys.executable))
    if not launcher.is_file():
        raise FixtureInfrastructureError(
            f"hidden verifier benchmark interpreter is unavailable: {launcher}"
        )
    probe = (
        "import json, os, platform, sys, sysconfig; "
        "print(json.dumps({'executable': os.path.abspath(sys.executable), "
        "'prefix': os.path.abspath(sys.prefix), "
        "'base_prefix': os.path.abspath(sys.base_prefix), "
        "'version': platform.python_version(), 'info': list(sys.version_info[:3]), "
        "'paths': sysconfig.get_paths()}))"
    )
    try:
        completed = subprocess.run(
            [str(launcher), "-I", "-c", probe],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FixtureInfrastructureError(
            f"hidden verifier benchmark interpreter preflight failed: {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise FixtureInfrastructureError(
            f"hidden verifier benchmark interpreter preflight failed: {detail[-1000:]}"
        )
    try:
        payload = json.loads(completed.stdout)
        executable = Path(str(payload["executable"]))
        if executable.absolute() != launcher.absolute():
            raise ValueError("interpreter executable changed during preflight")
        info = tuple(int(value) for value in payload["info"])
        version = str(payload["version"])
        prefix = Path(str(payload["prefix"]))
        base_prefix = Path(str(payload["base_prefix"]))
        raw_paths = payload["paths"]
        if not isinstance(raw_paths, dict):
            raise TypeError("sysconfig paths are not an object")
    except (json.JSONDecodeError, KeyError, TypeError, ValueError, OSError) as exc:
        raise FixtureInfrastructureError(
            "hidden verifier benchmark interpreter returned invalid provenance"
        ) from exc
    if info < (3, 11, 0):
        raise FixtureInfrastructureError(
            f"hidden verifier requires benchmark Python >=3.11, found {version}"
        )
    declared: list[tuple[str, Path]] = [
        ("interpreter", launcher),
        ("prefix", prefix),
        ("base_prefix", base_prefix),
    ]
    for key in ("stdlib", "platstdlib", "purelib", "platlib"):
        value = raw_paths.get(key)
        if isinstance(value, str) and value:
            declared.append((key, Path(value)))
    identities: list[_PathIdentity] = []
    for label, path in declared:
        identity = _path_identity(label, path)
        if not any(
            existing.path == identity.path and existing.resolved_path == identity.resolved_path
            for existing in identities
        ):
            identities.append(identity)
    for index, symlink_path in enumerate(_symlink_components(launcher)):
        identity = _path_identity(f"interpreter_symlink_{index}", symlink_path)
        if not any(existing.path == identity.path for existing in identities):
            identities.append(identity)
    return {
        "executable": str(launcher),
        "version": version,
        "prefix": str(prefix),
        "base_prefix": str(base_prefix),
        "runtime_paths": tuple(identities),
    }


def _path_identity(label: str, path: Path) -> _PathIdentity:
    if not path.is_absolute():
        raise FixtureInfrastructureError(
            f"hidden verifier runtime path is not absolute ({label}): {path}"
        )
    try:
        path_observed = path.lstat()
        resolved = path.resolve(strict=True)
        observed = resolved.stat()
    except OSError as exc:
        raise FixtureInfrastructureError(
            f"hidden verifier runtime path is unavailable ({label}): {path}: {exc}"
        ) from exc
    if resolved == Path("/"):
        raise FixtureInfrastructureError(
            f"hidden verifier runtime path is overbroad ({label}): {path}"
        )
    return _PathIdentity(
        label=label,
        path=str(path),
        resolved_path=str(resolved),
        path_device=path_observed.st_dev,
        path_inode=path_observed.st_ino,
        path_mode=path_observed.st_mode,
        device=observed.st_dev,
        inode=observed.st_ino,
        mode=observed.st_mode,
    )


def _symlink_components(path: Path) -> tuple[Path, ...]:
    """Return each symlink used by an absolute path, including target ancestors."""

    pending = path
    observed: list[Path] = []
    for _attempt in range(32):
        components = pending.parts
        current = Path(components[0])
        followed = False
        for component in components[1:]:
            current /= component
            try:
                is_link = current.is_symlink()
            except OSError as exc:
                raise FixtureInfrastructureError(
                    f"hidden verifier interpreter symlink probe failed: {current}: {exc}"
                ) from exc
            if not is_link:
                continue
            if current not in observed:
                observed.append(current)
            target = Path(os.readlink(current))
            if not target.is_absolute():
                target = current.parent / target
            suffix = components[len(current.parts) :]
            pending = target.joinpath(*suffix)
            followed = True
            break
        if not followed:
            return tuple(observed)
    raise FixtureInfrastructureError("hidden verifier interpreter symlink chain is too deep")


def _execute_permission_probe(
    backend: _PermissionProbeSandbox,
    *,
    source_workspace: Path,
    argument_templates: Sequence[str],
    timeout_seconds: float,
) -> VerifierProcessResult:
    """Execute only the fixed evaluator permission probe; never candidate code."""

    if timeout_seconds <= 0:
        raise FixtureInfrastructureError("hidden verifier timeout must be positive")
    with tempfile.TemporaryDirectory(prefix="echo-hidden-verifier-") as temporary:
        scratch = Path(temporary).resolve()
        shadow_workspace = scratch / "workspace"
        for directory in ("home", "tmp", "cache", "config", "data"):
            (scratch / directory).mkdir(mode=0o700)
        verifier_args = [
            str(part).replace("{workspace}", str(shadow_workspace)) for part in argument_templates
        ]
        command = backend.command(
            source_workspace=source_workspace,
            scratch=scratch,
            shadow_workspace=shadow_workspace,
            verifier_args=verifier_args,
        )
        env = _minimal_env(scratch)
        try:
            process = subprocess.Popen(
                command,
                cwd=scratch,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            raise FixtureInfrastructureError(
                f"hidden verifier sandbox failed to start: {exc}"
            ) from exc
        timed_out = False
        try:
            stdout, stderr = process.communicate(_PROBE_SOURCE, timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        returncode = 124 if timed_out else int(process.returncode or 0)
        started, cleaned_stdout = _remove_start_marker(stdout)
        if not started:
            detail = stderr.strip() or stdout.strip() or f"exit {returncode}"
            raise FixtureInfrastructureError(
                "hidden verifier sandbox/interpreter failed before verifier start: "
                + detail[-2000:]
            )
        return VerifierProcessResult(
            returncode=returncode,
            stdout=cleaned_stdout,
            stderr=stderr,
            timed_out=timed_out,
        )


def _minimal_env(scratch: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(scratch / "home"),
        "TMPDIR": str(scratch / "tmp"),
        "TMP": str(scratch / "tmp"),
        "TEMP": str(scratch / "tmp"),
        "XDG_CACHE_HOME": str(scratch / "cache"),
        "XDG_CONFIG_HOME": str(scratch / "config"),
        "XDG_DATA_HOME": str(scratch / "data"),
        "LC_ALL": "C",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "ECHO_BEHAVIORAL_EVAL": "1",
    }


def _remove_start_marker(stdout: str) -> tuple[bool, str]:
    lines = stdout.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == _START_MARKER:
            del lines[index]
            return True, "".join(lines)
    return False, stdout


def _probe_permission_boundaries(backend: _PermissionProbeSandbox) -> None:
    with tempfile.TemporaryDirectory(prefix="echo-sandbox-probe-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "trial"
        workspace.mkdir()
        outside_read = root / "outside-readable-sentinel.txt"
        outside_read.write_text("outside secret", encoding="utf-8")
        outside_write = root / "outside-write-sentinel.txt"
        original_write = workspace / "original-write-sentinel.txt"
        original_write.write_text("original", encoding="utf-8")
        symlink = workspace / "outside-link.txt"
        try:
            symlink.symlink_to(outside_read)
        except OSError as exc:
            raise FixtureInfrastructureError(
                f"hidden verifier symlink permission probe is unavailable: {exc}"
            ) from exc
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            result = _execute_permission_probe(
                backend,
                source_workspace=workspace,
                argument_templates=(
                    str(outside_read),
                    "{workspace}/outside-link.txt",
                    str(original_write),
                    str(outside_write),
                    str(port),
                ),
                timeout_seconds=10,
            )
        finally:
            listener.close()
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
            raise FixtureInfrastructureError(
                f"hidden verifier permission probe failed to run: {detail[-2000:]}"
            )
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        try:
            payload = json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise FixtureInfrastructureError(
                "hidden verifier permission probe returned invalid evidence"
            ) from exc
        expected = {
            "outside_read": False,
            "symlink_read": False,
            "original_workspace_write": False,
            "outside_write": False,
            "loopback_network": False,
            "inherited_secret": None,
        }
        if payload != expected or outside_write.exists():
            raise FixtureInfrastructureError(
                "hidden verifier permission probe failed: " + json.dumps(payload, sort_keys=True)
            )
        if outside_read.read_text(encoding="utf-8") != "outside secret":
            raise FixtureInfrastructureError(
                "hidden verifier permission probe modified the external sentinel"
            )
        if original_write.read_text(encoding="utf-8") != "original":
            raise FixtureInfrastructureError(
                "hidden verifier permission probe modified the source workspace"
            )


def _bubblewrap_command(
    executable: str,
    python_command: Sequence[str],
    *,
    source_workspace: Path,
    scratch: Path,
    runtime_roots: Sequence[Path],
) -> list[str]:
    command = [
        executable,
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup",
        "--unshare-net",
    ]
    system_roots: list[Path] = []
    for raw in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc"):
        path = Path(raw)
        if path.exists():
            command.extend(("--ro-bind", raw, raw))
            system_roots.append(path.resolve(strict=False))
    if Path("/dev").exists():
        command.extend(("--dev", "/dev"))
    if Path("/proc").exists():
        command.extend(("--proc", "/proc"))
    command.extend(("--tmpfs", "/tmp"))
    namespace_parents: list[Path] = []
    extra_runtime_roots = [
        root
        for root in runtime_roots
        if not any(root == system or root.is_relative_to(system) for system in system_roots)
    ]
    for mount in (source_workspace, scratch, *extra_runtime_roots):
        for parent in reversed(mount.parents):
            if parent.parent == parent or parent == Path("/tmp"):
                continue
            if any(parent == root or root in parent.parents for root in system_roots):
                continue
            if parent not in namespace_parents:
                namespace_parents.append(parent)
    for parent in namespace_parents:
        command.extend(("--dir", str(parent)))
    for runtime_root in sorted(extra_runtime_roots, key=lambda path: len(path.parts)):
        command.extend(("--ro-bind", str(runtime_root), str(runtime_root)))
    command.extend(("--ro-bind", str(source_workspace), str(source_workspace)))
    command.extend(("--bind", str(scratch), str(scratch)))
    command.extend(("--chdir", str(scratch), "--", *python_command))
    return command


def _seatbelt_profile(
    *, source_workspace: Path, scratch: Path, runtime_roots: Sequence[Path]
) -> str:
    read_paths = [
        Path("/usr"),
        Path("/System"),
        Path("/Library/Apple"),
        Path("/private/var/db/dyld"),
        *runtime_roots,
        source_workspace,
        scratch,
    ]
    read_rules: list[str] = []
    ancestor_metadata_rules: list[str] = []
    for path in read_paths:
        if not path.exists():
            continue
        for ancestor in reversed(path.parents):
            _append_unique(
                ancestor_metadata_rules,
                f"(literal {_sbpl_string(ancestor)})",
            )
        _append_unique(read_rules, f"(literal {_sbpl_string(path)})")
        _append_unique(read_rules, f"(subpath {_sbpl_string(path)})")
    for raw in (
        "/dev",
        "/dev/null",
        "/dev/urandom",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/fd",
        "/dev/fd/0",
        "/dev/fd/1",
        "/dev/fd/2",
    ):
        _append_unique(read_rules, f"(literal {_sbpl_string(Path(raw))})")
    write_rules = [
        f"(literal {_sbpl_string(Path('/dev/null'))})",
        f"(literal {_sbpl_string(Path('/dev/stdout'))})",
        f"(literal {_sbpl_string(Path('/dev/stderr'))})",
        f"(literal {_sbpl_string(Path('/dev/fd/1'))})",
        f"(literal {_sbpl_string(Path('/dev/fd/2'))})",
        f"(literal {_sbpl_string(scratch)})",
        f"(subpath {_sbpl_string(scratch)})",
    ]
    return (
        "(version 1)\n"
        "(deny default)\n"
        "(allow process*)\n"
        "(allow signal (target same-sandbox))\n"
        f"(allow file-read-metadata {' '.join(ancestor_metadata_rules)})\n"
        '(allow file-read-data (literal "/"))\n'
        f"(allow file-read* {' '.join(read_rules)})\n"
        f"(allow file-write* {' '.join(write_rules)})\n"
        "(deny network*)\n"
    )


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _sbpl_string(path: Path) -> str:
    # JSON string escaping is compatible with SBPL quoted string literals.
    return json.dumps(str(path), ensure_ascii=True)


__all__ = [
    "BROWSER_UNAVAILABLE_EXIT",
    "FixtureInfrastructureError",
    "HARDENED_RUNNER_CONTRACT",
    "HARDENED_RUNNER_ENV",
    "VerifierProcessResult",
    "run_hidden_verifier",
    "verifier_permission_diagnostics",
    "verifier_sandbox_provenance",
]

