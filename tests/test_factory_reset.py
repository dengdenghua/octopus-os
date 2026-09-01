from __future__ import annotations

import json
from pathlib import Path

from runtime.platform.lifecycle.factory_reset import perform_factory_reset


def _slash(path: str) -> str:
    return path.replace("\\", "/")


def test_factory_reset_clears_project_runtime_state_but_preserves_agents(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()

    data_dir = project_root / "data"
    echo_dir = project_root / ".echo"
    agents_dir = project_root / "agents"
    agent_dir = agents_dir / "general"
    agent_sessions_dir = agent_dir / "sessions"
    teams_dir = project_root / "teams"
    team_dir = teams_dir / "family"
    team_sessions_dir = team_dir / "sessions"
    data_dir.mkdir()
    echo_dir.mkdir()
    agent_sessions_dir.mkdir(parents=True)
    team_sessions_dir.mkdir(parents=True)

    (data_dir / "threads.jsonl").write_text("thread", encoding="utf-8")
    (data_dir / "workspaces").mkdir()
    (echo_dir / "intelligence.json").write_text("{}", encoding="utf-8")
    (agents_dir / "general.txt").write_text("keep me", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text("name: general\n", encoding="utf-8")
    (agent_sessions_dir / "old-chat.jsonl").write_text("chat", encoding="utf-8")
    (agent_sessions_dir / "session_index.jsonl").write_text("{}", encoding="utf-8")
    (team_dir / "team.yaml").write_text("name: family\n", encoding="utf-8")
    (team_sessions_dir / "old-team-chat.jsonl").write_text("chat", encoding="utf-8")
    (team_sessions_dir / "session_index.jsonl").write_text("{}", encoding="utf-8")

    home = tmp_path / "home"
    installed_dir = home / ".echo"
    installed_dir.mkdir(parents=True)
    (installed_dir / "agents-installed.json").write_text(
        json.dumps({"installed": ["general"]}),
        encoding="utf-8",
    )

    result = perform_factory_reset(
        project_root=project_root,
        user_home=home,
        clear_user_install_state=True,
    )

    assert not data_dir.exists()
    assert not echo_dir.exists()
    assert agents_dir.exists()
    assert (agents_dir / "general.txt").exists()
    assert agent_dir.exists()
    assert (agent_dir / "agent.yaml").exists()
    assert not agent_sessions_dir.exists()
    assert team_dir.exists()
    assert (team_dir / "team.yaml").exists()
    assert not team_sessions_dir.exists()
    assert not (installed_dir / "agents-installed.json").exists()
    assert any(path.endswith("data") for path in result.removed_paths)
    assert any(path.endswith(".echo") for path in result.removed_paths)
    removed_paths = [_slash(path) for path in result.removed_paths]
    assert any(path.endswith("agents/general/sessions") for path in removed_paths)
    assert any(path.endswith("teams/family/sessions") for path in removed_paths)
    assert any(path.endswith("agents-installed.json") for path in result.removed_paths)


def test_factory_reset_can_skip_user_install_state(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "data").mkdir()
    home = tmp_path / "home"
    installed_dir = home / ".echo"
    installed_dir.mkdir(parents=True)
    install_state = installed_dir / "agents-installed.json"
    install_state.write_text(json.dumps({"installed": ["general"]}), encoding="utf-8")

    result = perform_factory_reset(
        project_root=project_root,
        user_home=home,
        clear_user_install_state=False,
    )

    assert not (project_root / "data").exists()
    assert install_state.exists()
    assert all(not path.endswith("agents-installed.json") for path in result.removed_paths)
