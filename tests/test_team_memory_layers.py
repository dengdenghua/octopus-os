from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.execution.agents.loader import (
    _memory_tier_paths,
    compose_runtime_soul,
)
from runtime.execution.suckers import memory_skills
from runtime.platform.process.session import Session, session_scope


class _StubAgent:
    def __init__(self, agent_id: str = "coder", soul: str = "") -> None:
        self.agent_id = agent_id
        self.soul = soul
        self.capabilities: dict[str, Any] = {}


def test_memory_tiers_include_team_layers_when_metadata_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)

    agent_dir = tmp_path / "agents" / "coder"
    core = agent_dir / "agent-core"
    core.mkdir(parents=True)

    tiers = _memory_tier_paths(
        agent_dir,
        core,
        metadata={"team_id": "Alpha Team", "workspace_path": str(workspace)},
    )

    assert [name for name, _ in tiers] == [
        "global",
        "project",
        "team",
        "team-agent",
        "agent",
    ]
    assert tiers[0][1] == tmp_path / "home" / "MEMORY.md"
    assert tiers[1][1] == workspace / ".echo" / "MEMORY.md"
    assert tiers[2][1] == tmp_path / "teams" / "Alpha-Team" / "team-core" / "MEMORY.md"
    assert tiers[3][1] == tmp_path / "teams" / "Alpha-Team" / "agents" / "coder" / "MEMORY.md"
    assert tiers[4][1] == core / "MEMORY.md"


def test_runtime_soul_strips_stale_static_memory_and_injects_team_layers(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".echo").mkdir()
    (workspace / ".echo" / "MEMORY.md").write_text(
        "project convention: use pytest",
        encoding="utf-8",
    )
    (tmp_path / "teams" / "Alpha-Team" / "team-core").mkdir(parents=True)
    (tmp_path / "teams" / "Alpha-Team" / "team-core" / "MEMORY.md").write_text(
        "team decision: ship full backend",
        encoding="utf-8",
    )
    (tmp_path / "teams" / "Alpha-Team" / "agents" / "coder").mkdir(parents=True)
    (tmp_path / "teams" / "Alpha-Team" / "agents" / "coder" / "MEMORY.md").write_text(
        "team-agent note: coder owns migrations",
        encoding="utf-8",
    )
    (tmp_path / "agents" / "coder" / "agent-core").mkdir(parents=True)
    (tmp_path / "agents" / "coder" / "agent-core" / "MEMORY.md").write_text(
        "agent note: prefers small patches",
        encoding="utf-8",
    )

    agent = _StubAgent(
        "coder",
        soul=(
            "base persona\n\n"
            "## Long-term Memory (agent)\n\n"
            "stale memory should disappear\n\n"
            "## REMINDER\n\n"
            "stay in character"
        ),
    )

    soul = compose_runtime_soul(
        agent,
        metadata={"team_id": "Alpha Team", "workspace_path": str(workspace)},
        repo_root=tmp_path,
    )

    assert "stale memory should disappear" not in soul
    assert "Long-term Memory (project)" in soul
    assert "Long-term Memory (team)" in soul
    assert "Long-term Memory (team-agent)" in soul
    assert "Long-term Memory (agent)" in soul
    assert "ship full backend" in soul
    assert "coder owns migrations" in soul


def test_remember_and_recall_support_team_and_team_agent_scopes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(memory_skills, "_PROJECT_ROOT", tmp_path)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = Session(
        actor="u",
        agent=_StubAgent("coder"),
        thread_id="t1",
        conversation_id="t1",
        metadata={"team_id": "Alpha Team", "workspace_path": str(workspace)},
    )

    with session_scope(session):
        team = memory_skills._remember("shared team decision", scope="team")
        member = memory_skills._remember("member-specific finding", scope="team_agent")
        project = memory_skills._remember("repo uses pytest", scope="project")
        recalled = memory_skills._recall(scope="all", limit=10)

    assert team["scope"] == "team"
    assert member["scope"] == "team_agent"
    assert project["scope"] == "project"
    assert Path(team["path"]).exists()
    assert Path(member["path"]).exists()
    assert Path(project["path"]).exists()
    assert any(
        "[team] " in entry and "shared team decision" in entry for entry in recalled["entries"]
    )
    assert any(
        "[team-agent] " in entry and "member-specific finding" in entry
        for entry in recalled["entries"]
    )
    assert any(
        "[project] " in entry and "repo uses pytest" in entry for entry in recalled["entries"]
    )
