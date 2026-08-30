"""Tests for subagent lifecycle events flowing through the genome
journal so the realtime gateway / observability subscribers can render
a sub-agent tile from the spawn moment instead of waiting for the
first ``sub_tool_*`` event.

The bridge already fires ``subagent_spawned`` / ``subagent_finished``
through its in-memory ``event_emitter``. This lane mirrors those onto
the genome journal as ``SubToolStart/EndEvent`` rows whose ``tool_name``
field is one of the ``ItemMarker`` magic strings — so any subscriber
that already drinks the journal sees the lifecycle without separate
plumbing.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.execution.subagents import bridge
from runtime.execution.suckers.ephemeral_runner import (
    _emit_subagent_lifecycle_event,
)
from runtime.memory.journal import (
    InMemoryJournal,
    SubToolEndEvent,
    SubToolStartEvent,
)
from runtime.platform.process.session import Session, session_scope
from runtime.protocol.items import ItemMarker


def _restore_runner(orig):
    bridge._RUNNER = orig


def _scoped_session_with_journal(journal: InMemoryJournal) -> Session:
    return Session(metadata={"journal": journal})


def test_emit_lifecycle_writes_spawned_event_to_journal() -> None:
    """``_emit_subagent_lifecycle_event`` writes a SubToolStartEvent
    with the spawn marker name and codename/avatar/role packed into
    ``args_preview``."""
    journal = InMemoryJournal()
    payload = {
        "agent_id": "researcher_a",
        "role": "researcher",
        "codename": "Spark-abc",
        "avatar": "🔍",
        "prompt_preview": "explore vendor X",
        "use_cheap_model": True,
        "started_at": 12345.0,
    }
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_subagent_lifecycle_event("subagent_spawned", payload)

    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubToolStartEvent)
    assert ev.tool_name == ItemMarker.SUBAGENT_SPAWNED.value
    assert ev.role_id == "researcher"
    decoded = json.loads(ev.args_preview)
    assert decoded["codename"] == "Spark-abc"
    assert decoded["avatar"] == "🔍"
    assert decoded["role"] == "researcher"


def test_emit_lifecycle_writes_finished_event_to_journal() -> None:
    journal = InMemoryJournal()
    payload = {
        "agent_id": "researcher_a",
        "role": "researcher",
        "codename": "Spark-abc",
        "avatar": "🔍",
        "ok": True,
        "duration_s": 1.25,
        "iteration_count": 3,
        "files_touched": ["a.py", "b.py"],
        "error": None,
        "status": None,
    }
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_subagent_lifecycle_event("subagent_finished", payload)

    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubToolEndEvent)
    assert ev.tool_name == ItemMarker.SUBAGENT_FINISHED.value
    assert ev.is_error is False
    assert ev.duration_ms == 1250
    assert ev.iteration == 3
    decoded = json.loads(ev.output_preview)
    assert decoded["files_touched"] == ["a.py", "b.py"]
    assert decoded["codename"] == "Spark-abc"
    assert decoded["ok"] is True


def test_emit_lifecycle_finished_marks_failure() -> None:
    journal = InMemoryJournal()
    payload = {
        "agent_id": "researcher_a",
        "role": "researcher",
        "codename": "Spark-abc",
        "avatar": "🔍",
        "ok": False,
        "duration_s": 0.5,
        "iteration_count": 1,
        "files_touched": [],
        "error": "boom",
        "status": "error",
    }
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_subagent_lifecycle_event("subagent_finished", payload)

    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubToolEndEvent)
    assert ev.is_error is True


def test_bridge_call_writes_spawn_and_finish_to_journal() -> None:
    """End-to-end: the bridge, when called with a real session +
    journal, mirrors BOTH spawn + finish lifecycle events onto the
    journal (in addition to firing them through ``event_emitter``)."""
    journal = InMemoryJournal()
    received: list[dict[str, Any]] = []

    def _runner(prompt, *, subagent_name, context):
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        with session_scope(_scoped_session_with_journal(journal)):
            bridge.call_subagent(
                agent_id="custom_researcher",
                role="researcher",
                prompt="explore",
                event_emitter=lambda e: received.append(e),
            )
    finally:
        _restore_runner(orig)

    # In-memory emitter still fires (legacy contract).
    types = [e.get("type") for e in received]
    assert "subagent_spawned" in types
    assert "subagent_finished" in types

    # And the journal mirror sees both lifecycle markers.
    events = journal.read_all()
    tool_names = [getattr(e, "tool_name", None) for e in events]
    assert ItemMarker.SUBAGENT_SPAWNED.value in tool_names
    assert ItemMarker.SUBAGENT_FINISHED.value in tool_names


def test_bridge_call_without_session_does_not_crash() -> None:
    """No ambient session → journal write silently no-ops, the run
    still produces lifecycle events on the in-memory emitter, and the
    return envelope is unchanged."""
    received: list[dict[str, Any]] = []

    def _runner(prompt, *, subagent_name, context):
        return "done"

    orig = bridge._RUNNER
    bridge._RUNNER = _runner
    try:
        result = bridge.call_subagent(
            agent_id="custom_researcher",
            role="researcher",
            prompt="explore",
            event_emitter=lambda e: received.append(e),
        )
    finally:
        _restore_runner(orig)

    assert result["success"] is True
    types = [e.get("type") for e in received]
    assert "subagent_spawned" in types
    assert "subagent_finished" in types


def test_emit_lifecycle_tolerates_empty_payload() -> None:
    """An empty / malformed payload must not crash the helper — the
    runtime promises lifecycle mirroring is best-effort."""
    journal = InMemoryJournal()
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_subagent_lifecycle_event("subagent_spawned", {})
        _emit_subagent_lifecycle_event("subagent_finished", {})
        _emit_subagent_lifecycle_event("subagent_spawned", None)  # type: ignore[arg-type]
        _emit_subagent_lifecycle_event("unknown_kind", {"role": "x"})

    # Three valid kinds → three events; "unknown_kind" silently dropped.
    events = journal.read_all()
    tool_names = [getattr(e, "tool_name", None) for e in events]
    assert tool_names.count(ItemMarker.SUBAGENT_SPAWNED.value) == 2
    assert tool_names.count(ItemMarker.SUBAGENT_FINISHED.value) == 1


def test_emit_lifecycle_no_session_is_noop() -> None:
    """Outside of a session_scope, the helper writes nothing."""
    # No assertion on side-effects beyond "doesn't raise" — there is
    # no ambient journal to inspect.
    _emit_subagent_lifecycle_event("subagent_spawned", {"role": "x"})
    _emit_subagent_lifecycle_event("subagent_finished", {"role": "x"})
