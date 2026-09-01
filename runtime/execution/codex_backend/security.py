"""Security boundary for an Echo-managed Codex App Server sidecar.

The sidecar intentionally has two policy layers:

* Echo is the approval broker. It chooses ``read-only`` or
  ``workspace-write`` before a thread/turn starts and handles every Codex
  approval request. Broker errors and timeouts fail closed as ``decline``.
* Codex runs with ``approval_policy = "on-request"``, a user reviewer, and
  network disabled. It can request more authority, but cannot grant it. This
  validated inner profile is also the generated-tool sandbox on macOS local
  runs, where wrapping App Server in an outer Seatbelt would prevent Codex
  from applying its own nested Seatbelt profile. Production/shared remains
  fail-closed unless a compatible full-enforcement outer backend is active.

Every realm/tenant/thread gets a persistent isolated ``CODEX_HOME`` so Codex
resume/fork survives Echo task attempts; each task gets fresh process and
scratch directories. The host process environment is rebuilt from a small
allow-list, and the environment inherited by model-generated commands is rebuilt again by Codex's
``shell_environment_policy``.  No ambient user/project MCP, plugin, hook, app,
or credential configuration is trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Literal, cast

from ._config_validation import validate_apps_config as _validate_apps_config
from ._config_validation import (
    validate_permission_profile as _validate_permission_profile_section,
)
from ._config_validation import (
    validate_provider_profile as _validate_provider_profile,
)
from ._security_support import (
    _MARKER_FILE,
    CodexSecurityError,
    _atomic_write_private,
    _ensure_private_directory,
    _expect_value,
    _lock_down_directory,
    _mapping_at,
    _non_null_items,
    _opaque_id,
    _prepare_state_root,
    _prune_empty_parents,
    _read_owned_private_file,
    _remove_marked_tree,
    _validate_workspace,
    _write_marker,
)
from .types import CodexProviderProfile

CodexSandboxMode = Literal["read-only", "workspace-write"]
ApprovalFailureDecision = Literal["decline"]

APPROVAL_FAILURE_DECISION: ApprovalFailureDecision = "decline"
APPROVAL_POLICY = "on-request"
APPROVAL_REVIEWER = "user"
PERMISSION_PROFILE = "echo-sidecar"

_BINDING_KIND = "echo-codex-thread-binding/v1"
_PRODUCTION_DEPLOYMENT_MODES = frozenset({"commercial", "production", "server", "shared"})

# Deliberately excludes HOME/TMP/XDG values (replaced below), proxy variables,
# language package paths, and every form of credential.
DEFAULT_HOST_ENV_ALLOWLIST: tuple[str, ...] = (
    "PATH",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TERM",
    "COLORTERM",
    "SYSTEMROOT",
    "COMSPEC",
    "PATHEXT",
    "WINDIR",
)

_SENSITIVE_ENV_FRAGMENTS = (
    "ACCESSKEY",
    "APIKEY",
    "AUTH",
    "BEARER",
    "COOKIE",
    "CREDENTIAL",
    "OAUTH",
    "PASSWORD",
    "PASSWD",
    "PRIVATEKEY",
    "SECRET",
    "TOKEN",
)

_LOCKED_OFF_FEATURES = (
    "apps",
    "auth_elicitation",
    "enable_mcp_apps",
    "goals",
    "hooks",
    "mcp_2026_07_28",
    "memories",
    "multi_agent",
    "multi_agent_v2",
    "network_proxy",
    "plugins",
    "recommended_plugins",
    "remote_plugin",
    "remote_control",
    "respect_system_proxy",
    "shell_snapshot",
    "tool_suggest",
)
_ALLOWED_ENABLED_FEATURES = frozenset({"mentions_v2"})
_PROTECTED_WORKSPACE_SUBPATHS = (".git", ".agents", ".codex")


@dataclass(frozen=True, slots=True)
class CodexSecurityPolicy:
    """Deployment-owned inputs to the sidecar security boundary.

    ``require_outer_hard_sandbox`` can make local/test deployments fail closed
    too.  Production-like modes always require a hard outer sandbox; passing
    ``False`` cannot weaken that invariant.
    """

    state_root: Path
    allowed_workspace_roots: tuple[Path, ...]
    deployment_mode: str = "local"
    require_outer_hard_sandbox: bool = False
    host_env_allowlist: tuple[str, ...] = field(default_factory=lambda: DEFAULT_HOST_ENV_ALLOWLIST)

    def __post_init__(self) -> None:
        state_root = Path(self.state_root).expanduser()
        roots = tuple(Path(root).expanduser() for root in self.allowed_workspace_roots)
        allowlist = tuple(str(name).strip().upper() for name in self.host_env_allowlist)
        deployment_mode = str(self.deployment_mode).strip().lower()

        if not state_root.is_absolute():
            raise CodexSecurityError("Codex sidecar state_root must be absolute")
        if not roots:
            raise CodexSecurityError("at least one allowed workspace root is required")
        if any(not root.is_absolute() for root in roots):
            raise CodexSecurityError("allowed workspace roots must be absolute")
        if not deployment_mode:
            raise CodexSecurityError("deployment_mode cannot be empty")
        if not allowlist or any(not name for name in allowlist):
            raise CodexSecurityError("host environment allow-list cannot contain empty names")
        sensitive = sorted(name for name in allowlist if is_sensitive_env_name(name))
        if sensitive:
            raise CodexSecurityError(
                "credential-like names are forbidden in the host environment allow-list: "
                + ", ".join(sensitive)
            )

        object.__setattr__(self, "state_root", state_root)
        object.__setattr__(self, "allowed_workspace_roots", roots)
        object.__setattr__(self, "deployment_mode", deployment_mode)
        object.__setattr__(self, "host_env_allowlist", allowlist)

    @property
    def outer_hard_sandbox_required(self) -> bool:
        """Whether launch must carry an outer hard-sandbox attestation."""

        return self.require_outer_hard_sandbox or (
            self.deployment_mode in _PRODUCTION_DEPLOYMENT_MODES
        )


@dataclass(frozen=True, slots=True)
class CodexThreadBinding:
    """Server-owned mapping from an Echo thread to a Codex thread."""

    inner_thread_id: str
    cwd_hash: str
    realm_hash: str
    tenant_hash: str
    thread_hash: str


@dataclass(frozen=True, slots=True)
class CodexSidecarContext:
    """Provisioned paths and immutable launch policy for one sidecar."""

    state_root: Path
    thread_root: Path
    task_root: Path
    codex_home: Path
    app_home: Path
    scratch_root: Path
    scratch_marker_path: Path
    tool_home: Path
    tool_tmp: Path
    workspace: Path
    config_path: Path
    binding_path: Path
    sandbox_mode: CodexSandboxMode
    realm_key: str
    tenant_key: str
    thread_key: str
    task_key: str
    _launch_env: Mapping[str, str] = field(repr=False)
    provider_profile: CodexProviderProfile | None = field(default=None, repr=False)
    selected_app_ids: tuple[str, ...] = ()

    def launch_env(self) -> dict[str, str]:
        """Return a mutable copy suitable for ``subprocess.Popen(env=...)``."""

        return dict(self._launch_env)

    def thread_start_security_overrides(self) -> dict[str, object]:
        """Security-owned fields for the App Server ``thread/start`` request.

        The empty dynamic/capability lists keep caller-selected tools and
        plugin/skill roots out of the sidecar.  ``environments`` is
        deliberately omitted: in App Server v2 an empty list disables *all*
        execution environments, including the default local environment that
        owns Codex's built-in shell and patch tools.  Omission selects only the
        server's default local environment; no remote environment id is ever
        accepted from the caller.  The integration must not merge
        caller-provided values over these keys.
        """

        return {
            "cwd": str(self.workspace),
            "runtimeWorkspaceRoots": [str(self.workspace)],
            "approvalPolicy": APPROVAL_POLICY,
            "approvalsReviewer": APPROVAL_REVIEWER,
            "permissions": PERMISSION_PROFILE,
            "dynamicTools": [],
            "selectedCapabilityRoots": [],
        }

    def turn_start_security_overrides(self) -> dict[str, object]:
        """Security-owned fields for every App Server ``turn/start`` request."""

        return {
            "cwd": str(self.workspace),
            "runtimeWorkspaceRoots": [str(self.workspace)],
            "approvalPolicy": APPROVAL_POLICY,
            "approvalsReviewer": APPROVAL_REVIEWER,
            "permissions": PERMISSION_PROFILE,
        }

    def validate_effective_config(self, response: Mapping[str, object]) -> None:
        """Fail closed unless ``config/read`` matches the locked policy.

        Codex also has package, system, managed, and cloud configuration
        layers.  An isolated ``CODEX_HOME`` removes ambient user config, while
        this post-start check catches an administrator/system layer that still
        adds an MCP server or overrides a locked security value.
        """

        raw_config = response.get("config", response)
        if not isinstance(raw_config, Mapping):
            raise CodexSecurityError("Codex config/read did not return a config object")
        config = cast(Mapping[str, object], raw_config)
        errors: list[str] = []

        _expect_value(config, "approval_policy", APPROVAL_POLICY, errors)
        _expect_value(config, "approvals_reviewer", APPROVAL_REVIEWER, errors)
        _expect_value(config, "default_permissions", PERMISSION_PROFILE, errors)
        _expect_value(config, "web_search", "disabled", errors)
        _expect_value(config, "allow_login_shell", False, errors)
        _expect_value(config, "check_for_update_on_startup", False, errors)
        _expect_value(config, "cli_auth_credentials_store", "file", errors)
        _expect_value(config, "file_opener", "none", errors)
        _expect_value(config, "notify", [], errors)
        _validate_provider_profile(config, self.provider_profile, errors)

        if config.get("sandbox_mode") is not None:
            errors.append("legacy sandbox_mode must be absent when permissions profiles are active")
        if config.get("sandbox_workspace_write") is not None:
            errors.append(
                "legacy sandbox_workspace_write must be absent when permissions profiles are active"
            )

        mcp_servers = config.get("mcp_servers")
        if mcp_servers not in (None, {}):
            errors.append("mcp_servers must be empty")

        for extension_map in ("plugins", "marketplaces"):
            if config.get(extension_map) != {}:
                errors.append(f"{extension_map} must be empty")
        if config.get("skills") not in ({}, {"config": []}):
            errors.append("skills must be empty")
        for extension_config in ("hooks", "goals", "memories", "orchestrator", "tools"):
            if config.get(extension_config) is not None:
                errors.append(f"{extension_config} must be disabled")
        tool_suggest = config.get("tool_suggest")
        if not isinstance(tool_suggest, Mapping) or dict(tool_suggest) != {
            "discoverables": [],
            "disabled_tools": [],
        }:
            errors.append("tool_suggest must contain no discoverable or disabled tools")

        features = _mapping_at(config, "features", errors)
        if features is not None:
            for feature_name in _LOCKED_OFF_FEATURES:
                _expect_value(
                    features,
                    feature_name,
                    bool(self.selected_app_ids) if feature_name == "apps" else False,
                    errors,
                    prefix="features.",
                )
            for feature_name, enabled in features.items():
                if (
                    feature_name not in _LOCKED_OFF_FEATURES
                    and feature_name not in _ALLOWED_ENABLED_FEATURES
                    and enabled not in (None, False)
                ):
                    errors.append(f"unknown executable feature is enabled: features.{feature_name}")

        agents = _mapping_at(config, "agents", errors)
        if agents is not None and _non_null_items(agents) != {"enabled": False}:
            errors.append("agents must be fully disabled")

        _validate_apps_config(config, self.selected_app_ids, errors)

        _validate_permission_profile_section(
            config,
            self,
            errors,
            profile_name=PERMISSION_PROFILE,
            protected_workspace_subpaths=_PROTECTED_WORKSPACE_SUBPATHS,
        )

        shell_env = _mapping_at(config, "shell_environment_policy", errors)
        if shell_env is not None:
            _expect_value(shell_env, "inherit", "none", errors, prefix="shell_environment_policy.")
            _expect_value(
                shell_env,
                "experimental_use_profile",
                False,
                errors,
                prefix="shell_environment_policy.",
            )
            configured_set = shell_env.get("set")
            if not isinstance(configured_set, Mapping):
                errors.append("shell_environment_policy.set must be an object")
            else:
                expected_set = _tool_environment(self)
                if dict(configured_set) != expected_set:
                    errors.append("shell_environment_policy.set differs from the isolated tool env")

        projects = _mapping_at(config, "projects", errors)
        if projects is not None:
            project = projects.get(str(self.workspace))
            if not isinstance(project, Mapping):
                errors.append(f"projects.{self.workspace} trust policy is missing")
            elif project.get("trust_level") != "untrusted":
                errors.append(f"projects.{self.workspace}.trust_level must be 'untrusted'")

        if errors:
            raise CodexSecurityError("unsafe effective Codex configuration: " + "; ".join(errors))

    def cleanup(self) -> None:
        """Delete only the marked scratch and task trees for this context.

        Call this after the sidecar process has exited.  Thread state is kept
        until then so App Server resume/fork remains available.
        """

        _remove_marked_tree(
            self.scratch_root,
            root=self.state_root,
            marker_path=self.scratch_marker_path,
            expected_kind="scratch",
        )
        _remove_marked_tree(
            self.task_root,
            root=self.state_root,
            marker_path=self.task_root / _MARKER_FILE,
            expected_kind="task",
        )
        _prune_empty_parents(self.scratch_root.parent, stop=self.state_root)
        _prune_empty_parents(self.task_root.parent, stop=self.state_root)


class CodexSidecarSecurity:
    """Provision isolated Codex homes and enforce the outer broker contract."""

    def __init__(self, policy: CodexSecurityPolicy) -> None:
        self._policy = policy

    @property
    def policy(self) -> CodexSecurityPolicy:
        return self._policy

    def write_server_binding(
        self,
        context: CodexSidecarContext,
        *,
        inner_thread_id: str,
        authority: str,
        replace: bool = False,
    ) -> CodexThreadBinding:
        """Atomically persist an App Server-issued thread id.

        This is an internal server API. HTTP/UI payloads must never choose the
        binding; callers have to identify themselves as the trusted server
        integration and pass the id read from ``thread/start`` or
        ``thread/resume``. A conflicting binding is rejected unless the server
        explicitly performs a replacement after proving the old Codex thread
        is gone.
        """

        _require_server_authority(authority)
        _validate_context_layout(context, self._policy)
        normalized_inner_id = _normalize_inner_thread_id(inner_thread_id)
        expected = _binding_for_context(context, normalized_inner_id)
        existing = _read_binding_file(context)
        if existing is not None and existing != expected and not replace:
            raise CodexSecurityError("refusing to replace a conflicting Codex thread binding")
        _atomic_write_private(
            context.binding_path,
            (json.dumps(_binding_payload(expected), sort_keys=True) + "\n").encode("utf-8"),
        )
        return expected

    def read_server_binding(
        self,
        context: CodexSidecarContext,
        *,
        authority: str,
    ) -> CodexThreadBinding | None:
        """Read and identity-check the server-owned thread binding."""

        _require_server_authority(authority)
        _validate_context_layout(context, self._policy)
        return _read_binding_file(context)

    def seed_auth_from_codex_home(
        self,
        context: CodexSidecarContext,
        *,
        source_codex_home: Path,
        authority: str,
    ) -> bool:
        """Copy a validated ``auth.json`` into the isolated thread home.

        The source is always explicit; this method never guesses from HOME or
        the process environment. Missing auth returns ``False``. Symlinks,
        non-regular files, foreign ownership, group/world permissions, files
        over 1 MiB, and non-object JSON fail closed.
        """

        _require_server_authority(authority)
        _validate_context_layout(context, self._policy)
        source_home = Path(source_codex_home).expanduser()
        if not source_home.is_absolute():
            raise CodexSecurityError("source_codex_home must be absolute")
        if source_home.is_symlink():
            raise CodexSecurityError("source_codex_home cannot be a symlink")
        source = source_home / "auth.json"
        data = _read_owned_private_file(source, max_bytes=1024 * 1024)
        if data is None:
            return False
        try:
            decoded = data.decode("utf-8", errors="strict")
            parsed = json.loads(
                decoded,
                parse_constant=_reject_json_constant,
            )
        except ValueError as exc:
            raise CodexSecurityError("source Codex auth.json is not valid UTF-8 JSON") from exc
        if not isinstance(parsed, dict):
            raise CodexSecurityError("source Codex auth.json must contain a JSON object")
        canonical = (json.dumps(parsed, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
            "utf-8"
        )
        _atomic_write_private(context.codex_home / "auth.json", canonical)
        return True

    def prepare(
        self,
        *,
        realm_id: str,
        tenant_id: str,
        thread_id: str,
        task_id: str,
        workspace: Path,
        sandbox_mode: CodexSandboxMode = "workspace-write",
        provider_profile: CodexProviderProfile | None = None,
        selected_app_ids: tuple[str, ...] = (),
        outer_hard_sandbox_active: bool = False,
        host_env: Mapping[str, str] | None = None,
    ) -> CodexSidecarContext:
        """Provision one tenant/thread/task sidecar, failing closed on drift."""

        if sandbox_mode not in {"read-only", "workspace-write"}:
            raise CodexSecurityError(
                "Codex sidecars only allow 'read-only' or 'workspace-write' sandbox modes"
            )
        if provider_profile is not None and not isinstance(provider_profile, CodexProviderProfile):
            raise CodexSecurityError("provider_profile must be server-resolved")
        if len(selected_app_ids) > 32 or any(
            not isinstance(app_id, str)
            or not app_id.strip()
            or len(app_id) > 256
            or any(char in app_id for char in "\x00\r\n")
            for app_id in selected_app_ids
        ):
            raise CodexSecurityError("selected_app_ids must be server-resolved safe identifiers")
        if self._policy.outer_hard_sandbox_required and not outer_hard_sandbox_active:
            raise CodexSecurityError(
                "this deployment requires an active outer hard sandbox for Codex sidecars"
            )

        realm_key = _opaque_id("realm", realm_id)
        tenant_key = _opaque_id("tenant", tenant_id)
        thread_key = _opaque_id("thread", thread_id)
        task_key = _opaque_id("task", task_id)

        state_root = _prepare_state_root(self._policy.state_root)
        resolved_workspace = _validate_workspace(
            workspace,
            allowed_roots=self._policy.allowed_workspace_roots,
            state_root=state_root,
        )

        thread_root = (
            state_root / "realms" / realm_key / "tenants" / tenant_key / "threads" / thread_key
        )
        task_root = thread_root / "tasks" / task_key
        codex_home = thread_root / "codex-home"
        app_home = task_root / "app-home"
        scratch_id = secrets.token_hex(24)
        scratch_root = state_root / "scratch" / scratch_id
        scratch_marker_path = state_root / "scratch-markers" / f"{scratch_id}.json"
        tool_home = scratch_root / "home"
        tool_tmp = scratch_root / "tmp"

        for directory in (
            thread_root,
            task_root,
            codex_home,
            codex_home / "sqlite",
            app_home,
            app_home / "cache",
            app_home / "config",
            app_home / "data",
            task_root / "tmp",
            scratch_marker_path.parent,
            scratch_root,
            tool_home,
            tool_home / ".cache",
            tool_home / ".config",
            tool_home / ".local" / "share",
            tool_tmp,
        ):
            _ensure_private_directory(directory, root=state_root)

        _write_marker(
            thread_root / _MARKER_FILE,
            {
                "kind": "thread",
                "path": str(thread_root),
                "realm_key": realm_key,
                "tenant_key": tenant_key,
                "thread_key": thread_key,
            },
        )
        _write_marker(
            task_root / _MARKER_FILE,
            {
                "kind": "task",
                "path": str(task_root),
                "realm_key": realm_key,
                "tenant_key": tenant_key,
                "thread_key": thread_key,
                "task_key": task_key,
            },
        )
        _write_marker(
            scratch_marker_path,
            {
                "kind": "scratch",
                "path": str(scratch_root),
                "realm_key": realm_key,
                "tenant_key": tenant_key,
                "thread_key": thread_key,
                "task_key": task_key,
            },
        )

        launch_env = _build_launch_env(
            host_env if host_env is not None else os.environ,
            allowlist=self._policy.host_env_allowlist,
            codex_home=codex_home,
            app_home=app_home,
            app_tmp=task_root / "tmp",
        )
        if provider_profile is not None and provider_profile.scoped_bearer_token is not None:
            # This fixed, turn-scoped credential is visible only to the App
            # Server process.  `_tool_environment` below rebuilds command
            # environments from an explicit non-secret allowlist, so model-
            # controlled shell/tool subprocesses never inherit it.
            auth_env_key = provider_profile.auth_env_key
            if auth_env_key is None:  # guarded again at the security boundary
                raise CodexSecurityError("scoped provider authentication is incomplete")
            launch_env[auth_env_key] = provider_profile.scoped_bearer_token
        context = CodexSidecarContext(
            state_root=state_root,
            thread_root=thread_root,
            task_root=task_root,
            codex_home=codex_home,
            app_home=app_home,
            scratch_root=scratch_root,
            scratch_marker_path=scratch_marker_path,
            tool_home=tool_home,
            tool_tmp=tool_tmp,
            workspace=resolved_workspace,
            config_path=codex_home / "config.toml",
            binding_path=thread_root / "binding.json",
            sandbox_mode=sandbox_mode,
            realm_key=realm_key,
            tenant_key=tenant_key,
            thread_key=thread_key,
            task_key=task_key,
            provider_profile=provider_profile,
            selected_app_ids=tuple(dict.fromkeys(selected_app_ids)),
            _launch_env=MappingProxyType(launch_env),
        )
        _read_binding_file(context)
        config_text = _render_codex_config(context)
        _atomic_write_private(context.config_path, config_text.encode("utf-8"))
        return context


def is_sensitive_env_name(name: str) -> bool:
    """Return whether an environment name is credential-like.

    Separators and case are ignored so variants such as ``api-key``,
    ``Api_Key``, and ``PRIVATE__KEY`` are all rejected.
    """

    compact = "".join(char for char in str(name).upper() if char.isalnum())
    return any(fragment in compact for fragment in _SENSITIVE_ENV_FRAGMENTS)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _build_launch_env(
    host_env: Mapping[str, str],
    *,
    allowlist: Sequence[str],
    codex_home: Path,
    app_home: Path,
    app_tmp: Path,
) -> dict[str, str]:
    allowed = {name.upper() for name in allowlist}
    result: dict[str, str] = {}
    for raw_name, raw_value in host_env.items():
        name = str(raw_name)
        upper = name.upper()
        if upper not in allowed or is_sensitive_env_name(upper):
            continue
        result[upper] = str(raw_value)

    # A minimal PATH makes subprocess launch deterministic when the caller
    # supplied no PATH.  It contains no host credential material.
    if not any(name.upper() == "PATH" for name in result):
        result["PATH"] = os.defpath

    result.update(
        {
            "CODEX_HOME": str(codex_home),
            "HOME": str(app_home),
            "USERPROFILE": str(app_home),
            "TMPDIR": str(app_tmp),
            "TMP": str(app_tmp),
            "TEMP": str(app_tmp),
            "XDG_CACHE_HOME": str(app_home / "cache"),
            "XDG_CONFIG_HOME": str(app_home / "config"),
            "XDG_DATA_HOME": str(app_home / "data"),
            # The scoped Responses bridge is always loopback-only. Some HTTP
            # stacks still consult the OS proxy configuration even when no
            # proxy variables were inherited, so make the bypass explicit.
            # This is a fixed host policy value, never ambient user input.
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
        }
    )
    return result


def _tool_environment(context: CodexSidecarContext) -> dict[str, str]:
    launch_env = context._launch_env
    path_value = next(
        (value for name, value in launch_env.items() if name.upper() == "PATH"), os.defpath
    )
    tool_env = {
        "PATH": path_value,
        "HOME": str(context.tool_home),
        "USERPROFILE": str(context.tool_home),
        "TMPDIR": str(context.tool_tmp),
        "TMP": str(context.tool_tmp),
        "TEMP": str(context.tool_tmp),
        "XDG_CACHE_HOME": str(context.tool_home / ".cache"),
        "XDG_CONFIG_HOME": str(context.tool_home / ".config"),
        "XDG_DATA_HOME": str(context.tool_home / ".local" / "share"),
    }
    for name, value in launch_env.items():
        upper = name.upper()
        if upper in {
            "LANG",
            "LANGUAGE",
            "LC_ALL",
            "LC_CTYPE",
            "TZ",
            "TERM",
            "COLORTERM",
            "SYSTEMROOT",
            "COMSPEC",
            "PATHEXT",
            "WINDIR",
        }:
            tool_env[name] = value
    return tool_env


def _render_codex_config(context: CodexSidecarContext) -> str:
    tool_env = _tool_environment(context)
    lines = [
        "# Managed by Echo. Do not merge ambient user/project configuration.",
        f"approval_policy = {_toml_string(APPROVAL_POLICY)}",
        f"approvals_reviewer = {_toml_string(APPROVAL_REVIEWER)}",
        f"default_permissions = {_toml_string(PERMISSION_PROFILE)}",
        "allow_login_shell = false",
        'web_search = "disabled"',
        "check_for_update_on_startup = false",
        'cli_auth_credentials_store = "file"',
        'file_opener = "none"',
        f"sqlite_home = {_toml_string(str(context.codex_home / 'sqlite'))}",
        "notify = []",
        "mcp_servers = {}",
        "plugins = {}",
        "marketplaces = {}",
        "skills = { config = [] }",
        "agents = { enabled = false }",
        "tool_suggest = { discoverables = [], disabled_tools = [] }",
        "",
        "[feedback]",
        "enabled = false",
        "",
        "[analytics]",
        "enabled = false",
        "",
        "[shell_environment_policy]",
        'inherit = "none"',
        "ignore_default_excludes = false",
        "experimental_use_profile = false",
        "",
        "[shell_environment_policy.set]",
    ]
    if context.provider_profile is not None:
        profile = context.provider_profile
        lines[1:1] = [
            f"model = {_toml_string(profile.model)}",
            f"model_provider = {_toml_string(profile.provider_id)}",
        ]
    for name, value in sorted(tool_env.items(), key=lambda item: item[0].upper()):
        lines.append(f"{_toml_string(name)} = {_toml_string(value)}")

    if context.provider_profile is not None:
        profile = context.provider_profile
        lines.extend(
            [
                "",
                f"[model_providers.{profile.provider_id}]",
                f"name = {_toml_string(profile.name)}",
                f"base_url = {_toml_string(profile.base_url)}",
                f"wire_api = {_toml_string(profile.wire_api)}",
                f"requires_openai_auth = {str(profile.requires_openai_auth).lower()}",
            ]
        )
        if profile.auth_env_key is not None:
            lines.append(f"env_key = {_toml_string(profile.auth_env_key)}")

    lines.extend(
        [
            "",
            "[apps._default]",
            "enabled = false",
            "destructive_enabled = false",
            "open_world_enabled = false",
            *(
                ('approvals_reviewer = "user"', 'default_tools_approval_mode = "prompt"')
                if context.selected_app_ids
                else ()
            ),
            "",
            "[features]",
            *[
                f"{name} = {str(bool(context.selected_app_ids) if name == 'apps' else False).lower()}"
                for name in _LOCKED_OFF_FEATURES
            ],
            "",
            f"[permissions.{PERMISSION_PROFILE}]",
            'description = "Echo brokered Codex sidecar permissions."',
            "",
            f"[permissions.{PERMISSION_PROFILE}.workspace_roots]",
            f"{_toml_string(str(context.workspace))} = true",
            f"{_toml_string(str(context.scratch_root))} = true",
            "",
        ]
    )
    workspace_access = "write" if context.sandbox_mode == "workspace-write" else "read"
    for app_id in context.selected_app_ids:
        lines.extend(
            [
                "",
                f"[apps.{_toml_string(app_id)}]",
                "enabled = true",
                "destructive_enabled = false",
                "open_world_enabled = false",
                'approvals_reviewer = "user"',
                'default_tools_approval_mode = "prompt"',
            ]
        )
    lines.extend(
        [
            f"[permissions.{PERMISSION_PROFILE}.filesystem]",
            '":minimal" = "read"',
            f'{_toml_string(str(context.workspace))} = "{workspace_access}"',
            f'{_toml_string(str(context.scratch_root))} = "write"',
            *(
                f'{_toml_string(str(context.workspace / subpath))} = "read"'
                for subpath in _PROTECTED_WORKSPACE_SUBPATHS
            ),
            '":tmpdir" = "deny"',
            '":slash_tmp" = "deny"',
            f'{_toml_string(str(context.state_root))} = "deny"',
            "",
            f"[permissions.{PERMISSION_PROFILE}.network]",
            "enabled = false",
            "",
            f"[projects.{_toml_string(str(context.workspace))}]",
            'trust_level = "untrusted"',
            "",
        ]
    )
    return "\n".join(lines)


def _toml_string(value: str) -> str:
    # JSON basic-string escapes are a compatible subset of TOML basic strings.
    return json.dumps(value, ensure_ascii=False)


def _require_server_authority(authority: str) -> None:
    if authority != "server":
        raise CodexSecurityError("Codex thread bindings and auth seeding are server-only APIs")


def _validate_context_layout(
    context: CodexSidecarContext,
    policy: CodexSecurityPolicy,
) -> None:
    try:
        policy_root = policy.state_root.resolve(strict=True)
    except OSError as exc:
        raise CodexSecurityError("Codex sidecar state_root is unavailable") from exc
    if context.state_root != policy_root:
        raise CodexSecurityError("Codex sidecar context belongs to a different state_root")
    for label, value in (
        ("realm", context.realm_key),
        ("tenant", context.tenant_key),
        ("thread", context.thread_key),
        ("task", context.task_key),
    ):
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise CodexSecurityError(f"invalid {label} hash in Codex sidecar context")
    expected_thread_root = (
        context.state_root
        / "realms"
        / context.realm_key
        / "tenants"
        / context.tenant_key
        / "threads"
        / context.thread_key
    )
    if context.thread_root != expected_thread_root:
        raise CodexSecurityError("Codex sidecar thread_root does not match its identity hashes")
    if context.task_root != expected_thread_root / "tasks" / context.task_key:
        raise CodexSecurityError("Codex sidecar task_root does not match its identity hashes")
    if context.codex_home != expected_thread_root / "codex-home":
        raise CodexSecurityError("Codex sidecar CODEX_HOME is not thread-scoped")
    if context.binding_path != expected_thread_root / "binding.json":
        raise CodexSecurityError("Codex sidecar binding path is invalid")
    _lock_down_directory(context.thread_root)
    _lock_down_directory(context.codex_home)


def _normalize_inner_thread_id(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise CodexSecurityError("inner Codex thread id must be a UUID") from exc
    return str(parsed)


def _cwd_hash(workspace: Path) -> str:
    return hashlib.sha256(f"echo-codex-cwd\0{workspace}".encode()).hexdigest()


def _binding_for_context(
    context: CodexSidecarContext,
    inner_thread_id: str,
) -> CodexThreadBinding:
    return CodexThreadBinding(
        inner_thread_id=inner_thread_id,
        cwd_hash=_cwd_hash(context.workspace),
        realm_hash=context.realm_key,
        tenant_hash=context.tenant_key,
        thread_hash=context.thread_key,
    )


def _binding_payload(binding: CodexThreadBinding) -> dict[str, str]:
    return {
        "schema": _BINDING_KIND,
        "innerThreadId": binding.inner_thread_id,
        "cwdHash": binding.cwd_hash,
        "realmHash": binding.realm_hash,
        "tenantHash": binding.tenant_hash,
        "threadHash": binding.thread_hash,
    }


def _read_binding_file(context: CodexSidecarContext) -> CodexThreadBinding | None:
    data = _read_owned_private_file(context.binding_path, max_bytes=64 * 1024)
    if data is None:
        return None
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodexSecurityError("Codex thread binding is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("schema") != _BINDING_KIND:
        raise CodexSecurityError("Codex thread binding has an invalid schema")
    required = {
        "schema",
        "innerThreadId",
        "cwdHash",
        "realmHash",
        "tenantHash",
        "threadHash",
    }
    if set(payload) != required or any(not isinstance(payload[key], str) for key in required):
        raise CodexSecurityError("Codex thread binding has invalid fields")
    binding = CodexThreadBinding(
        inner_thread_id=_normalize_inner_thread_id(payload["innerThreadId"]),
        cwd_hash=payload["cwdHash"],
        realm_hash=payload["realmHash"],
        tenant_hash=payload["tenantHash"],
        thread_hash=payload["threadHash"],
    )
    expected = _binding_for_context(context, binding.inner_thread_id)
    if binding != expected:
        raise CodexSecurityError("Codex thread binding does not match realm/tenant/thread/cwd")
    return binding


__all__ = [
    "APPROVAL_FAILURE_DECISION",
    "APPROVAL_POLICY",
    "APPROVAL_REVIEWER",
    "PERMISSION_PROFILE",
    "ApprovalFailureDecision",
    "CodexSandboxMode",
    "CodexSecurityError",
    "CodexSecurityPolicy",
    "CodexSidecarContext",
    "CodexSidecarSecurity",
    "CodexThreadBinding",
    "DEFAULT_HOST_ENV_ALLOWLIST",
    "is_sensitive_env_name",
]
