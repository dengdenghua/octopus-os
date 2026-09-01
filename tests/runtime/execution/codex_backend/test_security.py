from __future__ import annotations

import copy
import json
import os
import stat
import tomllib
import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from runtime.execution.codex_backend.security import (
    APPROVAL_FAILURE_DECISION,
    APPROVAL_POLICY,
    APPROVAL_REVIEWER,
    PERMISSION_PROFILE,
    CodexSecurityError,
    CodexSecurityPolicy,
    CodexSidecarContext,
    CodexSidecarSecurity,
    is_sensitive_env_name,
)


def _manager(
    tmp_path: Path,
    *,
    deployment_mode: str = "local",
    require_outer_hard_sandbox: bool = False,
    state_root: Path | None = None,
    allowed_root: Path | None = None,
) -> tuple[CodexSidecarSecurity, Path, Path]:
    workspace_root = allowed_root or tmp_path / "workspaces"
    workspace_root.mkdir(parents=True, exist_ok=True)
    workspace = workspace_root / "project"
    workspace.mkdir(exist_ok=True)
    sidecar_root = state_root or tmp_path / "sidecar-state"
    manager = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=sidecar_root,
            allowed_workspace_roots=(workspace_root,),
            deployment_mode=deployment_mode,
            require_outer_hard_sandbox=require_outer_hard_sandbox,
        )
    )
    return manager, workspace, sidecar_root


def _prepare(
    manager: CodexSidecarSecurity,
    workspace: Path,
    *,
    realm_id: str = "realm-a",
    tenant_id: str = "tenant-a",
    thread_id: str = "thread-a",
    task_id: str = "task-a",
    sandbox_mode: str = "workspace-write",
    selected_app_ids: tuple[str, ...] = (),
    outer_hard_sandbox_active: bool = False,
    host_env: dict[str, str] | None = None,
) -> CodexSidecarContext:
    return manager.prepare(
        realm_id=realm_id,
        tenant_id=tenant_id,
        thread_id=thread_id,
        task_id=task_id,
        workspace=workspace,
        sandbox_mode=sandbox_mode,  # type: ignore[arg-type]
        selected_app_ids=selected_app_ids,
        outer_hard_sandbox_active=outer_hard_sandbox_active,
        host_env=host_env,
    )


def _config(context: CodexSidecarContext) -> dict[str, object]:
    return tomllib.loads(context.config_path.read_text(encoding="utf-8"))


def _private_file(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_thread_home_persists_while_task_and_scratch_are_isolated(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    first = _prepare(manager, workspace, task_id="task-1")
    second = _prepare(manager, workspace, task_id="task-2")

    assert first.thread_root == second.thread_root
    assert first.codex_home == second.codex_home
    assert first.config_path == second.config_path
    assert first.task_root != second.task_root
    assert first.scratch_root != second.scratch_root

    manager.write_server_binding(
        first,
        inner_thread_id=str(uuid.uuid4()),
        authority="server",
    )
    first.cleanup()

    assert not first.task_root.exists()
    assert not first.scratch_root.exists()
    assert first.thread_root.exists()
    assert first.codex_home.exists()
    assert first.config_path.exists()
    assert first.binding_path.exists()
    assert second.task_root.exists()
    assert second.scratch_root.exists()

    second.cleanup()
    second.cleanup()
    assert second.thread_root.exists()
    assert second.codex_home.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("realm_id", "realm-b"),
        ("tenant_id", "tenant-b"),
        ("thread_id", "thread-b"),
    ],
)
def test_realm_tenant_and_thread_each_partition_codex_home(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    baseline = _prepare(manager, workspace)
    ids = {
        "realm_id": "realm-a",
        "tenant_id": "tenant-a",
        "thread_id": "thread-a",
    }
    ids[field] = value
    other = _prepare(
        manager,
        workspace,
        realm_id=ids["realm_id"],
        tenant_id=ids["tenant_id"],
        thread_id=ids["thread_id"],
        task_id=f"task-for-{field}",
    )

    assert baseline.thread_root != other.thread_root
    assert baseline.codex_home != other.codex_home


def test_external_ids_are_hashed_and_cannot_shape_paths(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    raw_ids = ("../../realm", "/tenant/secret", "thread\\escape", "task/../escape")
    context = _prepare(
        manager,
        workspace,
        realm_id=raw_ids[0],
        tenant_id=raw_ids[1],
        thread_id=raw_ids[2],
        task_id=raw_ids[3],
    )

    rendered_paths = "\n".join(
        str(path)
        for path in (
            context.thread_root,
            context.task_root,
            context.codex_home,
            context.scratch_root,
        )
    )
    assert all(raw not in rendered_paths for raw in raw_ids)
    for key in (context.realm_key, context.tenant_key, context.thread_key, context.task_key):
        assert len(key) == 64
        assert set(key) <= set("0123456789abcdef")


def test_launch_and_tool_environments_do_not_inherit_credentials(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    host_env = {
        "Path": "/safe/bin",
        "LANG": "zh_CN.UTF-8",
        "TERM": "xterm-256color",
        "OPENAI_API_KEY": "openai-secret",
        "GITHUB_TOKEN": "github-secret",
        "SERVICE_PASSWORD": "password-secret",
        "SSH_PRIVATE_KEY": "private-secret",
        "AWS_CREDENTIAL_FILE": "/secret/credentials",
        "SSH_AUTH_SOCK": "/secret/socket",
        "SESSION_COOKIE": "cookie-secret",
        "PYTHONPATH": "/host/python",
        "UNLISTED": "ambient-value",
        "HOME": "/host/home",
        "TMPDIR": "/host/tmp",
    }
    context = _prepare(manager, workspace, host_env=host_env)
    launch_env = context.launch_env()

    assert launch_env["PATH"] == "/safe/bin"
    assert launch_env["LANG"] == "zh_CN.UTF-8"
    assert launch_env["HOME"] == str(context.app_home)
    assert launch_env["CODEX_HOME"] == str(context.codex_home)
    assert launch_env["TMPDIR"] == str(context.task_root / "tmp")
    for name in (
        "OPENAI_API_KEY",
        "GITHUB_TOKEN",
        "SERVICE_PASSWORD",
        "SSH_PRIVATE_KEY",
        "AWS_CREDENTIAL_FILE",
        "SSH_AUTH_SOCK",
        "SESSION_COOKIE",
        "PYTHONPATH",
        "UNLISTED",
    ):
        assert name not in launch_env

    tool_env = _config(context)["shell_environment_policy"]["set"]  # type: ignore[index]
    assert isinstance(tool_env, dict)
    assert tool_env["HOME"] == str(context.tool_home)
    assert tool_env["TMPDIR"] == str(context.tool_tmp)
    assert "CODEX_HOME" not in tool_env
    assert not any(value.endswith("-secret") for value in tool_env.values())


@pytest.mark.parametrize(
    "name",
    [
        "OPENAI_API_KEY",
        "github-token",
        "ServicePassword",
        "SSH__PRIVATE__KEY",
        "aws_credential_file",
        "SSH_AUTH_SOCK",
        "session_cookie",
    ],
)
def test_sensitive_environment_name_detection(name: str) -> None:
    assert is_sensitive_env_name(name)


def test_sensitive_names_cannot_be_added_to_host_allowlist(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    with pytest.raises(CodexSecurityError, match="credential-like"):
        CodexSecurityPolicy(
            state_root=tmp_path / "state",
            allowed_workspace_roots=(workspace_root,),
            host_env_allowlist=("PATH", "OPENAI_API_KEY"),
        )


def test_generated_workspace_write_config_is_locked_and_self_validating(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    config = _config(context)

    assert config["approval_policy"] == APPROVAL_POLICY == "on-request"
    assert config["approvals_reviewer"] == APPROVAL_REVIEWER == "user"
    assert config["default_permissions"] == PERMISSION_PROFILE
    assert config["mcp_servers"] == {}
    assert config["plugins"] == {}
    assert config["marketplaces"] == {}
    assert config["notify"] == []
    assert "sandbox_mode" not in config
    assert "sandbox_workspace_write" not in config
    assert config["projects"][str(workspace)]["trust_level"] == "untrusted"  # type: ignore[index]

    profile = config["permissions"][PERMISSION_PROFILE]  # type: ignore[index]
    assert "extends" not in profile
    assert profile["workspace_roots"] == {
        str(context.workspace): True,
        str(context.scratch_root): True,
    }
    assert profile["filesystem"] == {
        ":minimal": "read",
        str(context.workspace): "write",
        str(context.scratch_root): "write",
        str(context.workspace / ".git"): "read",
        str(context.workspace / ".agents"): "read",
        str(context.workspace / ".codex"): "read",
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
        str(context.state_root): "deny",
    }
    assert profile["network"] == {"enabled": False}
    features = config["features"]
    assert isinstance(features, dict)
    assert features and not any(features.values())

    context.validate_effective_config({"config": config})
    assert APPROVAL_FAILURE_DECISION == "decline"


def test_selected_apps_are_exact_and_keep_high_risk_tools_prompted(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace, selected_app_ids=("google_drive",))
    config = _config(context)

    assert config["features"]["apps"] is True  # type: ignore[index]
    assert config["apps"] == {
        "_default": {
            "enabled": False,
            "destructive_enabled": False,
            "open_world_enabled": False,
            "approvals_reviewer": "user",
            "default_tools_approval_mode": "prompt",
        },
        "google_drive": {
            "enabled": True,
            "destructive_enabled": False,
            "open_world_enabled": False,
            "approvals_reviewer": "user",
            "default_tools_approval_mode": "prompt",
        },
    }
    context.validate_effective_config({"config": config})

    unsafe = copy.deepcopy(config)
    unsafe["apps"]["google_drive"]["destructive_enabled"] = True  # type: ignore[index]
    with pytest.raises(CodexSecurityError, match="locked approval policy"):
        context.validate_effective_config({"config": unsafe})


def test_generated_read_only_config_keeps_workspace_read_only_and_private_scratch_writable(
    tmp_path: Path,
) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace, sandbox_mode="read-only")
    config = _config(context)
    profile = config["permissions"][PERMISSION_PROFILE]  # type: ignore[index]

    assert "extends" not in profile
    assert profile["workspace_roots"] == {
        str(context.workspace): True,
        str(context.scratch_root): True,
    }
    assert profile["filesystem"] == {
        ":minimal": "read",
        str(context.workspace): "read",
        str(context.scratch_root): "write",
        str(context.workspace / ".git"): "read",
        str(context.workspace / ".agents"): "read",
        str(context.workspace / ".codex"): "read",
        ":tmpdir": "deny",
        ":slash_tmp": "deny",
        str(context.state_root): "deny",
    }
    context.validate_effective_config(config)


def test_selected_apps_keep_locked_non_destructive_approval_policy(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace, selected_app_ids=("connector-a",))
    config = _config(context)

    assert config["features"]["apps"] is True  # type: ignore[index]
    assert config["apps"]["_default"] == {  # type: ignore[index]
        "enabled": False,
        "destructive_enabled": False,
        "open_world_enabled": False,
        "approvals_reviewer": "user",
        "default_tools_approval_mode": "prompt",
    }
    assert config["apps"]["connector-a"] == {  # type: ignore[index]
        "enabled": True,
        "destructive_enabled": False,
        "open_world_enabled": False,
        "approvals_reviewer": "user",
        "default_tools_approval_mode": "prompt",
    }
    context.validate_effective_config(config)

    widened = copy.deepcopy(config)
    widened["apps"]["connector-a"]["destructive_enabled"] = True  # type: ignore[index]
    with pytest.raises(CodexSecurityError, match="apps.connector-a"):
        context.validate_effective_config(widened)


def test_effective_config_accepts_app_server_schema_expansion_with_nulls(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    config = _config(context)
    profile = config["permissions"][PERMISSION_PROFILE]  # type: ignore[index]
    profile["filesystem"]["glob_scan_max_depth"] = None
    profile["network"].update(  # type: ignore[union-attr]
        {
            "proxy_url": None,
            "domains": None,
            "unix_sockets": None,
            "mitm": None,
        }
    )
    config["agents"].update({"max_depth": None, "default_subagent_model": None})  # type: ignore[attr-defined]
    config["apps"]["_default"].update(  # type: ignore[index,union-attr]
        {"approvals_reviewer": None, "default_tools_approval_mode": None}
    )
    config["features"]["mentions_v2"] = True  # type: ignore[index]
    config["skills"] = {}

    context.validate_effective_config({"config": config})


def test_thread_and_turn_overrides_cannot_select_legacy_or_remote_capabilities(
    tmp_path: Path,
) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    thread = context.thread_start_security_overrides()
    turn = context.turn_start_security_overrides()

    for payload in (thread, turn):
        assert payload["cwd"] == str(workspace)
        assert payload["runtimeWorkspaceRoots"] == [str(workspace)]
        assert payload["approvalPolicy"] == "on-request"
        assert payload["approvalsReviewer"] == "user"
        assert payload["permissions"] == PERMISSION_PROFILE
        assert "environments" not in payload
        assert "sandbox" not in payload
        assert "sandboxPolicy" not in payload
    assert thread["dynamicTools"] == []
    assert thread["selectedCapabilityRoots"] == []


def test_effective_config_validation_rejects_policy_drift(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    base = _config(context)
    unsafe_configs: list[dict[str, object]] = []

    approval = copy.deepcopy(base)
    approval["approval_policy"] = "never"
    unsafe_configs.append(approval)
    reviewer = copy.deepcopy(base)
    reviewer["approvals_reviewer"] = "auto_review"
    unsafe_configs.append(reviewer)
    mcp = copy.deepcopy(base)
    mcp["mcp_servers"] = {"ambient": {"command": "steal"}}
    unsafe_configs.append(mcp)
    legacy = copy.deepcopy(base)
    legacy["sandbox_mode"] = "workspace-write"
    unsafe_configs.append(legacy)
    roots = copy.deepcopy(base)
    roots["permissions"][PERMISSION_PROFILE]["workspace_roots"]["/extra"] = True  # type: ignore[index]
    unsafe_configs.append(roots)
    inherited_root_read = copy.deepcopy(base)
    inherited_root_read["permissions"][PERMISSION_PROFILE]["extends"] = ":workspace"  # type: ignore[index]
    unsafe_configs.append(inherited_root_read)
    full_host_read = copy.deepcopy(base)
    full_host_read["permissions"][PERMISSION_PROFILE]["filesystem"][":root"] = "read"  # type: ignore[index]
    unsafe_configs.append(full_host_read)
    missing_minimal_runtime = copy.deepcopy(base)
    del missing_minimal_runtime["permissions"][PERMISSION_PROFILE]["filesystem"][":minimal"]  # type: ignore[index]
    unsafe_configs.append(missing_minimal_runtime)
    filesystem = copy.deepcopy(base)
    del filesystem["permissions"][PERMISSION_PROFILE]["filesystem"][str(context.state_root)]  # type: ignore[index]
    unsafe_configs.append(filesystem)
    network = copy.deepcopy(base)
    network["permissions"][PERMISSION_PROFILE]["network"]["enabled"] = True  # type: ignore[index]
    unsafe_configs.append(network)
    tool_env = copy.deepcopy(base)
    tool_env["shell_environment_policy"]["set"]["OPENAI_API_KEY"] = "secret"  # type: ignore[index]
    unsafe_configs.append(tool_env)
    plugin = copy.deepcopy(base)
    plugin["plugins"] = {"ambient": {"enabled": True}}
    unsafe_configs.append(plugin)
    marketplace = copy.deepcopy(base)
    marketplace["marketplaces"] = {"ambient": {"source": "https://example.invalid"}}
    unsafe_configs.append(marketplace)
    skills = copy.deepcopy(base)
    skills["skills"] = {"config": [{"path": "/ambient/SKILL.md", "enabled": True}]}
    unsafe_configs.append(skills)
    suggestions = copy.deepcopy(base)
    suggestions["tool_suggest"] = {
        "discoverables": [{"name": "ambient"}],
        "disabled_tools": [],
    }
    unsafe_configs.append(suggestions)
    app = copy.deepcopy(base)
    app["apps"]["ambient"] = {"enabled": True}  # type: ignore[index]
    unsafe_configs.append(app)
    hook = copy.deepcopy(base)
    hook["hooks"] = {"after_turn": [{"command": "steal"}]}
    unsafe_configs.append(hook)
    unknown_feature = copy.deepcopy(base)
    unknown_feature["features"]["future_executable_surface"] = True  # type: ignore[index]
    unsafe_configs.append(unknown_feature)

    for unsafe in unsafe_configs:
        with pytest.raises(CodexSecurityError, match="unsafe effective"):
            context.validate_effective_config({"config": unsafe})


def test_workspace_must_be_absolute_existing_directory_under_allowed_root(
    tmp_path: Path,
) -> None:
    manager, workspace, _state_root = _manager(tmp_path)

    with pytest.raises(CodexSecurityError, match="absolute"):
        _prepare(manager, Path("relative/workspace"))
    with pytest.raises(CodexSecurityError, match="does not exist"):
        _prepare(manager, workspace / "missing")
    regular_file = workspace.parent / "file.txt"
    regular_file.write_text("not a directory", encoding="utf-8")
    with pytest.raises(CodexSecurityError, match="not a directory"):
        _prepare(manager, regular_file)
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(CodexSecurityError, match="escapes"):
        _prepare(manager, outside)


def test_workspace_symlink_escape_and_state_overlap_are_rejected(tmp_path: Path) -> None:
    manager, workspace, state_root = _manager(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    escape = workspace.parent / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(CodexSecurityError, match="escapes"):
        _prepare(manager, escape)

    state_root.mkdir(exist_ok=True)
    overlap = state_root / "project"
    overlap.mkdir()
    overlapping_manager = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=state_root,
            allowed_workspace_roots=(tmp_path,),
        )
    )
    with pytest.raises(CodexSecurityError, match="must not overlap"):
        _prepare(overlapping_manager, overlap)


def test_filesystem_root_cannot_be_an_allowed_workspace_root(tmp_path: Path) -> None:
    manager = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=tmp_path / "state",
            allowed_workspace_roots=(Path("/"),),
        )
    )
    with pytest.raises(CodexSecurityError, match="filesystem root"):
        _prepare(manager, tmp_path)


def test_outer_hard_sandbox_requirement_fails_before_creating_state(tmp_path: Path) -> None:
    manager, workspace, state_root = _manager(tmp_path, deployment_mode="production")

    with pytest.raises(CodexSecurityError, match="outer hard sandbox"):
        _prepare(manager, workspace)
    assert not state_root.exists()

    context = _prepare(manager, workspace, outer_hard_sandbox_active=True)
    assert context.config_path.exists()


def test_explicit_hard_sandbox_requirement_applies_to_local_mode(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path, require_outer_hard_sandbox=True)
    with pytest.raises(CodexSecurityError, match="outer hard sandbox"):
        _prepare(manager, workspace)


def test_danger_full_access_mode_is_never_accepted(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    with pytest.raises(CodexSecurityError, match="only allow"):
        _prepare(manager, workspace, sandbox_mode="danger-full-access")


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode assertion")
def test_created_paths_use_private_permissions(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    manager.write_server_binding(
        context,
        inner_thread_id=str(uuid.uuid4()),
        authority="server",
    )

    for directory in (
        context.state_root,
        context.thread_root,
        context.task_root,
        context.codex_home,
        context.app_home,
        context.scratch_root,
        context.tool_home,
        context.tool_tmp,
    ):
        assert _mode(directory) == 0o700
        assert directory.stat().st_uid == os.geteuid()
    for private_file in (
        context.config_path,
        context.binding_path,
        context.thread_root / ".echo-codex-sidecar.json",
        context.task_root / ".echo-codex-sidecar.json",
        context.scratch_marker_path,
    ):
        assert _mode(private_file) == 0o600
        assert private_file.stat().st_uid == os.geteuid()


def test_state_root_and_existing_config_symlink_attacks_fail_closed(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    workspace_root.mkdir()
    workspace = workspace_root / "project"
    workspace.mkdir()
    real_state = tmp_path / "real-state"
    real_state.mkdir()
    linked_state = tmp_path / "linked-state"
    try:
        linked_state.symlink_to(real_state, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    manager = CodexSidecarSecurity(
        CodexSecurityPolicy(
            state_root=linked_state,
            allowed_workspace_roots=(workspace_root,),
        )
    )
    with pytest.raises(CodexSecurityError, match="cannot be a symlink"):
        _prepare(manager, workspace)

    safe_manager, safe_workspace, _safe_state = _manager(tmp_path / "safe")
    first = _prepare(safe_manager, safe_workspace, task_id="first")
    target = tmp_path / "config-target"
    target.write_text("unchanged", encoding="utf-8")
    first.config_path.unlink()
    first.config_path.symlink_to(target)
    with pytest.raises(CodexSecurityError, match="symlink"):
        _prepare(safe_manager, safe_workspace, task_id="second")
    assert target.read_text(encoding="utf-8") == "unchanged"


def test_server_binding_is_private_atomic_identity_checked_and_server_only(
    tmp_path: Path,
) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    inner_id = str(uuid.uuid4())

    with pytest.raises(CodexSecurityError, match="server-only"):
        manager.write_server_binding(context, inner_thread_id=inner_id, authority="client")
    assert manager.read_server_binding(context, authority="server") is None

    binding = manager.write_server_binding(
        context,
        inner_thread_id=inner_id.upper(),
        authority="server",
    )
    assert binding.inner_thread_id == inner_id
    assert manager.read_server_binding(context, authority="server") == binding
    payload = json.loads(context.binding_path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema",
        "innerThreadId",
        "cwdHash",
        "realmHash",
        "tenantHash",
        "threadHash",
    }
    assert payload["innerThreadId"] == inner_id

    assert (
        manager.write_server_binding(
            context,
            inner_thread_id=inner_id,
            authority="server",
        )
        == binding
    )
    replacement = str(uuid.uuid4())
    with pytest.raises(CodexSecurityError, match="conflicting"):
        manager.write_server_binding(
            context,
            inner_thread_id=replacement,
            authority="server",
        )
    replaced = manager.write_server_binding(
        context,
        inner_thread_id=replacement,
        authority="server",
        replace=True,
    )
    assert replaced.inner_thread_id == replacement


def test_binding_prevents_resume_under_a_different_workspace(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    other_workspace = workspace.parent / "other"
    other_workspace.mkdir()
    context = _prepare(manager, workspace)
    manager.write_server_binding(
        context,
        inner_thread_id=str(uuid.uuid4()),
        authority="server",
    )

    with pytest.raises(CodexSecurityError, match="does not match"):
        _prepare(manager, other_workspace, task_id="next-task")


def test_binding_rejects_invalid_ids_tampering_and_symlinks(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    with pytest.raises(CodexSecurityError, match="UUID"):
        manager.write_server_binding(
            context,
            inner_thread_id="chosen-by-client",
            authority="server",
        )

    manager.write_server_binding(
        context,
        inner_thread_id=str(uuid.uuid4()),
        authority="server",
    )
    payload = json.loads(context.binding_path.read_text(encoding="utf-8"))
    payload["cwdHash"] = "0" * 64
    _private_file(context.binding_path, json.dumps(payload).encode())
    with pytest.raises(CodexSecurityError, match="does not match"):
        manager.read_server_binding(context, authority="server")

    target = tmp_path / "binding-target"
    _private_file(target, b"{}")
    context.binding_path.unlink()
    context.binding_path.symlink_to(target)
    with pytest.raises(CodexSecurityError, match="non-symlink"):
        manager.read_server_binding(context, authority="server")


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission assertion")
def test_binding_rejects_group_or_world_access(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    manager.write_server_binding(
        context,
        inner_thread_id=str(uuid.uuid4()),
        authority="server",
    )
    context.binding_path.chmod(0o644)
    with pytest.raises(CodexSecurityError, match="owner-only"):
        manager.read_server_binding(context, authority="server")


def test_auth_seed_missing_and_valid_source(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    source_home = tmp_path / "source-codex"
    source_home.mkdir()

    assert not manager.seed_auth_from_codex_home(
        context,
        source_codex_home=source_home,
        authority="server",
    )
    source_auth = {"tokens": {"access_token": "subscription-token"}, "mode": "chatgpt"}
    _private_file(source_home / "auth.json", json.dumps(source_auth).encode("utf-8"))
    assert manager.seed_auth_from_codex_home(
        context,
        source_codex_home=source_home,
        authority="server",
    )

    destination = context.codex_home / "auth.json"
    assert json.loads(destination.read_text(encoding="utf-8")) == source_auth
    if os.name == "posix":
        assert _mode(destination) == 0o600
        assert destination.stat().st_uid == os.geteuid()
    assert "subscription-token" not in context.config_path.read_text(encoding="utf-8")
    assert "subscription-token" not in context.launch_env().values()


def test_auth_seed_is_explicit_and_server_only(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    with pytest.raises(CodexSecurityError, match="server-only"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=tmp_path,
            authority="client",
        )
    with pytest.raises(CodexSecurityError, match="absolute"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=Path(".codex"),
            authority="server",
        )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not-json", "valid UTF-8 JSON"),
        (json.dumps(["not", "an", "object"]).encode(), "JSON object"),
        (b"\xff", "valid UTF-8 JSON"),
        (b'{"value": NaN}', "valid UTF-8 JSON"),
        (b"\xff\xfe{\x00}\x00", "valid UTF-8 JSON"),
    ],
)
def test_auth_seed_rejects_invalid_json(tmp_path: Path, data: bytes, message: str) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    source_home = tmp_path / "source-codex"
    source_home.mkdir()
    _private_file(source_home / "auth.json", data)

    with pytest.raises(CodexSecurityError, match=message):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=source_home,
            authority="server",
        )


def test_auth_seed_rejects_symlink_nonregular_and_oversize_sources(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)

    symlink_home = tmp_path / "symlink-home"
    symlink_home.mkdir()
    target = tmp_path / "real-auth.json"
    _private_file(target, b"{}")
    try:
        (symlink_home / "auth.json").symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable")
    with pytest.raises(CodexSecurityError, match="non-symlink"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=symlink_home,
            authority="server",
        )

    directory_home = tmp_path / "directory-home"
    directory_home.mkdir()
    (directory_home / "auth.json").mkdir()
    with pytest.raises(CodexSecurityError, match="regular"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=directory_home,
            authority="server",
        )

    large_home = tmp_path / "large-home"
    large_home.mkdir()
    _private_file(large_home / "auth.json", b"{" + b" " * (1024 * 1024) + b"}")
    with pytest.raises(CodexSecurityError, match="exceeds"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=large_home,
            authority="server",
        )


def test_auth_seed_rejects_symlink_source_home_and_destination(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    real_home = tmp_path / "real-home"
    real_home.mkdir()
    _private_file(real_home / "auth.json", b"{}")
    linked_home = tmp_path / "linked-home"
    try:
        linked_home.symlink_to(real_home, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(CodexSecurityError, match="source_codex_home"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=linked_home,
            authority="server",
        )

    target = tmp_path / "destination-target"
    target.write_text("unchanged", encoding="utf-8")
    destination = context.codex_home / "auth.json"
    destination.symlink_to(target)
    with pytest.raises(CodexSecurityError, match="symlink"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=real_home,
            authority="server",
        )
    assert target.read_text(encoding="utf-8") == "unchanged"


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission assertion")
def test_auth_seed_rejects_group_or_world_readable_source(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    source_home = tmp_path / "source-codex"
    source_home.mkdir()
    source = source_home / "auth.json"
    source.write_text("{}", encoding="utf-8")
    source.chmod(0o644)

    with pytest.raises(CodexSecurityError, match="owner-only"):
        manager.seed_auth_from_codex_home(
            context,
            source_codex_home=source_home,
            authority="server",
        )


def test_cleanup_refuses_invalid_markers_and_outside_paths(tmp_path: Path) -> None:
    manager, workspace, _state_root = _manager(tmp_path)
    context = _prepare(manager, workspace)
    task_marker = context.task_root / ".echo-codex-sidecar.json"
    _private_file(task_marker, b"{}")

    with pytest.raises(CodexSecurityError, match="invalid marker"):
        context.cleanup()
    assert context.task_root.exists()
    assert context.thread_root.exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    forged = replace(context, task_root=outside)
    with pytest.raises(CodexSecurityError, match="outside sidecar state_root|invalid task path"):
        forged.cleanup()
    assert outside.exists()

