from __future__ import annotations

from pathlib import Path

from runtime.memory import user_store
from runtime.memory.runtime_state.hub import (
    MemoryHub,
    MemoryQuery,
    MemoryRecord,
    format_records_for_prompt,
)


def test_memory_hub_retrieves_user_store_facts_with_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    user_store.add_fact(
        "Use blue green rollout for Echo deploys",
        category="ops",
        source="manual",
        scope="project",
        project="echo",
    )
    user_store.add_fact(
        "The user prefers concise Chinese architecture notes",
        category="profile",
        source="manual",
        scope="global",
    )

    records = MemoryHub(repo_root=tmp_path).retrieve(
        MemoryQuery(
            text="echo rollout",
            project="echo",
            agent_id="general",
            limit=5,
        )
    )

    assert records
    top = records[0]
    assert top.kind == "fact"
    assert top.scope == "project"
    assert top.source == "user_store"
    assert "blue green" in top.content
    assert top.score > 0


def test_memory_hub_respects_user_store_injection_toggle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    user_store.add_fact(
        "Do not inject this disabled memory",
        category="ops",
        source="manual",
    )
    user_store.write_config({"injection_enabled": False})

    records = MemoryHub(repo_root=tmp_path).retrieve(MemoryQuery(text="disabled memory", limit=5))

    assert all("disabled memory" not in record.content for record in records)


def test_memory_hub_reads_global_project_and_agent_memory_files(
    tmp_path: Path,
    monkeypatch,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ECHO_HOME", str(home))
    monkeypatch.chdir(repo)

    (home / "MEMORY.md").parent.mkdir(parents=True)
    (home / "MEMORY.md").write_text(
        "- [2026-05-01 · pref] User likes evidence-first answers\n",
        encoding="utf-8",
    )
    (repo / ".echo").mkdir()
    (repo / ".echo" / "MEMORY.md").write_text(
        "- [2026-05-02 · project] This repo uses pytest for runtime checks\n",
        encoding="utf-8",
    )
    core = repo / "agents" / "general" / "agent-core"
    core.mkdir(parents=True)
    (core / "MEMORY.md").write_text(
        "- [2026-05-03 · agent] Prefer rg before slower file search\n",
        encoding="utf-8",
    )

    records = MemoryHub(repo_root=repo).retrieve(
        MemoryQuery(text="pytest rg evidence", agent_id="general", limit=10)
    )

    contents = [record.content for record in records]
    assert any("evidence-first" in content for content in contents)
    assert any("pytest" in content for content in contents)
    assert any("Prefer rg" in content for content in contents)
    assert {record.source for record in records} >= {
        "memory_md:global",
        "memory_md:project",
        "memory_md:agent",
    }


def test_memory_hub_reads_team_memory_layers_when_team_id_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "home"))
    monkeypatch.chdir(repo)

    team_core = repo / "teams" / "Alpha-Team" / "team-core"
    team_core.mkdir(parents=True)
    (team_core / "MEMORY.md").write_text(
        "- [2026-05-04 team] Alpha uses release captain reviews\n",
        encoding="utf-8",
    )
    team_agent = repo / "teams" / "Alpha-Team" / "agents" / "coder"
    team_agent.mkdir(parents=True)
    (team_agent / "MEMORY.md").write_text(
        "- [2026-05-04 member] coder owns rollout checklist\n",
        encoding="utf-8",
    )

    records = MemoryHub(repo_root=repo).retrieve(
        MemoryQuery(
            text="release captain rollout checklist",
            agent_id="coder",
            team_id="Alpha Team",
            limit=10,
        )
    )

    contents = [record.content for record in records]
    assert any("release captain" in content for content in contents)
    assert any("rollout checklist" in content for content in contents)
    assert {record.source for record in records} >= {
        "memory_md:team",
        "memory_md:team-agent",
    }


def test_memory_hub_includes_planner_learned_sections() -> None:
    class Planner:
        learned_rules_section = (
            "LEARNED MITIGATIONS (from past failures):\n"
            "  - [HIGH·4x] fetch_url timeout -> split long browsing tasks"
        )
        learned_memories_section = (
            "CONSOLIDATED MEMORIES (past pattern stats):\n"
            "  - [HOT] react_arm/react_loop: 80% success · avg_steps=3"
        )

    records = MemoryHub(planner=Planner()).retrieve(
        MemoryQuery(text="timeout react loop", limit=10)
    )

    kinds = {record.kind for record in records}
    assert "learned_rule" in kinds
    assert "learned_memory" in kinds
    assert any("timeout" in record.content for record in records)
    assert any("react_loop" in record.content for record in records)


def test_format_records_for_prompt_renders_concise_source_tags() -> None:
    records = [
        MemoryRecord(
            id="1",
            kind="fact",
            content="Use pytest before claiming runtime changes are complete.",
            source="user_store",
            scope="project",
            score=1.0,
        )
    ]

    rendered = format_records_for_prompt(records)

    assert rendered.startswith("RELEVANT LONG-TERM MEMORY:")
    assert "[project/fact/user_store]" in rendered
    assert "Use pytest before claiming runtime changes are complete." in rendered
