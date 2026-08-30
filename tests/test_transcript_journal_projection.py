"""Bounded transcript prefers the real session journal (dsh checkpoint source).

Section 36/41 put the session's true story in the journal (``user/message``
rows + streamed ``sub_text_delta`` prose). The bounded transcript should
project THAT — real journal events — instead of the coarser Q/A pair
rebuilt from turn records, falling back only when no journal is reachable
or it has no rows for the session (legacy/one-shot children).
"""

from __future__ import annotations

from pathlib import Path

from runtime.execution.subagents.sessions import SubagentSessionStore
from runtime.memory.journal import InMemoryJournal, SubTextDeltaEvent
from runtime.platform.process.session import Session, session_scope


def _store(tmp_path: Path) -> SubagentSessionStore:
    return SubagentSessionStore(base_dir=tmp_path / "sessions")


def _seeded_session(store: SubagentSessionStore, thread_id: str = "t"):
    session = store.create(agent_id="researcher", thread_id=thread_id)
    store.append_turn(
        session.session_id,
        prompt="turn ask",
        output="turn answer",
        success=True,
    )
    loaded = store.get(session.session_id)
    assert loaded is not None
    return loaded


def test_journal_surface_preferred_over_turn_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _seeded_session(store)
    journal = InMemoryJournal()
    journal.write_user_message("真实问题", session_id=session.session_id)
    journal.write(
        SubTextDeltaEvent(
            session_id=session.session_id,
            role_id="researcher",
            round=1,
            delta="真实结论(流式散文)",
        )
    )

    with session_scope(Session(metadata={"journal": journal})):
        text = store.transcript_prompt(session, bounded=True)

    assert "## Referenced session (projected)" in text
    assert "真实结论(流式散文)" in text
    assert "真实问题" in text
    # The turn-store Q/A pair must NOT leak in when the journal has rows.
    assert "turn answer" not in text


def test_falls_back_to_turns_without_journal(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _seeded_session(store)
    text = store.transcript_prompt(session, bounded=True)
    assert "**user**: turn ask" in text
    assert "**assistant**: turn answer" in text


def test_falls_back_to_turns_when_journal_lacks_session_rows(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _seeded_session(store)
    journal = InMemoryJournal()
    # Rows exist, but for a DIFFERENT session — must not be attributed.
    journal.write_user_message("别处的消息", session_id="f" * 32)
    journal.write(SubTextDeltaEvent(session_id="f" * 32, role_id="x", round=1, delta="别处"))

    with session_scope(Session(metadata={"journal": journal})):
        text = store.transcript_prompt(session, bounded=True)

    assert "**user**: turn ask" in text
    assert "**assistant**: turn answer" in text
    assert "别处" not in text


def test_journal_path_respects_projection_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _seeded_session(store)
    journal = InMemoryJournal()
    journal.write_user_message("问题", session_id=session.session_id)
    journal.write(
        SubTextDeltaEvent(
            session_id=session.session_id,
            role_id="researcher",
            round=1,
            delta="x" * 5000,
        )
    )

    with session_scope(Session(metadata={"journal": journal})):
        text = store.transcript_prompt(session, bounded=True, max_projection_bytes=1000)

    assert len(text.encode("utf-8")) <= 2000
    assert "UTF-8 bytes" in text or "budget" in text


def test_journal_surface_multiple_rounds_interleaved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = _seeded_session(store)
    journal = InMemoryJournal()
    journal.write_user_message("第一问", session_id=session.session_id)
    journal.write(
        SubTextDeltaEvent(session_id=session.session_id, role_id="r", round=1, delta="第一答")
    )
    journal.write_user_message("第二问", session_id=session.session_id)
    journal.write(
        SubTextDeltaEvent(session_id=session.session_id, role_id="r", round=2, delta="第二答")
    )

    with session_scope(Session(metadata={"journal": journal})):
        text = store.transcript_prompt(session, bounded=True)

    assert "第一问" in text
    assert "第一答" in text
    assert "第二问" in text
    assert "第二答" in text

