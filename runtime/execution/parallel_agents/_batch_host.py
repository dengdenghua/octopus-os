"""Durable host lease for one asynchronous parallel batch.

Worker leases protect individual agent invocations.  This companion object
keeps the aggregate batch itself visible to ``TaskSupervisor`` after the HTTP
request has returned, renews its lease while the scheduler is alive, and
settles the record when the in-memory batch reaches a terminal state.
"""

from __future__ import annotations

import threading
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from runtime.execution.host_boundary import (
    HostExecutionBoundary,
    create_host_execution_boundary,
)
from runtime.platform.process.session import Session
from runtime.platform.process.task_execution import TaskExecutionGuard
from runtime.platform.process.task_supervisor import TaskRunStatus


@dataclass(slots=True)
class BatchHostLease:
    """One host-owned lease that outlives the dispatch HTTP request."""

    task_id: str
    session: Session
    guard: TaskExecutionGuard
    supervisor: Any = field(repr=False, compare=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _lost: threading.Event = field(default_factory=threading.Event, repr=False)
    _heartbeat_thread: threading.Thread | None = field(default=None, repr=False)

    @classmethod
    def start(
        cls,
        *,
        supervisor: Any,
        task_id: str,
        thread_id: str,
        owner_id: str | None,
        tenant_id: str | None,
        goal: str,
        timeout_s: float,
        metadata: dict[str, Any] | None = None,
    ) -> BatchHostLease:
        boundary: HostExecutionBoundary = create_host_execution_boundary(
            task_id=task_id,
            thread_id=thread_id,
            actor_id=owner_id,
            tenant_id=tenant_id,
            goal=goal,
            timeout_s=timeout_s,
            metadata=metadata,
        )
        guard = TaskExecutionGuard(supervisor)
        try:
            admission_metadata = dict(metadata or {})
            # Identity coordinates are host-owned even if a future caller
            # passes similarly named diagnostic metadata.
            admission_metadata.update(
                {
                    "host_execution": "octopus.execution.v1",
                    "tenant_id": tenant_id,
                }
            )
            guard.admit(
                task_id,
                kind="parallel_batch",
                owner_id=owner_id,
                thread_id=thread_id,
                title=goal[:200],
                goal=goal,
                mode="parallel",
                metadata=admission_metadata,
            )
            boundary.session.execution_lease = guard
            runtime = cls(
                task_id=task_id,
                session=boundary.session,
                guard=guard,
                supervisor=supervisor,
            )
        except BaseException:
            # Do not leave a half-admitted durable row if thread construction
            # or admission fails before the scheduler can own the runtime.
            with suppress(Exception):
                guard.transition(TaskRunStatus.FAILED, reason="host lease admission failed")
            guard.close()
            raise
        interval = max(
            0.1,
            min(float(getattr(supervisor, "lease_ttl_seconds", 300.0)) / 3.0, 30.0),
        )

        def _heartbeat_loop() -> None:
            while not runtime._stop.wait(interval):
                try:
                    runtime.guard.heartbeat()
                except Exception:  # noqa: BLE001 - lease loss is terminal for the batch
                    runtime._lost.set()
                    return

        runtime._heartbeat_thread = threading.Thread(
            target=_heartbeat_loop,
            name=f"parallel-batch-lease-heartbeat-{task_id[-12:]}",
            daemon=True,
        )
        runtime._heartbeat_thread.start()
        return runtime

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def settle(self, status: TaskRunStatus, *, reason: str) -> None:
        """Settle the exact lease epoch and stop its heartbeat thread."""

        self._stop.set()
        try:
            self.guard.transition(status, reason=reason)
        except Exception:
            # A lost epoch cannot be transitioned with its old token. If the
            # same supervisor still owns the process, take over the expired
            # row once and settle that new epoch; a different live holder wins
            # and is left untouched for its recovery workflow.
            try:
                self.supervisor.takeover_task(
                    self.task_id,
                    by=f"batch-host:{self.supervisor.holder_id}",
                    reason="settle after host lease loss",
                )
                self.supervisor.transition(self.task_id, status, reason=reason)
            except Exception:
                pass
        self.guard.close()
        heartbeat = self._heartbeat_thread
        if heartbeat is not None and heartbeat is not threading.current_thread():
            heartbeat.join(timeout=1.0)


__all__ = ["BatchHostLease"]
