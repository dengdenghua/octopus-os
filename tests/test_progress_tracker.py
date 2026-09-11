"""Implementation note."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from runtime.memory.journal import (
    InMemoryJournal,
    TaskProgressTracker,
    task_progress_snapshot,
)
from runtime.platform.models import (
    ArmId,
    CostEntry,
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


def _step(sid: int, sucker: str, success: bool = True) -> Step:
    call = ToolCall(caller="arms/x", sucker_id=sucker, args={})
    return Step(
        step_id=sid,
        node_id=f"n{sid}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
        ),
    )


def _wrap(base=None) -> StreamingJournal:
    return StreamingJournal(base or InMemoryJournal())


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBootstrap:
    def test_empty_journal(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        assert tracker.count() == 0
        assert tracker.snapshots == []
        assert tracker.get(uuid4()) is None
        tracker.close()

    def test_preexisting_events_picked_up(self):
        """Implementation note."""
        inner = InMemoryJournal()
        tid = TaskId(uuid4())
        inner.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="x")
        j = StreamingJournal(inner)

        tracker = TaskProgressTracker(j)
        snap = tracker.get(tid)
        assert snap is not None
        assert snap.total_nodes == 3
        assert snap.strategy == "x"
        tracker.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIncrementalUpdates:
    def test_task_started_then_node_started(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        j.write_node_started(tid, ArmId("a"), node_id="n0", skill_ref="list_cwd", node_index=0)

        snap = tracker.get(tid)
        assert snap.total_nodes == 2
        assert snap.current_node_id == "n0"
        assert snap.status == "running"
        tracker.close()

    def test_step_success_increments_completed(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd", success=True))
        j.write_step(tid, ArmId("a"), _step(1, "list_cwd", success=True))

        snap = tracker.get(tid)
        assert snap.nodes_completed == 2
        tracker.close()

    def test_failed_step_not_counted(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd", success=False))
        assert tracker.get(tid).nodes_completed == 0
        tracker.close()

    def test_trajectory_finalizes_status_and_cost(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        j.write_node_started(tid, ArmId("a"), node_id="n0", skill_ref="list_cwd", node_index=0)
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd"))
        j.write_trajectory(
            Trajectory(
                task_id=tid,
                arm_id=ArmId("a"),
                steps=[_step(0, "list_cwd")],
                outcome=TrajectoryOutcome(
                    success=True,
                    cost=CostEntry(tokens_in=50, tokens_out=30, usd=0.002),
                ),
            )
        )
        snap = tracker.get(tid)
        assert snap.status == "completed"
        assert snap.tokens_spent == 80
        assert snap.usd_spent == 0.002
        assert snap.current_node_id is None
        assert snap.current_node_index is None
        tracker.close()

    def test_checkpoint_updates_budget(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=5, strategy="s")
        j.write_checkpoint(
            tid,
            arm_id=ArmId("a"),
            nodes_completed=2,
            total_nodes=5,
            tokens_spent=150,
            usd_spent=0.015,
        )
        snap = tracker.get(tid)
        assert snap.tokens_spent == 150
        assert snap.usd_spent == 0.015
        tracker.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMultipleTasks:
    def test_tasks_do_not_bleed(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        t1 = TaskId(uuid4())
        t2 = TaskId(uuid4())

        j.write_task_started(t1, arm_id=ArmId("a"), total_nodes=2, strategy="s1")
        j.write_task_started(t2, arm_id=ArmId("b"), total_nodes=5, strategy="s2")
        j.write_step(t1, ArmId("a"), _step(0, "list_cwd"))

        assert tracker.get(t1).nodes_completed == 1
        assert tracker.get(t2).nodes_completed == 0
        assert tracker.get(t1).total_nodes == 2
        assert tracker.get(t2).total_nodes == 5
        tracker.close()

    def test_snapshots_sorted_by_last_event_ts_desc(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        t1 = TaskId(uuid4())
        t2 = TaskId(uuid4())

        j.write_task_started(t1, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        time.sleep(0.01)  # Implementation note.
        j.write_task_started(t2, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        time.sleep(0.01)
        # Implementation note.
        j.write_step(t1, ArmId("a"), _step(0, "list_cwd"))

        snaps = tracker.snapshots
        assert snaps[0].task_id == str(t1)
        tracker.close()

    def test_running_count_tracks_status(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        t1 = TaskId(uuid4())
        t2 = TaskId(uuid4())

        j.write_task_started(t1, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        j.write_task_started(t2, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        assert tracker.running_count() == 2

        # Implementation note.
        j.write_trajectory(
            Trajectory(
                task_id=t1,
                arm_id=ArmId("a"),
                steps=[_step(0, "list_cwd")],
                outcome=TrajectoryOutcome(success=True),
            )
        )
        assert tracker.running_count() == 1
        tracker.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEquivalenceWithPureFunction:
    def test_mirrors_all_task_progress(self):
        """Implementation note."""
        j = _wrap()
        tracker = TaskProgressTracker(j)
        tid = TaskId(uuid4())

        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd"))
        j.write_node_started(tid, ArmId("a"), node_id="n1", skill_ref="list_cwd", node_index=1)
        j.write_checkpoint(
            tid,
            arm_id=ArmId("a"),
            nodes_completed=1,
            total_nodes=3,
            tokens_spent=50,
            usd_spent=0.001,
        )

        from_tracker = tracker.get(tid)
        from_pure = task_progress_snapshot(j, tid)

        assert from_tracker.total_nodes == from_pure.total_nodes
        assert from_tracker.nodes_completed == from_pure.nodes_completed
        assert from_tracker.current_node_id == from_pure.current_node_id
        assert from_tracker.status == from_pure.status
        assert from_tracker.strategy == from_pure.strategy
        assert from_tracker.tokens_spent == from_pure.tokens_spent
        tracker.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLifecycle:
    def test_close_unsubscribes(self):
        j = _wrap()
        tracker = TaskProgressTracker(j)
        assert j.subscriber_count == 1
        tracker.close()
        assert j.subscriber_count == 0

    def test_context_manager(self):
        j = _wrap()
        with TaskProgressTracker(j) as tracker:
            assert j.subscriber_count == 1
            tid = TaskId(uuid4())
            j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=1, strategy="s")
            assert tracker.get(tid) is not None
        assert j.subscriber_count == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIncrementalPerformance:
    def test_tracker_get_is_fast_with_large_journal(self):
        """Implementation note."""
        j = _wrap()
        tracker = TaskProgressTracker(j)
        for _ in range(1000):
            tid = TaskId(uuid4())
            j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=1, strategy="s")
            j.write_step(tid, ArmId("a"), _step(0, "list_cwd"))

        t0 = time.monotonic()
        _ = tracker.snapshots
        elapsed_ms = (time.monotonic() - t0) * 1000
        assert elapsed_ms < 50, f"snapshots too slow: {elapsed_ms}ms"
        assert tracker.count() == 1000
        tracker.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from runtime.platform.ui import create_app  # noqa: E402


class TestUIWithTracker:
    def test_progress_endpoint_still_works(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        client.post("/api/run", json={"goal": "list stuff"})
        r = client.get("/api/progress")
        data = r.json()
        assert data["count"] >= 1
        assert len(data["tasks"]) >= 1

    def test_progress_endpoint_by_task_id(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        client.post("/api/run", json={"goal": "list"})
        all_r = client.get("/api/progress").json()
        tid = all_r["tasks"][0]["task_id"]
        single = client.get(f"/api/progress?task_id={tid}").json()
        assert single["task_id"] == tid
