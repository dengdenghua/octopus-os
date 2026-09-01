"""Durable subagent session tests — dsh ``continuable`` port."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.subagents import bridge
from runtime.execution.subagents.sessions import (
    SubagentSessionStore,
    get_subagent_session_store,
    set_subagent_session_store,
)


def _store(tmp_path: Path) -> SubagentSessionStore:
    return SubagentSessionStore(base_dir=tmp_path / "sessions")


def test_create_get_round_trip(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    assert session.session_id
    assert session.turns == []
    loaded = store.get(session.session_id)
    assert loaded is not None
    assert loaded.agent_id == "researcher"
    assert loaded.thread_id == "th-1"


def test_append_turn_persists_across_instances(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_turn(
        session.session_id,
        prompt="find patents",
        output="found 3",
        success=True,
        rounds=2,
    )
    fresh = SubagentSessionStore(base_dir=tmp_path / "sessions")
    loaded = fresh.get(session.session_id)
    assert loaded is not None
    assert len(loaded.turns) == 1
    assert loaded.turns[0].prompt == "find patents"
    assert loaded.turns[0].output == "found 3"
    assert loaded.turns[0].rounds == 2
    assert loaded.turns[0].success is True


def test_append_turn_unknown_session_returns_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.append_turn("missing", prompt="x", output="y", success=True) is None


def test_get_unknown_session_none(tmp_path: Path) -> None:
    assert _store(tmp_path).get("00000000000000000000000000000000") is None


def test_get_invalid_session_id_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("../evil") is None
    assert store.get("") is None


def test_transcript_prompt_empty_without_turns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="r", thread_id="t")
    assert store.transcript_prompt(session) == ""


def test_transcript_prompt_renders_turns(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="r", thread_id="t")
    store.append_turn(
        session.session_id,
        prompt="first ask",
        output="first answer",
        success=True,
    )
    loaded = store.get(session.session_id)
    assert loaded is not None
    text = store.transcript_prompt(loaded)
    assert "Previous turns in this subagent session" in text
    assert "first ask" in text
    assert "first answer" in text


def test_transcript_prompt_bounded_and_truncated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="r", thread_id="t")
    for index in range(10):
        store.append_turn(
            session.session_id,
            prompt=f"ask {index}",
            output="x" * 300,
            success=True,
        )
    loaded = store.get(session.session_id)
    assert loaded is not None
    text = store.transcript_prompt(loaded)
    assert len(text) <= 7000
    # Oldest turns dropped first (6-turn window).
    assert "ask 0" not in text
    assert "ask 9" in text


def test_store_degrades_to_memory_when_dir_unavailable(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file", encoding="utf-8")
    store = SubagentSessionStore(base_dir=blocker)
    session = store.create(agent_id="r", thread_id="t")
    assert store.get(session.session_id) is not None


def test_call_subagent_creates_session_and_records_turn(tmp_path: Path) -> None:
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    store = _store(tmp_path)
    try:
        bridge.set_sub_agent_runner(
            lambda prompt, **kw: "the answer"  # type: ignore[arg-type]
        )
        set_subagent_session_store(store)
        result = bridge.call_subagent(agent_id="zzz_custom_session_role", prompt="question?")
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)
    assert result["success"] is True
    session_id = result["session_id"]
    assert session_id
    session = store.get(session_id)
    assert session is not None
    assert len(session.turns) == 1
    assert session.turns[0].prompt == "question?"
    assert session.turns[0].output == "the answer"


def test_call_subagent_continues_session_with_transcript(tmp_path: Path) -> None:
    seen: list[str] = []
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    store = _store(tmp_path)
    try:

        def runner(prompt: str, **kw: object) -> str:
            seen.append(prompt)
            return "answer " + str(len(seen))

        bridge.set_sub_agent_runner(runner)  # type: ignore[arg-type]
        set_subagent_session_store(store)
        first = bridge.call_subagent(agent_id="zzz_custom_session_role", prompt="first ask")
        second = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="dig deeper",
            continue_session_id=first["session_id"],
        )
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)
    assert second["success"] is True
    assert second["session_id"] == first["session_id"]
    assert "Previous turns in this subagent session" in seen[1]
    assert "first ask" in seen[1]
    session = store.get(first["session_id"])
    assert session is not None
    assert len(session.turns) == 2


def test_call_subagent_cross_thread_continuation_blocked(tmp_path: Path) -> None:
    """A session spawned by thread A must read as unknown when thread B tries
    to continue it (cross-tenant IDOR guard)."""
    seen: list[str] = []
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    store = _store(tmp_path)
    try:

        def runner(prompt: str, **kw: object) -> str:
            seen.append(prompt)
            return "answer"

        bridge.set_sub_agent_runner(runner)  # type: ignore[arg-type]
        set_subagent_session_store(store)
        first = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="first ask",
            context={"thread_id": "thread-A"},
        )
        assert first["success"] is True
        # Same-thread continuation works.
        same = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="dig deeper",
            continue_session_id=first["session_id"],
            context={"thread_id": "thread-A"},
        )
        assert same["success"] is True
        # Cross-thread continuation is treated as unknown (fail-closed).
        cross = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="steal",
            continue_session_id=first["session_id"],
            context={"thread_id": "thread-B"},
        )
        assert cross["success"] is False
        assert cross["session_error"] == "unknown_session"
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)


def test_call_subagent_cross_principal_continuation_blocked(tmp_path: Path) -> None:
    """A guessed session id stays private even when a legacy thread id collides."""
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    store = _store(tmp_path)
    try:
        bridge.set_sub_agent_runner(lambda prompt, **kw: "answer")  # type: ignore[arg-type]
        set_subagent_session_store(store)
        first = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="alice secret",
            context={
                "thread_id": "shared-legacy-thread",
                "owner_actor_id": "alice",
                "tenant_id": "tenant-a",
            },
        )
        cross = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="steal",
            continue_session_id=first["session_id"],
            context={
                "thread_id": "shared-legacy-thread",
                "owner_actor_id": "bob",
                "tenant_id": "tenant-b",
            },
        )
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)

    assert first["success"] is True
    assert cross["success"] is False
    assert cross["session_error"] == "unknown_session"


def test_call_subagent_unknown_session_fails_loud(tmp_path: Path) -> None:
    seen: list[str] = []
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    try:

        def runner(prompt: str, **kw: object) -> str:
            seen.append(prompt)
            return "should not run"

        bridge.set_sub_agent_runner(runner)  # type: ignore[arg-type]
        set_subagent_session_store(_store(tmp_path))
        result = bridge.call_subagent(
            agent_id="zzz_custom_session_role",
            prompt="continue?",
            continue_session_id="00000000000000000000000000000000",
        )
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)
    assert result["success"] is False
    assert result["session_error"] == "unknown_session"
    assert "unknown subagent session" in result["error"]
    assert seen == []


# ── dsh session-reference projection (bounded transcript) ──


def test_transcript_prompt_bounded_projects_surface(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t")
    store.append_turn(
        session.session_id,
        prompt="first ask",
        output="first answer",
        success=True,
    )
    store.append_turn(
        session.session_id,
        prompt="second ask",
        output="second answer",
        success=True,
    )
    loaded = store.get(session.session_id)
    assert loaded is not None
    text = store.transcript_prompt(loaded, bounded=True)
    assert "## Referenced session (projected)" in text
    assert "**user**: first ask" in text
    assert "**assistant**: first answer" in text
    assert "**user**: second ask" in text
    assert "**assistant**: second answer" in text
    # within budget → no truncation notice
    assert "omitted" not in text.lower()


def test_transcript_prompt_bounded_bounds_bytes(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="t")
    for index in range(30):
        store.append_turn(
            session.session_id,
            prompt=f"ask {index}",
            output="x" * 2000,
            success=True,
        )
    loaded = store.get(session.session_id)
    assert loaded is not None
    text = store.transcript_prompt(
        loaded,
        bounded=True,
        max_projection_bytes=3000,
    )
    assert len(text.encode("utf-8")) <= 4000
    assert "UTF-8 bytes" in text or "budget" in text
    # default bounded mode is off (legacy behavior unchanged)
    legacy = store.transcript_prompt(loaded)
    assert "Previous turns in this subagent session" in legacy


def test_transcript_prompt_bounded_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="r", thread_id="t")
    assert store.transcript_prompt(session, bounded=True) == ""

