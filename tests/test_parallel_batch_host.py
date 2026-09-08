"""Durable aggregate host lifecycle for standalone parallel batches."""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from runtime.execution.host_boundary import host_execution_scope
from runtime.execution.parallel_agents.orchestrator import ParallelAgentOrchestrator
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor


def _wait_for(predicate: Any, *, timeout: float = 4.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    return predicate()


def test_standalone_batch_exposes_and_settles_durable_host_task(tmp_path) -> None:
    supervisor_path = tmp_path / "task-runs.json"
    supervisor = TaskSupervisor.from_path(supervisor_path, holder_id="parallel-host")

    def runner(description: str, **_kwargs: Any) -> str:
        return f"done: {description}"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    try:
        batch = orchestrator.dispatch(
            [{"description": "durable aggregate"}],
            thread_id="standalone-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert batch.host_task_id == f"parallel-batch:{batch.batch_id}"

        admitted = supervisor.store.get(batch.host_task_id)
        assert admitted is not None
        assert admitted.kind == "parallel_batch"
        assert admitted.owner_id == "alice"
        assert admitted.thread_id == "standalone-thread"
        assert admitted.metadata["tenant_id"] == "tenant-a"
        assert admitted.status in {TaskRunStatus.RUNNING, TaskRunStatus.COMPLETED}

        def completed_batch() -> Any:
            current = orchestrator.get_batch(batch.batch_id)
            return current if current is not None and current.status == "completed" else None

        completed = _wait_for(completed_batch)
        assert completed is not None
        assert completed.host_task_id == batch.host_task_id

        settled = supervisor.store.get(batch.host_task_id)
        assert settled is not None
        assert settled.status == TaskRunStatus.COMPLETED
        assert settled.lease is None
        workers = supervisor.store.list(kind="parallel_agent", owner_id="alice")
        assert len(workers) == 1
        assert workers[0].parent_task_id == batch.host_task_id

        reopened = TaskSupervisor.from_path(supervisor_path, holder_id="reader")
        persisted = reopened.store.get(batch.host_task_id)
        assert persisted is not None
        assert persisted.status == TaskRunStatus.COMPLETED

        recovery = orchestrator.recovery_snapshot(batch.batch_id)
        assert recovery is not None
        assert recovery.host_task_id == batch.host_task_id
    finally:
        orchestrator.shutdown(wait=True)


def test_host_lease_loss_fails_batch_and_closes_durable_row(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-host",
        lease_ttl_seconds=1.0,
    )
    started = threading.Event()
    release = threading.Event()

    def runner(_description: str, **_kwargs: Any) -> str:
        started.set()
        release.wait(timeout=5.0)
        return "late result"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    try:
        batch = orchestrator.dispatch(
            [{"description": "lease loss"}],
            thread_id="lease-loss-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert started.wait(timeout=3.0)
        assert batch.host_task_id is not None

        def expire(record: Any) -> Any:
            assert record.lease is not None
            return record.model_copy(
                update={
                    "lease": record.lease.model_copy(update={"expires_at": time.time() - 1}),
                }
            )

        supervisor.store.mutate(batch.host_task_id, expire)

        def failed_batch() -> Any:
            current = orchestrator.get_batch(batch.batch_id)
            return (
                current if current is not None and current.status in {"failed", "partial"} else None
            )

        failed = _wait_for(failed_batch, timeout=5.0)
        assert failed is not None
        assert failed.results[0].error == "host_execution_lease_lost"

        settled = supervisor.store.get(batch.host_task_id)
        assert settled is not None
        assert settled.status == TaskRunStatus.FAILED
        assert settled.lease is None
    finally:
        release.set()
        orchestrator.shutdown(wait=True)


def test_recovery_snapshot_uses_durable_host_after_orchestrator_restart(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-host",
    )
    started = threading.Event()
    release = threading.Event()

    def runner(description: str, **_kwargs: Any) -> str:
        started.set()
        release.wait(timeout=5.0)
        return f"done: {description}"

    running = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    restarted = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    try:
        batch = running.dispatch(
            [
                {
                    "task_id": "durable-task",
                    "description": "recover this lane",
                    "subagent_name": "researcher",
                    "depends_on": [],
                    "priority": 3,
                    "write_paths": ["report.md"],
                }
            ],
            thread_id="restart-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert started.wait(timeout=3.0)

        snapshot = restarted.recovery_snapshot(batch.batch_id)
        assert snapshot is not None
        assert snapshot.host_task_id == batch.host_task_id
        assert snapshot.status == "running"
        assert snapshot.terminal is False
        assert snapshot.resume_available is False
        assert snapshot.safety["durable_only"] is True
        assert snapshot.recovery_hints["automatic_batch_reconstruction"] is False
        assert snapshot.tasks[0].task_id == "durable-task"
        assert snapshot.tasks[0].description_preview == "recover this lane"
        assert snapshot.tasks[0].status == "running"
        assert restarted.get_batch_owner(batch.batch_id) == "alice"
        assert restarted.get_batch_tenant(batch.batch_id) == "tenant-a"
        listed = restarted.list_recovery_snapshots(
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert [item.batch_id for item in listed] == [batch.batch_id]
        assert listed[0].safety["durable_only"] is True
        assert (
            restarted.list_recovery_snapshots(
                owner_id="bob",
                tenant_id="tenant-b",
            )
            == []
        )
    finally:
        release.set()
        running.shutdown(wait=True)
        restarted.shutdown(wait=True)


def test_explicit_recovery_reruns_only_named_failed_lanes(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-recovery-source",
    )
    attempts: dict[str, int] = {}

    def runner(description: str, **_kwargs: Any) -> str:
        attempts[description] = attempts.get(description, 0) + 1
        if description == "retry me" and attempts[description] == 1:
            raise RuntimeError("first attempt failed")
        return f"done: {description}"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="thread",
    )
    try:
        original = orchestrator.dispatch(
            [{"task_id": "failed-lane", "description": "retry me"}],
            thread_id="recovery-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        failed = _wait_for(
            lambda: (
                current
                if (current := orchestrator.get_batch(original.batch_id)) is not None
                and current.status == "failed"
                else None
            )
        )
        assert failed is not None
        snapshot = orchestrator.recovery_snapshot(original.batch_id)
        assert snapshot is not None
        assert snapshot.resume_available is True
        assert snapshot.recovery_hints["rerunnable_task_ids"] == ["failed-lane"]

        resumed = orchestrator.resume_recovery(
            original.batch_id,
            ["failed-lane"],
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert resumed.batch_id != original.batch_id
        assert resumed.results[0].task_id != "failed-lane"
        assert resumed.results[0].status in {"pending", "running", "completed"}
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := orchestrator.get_batch(resumed.batch_id)) is not None
                    and current.status == "completed"
                    else None
                )
            )
            is not None
        )
        assert attempts["retry me"] == 2
        new_record = supervisor.store.get(resumed.host_task_id or "")
        assert new_record is not None
        assert new_record.metadata["recovery_source_batch_id"] == original.batch_id
        assert list(new_record.metadata["recovery_source_task_map"].values()) == ["failed-lane"]
        with pytest.raises(ValueError, match="already rerun"):
            orchestrator.resume_recovery(
                original.batch_id,
                ["failed-lane"],
                owner_id="alice",
                tenant_id="tenant-a",
            )
    finally:
        orchestrator.shutdown(wait=True)


def test_explicit_recovery_resumes_durable_failed_batch_after_restart(tmp_path) -> None:
    """A confirmed retry can use durable specs after the scheduler is replaced."""

    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-recovery-restart-source",
    )
    attempts: dict[str, int] = {}

    def runner(description: str, **_kwargs: Any) -> str:
        attempts[description] = attempts.get(description, 0) + 1
        if description == "fail before restart" and attempts[description] == 1:
            raise RuntimeError("source attempt failed")
        return f"done: {description}"

    source_orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="thread",
    )
    restarted_orchestrator: ParallelAgentOrchestrator | None = None
    try:
        source = source_orchestrator.dispatch(
            [
                {
                    "task_id": "restart-failed-lane",
                    "description": "fail before restart",
                    "subagent_name": "researcher",
                    "priority": 2,
                    "write_paths": ["restart-report.md"],
                }
            ],
            thread_id="restart-recovery-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        failed = _wait_for(
            lambda: (
                current
                if (current := source_orchestrator.get_batch(source.batch_id)) is not None
                and current.status == "failed"
                else None
            )
        )
        assert failed is not None
        source_orchestrator.shutdown(wait=True)

        restarted_orchestrator = ParallelAgentOrchestrator(
            task_runner=runner,
            task_supervisor=supervisor,
            worker_isolation="thread",
        )
        snapshot = restarted_orchestrator.recovery_snapshot(source.batch_id)
        assert snapshot is not None
        assert snapshot.safety["durable_only"] is True
        assert snapshot.resume_available is True
        assert snapshot.recovery_hints["rerunnable_task_ids"] == ["restart-failed-lane"]

        resumed = restarted_orchestrator.resume_recovery(
            source.batch_id,
            ["restart-failed-lane"],
            owner_id="alice",
            tenant_id="tenant-a",
        )
        completed = _wait_for(
            lambda: (
                current
                if (current := restarted_orchestrator.get_batch(resumed.batch_id)) is not None
                and current.status == "completed"
                else None
            )
        )
        assert completed is not None
        assert attempts["fail before restart"] == 2
        assert completed.results[0].status == "completed"

        resumed_host = supervisor.store.get(resumed.host_task_id or "")
        assert resumed_host is not None
        assert resumed_host.metadata["recovery_source_batch_id"] == source.batch_id
        assert list(resumed_host.metadata["recovery_source_task_map"].values()) == [
            "restart-failed-lane"
        ]
        with pytest.raises(ValueError, match="already rerun"):
            restarted_orchestrator.resume_recovery(
                source.batch_id,
                ["restart-failed-lane"],
                owner_id="alice",
                tenant_id="tenant-a",
            )
    finally:
        source_orchestrator.shutdown(wait=True)
        if restarted_orchestrator is not None:
            restarted_orchestrator.shutdown(wait=True)


def test_recovery_rejects_running_lane_and_unresolved_dependency(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-recovery-safety",
    )
    started = threading.Event()
    release = threading.Event()

    def runner(_description: str, **_kwargs: Any) -> str:
        started.set()
        release.wait(timeout=5.0)
        return "late"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="thread",
    )
    try:
        running = orchestrator.dispatch(
            [{"task_id": "live-lane", "description": "still running"}],
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert started.wait(timeout=3.0)
        with pytest.raises(ValueError, match="still active"):
            orchestrator.resume_recovery(
                running.batch_id,
                ["live-lane"],
                owner_id="alice",
                tenant_id="tenant-a",
            )
    finally:
        release.set()
        orchestrator.shutdown(wait=True)


def test_nested_dispatch_cannot_replace_parent_principal(tmp_path) -> None:
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="parent-host")
    orchestrator = ParallelAgentOrchestrator(
        task_runner=lambda _description, **_kwargs: "ok",
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    try:
        with host_execution_scope(
            supervisor=supervisor,
            task_id="parent-task",
            thread_id="parent-thread",
            actor_id="alice",
            tenant_id="tenant-a",
            goal="parent execution",
            timeout_s=30,
        ):
            batch = orchestrator.dispatch(
                [{"description": "nested work"}],
                owner_id="attacker",
                tenant_id="tenant-b",
            )
        assert orchestrator.get_batch_owner(batch.batch_id) == "alice"
        assert orchestrator.get_batch_tenant(batch.batch_id) == "tenant-a"
        assert batch.host_task_id is None
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := orchestrator.get_batch(batch.batch_id)) is not None
                    and current.status == "completed"
                    else None
                )
            )
            is not None
        )
    finally:
        orchestrator.shutdown(wait=True)
