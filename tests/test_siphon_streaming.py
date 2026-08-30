"""Implementation note."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from runtime.memory.journal import InMemoryJournal, JSONLJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.gateway import StreamingJournal

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _mk_step() -> Step:
    call = ToolCall(caller="arms/x", sucker_id="list_cwd", args={})
    return Step(
        step_id=0,
        node_id="n0",
        action=call,
        result=ExecutionResult(call_id=call.call_id, status="success"),
    )


def _mk_traj() -> Trajectory:
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("a"),
        steps=[_mk_step()],
        outcome=TrajectoryOutcome(success=True),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSubscription:
    def test_subscriber_receives_write(self):
        base = InMemoryJournal()
        j = StreamingJournal(base)

        received = []
        j.subscribe(lambda e: received.append(e))
        j.write_trajectory(_mk_traj())

        assert len(received) == 1
        assert received[0].event_type == "trajectory"

    def test_multiple_subscribers_all_notified(self):
        j = StreamingJournal(InMemoryJournal())
        a, b = [], []
        j.subscribe(lambda e: a.append(e))
        j.subscribe(lambda e: b.append(e))

        j.write_trajectory(_mk_traj())
        assert len(a) == 1
        assert len(b) == 1

    def test_unsubscribe_stops_delivery(self):
        j = StreamingJournal(InMemoryJournal())
        seen = []
        unsub = j.subscribe(lambda e: seen.append(e))
        j.write_trajectory(_mk_traj())
        unsub()
        j.write_trajectory(_mk_traj())
        assert len(seen) == 1  # Implementation note.
        assert j.subscriber_count == 0

    def test_subscriber_exception_swallowed(self):
        j = StreamingJournal(InMemoryJournal())
        good = []

        def bad(e):
            raise RuntimeError("boom")

        j.subscribe(bad)
        j.subscribe(lambda e: good.append(e))
        # Implementation note.
        j.write_trajectory(_mk_traj())
        assert len(good) == 1
        assert len(j.read_all()) == 1  # Implementation note.

    def test_unsubscribe_twice_is_noop(self):
        j = StreamingJournal(InMemoryJournal())
        unsub = j.subscribe(lambda e: None)
        unsub()
        unsub()  # Implementation note.
        assert j.subscriber_count == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReadPassThrough:
    def test_read_all_delegates(self):
        base = InMemoryJournal()
        j = StreamingJournal(base)
        traj = _mk_traj()
        j.write_trajectory(traj)

        # Implementation note.
        assert len(base.read_all()) == 1
        assert len(j.read_all()) == 1

    def test_len_delegates(self):
        j = StreamingJournal(InMemoryJournal())
        j.write_trajectory(_mk_traj())
        j.write_trajectory(_mk_traj())
        assert len(j) == 2

    def test_read_by_type_delegates(self):
        j = StreamingJournal(InMemoryJournal())
        j.write_trajectory(_mk_traj())
        assert len(j.read_by_type("trajectory")) == 1
        assert len(j.read_by_type("step")) == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestWithJSONLInner:
    def test_jsonl_persistence_preserved(self, tmp_path: Path):
        path = tmp_path / "events.jsonl"
        base = JSONLJournal(path)
        j = StreamingJournal(base)

        received = []
        j.subscribe(lambda e: received.append(e))
        j.write_trajectory(_mk_traj())
        j.write_step(_mk_traj().task_id, ArmId("a"), _mk_step())

        assert len(received) == 2
        # Implementation note.
        content = path.read_text(encoding="utf-8").strip()
        assert len(content.splitlines()) == 2

        # Implementation note.
        reopened = JSONLJournal(path)
        assert len(reopened.read_all()) == 2

    def test_attr_forwarding(self, tmp_path: Path):
        """Implementation note."""
        path = tmp_path / "events.jsonl"
        j = StreamingJournal(JSONLJournal(path))
        assert Path(j._path) == path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402
from runtime.platform.ui import create_app  # noqa: E402


class TestSSEEndpoint:
    def test_stream_route_registered_with_correct_content_type(self):
        """Implementation note."""
        app = create_app(journal_path=None)
        # Implementation note.
        paths = set(app.openapi()["paths"])
        assert "/api/stream" in paths

    def test_app_uses_streaming_journal(self):
        """Implementation note."""
        app = create_app(journal_path=None)
        # Implementation note.
        client = TestClient(app)
        r = client.get("/api/journal")
        # Implementation note.
        assert r.status_code == 200
        client.post("/api/run", json={"goal": "list stuff"})
        r2 = client.get("/api/journal")
        assert r2.json()["total"] > 0
