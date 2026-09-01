from __future__ import annotations

import json
from pathlib import Path

from runtime.memory.threads import ThreadStateStore


def _records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_team_thread_routes_to_team_sessions_before_agent(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)

    store.ensure_thread(
        "team-thread",
        metadata={
            "mode": "team",
            "team_id": "team-alpha",
            "agent": "general",
        },
    )

    team_path = tmp_path / "teams" / "team-alpha" / "sessions" / "team-thread.jsonl"
    agent_path = tmp_path / "agents" / "general" / "sessions" / "team-thread.jsonl"

    assert team_path.exists()
    assert not agent_path.exists()
    assert _records(team_path)[-1]["thread"]["metadata"]["team_id"] == "team-alpha"


def test_promoting_misc_thread_to_team_moves_canonical_file(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread("promoted-thread", metadata={})

    misc_path = tmp_path / "data" / "sessions" / "misc" / "promoted-thread.jsonl"
    assert misc_path.exists()

    store.update_state(
        "promoted-thread",
        metadata={"mode": "team", "team_id": "team-alpha", "agent": "general"},
        values={"title": "Team work"},
    )

    team_path = tmp_path / "teams" / "team-alpha" / "sessions" / "promoted-thread.jsonl"
    agent_path = tmp_path / "agents" / "general" / "sessions" / "promoted-thread.jsonl"

    assert team_path.exists()
    assert not misc_path.exists()
    assert not agent_path.exists()

    reloaded = ThreadStateStore(per_agent_base=tmp_path)
    thread = reloaded.get("promoted-thread")
    assert thread is not None
    assert thread["metadata"]["team_id"] == "team-alpha"
    assert thread["metadata"]["agent"] == "general"


def test_team_delete_record_is_written_to_team_file(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread(
        "deleted-team-thread",
        metadata={"mode": "team", "team_id": "team-alpha", "agent": "general"},
    )

    assert store.delete("deleted-team-thread") is True

    team_path = tmp_path / "teams" / "team-alpha" / "sessions" / "deleted-team-thread.jsonl"
    record = _records(team_path)[-1]
    assert record["op"] == "delete"
    assert record["thread_id"] == "deleted-team-thread"
    assert record["revision"] == 2
    assert record["operation_at"].endswith("Z")

    reloaded = ThreadStateStore(per_agent_base=tmp_path)
    assert reloaded.get("deleted-team-thread") is None
