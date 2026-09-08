"""Both Codex entry points must enforce the same host/login boundary."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.execution.codex_backend import role_runner
from runtime.execution.codex_backend.security import CodexSecurityError
from runtime.platform.runtime_policy import feature_flags
from runtime.sensing.gateway import realtime_codex_backend


@pytest.mark.parametrize("mode", ["commercial", "production", "server", "shared"])
def test_production_never_inherits_local_login(monkeypatch, mode):
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", f" {mode.upper()} ")
    monkeypatch.delenv("ECHO_CODEX_SOURCE_HOME", raising=False)
    assert role_runner.source_codex_home() is None
    assert realtime_codex_backend._source_codex_home() is None


@pytest.mark.parametrize("mode", ["local", "production"])
def test_login_source_requires_absolute_operator_path(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", mode)
    monkeypatch.setenv("ECHO_CODEX_SOURCE_HOME", "relative-login")
    for resolve in (role_runner.source_codex_home, realtime_codex_backend._source_codex_home):
        with pytest.raises(CodexSecurityError, match="must be absolute"):
            resolve()
    source = tmp_path / "operator-login"
    monkeypatch.setenv("ECHO_CODEX_SOURCE_HOME", str(source))
    assert role_runner.source_codex_home() == source.resolve()
    assert realtime_codex_backend._source_codex_home() == source.resolve()
    assert not source.exists()  # Resolving policy never creates or copies credentials.


def test_local_login_default_is_shared(monkeypatch):
    monkeypatch.delenv("ECHO_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("ECHO_CODEX_SOURCE_HOME", raising=False)
    expected = (Path.home() / ".codex").resolve()
    assert role_runner.source_codex_home() == expected
    assert realtime_codex_backend._source_codex_home() == expected


@pytest.mark.parametrize("capabilities", [None, {}, {"execution_backend": "echo"}])
def test_native_agents_are_not_routed_into_codex(capabilities):
    agent = SimpleNamespace(capabilities=capabilities)
    assert not role_runner.agent_uses_codex_execution_backend(agent)
    assert not realtime_codex_backend.agent_is_codex_app_server_partner(agent)


@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), (1, False), ("false", False),
    ({"enabled": True}, False), ([True], False), (None, False),
])
def test_local_engine_gate_accepts_only_boolean_configuration(monkeypatch, tmp_path, value, expected):
    previous_file = feature_flags._FILE_PATH
    config = tmp_path / "flags.json"
    config.write_text(json.dumps({"execution.codex_app_server": value}), encoding="utf-8")
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "local")
    monkeypatch.delenv("ECHO_FF_EXECUTION_CODEX_APP_SERVER", raising=False)
    monkeypatch.delenv("ECHO_CODEX_APP_SERVER_ENABLED", raising=False)
    agent = SimpleNamespace(capabilities={"execution_backend": "codex_app_server"})
    try:
        feature_flags.configure(config)
        assert role_runner.agent_uses_codex_execution_backend(agent) is expected
        assert realtime_codex_backend.agent_is_codex_app_server_partner(agent) is expected
    finally:
        feature_flags.configure(previous_file)

@pytest.mark.parametrize("approved, readonly, expected", [
    (False, False, "workspace-write"), (True, False, "danger-full-access"),
    (True, True, "read-only"), (1, False, "workspace-write"),
])
def test_shared_request_policy_keeps_parent_readonly_and_strict_approval(monkeypatch, approved, readonly, expected):
    monkeypatch.setenv("ECHO_CODEX_REALM", "shared-realm")
    policy = role_runner.codex_request_policy(
        {}, trusted_parent_metadata={"workspace_contract": "read-only"} if readonly else {},
        server_auto_approve=approved,
    )
    assert policy["realm_id"] == "shared-realm"
    assert policy["sandbox_mode"] == expected
    assert policy["approval_policy"] == ("never" if approved is True else "on-request")
