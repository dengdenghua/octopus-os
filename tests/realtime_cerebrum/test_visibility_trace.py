"""Tests for the cerebrum visibility-trace module (decision → basis → conclusion).

Covers record/export correctness, JSON serializability, thread safety of
concurrent producers, ContextVar no-op behaviour, and error swallowing.
"""

from __future__ import annotations

import json
import threading

import pytest

from runtime.core.cerebrum._visibility_trace import (
    VisibilityEntry,
    active_trace,
    new_trace,
    record_visibility,
    reset_active_trace,
    set_active_trace,
)


def test_record_and_export_preserve_order_and_fields() -> None:
    trace = new_trace()
    trace.record_decision(
        "capability_activation",
        "research",
        "goal contains 'research'",
        lane="research",
    )
    trace.record(
        VisibilityEntry(
            decision_point="skill_catalog",
            conclusion="include deep-research",
            basis="research lane activated",
            ts=123.0,
        )
    )

    assert len(trace) == 2
    assert not trace.empty()
    entries = trace.entries()
    assert [e.decision_point for e in entries] == [
        "capability_activation",
        "skill_catalog",
    ]
    assert entries[0].conclusion == "research"
    assert entries[0].basis == "goal contains 'research'"
    assert entries[0].details == {"lane": "research"}

    exported = trace.export()
    assert exported[0]["decision_point"] == "capability_activation"
    assert exported[0]["details"] == {"lane": "research"}
    assert exported[1]["ts"] == 123.0
    assert exported[1]["conclusion"] == "include deep-research"
    # Round-trip through JSON proves serializability.
    assert json.loads(json.dumps(exported)) == exported


def test_export_is_json_safe_with_hostile_details() -> None:
    trace = new_trace()
    trace.record_decision("dp", "conclusion", "basis", weird=object(), nested={"s": {1, 2}})
    payload = json.dumps(trace.export())
    assert '"dp"' in payload


def test_entries_returns_a_snapshot_copy() -> None:
    trace = new_trace()
    trace.record_decision("dp", "c1", "b1")
    snapshot = trace.entries()
    snapshot.append(VisibilityEntry(decision_point="x", conclusion="y", basis="z"))
    assert len(trace) == 1
    assert len(snapshot) == 2


def test_latest() -> None:
    trace = new_trace()
    assert trace.latest() is None
    assert trace.latest("missing") is None
    trace.record_decision("a", "c1", "b1")
    trace.record_decision("b", "c2", "b2")
    trace.record_decision("a", "c3", "b3")
    assert trace.latest() is not None
    assert trace.latest().conclusion == "c3"
    assert trace.latest("a") is not None
    assert trace.latest("a").conclusion == "c3"
    assert trace.latest("b") is not None
    assert trace.latest("b").conclusion == "c2"
    assert trace.latest("missing") is None


def test_thread_safety_no_lost_records() -> None:
    trace = new_trace()
    n_threads = 8
    per_thread = 250

    def worker(tid: int) -> None:
        for i in range(per_thread):
            trace.record_decision("dp", f"c-{tid}-{i}", "basis", tid=tid, i=i)

    threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * per_thread
    assert len(trace) == expected
    exported = trace.export()
    assert len(exported) == expected
    seen = {(e["details"]["tid"], e["details"]["i"]) for e in exported}
    assert seen == {(tid, i) for tid in range(n_threads) for i in range(per_thread)}


def test_active_trace_unset_is_noop() -> None:
    assert active_trace() is None
    # Must not raise and must not record anywhere observable.
    record_visibility("dp", "c", "b", extra=1)
    assert active_trace() is None


def test_active_trace_set_and_reset() -> None:
    trace = new_trace()
    token = set_active_trace(trace)
    try:
        assert active_trace() is trace
        record_visibility("dp", "c", "b", extra=1)
        assert len(trace) == 1
        assert trace.export()[0]["decision_point"] == "dp"
    finally:
        reset_active_trace(token)
    assert active_trace() is None
    record_visibility("dp2", "c2", "b2")
    assert len(trace) == 1  # no-op after reset


def test_reset_active_trace_with_none_token_is_noop() -> None:
    reset_active_trace(None)  # must not raise


def test_active_trace_is_isolated_per_thread() -> None:
    trace_main = new_trace()
    token = set_active_trace(trace_main)
    results: dict[str, object] = {}

    def worker() -> None:
        thread_trace = new_trace()
        set_active_trace(thread_trace)
        record_visibility("dp", "c", "b")
        results["thread_len"] = len(thread_trace)
        results["main_len"] = len(trace_main)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    reset_active_trace(token)

    assert results["thread_len"] == 1
    assert results["main_len"] == 0  # 线程内记录不污染主线程 trace
    assert len(trace_main) == 0


def test_record_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    trace = new_trace()

    def boom(_entry: VisibilityEntry) -> None:
        raise RuntimeError("boom")

    # Simulate an internal error inside record(): must not reach the caller.
    monkeypatch.setattr(trace, "record", boom)
    trace.record_decision("dp", "c", "b")  # must not raise
    assert len(trace) == 0

    # Simulate an error while building the entry: also swallowed.
    monkeypatch.setattr(
        "runtime.core.cerebrum._visibility_trace.VisibilityEntry",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("entry build")),
    )
    trace.record_decision("dp", "c", "b")  # must not raise
    assert len(trace) == 0

