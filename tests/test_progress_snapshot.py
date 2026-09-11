"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest

from runtime.memory.journal import (
    InMemoryJournal,
    all_task_progress,
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


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSnapshot:
    def test_empty_journal_single_task_none(self):
        assert task_progress_snapshot(InMemoryJournal(), uuid4()) is None

    def test_empty_journal_all_empty(self):
        assert all_task_progress(InMemoryJournal()) == []

    def test_task_started_gives_running(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(
            tid,
            arm_id=ArmId("a"),
            total_nodes=3,
            strategy="rule_x",
            task_type="probe",
        )
        snap = task_progress_snapshot(j, tid)
        assert snap is not None
        assert snap.status == "running"
        assert snap.total_nodes == 3
        assert snap.nodes_completed == 0
        assert snap.strategy == "rule_x"
        assert snap.progress_pct == 0.0

    def test_node_started_sets_current(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")
        j.write_node_started(tid, ArmId("a"), node_id="n1", skill_ref="list_cwd", node_index=1)
        snap = task_progress_snapshot(j, tid)
        assert snap.current_node_id == "n1"
        assert snap.current_node_index == 1
        assert snap.status == "running"

    def test_step_success_increments_completed(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=3, strategy="s")
        j.write_node_started(tid, ArmId("a"), node_id="n0", skill_ref="list_cwd", node_index=0)
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd"))
        j.write_node_started(tid, ArmId("a"), node_id="n1", skill_ref="list_cwd", node_index=1)
        j.write_step(tid, ArmId("a"), _step(1, "list_cwd"))
        snap = task_progress_snapshot(j, tid)
        assert snap.nodes_completed == 2
        assert round(snap.progress_pct, 1) == round(2 / 3 * 100, 1)

    def test_failed_step_not_counted(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        j.write_step(tid, ArmId("a"), _step(0, "list_cwd", success=False))
        snap = task_progress_snapshot(j, tid)
        assert snap.nodes_completed == 0

    def test_trajectory_finalizes_status(self):
        j = InMemoryJournal()
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
        snap = task_progress_snapshot(j, tid)
        assert snap.status == "completed"
        assert snap.tokens_spent == 80
        assert snap.usd_spent == 0.002
        assert snap.current_node_id is None
        assert snap.current_node_index is None

    def test_failed_trajectory(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        j.write_trajectory(
            Trajectory(
                task_id=tid,
                arm_id=ArmId("a"),
                steps=[_step(0, "list_cwd", success=False)],
                outcome=TrajectoryOutcome(success=False),
            )
        )
        snap = task_progress_snapshot(j, tid)
        assert snap.status == "failed"

    def test_checkpoint_updates_budget(self):
        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=5, strategy="s")
        j.write_checkpoint(
            tid,
            arm_id=ArmId("a"),
            nodes_completed=2,
            total_nodes=5,
            tokens_spent=150,
            usd_spent=0.0123,
        )
        snap = task_progress_snapshot(j, tid)
        assert snap.tokens_spent == 150
        assert snap.usd_spent == 0.0123


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAllTasks:
    def test_multiple_tasks_listed(self):
        j = InMemoryJournal()
        t1 = TaskId(uuid4())
        t2 = TaskId(uuid4())
        j.write_task_started(t1, arm_id=ArmId("a"), total_nodes=2, strategy="s")
        j.write_task_started(t2, arm_id=ArmId("b"), total_nodes=1, strategy="t")
        snaps = all_task_progress(j)
        assert len(snaps) == 2
        ids = {s.task_id for s in snaps}
        assert ids == {str(t1), str(t2)}

    def test_events_without_task_id_skipped(self):
        """Implementation note."""
        from runtime.memory.journal import BudgetEvent
        from runtime.platform.models import CostEntry as _Cost

        j = InMemoryJournal()
        tid = TaskId(uuid4())
        j.write_task_started(tid, arm_id=ArmId("a"), total_nodes=1, strategy="s")
        # Implementation note.
        j.write(
            BudgetEvent(
                task_id=None,
                arm_id=None,
                event_type="budget_commit",
                reason="global",
                cost=_Cost(usd=0.01),
            )
        )
        snaps = all_task_progress(j)
        assert len(snaps) == 1
        assert snaps[0].task_id == str(tid)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from runtime.platform.ui import create_app  # noqa: E402


class TestProgressAPI:
    def test_empty_progress(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        r = client.get("/api/progress")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 0
        assert data["running"] == 0
        assert data["tasks"] == []

    def test_progress_after_run(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        # Implementation note.
        client.post("/api/run", json={"goal": "list stuff"})
        r = client.get("/api/progress")
        data = r.json()
        assert data["count"] >= 1
        t = data["tasks"][0]
        assert t["status"] in ("completed", "running")
        assert t["total_nodes"] >= 1
        assert "progress_pct" in t
        assert "strategy" in t

    def test_progress_by_task_id(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        client.post("/api/run", json={"goal": "list"})
        all_r = client.get("/api/progress").json()
        tid = all_r["tasks"][0]["task_id"]
        single = client.get(f"/api/progress?task_id={tid}").json()
        assert single["task_id"] == tid
        assert "status" in single

    def test_unknown_task_id_404(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        r = client.get("/api/progress?task_id=ghost-1234")
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestDashboardMarkup:
    def test_index_has_tasks_card(self):
        app = create_app(journal_path=None)
        client = TestClient(app)
        html = client.get("/").text
        assert "Tasks (" in html  # Implementation note.
        assert "loadProgress" in html  # Implementation note.
        assert "/api/progress" in html
