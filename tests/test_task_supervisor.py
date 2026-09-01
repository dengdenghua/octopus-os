from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from runtime.platform.process.task_supervisor import (
    LostTaskLease,
    TaskCapabilityManifest,
    TaskLeaseConflict,
    TaskRunRecord,
    TaskRunStatus,
    TaskSupervisor,
    TaskSupervisorStore,
    task_lease_health,
)
from runtime.protocol import Turn, TurnParams, TurnStatus
from runtime.sensing.gateway.realtime_turn_outcome import (
    _record_react_trace_event,
    _record_task_run_finished,
)


def test_realtime_react_lifecycle_projects_to_task_supervisor(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="realtime-worker",
        lease_ttl_seconds=30,
    )
    runtime = SimpleNamespace(_task_supervisor=supervisor, _trace_store=None)
    params = TurnParams(threadId="thread-1", input=[], cwd=str(tmp_path))
    turn = Turn(id="turn-1", threadId="thread-1", params=params)
    turn.execution_workspace_path = str(tmp_path / "resolved-workspace")

    _record_react_trace_event(
        runtime,
        turn,
        {"type": "react_started", "task_id": "react-task-1"},
    )
    started = supervisor.store.get("react-task-1")
    assert started is not None
    assert started.status == TaskRunStatus.RUNNING
    assert started.origin_task_id == "turn-1"
    assert started.workspace_path == str(tmp_path / "resolved-workspace")
    assert started.capabilities.workspace_paths == [str(tmp_path / "resolved-workspace")]

    turn.task_id = "react-task-1"
    turn.objective_id = "react-task-1"
    turn.status = TurnStatus.PAUSED
    turn.checkpoint_id = 7
    turn.outcome_reason = "iteration_limit"
    _record_task_run_finished(runtime, turn)

    paused = supervisor.store.get("react-task-1")
    assert paused is not None
    assert paused.status == TaskRunStatus.PAUSED
    assert paused.latest_checkpoint_id == 7
    assert paused.terminal_reason == "iteration_limit"
    assert paused.metadata["turn_id"] == "turn-1"


def test_task_supervisor_persists_lifecycle_and_releases_terminal_lease(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )

    started = supervisor.start_task(
        task_id="task-1",
        kind="loop",
        owner_id="alice",
        thread_id="thread-1",
        title="Fix failing tests",
        goal="Fix failing tests",
        mode="code",
        workspace_path=str(tmp_path / "workspace"),
    )

    assert started.status == TaskRunStatus.RUNNING
    assert started.lease is not None
    assert started.lease.holder_id == "worker-a"
    assert started.capabilities.allows_group("shell") is True
    assert started.capabilities.workspace_paths == [str(tmp_path / "workspace")]

    heartbeat = supervisor.heartbeat("task-1")
    assert heartbeat.heartbeat_at is not None
    assert heartbeat.lease is not None
    assert heartbeat.lease.token == started.lease.token

    completed = supervisor.transition(
        "task-1",
        TaskRunStatus.COMPLETED,
        checkpoint_id=42,
    )
    assert completed.status == TaskRunStatus.COMPLETED
    assert completed.completed_at is not None
    assert completed.latest_checkpoint_id == 42
    assert completed.lease is None

    reloaded = TaskSupervisorStore(tmp_path / "task_runs.json").get("task-1")
    assert reloaded is not None
    assert reloaded.status == TaskRunStatus.COMPLETED
    assert reloaded.latest_checkpoint_id == 42


def test_stale_turn_recovery_is_identity_scoped_and_releases_foreign_lease(tmp_path):
    worker = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="dead-worker",
        lease_ttl_seconds=300,
    )
    worker.start_task(
        task_id="task-stale",
        kind="realtime_objective",
        origin_task_id="turn-stale",
        metadata={"turn_id": "turn-stale"},
    )
    recovery = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="recovery-worker",
        lease_ttl_seconds=300,
    )

    with pytest.raises(ValueError, match="not 'different-turn'"):
        recovery.recover_stale_turn(
            "task-stale",
            TaskRunStatus.FAILED,
            expected_turn_id="different-turn",
            reason="stale_in_progress_turn",
        )

    recovered = recovery.recover_stale_turn(
        "task-stale",
        TaskRunStatus.FAILED,
        expected_turn_id="turn-stale",
        reason="stale_in_progress_turn",
    )
    assert recovered.status == TaskRunStatus.FAILED
    assert recovered.lease is None
    assert recovered.terminal_reason == "stale_in_progress_turn"


def test_task_supervisor_terminal_transition_is_idempotent_and_non_downgrading(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(task_id="task-terminal", kind="loop")
    completed = supervisor.transition(
        "task-terminal",
        TaskRunStatus.COMPLETED,
        reason="verified",
        checkpoint_id=42,
    )

    stale_failed = supervisor.transition(
        "task-terminal",
        TaskRunStatus.FAILED,
        reason="stale failure callback",
        checkpoint_id=43,
        metadata_patch={"late_callback": True},
    )

    assert stale_failed.status == TaskRunStatus.COMPLETED
    assert stale_failed.completed_at == completed.completed_at
    assert stale_failed.terminal_reason == "verified"
    assert stale_failed.latest_checkpoint_id == 42
    assert stale_failed.metadata["late_callback"] is True
    terminal_events = stale_failed.metadata["terminal_transition_events"]
    assert terminal_events[-1]["ignored_status"] == "failed"
    assert terminal_events[-1]["reason"] == "stale failure callback"
    assert terminal_events[-1]["checkpoint_id"] == 43
    assert terminal_events[-1]["checkpoint_recorded_to_latest"] is False
    assert terminal_events[-1]["previous_checkpoint_id"] == 42
    assert terminal_events[-1]["previous_status"] == "completed"
    assert terminal_events[-1]["previous_terminal_reason"] == "verified"
    assert terminal_events[-1]["previous_completed_at"] == completed.completed_at
    assert terminal_events[-1]["holder_id"] == "worker-a"
    assert stale_failed.lease is None

    reloaded = TaskSupervisorStore(tmp_path / "task_runs.json").get("task-terminal")
    assert reloaded is not None
    assert reloaded.status == TaskRunStatus.COMPLETED
    assert reloaded.terminal_reason == "verified"
    assert reloaded.latest_checkpoint_id == 42
    assert reloaded.metadata["terminal_transition_events"][-1]["ignored_status"] == "failed"


def test_task_supervisor_terminal_transition_backfills_missing_checkpoint(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(task_id="task-terminal", kind="loop")
    completed = supervisor.transition(
        "task-terminal",
        TaskRunStatus.COMPLETED,
        reason="verified before trace write",
    )
    assert completed.latest_checkpoint_id is None

    backfilled = supervisor.transition(
        "task-terminal",
        TaskRunStatus.COMPLETED,
        reason="terminal trace backfill after crash",
        checkpoint_id="ckpt-terminal",
    )

    assert backfilled.status == TaskRunStatus.COMPLETED
    assert backfilled.latest_checkpoint_id == "ckpt-terminal"
    terminal_events = backfilled.metadata["terminal_transition_events"]
    assert terminal_events[-1]["ignored_status"] == "completed"
    assert terminal_events[-1]["checkpoint_recorded_to_latest"] is True
    assert terminal_events[-1]["previous_checkpoint_id"] is None


def test_task_supervisor_rejects_foreign_lease_until_expired(tmp_path):
    path = tmp_path / "task_runs.json"
    worker_a = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    worker_b = TaskSupervisor.from_path(path, holder_id="worker-b", lease_ttl_seconds=30)

    started = worker_a.start_task(task_id="task-lease", kind="loop")

    assert started.lease is not None
    assert started.lease.holder_id == "worker-a"
    with pytest.raises(TaskLeaseConflict):
        worker_b.start_task(task_id="task-lease", kind="loop")
    with pytest.raises(LostTaskLease):
        worker_b.heartbeat("task-lease")
    with pytest.raises(LostTaskLease):
        worker_b.transition("task-lease", TaskRunStatus.VERIFYING)

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    worker_a.store.mutate("task-lease", _expire)
    takeover = worker_b.start_task(task_id="task-lease", kind="loop")

    assert takeover.lease is not None
    assert takeover.lease.holder_id == "worker-b"
    assert takeover.lease.token != started.lease.token
    assert worker_b.is_current_holder("task-lease") is True
    assert worker_a.is_current_holder("task-lease") is False
    with pytest.raises(LostTaskLease):
        worker_a.heartbeat("task-lease")


def test_task_supervisor_takeover_preserves_recovery_context(tmp_path):
    path = tmp_path / "task_runs.json"
    worker_a = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    worker_b = TaskSupervisor.from_path(path, holder_id="worker-b", lease_ttl_seconds=30)
    started = worker_a.start_task(
        task_id="task-preserve",
        kind="loop",
        owner_id="alice",
        thread_id="thread-1",
        parent_task_id="parent-1",
        origin_task_id="origin-1",
        resume_checkpoint_id="resume-1",
        title="Recover me",
        goal="Keep context across takeover",
        mode="code",
        workspace_path=str(tmp_path / "workspace-a"),
        metadata={"attempt_count": 1, "source": "worker-a"},
    )
    worker_a.transition(
        "task-preserve",
        TaskRunStatus.VERIFYING,
        checkpoint_id="ckpt-1",
        metadata_patch={"last_loop_status": "verifying"},
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    worker_a.store.mutate("task-preserve", _expire)
    takeover = worker_b.start_task(
        task_id="task-preserve",
        workspace_path=str(tmp_path / "workspace-b"),
        metadata={"source": "worker-b", "takeover": True},
    )

    assert started.lease is not None
    assert takeover.lease is not None
    assert takeover.lease.holder_id == "worker-b"
    assert takeover.lease.token != started.lease.token
    assert takeover.kind == "loop"
    assert takeover.owner_id == "alice"
    assert takeover.thread_id == "thread-1"
    assert takeover.parent_task_id == "parent-1"
    assert takeover.origin_task_id == "origin-1"
    assert takeover.resume_checkpoint_id == "resume-1"
    assert takeover.latest_checkpoint_id == "ckpt-1"
    assert takeover.title == "Recover me"
    assert takeover.goal == "Keep context across takeover"
    assert takeover.mode == "code"
    assert takeover.workspace_path == str(tmp_path / "workspace-b")
    assert takeover.metadata["attempt_count"] == 1
    assert takeover.metadata["last_loop_status"] == "verifying"
    assert takeover.metadata["source"] == "worker-b"
    assert takeover.metadata["takeover"] is True
    assert str(tmp_path / "workspace-b") in takeover.capabilities.workspace_paths


def test_task_supervisor_operator_takeover_requires_stale_lease(tmp_path):
    path = tmp_path / "task_runs.json"
    worker_a = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    worker_b = TaskSupervisor.from_path(path, holder_id="worker-b", lease_ttl_seconds=30)
    started = worker_a.start_task(task_id="task-takeover", kind="loop", owner_id="alice")

    with pytest.raises(TaskLeaseConflict):
        worker_b.takeover_task("task-takeover", by="bob")

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    worker_a.store.mutate("task-takeover", _expire)
    takeover = worker_b.takeover_task(
        "task-takeover",
        by="bob",
        reason="worker-a heartbeat expired",
    )

    assert started.lease is not None
    assert takeover.lease is not None
    assert takeover.lease.holder_id == "worker-b"
    assert takeover.lease.token != started.lease.token
    assert takeover.status == TaskRunStatus.RUNNING
    assert takeover.owner_id == "alice"
    assert takeover.metadata["takeover"] is True
    assert takeover.metadata["takeover_by"] == "bob"
    assert takeover.metadata["takeover_reason"] == "worker-a heartbeat expired"
    assert takeover.metadata["takeover_events"][-1]["previous_holder_id"] == "worker-a"
    assert takeover.metadata["takeover_events"][-1]["previous_lease_token"] == started.lease.token
    assert worker_b.is_current_holder("task-takeover") is True
    assert worker_a.is_current_holder("task-takeover") is False


def test_task_supervisor_operator_takeover_allows_missing_lease(tmp_path):
    path = tmp_path / "task_runs.json"
    store = TaskSupervisor.from_path(path, holder_id="seed").store
    store.upsert(
        TaskRunRecord(
            task_id="task-orphan",
            kind="loop",
            status=TaskRunStatus.RUNNING,
            metadata={"attempt_count": 1},
        )
    )
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)

    takeover = operator.takeover_task("task-orphan", by="alice", reason="orphaned")

    assert takeover.lease is not None
    assert takeover.lease.holder_id == "operator"
    assert takeover.metadata["attempt_count"] == 1
    assert takeover.metadata["takeover_events"][-1]["previous_holder_id"] is None


def test_task_supervisor_operator_takeover_normalizes_status_strings(tmp_path):
    path = tmp_path / "task_runs.json"
    store = TaskSupervisor.from_path(path, holder_id="seed").store
    store.upsert(
        TaskRunRecord(
            task_id="task-orphan",
            kind="loop",
            status=TaskRunStatus.RUNNING,
        )
    )
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)

    takeover = operator.takeover_task("task-orphan", by="alice", status="paused")

    assert takeover.status == TaskRunStatus.PAUSED
    assert isinstance(takeover.status, TaskRunStatus)


def test_task_supervisor_restart_clears_terminal_runtime_state(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    supervisor.start_task(task_id="task-retry", kind="loop", title="Retry me")
    failed = supervisor.transition(
        "task-retry",
        TaskRunStatus.FAILED,
        reason="verifier failed",
        checkpoint_id="ckpt-failed",
    )

    assert failed.completed_at is not None
    assert failed.terminal_reason == "verifier failed"

    restarted = supervisor.start_task(task_id="task-retry", kind="loop")

    assert restarted.status == TaskRunStatus.RUNNING
    assert restarted.completed_at is None
    assert restarted.terminal_reason == ""
    assert restarted.latest_checkpoint_id == "ckpt-failed"
    assert restarted.title == "Retry me"
    assert restarted.lease is not None
    assert restarted.metadata["restart"] is True
    assert restarted.metadata["restart_from_status"] == "failed"
    assert restarted.metadata["restart_from_checkpoint_id"] == "ckpt-failed"
    assert restarted.metadata["restart_holder_id"] == "worker-a"
    assert restarted.metadata["restart_events"][-1]["previous_status"] == "failed"
    assert restarted.metadata["restart_events"][-1]["previous_completed_at"] == failed.completed_at
    assert restarted.metadata["restart_events"][-1]["previous_terminal_reason"] == "verifier failed"
    assert restarted.metadata["restart_events"][-1]["previous_checkpoint_id"] == "ckpt-failed"
    assert restarted.metadata["restart_events"][-1]["next_status"] == "running"


def test_task_supervisor_detects_stale_same_holder_token(tmp_path):
    path = tmp_path / "task_runs.json"
    first = TaskSupervisor.from_path(path, holder_id="same-worker", lease_ttl_seconds=30)
    second = TaskSupervisor.from_path(path, holder_id="same-worker", lease_ttl_seconds=30)

    original = first.start_task(task_id="task-token", kind="loop")
    replacement = second.start_task(task_id="task-token", kind="loop")

    assert original.lease is not None
    assert replacement.lease is not None
    assert replacement.lease.token != original.lease.token
    with pytest.raises(LostTaskLease, match="token"):
        first.heartbeat("task-token")


def test_task_supervisor_store_overview_surfaces_stale_active_runs(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    running = supervisor.start_task(task_id="task-running", kind="loop")
    supervisor.start_task(task_id="task-expired", kind="loop")
    supervisor.start_task(task_id="task-complete", kind="loop")
    supervisor.transition("task-complete", TaskRunStatus.COMPLETED, checkpoint_id=5)
    supervisor.store.upsert(
        TaskRunRecord(
            task_id="task-orphan",
            kind="loop",
            status=TaskRunStatus.RUNNING,
        )
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    supervisor.store.mutate("task-expired", _expire)

    overview = TaskSupervisorStore(path).overview()

    assert overview["schema"] == "echo.task_runs_overview.v1"
    assert overview["total"] == 4
    assert overview["active_count"] == 3
    assert overview["terminal_count"] == 1
    assert overview["leased_count"] == 2
    assert overview["expired_lease_count"] == 1
    assert overview["stale_nonterminal_count"] == 2
    assert overview["takeover_recommended_count"] == 2
    assert overview["resumable_count"] == 0
    assert overview["by_status"]["running"] == 3
    assert overview["by_status"]["completed"] == 1
    assert overview["by_kind"]["loop"] == 4
    assert overview["by_recommended_action"]["monitor"] == 1
    assert overview["by_recommended_action"]["none"] == 1
    assert overview["by_recommended_action"]["takeover"] == 2
    assert "task-running" in overview["active_task_ids"]
    assert "task-expired" in overview["expired_lease_task_ids"]
    assert set(overview["stale_nonterminal_task_ids"]) == {"task-expired", "task-orphan"}
    assert set(overview["takeover_task_ids"]) == {"task-expired", "task-orphan"}
    assert overview["resumable_task_ids"] == []
    assert running.task_id in overview["active_task_ids"]
    health = {item["task_id"]: item for item in overview["lease_health"]}
    assert health["task-running"]["state"] == "ok"
    assert health["task-running"]["holder_id"] == "worker-a"
    assert health["task-running"]["recommended_action"] == "monitor"
    assert health["task-running"]["can_takeover"] is False
    assert health["task-expired"]["state"] == "expired"
    assert health["task-expired"]["recommended_action"] == "takeover"
    assert health["task-expired"]["can_takeover"] is True
    assert health["task-orphan"]["state"] == "missing_lease"
    assert health["task-orphan"]["recommended_action"] == "takeover"
    assert "task-complete" not in health


def test_task_supervisor_store_list_page_returns_total_from_same_filtered_snapshot(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    for index in range(4):
        supervisor.start_task(
            task_id=f"task-loop-{index}",
            kind="loop",
            owner_id="alice",
            thread_id=f"thread-{index}",
        )
    supervisor.start_task(task_id="task-background", kind="background", owner_id="alice")

    page = supervisor.store.list_page(kind="loop", owner_id="alice", limit=2, offset=1)

    assert page["total"] == 4
    assert page["limit"] == 2
    assert page["offset"] == 1
    assert len(page["items"]) == 2
    assert all(task.kind == "loop" for task in page["items"])
    assert supervisor.store.count(kind="loop", owner_id="alice") == 4
    empty_page = supervisor.store.list_page(kind="loop", owner_id="alice", limit=2, offset=99)
    assert empty_page["total"] == 4
    assert empty_page["items"] == []


def test_task_lease_health_recommends_takeover_resume_and_terminal_recovery(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    supervisor.start_task(task_id="task-expired", kind="loop")
    supervisor.start_task(task_id="task-failed", kind="loop")
    supervisor.transition("task-failed", TaskRunStatus.FAILED, checkpoint_id="ckpt-failed")

    def _expire_with_checkpoint(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": "ckpt-active",
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    supervisor.store.mutate("task-expired", _expire_with_checkpoint)

    expired = supervisor.store.get("task-expired")
    failed = supervisor.store.get("task-failed")
    assert expired is not None
    assert failed is not None

    expired_health = task_lease_health(expired)
    failed_health = task_lease_health(failed)
    overview = TaskSupervisorStore(path).overview()

    assert expired_health["state"] == "expired"
    assert expired_health["can_takeover"] is True
    assert expired_health["can_resume"] is True
    assert expired_health["recommended_action"] == "takeover_and_resume"
    assert expired_health["recovery"]["latest_checkpoint_id"] == "ckpt-active"
    assert expired_health["recovery"]["operation"] == "takeover_then_resume"
    assert expired_health["recovery"]["steps"] == [
        "takeover_task",
        "resume_from_checkpoint",
    ]
    assert expired_health["recovery"]["checkpoint_id"] == "ckpt-active"

    assert failed_health["state"] == "terminal"
    assert failed_health["can_takeover"] is False
    assert failed_health["can_resume"] is True
    assert failed_health["recommended_action"] == "resume_from_checkpoint"
    assert failed_health["recovery"]["latest_checkpoint_id"] == "ckpt-failed"
    assert failed_health["recovery"]["operation"] == "resume_from_checkpoint"
    assert failed_health["recovery"]["steps"] == ["resume_from_checkpoint"]
    assert failed_health["recovery"]["checkpoint_id"] == "ckpt-failed"

    assert overview["takeover_recommended_count"] == 1
    assert overview["resumable_count"] == 2
    assert overview["takeover_task_ids"] == ["task-expired"]
    assert set(overview["resumable_task_ids"]) == {"task-expired", "task-failed"}
    assert overview["by_recommended_action"]["resume_from_checkpoint"] == 1
    assert overview["by_recommended_action"]["takeover_and_resume"] == 1


def test_task_supervisor_recovery_queue_prioritizes_actionable_work(tmp_path):
    path = tmp_path / "task_runs.json"
    supervisor = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    supervisor.start_task(task_id="task-monitor", kind="loop")
    supervisor.start_task(task_id="task-expired", kind="loop", title="Expired")
    supervisor.start_task(task_id="task-failed", kind="loop", title="Failed")
    supervisor.transition("task-failed", TaskRunStatus.FAILED, checkpoint_id="ckpt-failed")
    supervisor.start_task(
        task_id="task-approval",
        kind="loop",
        title="Approval",
        metadata={"approval_required": True},
    )
    supervisor.transition(
        "task-approval",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": "ckpt-expired",
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    supervisor.store.mutate("task-expired", _expire)

    queue = supervisor.store.recovery_queue(kind="loop")
    with_monitor = supervisor.store.recovery_queue(kind="loop", include_monitor=True)
    limited = supervisor.store.recovery_queue(kind="loop", include_monitor=True, limit=2)

    assert queue["schema"] == "echo.task_recovery_queue.v1"
    assert queue["total"] == 3
    assert [item["task_id"] for item in queue["items"]] == [
        "task-expired",
        "task-failed",
        "task-approval",
    ]
    assert queue["items"][0]["recommended_action"] == "takeover_and_resume"
    assert queue["items"][0]["can_takeover"] is True
    assert queue["items"][0]["can_resume"] is True
    assert queue["items"][0]["latest_checkpoint_id"] == "ckpt-expired"
    assert queue["items"][0]["checkpoint_id"] == "ckpt-expired"
    assert queue["items"][0]["operation"] == "takeover_then_resume"
    assert queue["items"][0]["steps"] == [
        "takeover_task",
        "resume_from_checkpoint",
    ]
    assert queue["items"][0]["recovery_plan"]["checkpoint_id"] == "ckpt-expired"
    assert queue["items"][1]["recommended_action"] == "resume_from_checkpoint"
    assert queue["items"][1]["operation"] == "resume_from_checkpoint"
    assert queue["items"][2]["recommended_action"] == "await_operator_approval"
    assert queue["items"][2]["operation"] == "approval_decision"
    assert queue["items"][2]["steps"] == ["approval_decision"]

    assert with_monitor["total"] == 4
    assert with_monitor["items"][-1]["task_id"] == "task-monitor"
    assert with_monitor["items"][-1]["recommended_action"] == "monitor"
    assert limited["count"] == 2
    assert len(limited["items"]) == 2


def test_task_lease_health_recommends_operator_approval_for_waiting_task(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(task_id="task-approval", kind="loop")
    supervisor.transition(
        "task-approval",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
        metadata_patch={"approval_required": True},
    )

    record = supervisor.store.get("task-approval")
    assert record is not None
    health = task_lease_health(record)
    overview = supervisor.store.overview()

    assert health["state"] == "ok"
    assert health["recommended_action"] == "await_operator_approval"
    assert health["can_takeover"] is False
    assert health["can_resume"] is False
    assert overview["by_status"]["waiting_approval"] == 1
    assert overview["by_recommended_action"]["await_operator_approval"] == 1
    assert overview["lease_health"][0]["recommended_action"] == "await_operator_approval"


def test_task_lease_health_recommends_takeover_for_expired_approval_task(tmp_path):
    path = tmp_path / "task_runs.json"
    worker = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)
    waiting = worker.start_task(
        task_id="task-approval-expired",
        kind="loop",
        metadata={
            "approval_required": True,
            "approval_tool_name": "exec_shell",
            "approval_action": "confirm",
        },
    )
    worker.transition(
        "task-approval-expired",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={
                "latest_checkpoint_id": "ckpt-active",
                "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
            },
            deep=True,
        )

    worker.store.mutate("task-approval-expired", _expire)

    record = worker.store.get("task-approval-expired")
    assert record is not None
    health = task_lease_health(record)
    overview = worker.store.overview()
    takeover = operator.takeover_task(
        "task-approval-expired",
        by="alice",
        reason="approval worker died",
    )

    assert waiting.lease is not None
    assert health["state"] == "expired"
    assert health["recommended_action"] == "takeover_for_approval"
    assert health["can_takeover"] is True
    assert health["can_resume"] is False
    assert overview["by_recommended_action"]["takeover_for_approval"] == 1
    assert takeover.status == TaskRunStatus.WAITING_APPROVAL
    assert takeover.lease is not None
    assert takeover.lease.holder_id == "operator"
    assert takeover.latest_checkpoint_id == "ckpt-active"
    assert takeover.metadata["takeover_events"][-1]["previous_status"] == "waiting_approval"


def test_task_lease_health_distinguishes_approval_policy_denial(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(task_id="task-denied", kind="loop")
    supervisor.transition(
        "task-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval policy denied",
        metadata_patch={
            "approval_required": False,
            "approval_denied": True,
        },
    )

    record = supervisor.store.get("task-denied")
    assert record is not None
    health = task_lease_health(record)

    assert health["recommended_action"] == "approval_policy_denied"
    assert health["recovery_reason"] == "task is blocked by approval policy"


def test_task_lease_health_distinguishes_capability_policy_denial(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(task_id="task-capability-denied", kind="loop")
    supervisor.transition(
        "task-capability-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="task capability group disabled: shell",
        metadata_patch={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "capability_denied",
            "capability_denied": True,
        },
    )

    record = supervisor.store.get("task-capability-denied")
    assert record is not None
    health = task_lease_health(record)
    overview = supervisor.store.overview()

    assert health["recommended_action"] == "capability_policy_denied"
    assert health["recovery_reason"] == "task is blocked by disabled capability"
    assert overview["by_recommended_action"]["capability_policy_denied"] == 1


def test_task_supervisor_rejects_takeover_for_non_approvable_denials(tmp_path):
    path = tmp_path / "task_runs.json"
    worker = TaskSupervisor.from_path(path, holder_id="worker-a", lease_ttl_seconds=30)
    operator = TaskSupervisor.from_path(path, holder_id="operator", lease_ttl_seconds=30)
    worker.start_task(
        task_id="task-policy-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "deny",
        },
    )
    worker.transition(
        "task-policy-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval policy denied",
    )
    worker.start_task(
        task_id="task-capability-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "capability_denied",
            "capability_denied": True,
        },
    )
    worker.transition(
        "task-capability-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="task capability group disabled: shell",
    )

    def _expire(record):
        assert record.lease is not None
        return record.model_copy(
            update={"lease": record.lease.model_copy(update={"expires_at": time.time() - 1})},
            deep=True,
        )

    worker.store.mutate("task-policy-denied", _expire)
    worker.store.mutate("task-capability-denied", _expire)

    with pytest.raises(ValueError, match="non-approvable task"):
        operator.takeover_task("task-policy-denied", by="alice")
    with pytest.raises(ValueError, match="non-approvable task"):
        operator.takeover_task("task-capability-denied", by="alice")

    assert (
        task_lease_health(worker.store.get("task-policy-denied"))["recommended_action"]
        == "approval_policy_denied"
    )
    assert (
        task_lease_health(worker.store.get("task-capability-denied"))["recommended_action"]
        == "capability_policy_denied"
    )


def test_task_supervisor_records_approval_decisions(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(
        task_id="task-approval",
        kind="loop",
        metadata={
            "approval_required": True,
            "approval_tool_name": "exec_shell",
            "approval_action": "confirm",
        },
    )
    waiting = supervisor.transition(
        "task-approval",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    approved = supervisor.record_approval_decision(
        "task-approval",
        approved=True,
        decided_by="alice",
        reason="looks safe",
    )

    assert waiting.status == TaskRunStatus.WAITING_APPROVAL
    assert approved.status == TaskRunStatus.RUNNING
    assert approved.metadata["approval_required"] is False
    assert approved.metadata["approval_decision"] == "approved"
    assert approved.metadata["approval_decided_by"] == "alice"
    assert approved.metadata["approval_decision_reason"] == "looks safe"
    assert approved.metadata["approval_decisions"][-1]["tool_name"] == "exec_shell"
    assert "approval_reason" not in approved.metadata
    assert approved.lease is not None


def test_task_supervisor_records_approval_rejection_as_paused(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(
        task_id="task-rejected",
        kind="loop",
        metadata={"approval_required": True, "approval_tool_name": "exec_shell"},
    )
    supervisor.transition(
        "task-rejected",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval required",
    )

    rejected = supervisor.record_approval_decision(
        "task-rejected",
        approved=False,
        decided_by="alice",
        reason="too risky",
    )

    assert rejected.status == TaskRunStatus.PAUSED
    assert rejected.metadata["approval_required"] is False
    assert rejected.metadata["approval_denied"] is True
    assert rejected.metadata["approval_decision"] == "rejected"
    assert rejected.terminal_reason == "too risky"
    assert task_lease_health(rejected)["recommended_action"] == "resume_paused_task"


def test_task_supervisor_rejects_decision_for_non_approvable_denials(tmp_path):
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task_runs.json",
        holder_id="worker-a",
        lease_ttl_seconds=30,
    )
    supervisor.start_task(
        task_id="task-policy-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "deny",
        },
    )
    supervisor.transition(
        "task-policy-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="approval policy denied",
    )
    supervisor.start_task(
        task_id="task-capability-denied",
        kind="loop",
        metadata={
            "approval_required": False,
            "approval_denied": True,
            "approval_action": "capability_denied",
            "capability_denied": True,
        },
    )
    supervisor.transition(
        "task-capability-denied",
        TaskRunStatus.WAITING_APPROVAL,
        reason="task capability group disabled: shell",
    )

    for approved in (True, False):
        with pytest.raises(ValueError, match="approval policy"):
            supervisor.record_approval_decision("task-policy-denied", approved=approved)
        with pytest.raises(ValueError, match="disabled capability"):
            supervisor.record_approval_decision("task-capability-denied", approved=approved)

    assert supervisor.store.get("task-policy-denied").status == TaskRunStatus.WAITING_APPROVAL
    assert supervisor.store.get("task-capability-denied").status == TaskRunStatus.WAITING_APPROVAL


def test_task_capability_manifest_fails_closed_for_disabled_group():
    manifest = TaskCapabilityManifest(groups={"shell": False})

    assert manifest.allows_group("builtin") is True
    assert manifest.allows_group("shell") is False
    assert manifest.allows_group("unknown") is False
    assert manifest.allows_group(None) is True

