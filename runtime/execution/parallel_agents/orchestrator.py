"""Multi-agent work orchestrator with dependency + concurrency control.

The ``ParallelAgentOrchestrator`` class manages batches of parallel tasks,
each with optional dependencies. Phases are derived from the dependency
graph via topological sort.

Scheduling, worker-generation replacement, process-isolation launching and
event publishing are split into ``_orchestrator_scheduler`` (a mixin), while
the internal task/batch entry dataclasses and pure wire-format builders live
in ``_orchestrator_models``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from runtime.execution.misc.multiagent_contracts import validate_work_plan

from ._orchestrator_models import (
    _TERMINAL_TASK_STATUSES,
    TaskRunner,
    _BatchEntry,
    _build_recovery_snapshot,
    _iso,
    _now,
    _TaskEntry,
    _timeout_policy,
)
from ._orchestrator_scheduler import _SchedulerMixin
from .helpers import (
    build_plan as _build_plan,
)
from .helpers import (
    contract_for as _contract_for,
)
from .helpers import (
    default_runner as _default_runner,
)
from .helpers import (
    initial_runtime_session_metadata as _initial_runtime_session_metadata,
)
from .models import (
    BatchRecoverySnapshot,
    BatchResult,
    BatchStreamEvent,
    DispatchTaskInput,
    OrchestratorStatus,
    SplitResult,
    SplitTask,
)
from .ownership import OwnershipMixin

_log = logging.getLogger(__name__)


# ─── orchestrator ────────────────────────────────────────────


class ParallelAgentOrchestrator(OwnershipMixin, _SchedulerMixin):
    """Multi-agent work orchestrator with dependency + concurrency control.

    Manages batches of parallel tasks, each with optional dependencies.
    Phases are derived from the dependency graph via topological sort.
    """

    def __init__(
        self,
        *,
        max_concurrency: int = 4,
        task_runner: TaskRunner | None = None,
        splitter: Callable[..., SplitResult] | None = None,
        event_log_limit: int = 2048,
        completed_batch_limit: int = 512,
        worker_isolation: str = "auto",
    ) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if event_log_limit < 32:
            raise ValueError("event_log_limit must be >= 32")
        if completed_batch_limit < 1:
            raise ValueError("completed_batch_limit must be >= 1")
        if worker_isolation not in {"auto", "thread", "process"}:
            raise ValueError("worker_isolation must be one of: auto, thread, process")
        self._max_concurrency = max_concurrency
        self._event_log_limit = event_log_limit
        self._completed_batch_limit = completed_batch_limit
        self._default_worker_isolation = worker_isolation
        self._pool = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="parallel-agent",
        )
        self._pool_generation = 0
        self._retired_pools: dict[int, ThreadPoolExecutor] = {}
        self._lock = threading.RLock()
        self._batches: dict[str, _BatchEntry] = {}
        self._task_index: dict[str, str] = {}  # task_id → batch_id
        self._runner: TaskRunner = task_runner or _default_runner
        self._splitter = splitter
        self._closed = False

    # ═══ public API ═══════════════════════════════════════════

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def dispatch(
        self,
        tasks: list[DispatchTaskInput] | list[dict[str, Any]],
        *,
        max_concurrency: int | None = None,  # Implementation note.
        aggregation_strategy: str | None = None,
        execution_mode: str | None = None,
        thread_id: str | None = None,
        model_name: str | None = None,
        context: dict[str, Any] | None = None,
        owner_id: str | None = None,
    ) -> BatchResult:
        """Create + start a batch.

        ``owner_id`` is stamped on the batch and used by ``get_batch``,
        ``cancel_task``, ``cancel_all`` and ``subscribe`` to enforce
        per-user scoping at the endpoint layer. ``None`` means
        unscoped (legacy / dev mode) and is visible to everyone.
        """
        self._guard_open()

        raw: list[DispatchTaskInput] = [
            t if isinstance(t, DispatchTaskInput) else DispatchTaskInput(**t) for t in tasks
        ]
        if not raw:
            raise ValueError("dispatch: tasks must be non-empty")

        batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        now = _now()
        entries: dict[str, _TaskEntry] = {}
        for t in raw:
            tid = t.task_id or f"task_{uuid.uuid4().hex[:10]}"
            if tid in entries:
                raise ValueError(f"dispatch: duplicate task_id {tid!r}")
            entries[tid] = _TaskEntry(
                task_id=tid,
                batch_id=batch_id,
                description=t.description,
                subagent_name=t.subagent_name,
                depends_on=list(t.depends_on),
                priority=t.priority,
                write_paths=list(t.write_paths),
            )

        batch = _BatchEntry(
            batch_id=batch_id,
            tasks=entries,
            created_at=now,
            aggregation_strategy=aggregation_strategy,
            runtime_session_metadata=_initial_runtime_session_metadata(context),
            owner_id=owner_id,
        )
        batch.plan = _build_plan(
            batch_id=batch_id,
            entries=entries,
            max_concurrency=max_concurrency or self._max_concurrency,
        )
        validation = validate_work_plan(batch.plan)
        batch.plan.validation_issues = list(validation.errors)
        batch.plan.validation_warnings = list(validation.warnings)
        batch.conflicts.extend(validation.errors)
        for entry in entries.values():
            entry.work_contract = _contract_for(batch.plan, entry.task_id)

        run_context = {
            "thread_id": thread_id,
            "model_name": model_name,
            "execution_mode": execution_mode,
        }
        if context:
            run_context.update(context)
        batch.timeout_policy = _timeout_policy(run_context)

        # Carry the spawning parent's prompt-injection taint into the batch's
        # subagents. dispatch() runs in the parent's context; the per-task
        # threads spawned by the scheduler start with a fresh contextvar, so
        # capture HERE (before the pool boundary) and let the runner thread it
        # into each subagent intent's user_context (honored at react-loop start).
        try:
            from runtime.safety.validation.prompt_injection import (
                current_injection_taint,
            )

            _taint = current_injection_taint()
            if _taint and _taint != "none":
                run_context.setdefault("_inherited_injection_taint", _taint)
        except Exception:  # noqa: BLE001 - taint propagation is best-effort
            pass

        should_start_scheduler = True
        with self._lock:
            self._batches[batch_id] = batch
            self._prune_completed_batches_locked()
            # Audit T-12: journal the batch as running so a crash mid-run
            # leaves a durable trace the startup sweep can close.
            from .helpers import journal_batch_lifecycle

            journal_batch_lifecycle(batch_id, status="running", detail="parallel batch started")
            for tid in entries:
                self._task_index[tid] = batch_id
            self._publish_stage_change_locked(
                batch,
                stage="task_analysis",
                status="running",
                progress=0.10,
                message="Task graph received",
            )
            self._publish_stage_change_locked(
                batch,
                stage="matching_agents",
                status="running",
                progress=0.22,
                message="Matching available agents to work lanes",
            )
            for entry in entries.values():
                self._publish_task_update_locked(
                    batch,
                    entry,
                    phase="planned",
                    message=(f"{entry.subagent_name} queued for a focused research lane"),
                )
            self._publish_stage_change_locked(
                batch,
                stage="assigning_tasks",
                status="running",
                progress=0.35,
                message="Tasks assigned; agents are starting",
            )
            if batch.plan.validation_issues or batch.plan.validation_warnings:
                self._publish_stage_change_locked(
                    batch,
                    stage="contract_validation",
                    status="failed" if batch.plan.validation_issues else "warning",
                    progress=0.36,
                    message="Work contracts validated",
                )
            if self._fail_unrunnable_plan_locked(batch):
                should_start_scheduler = False

        if should_start_scheduler:
            threading.Thread(
                target=self._schedule_batch,
                args=(batch.batch_id, run_context),
                name=f"parallel-agent-scheduler-{batch.batch_id}",
                daemon=True,
            ).start()

        return batch.to_wire()

    def get_batch(self, batch_id: str) -> BatchResult | None:
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            return batch.to_wire()

    def recovery_snapshot(self, batch_id: str) -> BatchRecoverySnapshot | None:
        """Return a redacted recovery/audit view for a parallel batch."""
        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return None
            return _build_recovery_snapshot(batch)

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            bid = self._task_index.get(task_id)
            if bid is None:
                return False
            batch = self._batches[bid]
            entry = batch.tasks.get(task_id)
            if entry is None:
                return False
            if entry.status in _TERMINAL_TASK_STATUSES:
                return False
            entry.cancel_event.set()
            entry.cancel_requested_at = entry.cancel_requested_at or _now()
            if entry.status == "pending":
                entry.status = "cancelled"
                entry.completed_at = _now()
                self._publish_task_update_locked(
                    batch,
                    entry,
                    phase="cancelled",
                    message=f"{entry.subagent_name} cancelled before start",
                )
                self._maybe_close_batch_locked(batch)
            return True

    def cancel_all(self) -> bool:
        with self._lock:
            for batch in list(self._batches.values()):
                for entry in batch.tasks.values():
                    if entry.status in _TERMINAL_TASK_STATUSES:
                        continue
                    entry.cancel_event.set()
                    entry.cancel_requested_at = entry.cancel_requested_at or _now()
                    if entry.status == "pending":
                        entry.status = "cancelled"
                        entry.completed_at = _now()
                        self._publish_task_update_locked(
                            batch,
                            entry,
                            phase="cancelled",
                            message=(f"{entry.subagent_name} cancelled before start"),
                        )
                self._maybe_close_batch_locked(batch)
        return True

    # Ownership helpers (get_batch_owner, get_task_owner,
    # list_batch_ids_for_owner, cancel_all_for_owner) live in
    # OwnershipMixin — see runtime/execution/parallel_agents/ownership.py.

    def status(self) -> OrchestratorStatus:
        with self._lock:
            active = 0
            pending = 0
            completed = 0
            failed = 0
            cancelled = 0
            batches_map: dict[str, str] = {}
            for bid, batch in self._batches.items():
                batches_map[bid] = batch.derived_status()
                for t in batch.tasks.values():
                    s = t.status
                    if s == "running":
                        active += 1
                    elif s == "pending":
                        pending += 1
                    elif s == "completed":
                        completed += 1
                    elif s == "failed" or s == "timed_out":
                        failed += 1
                    elif s == "cancelled":
                        cancelled += 1
            return OrchestratorStatus(
                active_count=active,
                pending_count=pending,
                completed_count=completed,
                failed_count=failed,
                cancelled_count=cancelled,
                max_concurrency=self._max_concurrency,
                batches=batches_map,
                worker_generation=self._pool_generation,
                worker_replacement_count=sum(
                    1
                    for batch in self._batches.values()
                    for row in batch.worker_replacements
                    if row.get("event")
                    in {"worker_generation_replaced", "process_worker_terminated"}
                ),
                retired_worker_generation_count=len(self._retired_pools),
            )

    def split(
        self,
        task: str,
        *,
        max_subtasks: int | None = None,
        context: str | None = None,
        model_name: str | None = None,
    ) -> SplitResult:
        if self._splitter is not None:
            try:
                return self._splitter(
                    task,
                    max_subtasks=max_subtasks,
                    context=context,
                    model_name=model_name,
                )
            except Exception as e:  # noqa: BLE001
                _log.warning("splitter failed · fallback stub · err=%s", e)

        tid = f"task_{uuid.uuid4().hex[:10]}"
        return SplitResult(
            tasks=[
                SplitTask(
                    task_id=tid,
                    description=task,
                    subagent_name="general-purpose",
                    depends_on=[],
                    priority=0,
                )
            ],
            dag_levels=[[tid]],
            total_levels=1,
            is_parallelizable=False,
        )

    async def subscribe(
        self,
        batch_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[BatchStreamEvent]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()

        with self._lock:
            batch = self._batches.get(batch_id)
            if batch is None:
                return
            for past in batch.event_log:
                if (past.sequence or 0) > after_sequence:
                    queue.put_nowait(past)
            if batch.completed_at is not None and queue.empty():
                return
            batch.subscribers.append((queue, loop))

        try:
            while True:
                ev: BatchStreamEvent = await queue.get()
                yield ev
                if ev.type == "batch_complete":
                    return
        finally:
            with self._lock:
                batch = self._batches.get(batch_id)
                if batch is not None:
                    batch.subscribers = [
                        (q, lease) for (q, lease) in batch.subscribers if q is not queue
                    ]

    def shutdown(self, wait: bool = False) -> None:
        self._closed = True
        self._pool.shutdown(wait=wait, cancel_futures=True)
        for pool in list(self._retired_pools.values()):
            pool.shutdown(wait=wait, cancel_futures=True)
        self._retired_pools.clear()

    def _guard_open(self) -> None:
        if self._closed:
            raise RuntimeError("orchestrator is closed")

    def _prune_completed_batches_locked(self) -> None:
        """Keep terminal batch state bounded without evicting live work.

        Completed batches are intentionally retained for a while because the
        UI may fetch the final report after the worker has finished. Once the
        configured retention is exceeded, the oldest terminal batches are
        removed together with their task-index entries. Batches with an active
        stream subscriber are pinned until that subscriber disconnects.
        """
        completed = sorted(
            (
                batch
                for batch in self._batches.values()
                if batch.completed_at is not None and not batch.subscribers
            ),
            key=lambda batch: batch.created_at,
        )
        overflow = len(completed) - self._completed_batch_limit
        if overflow <= 0:
            return
        for batch in completed[:overflow]:
            self._batches.pop(batch.batch_id, None)
            for task_id in batch.tasks:
                if self._task_index.get(task_id) == batch.batch_id:
                    self._task_index.pop(task_id, None)

    def _publish_task_update_locked(
        self,
        batch: _BatchEntry,
        entry: _TaskEntry,
        *,
        phase: str | None = None,
        message: str | None = None,
        result_preview: str | None = None,
    ) -> None:
        ev = BatchStreamEvent(
            type="task_update",
            batch_id=batch.batch_id,
            task_id=entry.task_id,
            lane="agent",
            status=entry.status,
            subagent_name=entry.subagent_name,
            phase=phase,
            node_ids=[entry.task_id],
            payload={
                "contract_id": (
                    entry.work_contract.contract_id
                    if entry.work_contract is not None
                    else entry.task_id
                ),
                "depends_on": list(entry.depends_on),
                "owned_scope": (
                    list(entry.work_contract.owned_scope)
                    if entry.work_contract is not None
                    else [f"task:{entry.task_id}"]
                ),
                "write_paths": list(entry.write_paths),
                "worker_generation": entry.worker_generation,
                "worker_state": entry.worker_state,
                "replacement_generation": entry.replacement_generation,
                "worker_isolation": entry.worker_isolation,
                "worker_isolation_reason": entry.worker_isolation_reason,
                **(
                    {"subagent_route_decision": entry.route_decision}
                    if entry.route_decision is not None
                    else {}
                ),
            },
            message=message,
            description=entry.description,
            result_preview=result_preview,
            duration_seconds=entry.duration(),
            error=entry.error,
        )
        self._broadcast_locked(batch, ev)

    def _publish_stage_change_locked(
        self,
        batch: _BatchEntry,
        *,
        stage: str,
        status: str,
        progress: float | None = None,
        message: str | None = None,
    ) -> None:
        ev = BatchStreamEvent(
            type="stage_change",
            batch_id=batch.batch_id,
            lane="workflow",
            status=status,
            stage=stage,
            payload={
                "stage": stage,
                "total_tasks": len(batch.tasks),
                "completed_tasks": batch.counts()[1],
                "failed_tasks": batch.counts()[2],
                "cancelled_tasks": batch.counts()[3],
            },
            progress=progress,
            message=message,
        )
        self._broadcast_locked(batch, ev)

    def _broadcast_locked(
        self,
        batch: _BatchEntry,
        ev: BatchStreamEvent,
    ) -> None:
        batch.event_sequence += 1
        ev.sequence = batch.event_sequence
        ev.created_at = _iso(_now())
        batch.event_log.append(ev)
        if ev.artifact_paths:
            bucket = batch.artifact_paths_by_task.setdefault(ev.task_id or "__batch__", [])
            for path in ev.artifact_paths:
                if path not in bucket:
                    bucket.append(path)
        overflow = len(batch.event_log) - self._event_log_limit
        if overflow > 0:
            del batch.event_log[:overflow]
            batch.event_log_dropped_count += overflow
        dead: list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = []
        for queue, loop in batch.subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, ev)
            except RuntimeError:
                dead.append((queue, loop))
        if dead:
            batch.subscribers = [x for x in batch.subscribers if x not in dead]
