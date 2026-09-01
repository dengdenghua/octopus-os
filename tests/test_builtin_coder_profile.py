"""Regression coverage for built-in Codex execution boundaries."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.agents.loader import parse_template
from runtime.execution.codex_backend import role_runner


def test_all_builtin_agents_select_codex_app_server(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    profile_paths = sorted((root / "agents").glob("*/profile.jsonc"))
    assert profile_paths

    monkeypatch.setattr(role_runner, "deployment_mode", lambda: "local")
    monkeypatch.setattr(role_runner, "_explicit_feature_flag", lambda: None)

    for profile_path in profile_paths:
        template = parse_template(profile_path.parent, root / "agents" / "_shared")
        assert template.capabilities.get("execution_backend") == "codex_app_server", template.agent_id
        assert "code_mode_unlock" not in template.capabilities, template.agent_id
        assert role_runner.agent_uses_codex_execution_backend(template) is True, template.agent_id
