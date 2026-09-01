"""Tests for the typed sub-agent lifecycle event bus."""

from __future__ import annotations

import pytest

from runtime.execution.subagents.event_bus import (
    EVT_SUB_CONCLUDED,
    EVT_SUB_INCOMPLETE,
    EVT_SUB_STARTED,
    SubAgentEventBus,
    get_bus,
    list_active_buses,
    publish_subagent_event,
    reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_buses():
    reset_for_tests()
    yield
    reset_for_tests()


# ── unit: bus semantics ─────────────────────────────────────


def test_append_only_monotonic_seq():
    bus = SubAgentEventBus()
    e1 = bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    e2 = bus.publish({"type": EVT_SUB_CONCLUDED, "thread_id": "c", "root_thread_id": "r"})
    assert e1["seq"] == 1
    assert e2["seq"] == 2
    assert e2["ts"] >= e1["ts"]
    assert len(bus.replay(0)) == 2


def test_replay_after_seq():
    bus = SubAgentEventBus()
    for _i in range(5):
        bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    backfill = bus.replay(after_seq=3)
    assert [e["seq"] for e in backfill] == [4, 5]


def test_subscriber_gets_each_event_and_unsubscribe():
    bus = SubAgentEventBus()
    got = []
    unsub = bus.subscribe(lambda ev: got.append(ev["seq"]))
    bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    assert got == [1, 2]
    unsub()
    bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    assert got == [1, 2]


def test_bad_subscriber_is_dropped_not_fatal():
    bus = SubAgentEventBus()

    def boom(_ev):
        raise RuntimeError("boom")

    bus.subscribe(boom)
    # must not raise
    bus.publish({"type": EVT_SUB_STARTED, "thread_id": "c", "root_thread_id": "r"})
    assert len(bus.replay(0)) == 1


def test_unknown_type_rejected():
    bus = SubAgentEventBus()
    with pytest.raises(ValueError):
        bus.publish({"type": "nope", "thread_id": "c", "root_thread_id": "r"})


def test_registry_scoped_by_root_and_reset():
    b1 = get_bus("root-a")
    b2 = get_bus("root-b")
    assert b1 is not None and b2 is not None
    assert b1 is not b2
    assert get_bus("root-a") is b1
    assert len(list_active_buses()) == 2
    reset_for_tests()
    assert list_active_buses() == []


# ── integration: publish helper resolves ids from session ───


class _FakeSession:
    def __init__(self, metadata, thread_id=None):
        self.metadata = metadata
        self.thread_id = thread_id


def test_publish_derives_root_from_metadata(monkeypatch):
    import runtime.platform.process.session as _sess

    monkeypatch.setattr(
        _sess,
        "current_session",
        lambda: _FakeSession({"root_thread_id": "ROOT", "thread_id": "CHILD"}),
    )
    ev = publish_subagent_event(EVT_SUB_INCOMPLETE, {"reason": "cap"})
    assert ev is not None
    assert ev["root_thread_id"] == "ROOT"
    assert ev["thread_id"] == "CHILD"
    assert ev["type"] == EVT_SUB_INCOMPLETE
    assert ev["payload"] == {"reason": "cap"}
    # readable back from the root bus
    assert get_bus("ROOT").replay(0)[-1]["seq"] == ev["seq"]


def test_publish_falls_back_to_session_thread_as_root(monkeypatch):
    import runtime.platform.process.session as _sess

    monkeypatch.setattr(
        _sess,
        "current_session",
        lambda: _FakeSession({}, thread_id="PLAIN"),
    )
    ev = publish_subagent_event(EVT_SUB_STARTED, {})
    assert ev is not None
    assert ev["root_thread_id"] == "PLAIN"


def test_publish_no_session_returns_none(monkeypatch):
    import runtime.platform.process.session as _sess

    monkeypatch.setattr(_sess, "current_session", lambda: None)
    assert publish_subagent_event(EVT_SUB_STARTED, {}) is None


# ── integration: ephemeral emitters mirror onto the bus ─────


def _session_with_meta(monkeypatch, meta):
    import runtime.platform.process.session as _sess

    sess = _FakeSession(meta, thread_id=meta.get("thread_id"))
    monkeypatch.setattr(_sess, "current_session", lambda: sess)
    return sess


def test_tool_events_mirror_to_bus(monkeypatch):
    from unittest.mock import MagicMock

    from runtime.execution.suckers._ephemeral_events import (
        _emit_sub_text_delta,
        _emit_sub_tool_event,
    )
    from runtime.memory.journal import InMemoryJournal

    journal = InMemoryJournal()
    _session_with_meta(
        monkeypatch,
        {
            "root_thread_id": "R",
            "thread_id": "C",
            "_active_parent_tool_use_id": "p-1",
            "journal": journal,
            "subagent_agent_id": "researcher-a",
            "subagent_codename": "Spark-a1",
            "subagent_avatar": "🔎",
        },
    )
    tc = MagicMock()
    tc.id = "call-1"
    tc.name = "web_search"
    tc.input = {"q": "x"}
    _emit_sub_tool_event("sub_tool_start", role_id="researcher", tool_call=tc, iteration=1)
    _emit_sub_tool_event(
        "sub_tool_end",
        role_id="researcher",
        tool_call=tc,
        iteration=1,
        output="result",
        is_error=False,
        duration_ms=12,
    )
    events = get_bus("R").replay(0)
    assert [e["type"] for e in events] == ["sub_tool_start", "sub_tool_end"]
    assert events[0]["payload"]["tool"] == "web_search"
    assert events[0]["payload"]["parent_tool_use_id"] == "p-1"
    assert events[1]["payload"]["status"] == "success"
    assert events[1]["payload"]["duration_ms"] == 12
    _emit_sub_text_delta(
        "researcher",
        1,
        "public progress",
        session_id="session-a",
    )
    journal_events = journal.read_all()
    assert journal_events[0].agent_id == "researcher-a"
    assert journal_events[0].codename == "Spark-a1"
    assert journal_events[1].agent_id == "researcher-a"
    assert journal_events[2].agent_id == "researcher-a"
    assert journal_events[2].session_id == "session-a"


def test_lifecycle_events_mirror_to_bus(monkeypatch):
    from runtime.execution.suckers._ephemeral_events import (
        _emit_subagent_lifecycle_event,
    )
    from runtime.memory.journal import InMemoryJournal

    sess = _FakeSession({"root_thread_id": "R", "thread_id": "C", "journal": InMemoryJournal()})
    import runtime.platform.process.session as _sess_mod

    monkeypatch.setattr(_sess_mod, "current_session", lambda: sess)

    _emit_subagent_lifecycle_event(
        "subagent_spawned",
        {"role": "researcher", "codename": "exp", "prompt_preview": "find x"},
    )
    _emit_subagent_lifecycle_event(
        "subagent_finished",
        {"role": "researcher", "ok": True, "duration_s": 3.2, "files_touched": 1},
    )
    _emit_subagent_lifecycle_event(
        "subagent_finished",
        {"role": "researcher", "ok": False, "error": "boom"},
    )
    events = get_bus("R").replay(0)
    assert [e["type"] for e in events] == ["sub_started", "sub_concluded", "sub_failed"]
    assert events[0]["payload"]["role"] == "researcher"
    assert events[1]["payload"]["ok"] is True
    assert events[2]["payload"]["error"] == "boom"


# ── integration: incomplete semantics surface as failure + bus event ───


def test_converged_early_maps_to_failure_and_bus_event(monkeypatch):
    """A sub-agent that only repeats tool calls (convergence guard) must be
    returned as success=False + partial, and mirror sub_incomplete on the bus —
    never a silent success."""
    import runtime.platform.process.session as _sess_mod
    from runtime.execution.suckers.ephemeral_agents import (
        BUILTIN_ROLES,
        set_ephemeral_role_runner,
    )
    from runtime.execution.suckers.ephemeral_runner import make_llm_ephemeral_runner
    from runtime.memory.journal import InMemoryJournal

    monkeypatch.setattr(
        _sess_mod,
        "current_session",
        lambda: _FakeSession(
            {"root_thread_id": "R", "thread_id": "C", "journal": InMemoryJournal()},
            thread_id="C",
        ),
    )

    try:
        from tests.test_ephemeral_runner import (
            _ScriptedAgenticRouter,
            _StubRegistry,
        )
    except ImportError:  # pragma: no cover · path fallback for non-root runs
        from test_ephemeral_runner import (
            _ScriptedAgenticRouter,
            _StubRegistry,
        )

    script = [[{"name": "read_file", "input": {"path": "x.py"}}]] * 10
    router = _ScriptedAgenticRouter(script=script)
    registry = _StubRegistry({"read_file": lambda **kw: {"ok": True}})
    set_ephemeral_role_runner(
        make_llm_ephemeral_runner(router, registry=registry, default_model="m")
    )
    try:
        from runtime.execution.suckers.ephemeral_agents import run_ephemeral_definition

        role = BUILTIN_ROLES["reviewer"]
        result = run_ephemeral_definition(
            role,
            "review this",
            session=_FakeSession({"root_thread_id": "R", "thread_id": "C"}),
            context={},
        )
    finally:
        set_ephemeral_role_runner(None)

    assert result["success"] is False
    assert result["partial"] is True
    assert result["converged_early"] is True
    assert result["round_cap_exceeded"] is False
    # bus carries the incomplete signal
    types = [e["type"] for e in get_bus("R").replay(0)]
    assert "sub_incomplete" in types
    inc = [e for e in get_bus("R").replay(0) if e["type"] == "sub_incomplete"][-1]
    assert inc["payload"]["reason"] == "converged_early"
    assert inc["payload"]["rounds"] >= 1


# ── integration: real dispatch path mirrors lifecycle onto the bus ───


def test_call_subagent_mirrors_started_and_concluded_to_bus(monkeypatch):
    """A subagent dispatched through the real bridge must surface
    sub_started + sub_concluded on the event bus keyed to the caller's
    thread — the substrate the workbench subscribes to."""
    import runtime.execution.subagents.bridge as _bridge
    from runtime.memory.journal import InMemoryJournal
    from runtime.platform.process.session import Session, session_scope

    def _fake_runner(prompt, *, subagent_name, context):
        del prompt, subagent_name, context
        return "Final Answer: ok."

    orig = _bridge._RUNNER
    _bridge._RUNNER = _fake_runner
    reset_for_tests()
    try:
        with session_scope(
            Session(
                thread_id="parent-thread",
                metadata={"journal": InMemoryJournal()},
            )
        ):
            result = _bridge.call_subagent(agent_id="custom-probe", prompt="look around")
    finally:
        _bridge._RUNNER = orig

    assert result["success"] is True
    events = get_bus("parent-thread").replay(0)
    types = [e["type"] for e in events]
    assert types.count("sub_started") == 1
    assert types.count("sub_concluded") == 1
    started = [e for e in events if e["type"] == "sub_started"][0]
    assert started["root_thread_id"] == "parent-thread"

