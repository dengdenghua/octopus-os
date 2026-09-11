from __future__ import annotations

import time
from uuid import uuid4

from runtime.core.cerebrum.checkpoint_integrity import validate_checkpoint_state
from runtime.core.cerebrum.completion_receipt import build_completion_receipt
from runtime.core.cerebrum.run_state import converge_run_state
from runtime.execution.misc.file_write_leases import (
    FileWriteLeaseConflict,
    acquire_file_write_lease,
    authorize_file_write_handoff,
    release_file_write_lease,
)
from runtime.execution.misc.multiagent_contracts import validate_work_plan
from runtime.execution.parallel_agents import DispatchTaskInput, ParallelAgentOrchestrator
from runtime.execution.swarm import SwarmRuntime
from runtime.memory.journal import InMemoryJournal
from runtime.memory.runtime_state.blackboard import Blackboard
from runtime.memory.runtime_state.file_transactions import summarize_file_ops
from runtime.platform.models import Budget, BudgetLimits, BudgetSpec, TaskGraph, TaskId, TaskNode
from runtime.safety.approval.approval_gate import assess_approval_risk
from tests.test_swarm_runtime import FakeArm, FakeArmPool


def test_checkpoint_integrity_flags_corrupt_shapes() -> None:
    report = validate_checkpoint_state(
        {
            "messages_snapshot": {"bad": "shape"},
            "steps_snapshot": [{"iteration": 5, "action": "read_file({})"}],
            "working_set_snapshot": "bad",
        },
        iteration=2,
    )

    assert report.resume_safe is False
    assert "messages_snapshot_not_list" in report.errors
    assert "working_set_snapshot_not_list" in report.errors
    assert "steps_ahead_of_checkpoint_iteration" in report.errors


def test_checkpoint_integrity_accepts_restorable_checkpoint() -> None:
    report = validate_checkpoint_state(
        {
            "messages_snapshot": [{"role": "user", "content": "continue"}],
            "steps_snapshot": [{"iteration": 2, "action": "read_file({})"}],
            "working_set_snapshot": [{"path": "runtime/foo.py"}],
        },
        iteration=2,
    )

    assert report.resume_safe is True
    assert report.continue_from_iteration == 3
    assert report.working_set_count == 1


def test_file_transaction_summary_detects_contested_and_risky_paths() -> None:
    journal = InMemoryJournal()
    task_id = TaskId(uuid4())
    journal.write_file_op(
        path="../.env",
        action="write",
        task_id=task_id,
        actor="agent-a",
        old_size=0,
        new_size=10,
    )
    journal.write_file_op(
        path="../.env",
        action="edit",
        task_id=task_id,
        actor="agent-b",
        old_size=10,
        new_size=14,
    )

    summary = summarize_file_ops(journal.read_by_type("file_op"))

    assert summary.operation_count == 2
    assert summary.bytes_delta == 14
    assert summary.risky_paths == ("../.env",)
    assert summary.contested_paths == ("../.env",)
    assert journal.file_transaction_summary(task_id=task_id)["operation_count"] == 2


def test_file_write_lease_records_owner_and_rejects_conflict(tmp_path) -> None:
    class _Session:
        metadata: dict = {}

    session = _Session()
    target = tmp_path / "src" / "app.py"

    first = acquire_file_write_lease(session, target, owner="agent-a")
    again = acquire_file_write_lease(session, target, owner="agent-a")

    assert first is not None
    assert again is not None
    assert again.reentrant is True
    assert len(session.metadata["_file_write_leases"]) == 1

    try:
        acquire_file_write_lease(session, target, owner="agent-b")
    except FileWriteLeaseConflict as exc:
        assert "agent-a" in str(exc)
        assert "agent-b" in str(exc)
    else:  # pragma: no cover - assertion clarity
        raise AssertionError("expected FileWriteLeaseConflict")


def test_file_write_lease_allows_explicit_handoff_and_release(tmp_path) -> None:
    class _Session:
        metadata: dict = {}

    session = _Session()
    target = tmp_path / "src" / "app.py"

    acquire_file_write_lease(session, target, owner="agent-a")
    authorize_file_write_handoff(
        session,
        target,
        from_owner="agent-a",
        to_owner="agent-b",
    )
    transferred = acquire_file_write_lease(session, target, owner="agent-b")

    assert transferred is not None
    assert transferred.owner == "agent-b"
    assert len(session.metadata["_file_write_lease_handoffs"]) == 0

    assert release_file_write_lease(session, target, owner="agent-b") is True
    assert session.metadata["_file_write_leases"] == {}


def test_approval_risk_promotes_destructive_shell_to_critical() -> None:
    risk = assess_approval_risk("exec_shell", "git reset --hard && rm -rf dist")

    assert risk.level == "critical"
    assert "shell_execution" in risk.categories
    assert "destructive_command" in risk.categories
    assert risk.to_dict()["requires_approval"] is True


def test_approval_risk_keeps_local_reads_low() -> None:
    assert assess_approval_risk("read_file", "runtime/foo.py").level == "low"


def test_run_state_convergence_is_terminal_for_mixed_done_states() -> None:
    summary = converge_run_state(["completed", "failed", "cancelled"])

    assert summary.state == "partial"
    assert summary.terminal is True
    assert summary.completed == 1
    assert summary.failed == 1
    assert summary.cancelled == 1


def test_run_state_convergence_keeps_pending_batches_running() -> None:
    summary = converge_run_state(["completed", "pending"])

    assert summary.state == "running"
    assert summary.terminal is False


def test_completion_receipt_requires_terminal_clean_success() -> None:
    ready = build_completion_receipt(
        ["completed", "completed"],
        output_present=True,
    )
    failed = build_completion_receipt(
        ["completed", "failed"],
        output_present=True,
    )

    assert ready.ready is True
    assert ready.to_dict()["state"]["state"] == "completed"
    assert failed.ready is False
    assert "failed_work_items" in failed.issues


def test_blackboard_audit_detects_overwrites_contested_keys_and_large_values() -> None:
    board = Blackboard()
    board.write("plan", "first", writer="planner")
    board.write("plan", "second", writer="reviewer")
    board.write("blob", "x" * 8_100, writer="researcher")

    audit = board.audit()

    assert audit["key_count"] == 2
    assert audit["write_count"] == 3
    assert audit["overwrite_count"] == 1
    assert audit["contested_keys"] == ["plan"]
    assert audit["large_value_keys"] == ["blob"]
    assert audit["writers_by_key"]["plan"] == ["planner", "reviewer"]


def test_parallel_agent_contract_validation_reaches_batch_plan() -> None:
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"done:{description}"

    orch = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
    try:
        batch = orch.dispatch(
            [
                DispatchTaskInput(task_id="a", description="first"),
                DispatchTaskInput(task_id="b", description="second", depends_on=["a"]),
            ]
        )
        assert batch.plan is not None
        assert batch.plan.validation_issues == []
        assert validate_work_plan(batch.plan).valid is True
        assert batch.completion_receipt["ready"] is False
    finally:
        orch.shutdown(wait=False)


def test_parallel_agent_plan_flags_parallel_file_write_conflict() -> None:
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"done:{description}"

    orch = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
    try:
        batch = orch.dispatch(
            [
                DispatchTaskInput(task_id="a", description="first", write_paths=["src/app.py"]),
                DispatchTaskInput(task_id="b", description="second", write_paths=["src/app.py"]),
            ]
        )

        assert batch.plan is not None
        assert any(
            issue.startswith("parallel_file_write_conflict:src/app.py:a:b")
            for issue in batch.plan.validation_issues
        )
        assert batch.completion_receipt["ready"] is False
    finally:
        orch.shutdown(wait=False)


def test_parallel_agent_plan_allows_serialized_file_write_overlap() -> None:
    def runner(description, *, subagent_name, context=None, cancel_event=None):
        return f"done:{description}"

    orch = ParallelAgentOrchestrator(max_concurrency=2, task_runner=runner)
    try:
        batch = orch.dispatch(
            [
                DispatchTaskInput(task_id="a", description="first", write_paths=["src/app.py"]),
                DispatchTaskInput(
                    task_id="b",
                    description="second",
                    depends_on=["a"],
                    write_paths=["src/app.py"],
                ),
            ]
        )

        assert batch.plan is not None
        assert batch.plan.validation_issues == []
        assert batch.plan.contracts[0].write_paths == ["src/app.py"]
        assert batch.plan.contracts[1].write_paths == ["src/app.py"]
    finally:
        orch.shutdown(wait=False)


def test_parallel_agent_empty_result_is_contract_failure() -> None:
    def empty(description, *, subagent_name, context=None, cancel_event=None):
        return ""

    orch = ParallelAgentOrchestrator(max_concurrency=1, task_runner=empty)
    try:
        batch = orch.dispatch([DispatchTaskInput(description="must report")])
        for _ in range(100):
            snap = orch.get_batch(batch.batch_id)
            assert snap is not None
            if snap.status in {"failed", "partial"}:
                break
            time.sleep(0.02)
        snap = orch.get_batch(batch.batch_id)
        assert snap is not None
        assert snap.failed_tasks == 1
        assert snap.results[0].error == "empty_result_contract_violation"
        assert snap.completion_receipt["ready"] is False
        assert "failed_work_items" in snap.completion_receipt["issues"]
    finally:
        orch.shutdown(wait=False)


def test_swarm_plan_is_validated() -> None:
    graph = TaskGraph(
        nodes=[
            TaskNode(node_id="n1", skill_ref="read_file"),
            TaskNode(node_id="n2", skill_ref="run_test"),
        ],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
        task_type="code_fix",
    )
    arm = FakeArm("code_arm", allowed=["read_file", "run_test"])
    runtime = SwarmRuntime(arm_pool=FakeArmPool([arm]), max_workers=2)
    budget = Budget(
        task_id=TaskId(uuid4()),
        limits=BudgetLimits(tokens=100_000, usd=10.0),
    )

    result = runtime.run(graph=graph, budget=budget)

    assert result.plan is not None
    assert result.plan.validation_issues == []
    assert result.contract_validation_issues == []
    assert result.contract_validation_warnings == []
    assert result.completion_receipt["ready"] is True
