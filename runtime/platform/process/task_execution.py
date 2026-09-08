"""One server-owned execution attempt's immutable TaskSupervisor lease.

This guard is carried by Session, not parsed from tool arguments or metadata.
It prevents a stale attempt from borrowing a newer same-holder token. Checks
are dispatch fences, not a way to kill a handler already performing IO; each
domain still owns its operation claims and side-effect receipts.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from runtime.platform.process.task_supervisor import (
    LostTaskLease,
    TaskRunRecord,
    TaskRunStatus,
    TaskSupervisor,
)

_EXECUTING = frozenset({TaskRunStatus.RUNNING, TaskRunStatus.VERIFYING, TaskRunStatus.REPAIRING})


@dataclass(frozen=True, slots=True)
class _LeaseBinding:
    task_id: str
    holder_id: str
    token: int
    incarnation: str


class TaskExecutionGuard:
    """Bind once; retain the original token across threads and child Sessions.

    A pending guard denies tools until the producer admits its real task ID.
    Same-task duplicate start events validate but never renew or replace the
    binding. close stops later dispatch/heartbeats, while transition may still
    settle that exact lease during teardown. No method takes over a lost lease.
    """

    def __init__(self, supervisor: TaskSupervisor) -> None:
        self._supervisor = supervisor
        self._binding: _LeaseBinding | None = None
        self._closed = False
        self._lock = threading.RLock()

    @property
    def bound_task_id(self) -> str | None:
        with self._lock:
            return self._binding.task_id if self._binding is not None else None

    def _bound(self, *, require_open: bool) -> _LeaseBinding:
        task_id = self._binding.task_id if self._binding is not None else ""
        if require_open and self._closed:
            raise LostTaskLease(task_id, "execution guard closed")
        if self._binding is None:
            raise LostTaskLease(task_id, "execution guard pending")
        if self._supervisor.holder_id != self._binding.holder_id:
            raise LostTaskLease(task_id, "execution holder changed")
        return self._binding

    @staticmethod
    def _observe(binding: _LeaseBinding, operation: Callable[[], TaskRunRecord]) -> TaskRunRecord:
        try:
            return operation()
        except (KeyError, OSError, ValueError) as exc:
            raise LostTaskLease(binding.task_id, "execution authority unavailable") from exc

    def admit(self, task_id: str, **start_kwargs: Any) -> TaskRunRecord:
        """Bind a new epoch, optionally consuming a server-issued approval permit.

        ``approved_resume_lease_token`` is validated by start_task in its
        mutation, never inferred from Session metadata or a cached task record.
        An already bound scope remains a read-only duplicate admission.
        """
        with self._lock:
            if self._closed:
                raise LostTaskLease(self.bound_task_id or "", "execution guard closed")
            if not isinstance(task_id, str) or not task_id.strip() or task_id != task_id.strip():
                raise ValueError("execution task ID must be a nonempty canonical string")
            if self._binding is not None:
                binding = self._bound(require_open=True)
                if task_id != binding.task_id:
                    raise LostTaskLease(binding.task_id, "execution already bound to another task")
                return self._observe(
                    binding,
                    lambda: self._supervisor.assert_current_holder(
                        binding.task_id,
                        expected_lease_token=binding.token,
                        expected_lease_incarnation=binding.incarnation,
                    ),
                )
            try:
                # This admission policy cannot be weakened by a caller's
                # convenience kwargs; legacy direct start_task remains opt-in.
                start_kwargs.pop("reject_active_lease", None)
                record = self._supervisor.start_task(
                    task_id=task_id, reject_active_lease=True, **start_kwargs
                )
                lease = record.lease
                if (
                    lease is None
                    or lease.holder_id != self._supervisor.holder_id
                    or not lease.incarnation
                ):
                    raise LostTaskLease(task_id, "admission did not return an owned lease")
                self._binding = _LeaseBinding(
                    task_id, lease.holder_id, lease.token, lease.incarnation
                )
                return record
            except BaseException:
                # A failed admission cannot be silently retried into a new
                # execution epoch by another duplicate event.
                self._closed = True
                raise

    def assert_held(self, *, require_open: bool = True) -> TaskRunRecord:
        """Read frozen authority without granting tool execution to waiting tasks."""
        with self._lock:
            binding = self._bound(require_open=require_open)
            return self._observe(
                binding,
                lambda: self._supervisor.assert_current_holder(
                    binding.task_id,
                    expected_lease_token=binding.token,
                    expected_lease_incarnation=binding.incarnation,
                ),
            )

    def assert_allowed(self) -> TaskRunRecord:
        with self._lock:
            record = self.assert_held()
            if record.status not in _EXECUTING:
                raise LostTaskLease(record.task_id, f"task is {record.status.value}")
            return record

    def heartbeat(self) -> TaskRunRecord:
        with self._lock:
            binding = self._bound(require_open=True)
            return self._observe(
                binding,
                lambda: self._supervisor.heartbeat(
                    binding.task_id,
                    expected_lease_token=binding.token,
                    expected_lease_incarnation=binding.incarnation,
                ),
            )

    def transition(
        self, status: TaskRunStatus | str, *, require_open: bool = False, **kwargs: Any
    ) -> TaskRunRecord:
        with self._lock:
            binding = self._bound(require_open=require_open)
            return self._observe(
                binding,
                lambda: self._supervisor.transition(
                    binding.task_id,
                    status,
                    expected_lease_token=binding.token,
                    expected_lease_incarnation=binding.incarnation,
                    **kwargs,
                ),
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def fork(self) -> TaskExecutionGuard:
        """Open a new driver scope for this epoch without reviving old workers.

        Automatic verify/repair can follow a closed stream scope. The copy
        keeps its original lease token; a takeover or terminal task cannot be
        adopted, and no register/heartbeat changes the durable record here.
        """
        with self._lock:
            binding = self._bound(require_open=False)
            self._observe(
                binding,
                lambda: self._supervisor.assert_current_holder(
                    binding.task_id,
                    expected_lease_token=binding.token,
                    expected_lease_incarnation=binding.incarnation,
                ),
            )
            child = TaskExecutionGuard(self._supervisor)
            child._binding = binding
            return child


__all__ = ["TaskExecutionGuard"]
