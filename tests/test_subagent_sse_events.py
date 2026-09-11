"""Tests for sub-agent SSE event propagation via the journal.

Verifies that when an ephemeral sub-agent executes a tool call, the
``sub_tool_start`` / ``sub_tool_end`` events are mirrored into the
journal so the SSE pump (which subscribes to the journal) sees them.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from runtime.execution.suckers.ephemeral_runner import _emit_sub_tool_event
from runtime.memory.journal import (
    InMemoryJournal,
    SubToolEndEvent,
    SubToolStartEvent,
)
from runtime.memory.journal.journal import _parse_event


class _FakeSession:
    """Minimal fake of Session for unit tests."""

    def __init__(self, journal, queue=None):
        self.metadata = {"journal": journal}
        if queue is not None:
            self.metadata["sub_tool_event_queue"] = queue


@pytest.fixture
def _session(monkeypatch):
    """Patch ``current_session`` to return our fake. Cleanup ensures
    no leakage across tests."""
    journal = InMemoryJournal()
    session = _FakeSession(journal)

    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: session)
    return session, journal


def _make_tool_call(name: str = "echo", call_id: str = "call-1"):
    tc = MagicMock()
    tc.id = call_id
    tc.name = name
    tc.input = {"text": "hi"}
    return tc


# ─── journal mirroring ──────────────────────────────────────


def test_sub_tool_start_writes_journal_event(_session):
    _, journal = _session
    _emit_sub_tool_event(
        "sub_tool_start",
        role_id="research",
        tool_call=_make_tool_call(),
        iteration=1,
    )
    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubToolStartEvent)
    assert ev.role_id == "research"
    assert ev.tool_name == "echo"
    assert ev.iteration == 1
    assert ev.event_type == "sub_tool_start"


def test_sub_tool_end_writes_journal_event(_session):
    _, journal = _session
    _emit_sub_tool_event(
        "sub_tool_end",
        role_id="research",
        tool_call=_make_tool_call(),
        iteration=1,
        output="result",
        is_error=False,
        duration_ms=42,
    )
    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubToolEndEvent)
    assert ev.duration_ms == 42
    assert ev.is_error is False
    assert "result" in ev.output_preview


def test_error_end_event_captures_error_flag(_session):
    _, journal = _session
    _emit_sub_tool_event(
        "sub_tool_end",
        role_id="coder",
        tool_call=_make_tool_call(name="bad"),
        iteration=2,
        output="boom",
        is_error=True,
        duration_ms=10,
    )
    ev = journal.read_all()[0]
    assert isinstance(ev, SubToolEndEvent)
    assert ev.is_error is True


def test_output_preview_truncated_to_200_chars(_session):
    _, journal = _session
    long_output = "x" * 500
    _emit_sub_tool_event(
        "sub_tool_end",
        role_id="r",
        tool_call=_make_tool_call(),
        iteration=1,
        output=long_output,
        is_error=False,
        duration_ms=0,
    )
    ev = journal.read_all()[0]
    assert len(ev.output_preview) <= 200


# ─── Queue + journal dual path ──────────────────────────────


def test_queue_path_still_works_alongside_journal(_session):
    """When an SSE pump queue is also present, BOTH mechanisms fire.
    The queue path is for the agentic-fallback SSE route; the journal
    path is for the OpenAI-gateway worker. Having both lets us serve
    either transport without changing call-site code.
    """
    import queue as _queue

    session, journal = _session
    q = _queue.Queue(maxsize=10)
    session.metadata["sub_tool_event_queue"] = q

    _emit_sub_tool_event(
        "sub_tool_start",
        role_id="r",
        tool_call=_make_tool_call(),
        iteration=1,
    )

    # Both destinations saw the event.
    assert not q.empty()
    assert len(journal.read_all()) == 1


# ─── Parent tool use propagation ────────────────────────────


def test_parent_tool_use_id_flows_through(_session):
    session, journal = _session
    session.metadata["_active_parent_tool_use_id"] = "parent-xyz"

    _emit_sub_tool_event(
        "sub_tool_start",
        role_id="r",
        tool_call=_make_tool_call(),
        iteration=1,
    )
    ev = journal.read_all()[0]
    assert ev.parent_tool_use_id == "parent-xyz"


# ─── No-journal graceful degradation ────────────────────────


def test_no_journal_no_crash(monkeypatch):
    """When the session has no journal AND no queue, the helper
    must silently return without raising."""

    class _SessionNoJournal:
        metadata: dict = {}

    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: _SessionNoJournal())

    # Should not raise.
    _emit_sub_tool_event(
        "sub_tool_start",
        role_id="r",
        tool_call=_make_tool_call(),
        iteration=1,
    )


# ─── Round-trip serialization ───────────────────────────────


def test_sub_tool_start_round_trips_through_jsonl():
    """Serialize + deserialize via the journal's _parse_event ensures
    JSONLJournal replay works for these new events."""
    ev = SubToolStartEvent(
        role_id="r",
        tool_call_id="c1",
        tool_name="echo",
        iteration=2,
        args_preview='{"x":1}',
    )
    line = ev.model_dump_json()
    parsed = _parse_event(line)
    assert isinstance(parsed, SubToolStartEvent)
    assert parsed.role_id == "r"
    assert parsed.iteration == 2


def test_sub_tool_end_round_trips_through_jsonl():
    ev = SubToolEndEvent(
        role_id="r",
        tool_call_id="c1",
        tool_name="echo",
        iteration=2,
        is_error=False,
        duration_ms=5,
        output_preview="ok",
    )
    parsed = _parse_event(ev.model_dump_json())
    assert isinstance(parsed, SubToolEndEvent)
    assert parsed.duration_ms == 5
