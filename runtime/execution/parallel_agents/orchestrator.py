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
import re
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from runtime.execution.misc.multiagent_contracts import validate_work_plan

from ._batch_host import BatchHostLease
from ._orchestrator_models import (
    _TERMINAL_TASK_STATUSES,
    TaskRunner,
    _BatchEntry,
    _build_recovery_snapshot,
    _build_recovery_snapshot_from_host_record,
    _durable_task_specs,
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
        task_supervisor: Any = None,
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
        self._task_supervisor = task_supervisor
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
        tenant_id: str | None = None,
        batch_id: str | None = None,
    ) -> BatchResult:
        """Create + start a batch.

        ``owner_id`` and ``tenant_id`` are stamped on the batch and used by
        the gateway to enforce per-user and per-tenant scoping. ``None`` is
        retained for legacy/dev callers; authenticated gateways fail closed
        for such records while development mode remains unscoped.
        """
        self._guard_open()

        raw: list[DispatchTaskInput] = [
            t if isinstance(t, DispatchTaskInput) else DispatchTaskInput(**t) for t in tasks
        ]
        if not raw:
            raise ValueError("dispatch: tasks must be non-empty")

        # Capture the trusted host Session before the scheduler thread starts;
        # ContextVars do not cross that boundary. A child worker can then
        # re-enter this exact request instead of accepting a model-supplied
        # identity or silently becoming an unscoped background run.
        try:
            from runtime.platform.process.session import current_session

            parent_session = current_session()
        except (ImportError, AttributeError):
            parent_session = None
        has_host_parent = bool(
            parent_session is not None
            and (
                getattr(parent_session, "execution_request", None)
                or getattr(parent_session, "execution_lease", None)
            )
        )
        if has_host_parent:
            # A nested caller cannot replace the host principal by passing a
            # different owner or tenant through an orchestration helper.
            owner_id = str(getattr(parent_session, "actor", "") or "").strip() or None
            request = getattr(parent_session, "execution_request", None)
            task = getattr(request, "task", None)
            tenant_id = (
                str(
                    getattr(task, "tenant_id", None)
                    or (getattr(parent_session, "metadata", {}) or {}).get("tenant_id")
                    or ""
                ).strip()
                or None
            )

        if batch_id is None:
            batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        elif re.fullmatch(r"batch_[0-9a-f]{12,64}", batch_id) is None:
            raise ValueError("dispatch: batch_id is invalid")
        with self._lock:
            if batch_id in self._batches:
                raise ValueError(f"dispatch: batch_id already exists: {batch_id}")
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
            owner_id=str(owner_id or "").strip() or None,
            tenant_id=str(tenant_id or "").strip() or None,
        )
        if context and str(context.get("recovery_source_batch_id") or "").strip():
            raw_map = context.get("recovery_source_task_map")
            if isinstance(raw_map, dict):
                batch.recovery_source_batch_id = str(context["recovery_source_batch_id"]).strip()
                batch.recovery_source_task_map = {
                    str(new_id): str(old_id)
                    for new_id, old_id in raw_map.items()
                    if str(new_id).strip() and str(old_id).strip()
                }
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
        if tenant_id is not None:
            run_context["tenant_id"] = tenant_id
        if has_host_parent:
            # Parent identity wins over any context field that arrived through
            # an orchestration helper. The Session object is kept only in the
            # in-process scheduler context; it is never part of the wire
            # BatchResult or the persisted runtime metadata.
            run_context["caller_session"] = parent_session
        else:
            # ``caller_session`` is an in-process authority object, never a
            # caller-controlled orchestration field. Drop it when dispatch is
            # not itself executing inside a host boundary so a stale or
            # forged Session cannot grant a worker another task's lease.
            run_context.pop("caller_session", None)
        batch.timeout_policy = _timeout_policy(run_context)

        # A standalone HTTP dispatch outlives the request that authenticated
        # it. Keep a durable aggregate host record in addition to the
        # per-worker records so restart/lease sweeps can identify the batch as
        # interrupted instead of leaving only an in-memory batch ID.
        task_supervisor = getattr(self, "_task_supervisor", None)
        if task_supervisor is not None and not has_host_parent:
            host_task_id = f"parallel-batch:{batch_id}"
            host_thread_id = str(run_context.get("thread_id") or f"parallel:{batch_id}").strip()
            host_owner_id = batch.owner_id
            host_tenant_id = batch.tenant_id
            if bool(host_owner_id) != bool(host_tenant_id):
                host_owner_id = None
                host_tenant_id = None
            batch.host_task_id = host_task_id
            batch.host_execution = BatchHostLease.start(
                supervisor=task_supervisor,
                task_id=host_task_id,
                thread_id=host_thread_id,
                owner_id=host_owner_id,
                tenant_id=host_tenant_id,
                goal=f"parallel batch {batch_id}",
                timeout_s=min(
                    max(
                        1.0,
                        batch.timeout_policy["queue_timeout_s"]
                        + batch.timeout_policy["task_timeout_s"] * max(1, len(entries)),
                    ),
                    86400.0,
                ),
                metadata={
                    "mode": "code",
                    "permission_mode": "default",
                    "approval_policy": "on-request",
                    "sandbox_mode": "full",
                    "execution_environment": "sandbox",
                    "source": "parallel_agents",
                    "batch_id": batch_id,
                    "parallel_task_count": len(entries),
                    "parallel_task_ids": list(entries),
                    "parallel_task_specs": [
                        {
                            "task_id": entry.task_id,
                            "description": entry.description[:4000],
                            "subagent_name": entry.subagent_name,
                            "depends_on": list(entry.depends_on),
                            "priority": entry.priority,
                            "write_paths": list(entry.write_paths),
                        }
                        for entry in entries.values()
                    ],
                    "parallel_plan": batch.plan.model_dump(),
                    **(
                        {"research_job_id": str(run_context["research_job_id"]).strip()}
                        if str(run_context.get("research_job_id") or "").strip()
                        else {}
                    ),
                    **(
                        {
                            "recovery_source_batch_id": str(
                                run_context["recovery_source_batch_id"]
                            ).strip(),
                            "recovery_source_task_map": dict(
                                run_context.get("recovery_source_task_map") or {}
                            ),
                        }
                        if str(run_context.get("recovery_source_batch_id") or "").strip()
                        and isinstance(run_context.get("recovery_source_task_map"), dict)
                        else {}
                    ),
                },
            )

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
            if batch is not None:
                return _build_recovery_snapshot(batch)

        # The scheduler state is intentionally process-local, but the host
        # record survives in TaskSupervisor. Reconstruct a redacted recovery
        # view from those durable rows after restart; do not fabricate results
        # or silently restart work from this read path.
        supervisor = getattr(self, "_task_supervisor", None)
        store = getattr(supervisor, "store", None)
        if store is None or not hasattr(store, "get"):
            return None
        host_task_id = f"parallel-batch:{batch_id}"
        host_record = store.get(host_task_id)
        if host_record is None or getattr(host_record, "kind", None) != "parallel_batch":
            return None
        metadata = getattr(host_record, "metadata", {})
        if isinstance(metadata, dict) and metadata.get("batch_id") not in {None, batch_id}:
            return None
        workers: list[Any] = []
        list_records = getattr(store, "list", None)
        if callable(list_records):
            try:
                workers = [
                    row
                    for row in list_records(kind="parallel_agent", limit=10000)
                    if getattr(row, "parent_task_id", None) == host_task_id
                    or (
                        isinstance(getattr(row, "metadata", None), dict)
                        and row.metadata.get("batch_id") == batch_id
                    )
                ]
            except (OSError, ValueError, TypeError):
                workers = []
        return _build_recovery_snapshot_from_host_record(host_record, workers)

    def resume_recovery(
        self,
        batch_id: str,
        task_ids: list[str],
        *,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        thread_id: str | None = None,
        model_name: str | None = None,
    ) -> BatchResult:
        """Explicitly rerun selected safe lanes from a lost/terminal batch.

        A recovery snapshot is evidence, not an execution command.  This
        method requires the caller to name the lanes again, rejects a batch
        that still owns a live lease, and never replays a task whose previous
        state is ``running`` (its side-effect boundary is unknown after a
        restart).  A new batch and new task ids are used so the old receipt
        remains immutable and visible for audit.
        """

        if not isinstance(batch_id, str) or not batch_id.strip():
            raise ValueError("recovery batch_id is required")
        if not isinstance(task_ids, list) or not 1 <= len(task_ids) <= 100:
            raise ValueError("recovery task_ids must contain 1 to 100 tasks")
        requested = [str(task_id).strip() for task_id in task_ids]
        if any(not task_id for task_id in requested) or len(set(requested)) != len(requested):
            raise ValueError("recovery task_ids must be unique non-empty strings")

        source_batch: _BatchEntry | None = None
        source_record: Any = None
        source_specs: dict[str, dict[str, Any]] = {}
        source_thread: str | None = None
        source_owner: str | None = None
        source_tenant: str | None = None
        with self._lock:
            source_batch = self._batches.get(batch_id)
            if source_batch is not None:
                source_owner = source_batch.owner_id
                source_tenant = source_batch.tenant_id
                if source_batch.host_execution is not None or any(
                    entry.status in {"running", "pending"}
                    and entry.future is not None
                    and not entry.future.done()
                    for entry in source_batch.tasks.values()
                ):
                    raise ValueError("recovery batch is still active")
                snapshot = _build_recovery_snapshot(source_batch)
                source_specs = {
                    entry.task_id: {
                        "description": entry.description,
                        "subagent_name": entry.subagent_name,
                        "depends_on": list(entry.depends_on),
                        "priority": entry.priority,
                        "write_paths": list(entry.write_paths),
                    }
                    for entry in source_batch.tasks.values()
                }
                raw_thread = source_batch.runtime_session_metadata.get("thread_id")
                source_thread = raw_thread if isinstance(raw_thread, str) else None

        if source_batch is None:
            supervisor = getattr(self, "_task_supervisor", None)
            store = getattr(supervisor, "store", None)
            getter = getattr(store, "get", None)
            if not callable(getter):
                raise ValueError("recovery storage is unavailable")
            source_record = getter(f"parallel-batch:{batch_id}")
            if source_record is None or getattr(source_record, "kind", None) != "parallel_batch":
                raise ValueError("recovery batch not found")
            source_owner = str(getattr(source_record, "owner_id", None) or "").strip() or None
            metadata = getattr(source_record, "metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            source_tenant = str(metadata.get("tenant_id") or "").strip() or None
            lease = getattr(source_record, "lease", None)
            if lease is not None and not lease.expired:
                raise ValueError("recovery batch is still active")
            source_thread = str(getattr(source_record, "thread_id", None) or "").strip() or None
            for spec in _durable_task_specs(source_record):
                source_specs[str(spec["task_id"])] = dict(spec)
            workers: list[Any] = []
            list_records = getattr(store, "list", None)
            if callable(list_records):
                workers = [
                    row
                    for row in list_records(kind="parallel_agent", limit=10000)
                    if getattr(row, "parent_task_id", None) == f"parallel-batch:{batch_id}"
                    or (
                        isinstance(getattr(row, "metadata", None), dict)
                        and row.metadata.get("batch_id") == batch_id
                    )
                ]
            snapshot = _build_recovery_snapshot_from_host_record(source_record, workers)

        if owner_id is not None and source_owner != owner_id:
            raise PermissionError("recovery batch is not owned by the caller")
        if tenant_id is not None and source_tenant != tenant_id:
            raise PermissionError("recovery batch is outside the caller tenant")

        task_by_id = {task.task_id: task for task in snapshot.tasks}
        missing = [task_id for task_id in requested if task_id not in task_by_id]
        if missing:
            raise ValueError(f"recovery task not found: {', '.join(missing)}")
        allowed = {"failed", "cancelled", "timed_out", "pending"}
        for task_id in requested:
            status = task_by_id[task_id].status
            if status == "running":
                raise ValueError(
                    f"recovery task {task_id} has unknown side effects and cannot be replayed"
                )
            if status not in allowed:
                raise ValueError(f"recovery task {task_id} is not rerunnable ({status})")

        selected = set(requested)
        completed = {task.task_id for task in snapshot.tasks if task.status == "completed"}
        for task_id in requested:
            unresolved = [
                dependency
                for dependency in task_by_id[task_id].depends_on
                if dependency not in selected and dependency not in completed
            ]
            if unresolved:
                raise ValueError(
                    f"recovery task {task_id} has unresolved dependencies: " + ", ".join(unresolved)
                )

        requested_set = set(requested)
        with self._lock:
            for candidate in self._batches.values():
                if candidate.recovery_source_batch_id != batch_id:
                    continue
                if set(candidate.recovery_source_task_map.values()) == requested_set:
                    raise ValueError("recovery selection was already rerun")

        # A restarted process has no in-memory child entries. The durable
        # aggregate host rows carry the same provenance, so reject a repeated
        # explicit click there as well. This is a sequential idempotency guard;
        # the source snapshot remains immutable and still available for audit.
        supervisor = getattr(self, "_task_supervisor", None)
        store = getattr(supervisor, "store", None)
        list_records = getattr(store, "list", None)
        if callable(list_records):
            try:
                children = list_records(
                    kind="parallel_batch",
                    owner_id=owner_id,
                    limit=10000,
                    include_unowned=owner_id is None,
                )
            except (OSError, ValueError, TypeError):
                children = []
            for child in children:
                metadata = getattr(child, "metadata", {})
                if not isinstance(metadata, dict):
                    continue
                if str(metadata.get("recovery_source_batch_id") or "").strip() != batch_id:
                    continue
                raw_map = metadata.get("recovery_source_task_map")
                if (
                    isinstance(raw_map, dict)
                    and {str(old_id) for old_id in raw_map.values()} == requested_set
                ):
                    raise ValueError("recovery selection was already rerun")

        new_batch_id = f"batch_{uuid.uuid4().hex[:12]}"
        new_task_ids = {task_id: f"recovery_{uuid.uuid4().hex[:16]}" for task_id in requested}
        tasks: list[dict[str, Any]] = []
        source_task_map: dict[str, str] = {}
        for task_id in requested:
            task = task_by_id[task_id]
            spec = source_specs.get(task_id, {})
            description = str(spec.get("description") or task.description_preview or "").strip()
            if not description:
                raise ValueError(f"recovery task {task_id} has no durable description")
            source_task_map[new_task_ids[task_id]] = task_id
            tasks.append(
                {
                    "task_id": new_task_ids[task_id],
                    "description": description,
                    "subagent_name": str(spec.get("subagent_name") or task.subagent_name),
                    "depends_on": [
                        new_task_ids[dependency]
                        for dependency in task.depends_on
                        if dependency in new_task_ids
                    ],
                    "priority": int(spec.get("priority") or task.priority or 0),
                    "write_paths": list(spec.get("write_paths") or task.write_paths),
                }
            )

        resume_context = {
            "recovery_source_batch_id": batch_id,
            "recovery_source_task_map": source_task_map,
            "recovery_explicit": True,
        }
        return self.dispatch(
            tasks,
            thread_id=thread_id or source_thread,
            model_name=model_name,
            context=resume_context,
            owner_id=source_owner,
            tenant_id=source_tenant,
            batch_id=new_batch_id,
        )

    def list_recovery_snapshots(
        self,
        *,
        owner_id: str | None = None,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[BatchRecoverySnapshot]:
        """List redacted batch views visible to one owner after a restart.

        The scheduler is intentionally process-local.  This read-only method
        therefore joins live entries with durable ``parallel_batch`` host
        rows, using the host row as the source of truth whenever the in-memory
        scheduler no longer has the batch.  It never creates workers or
        rebuilds a scheduler, and durable rows are filtered before any
        snapshot is returned to the caller.
        """

        clean_limit = max(1, min(int(limit or 100), 500))
        snapshots: dict[str, BatchRecoverySnapshot] = {}
        with self._lock:
            for batch_id, batch in self._batches.items():
                if owner_id is not None and batch.owner_id != owner_id:
                    continue
                if tenant_id is not None and batch.tenant_id != tenant_id:
                    continue
                snapshots[batch_id] = _build_recovery_snapshot(batch)

        supervisor = getattr(self, "_task_supervisor", None)
        store = getattr(supervisor, "store", None)
        list_records = getattr(store, "list", None)
        if not callable(list_records):
            return sorted(
                snapshots.values(),
                key=lambda item: (str(item.created_at or ""), item.batch_id),
                reverse=True,
            )[:clean_limit]

        hosts = list_records(
            kind="parallel_batch",
            owner_id=owner_id,
            limit=10000,
            include_unowned=owner_id is None,
        )
        workers = list_records(
            kind="parallel_agent",
            owner_id=owner_id,
            limit=10000,
            include_unowned=owner_id is None,
        )
        workers_by_batch: dict[str, list[Any]] = {}
        for worker in workers:
            parent_task_id = str(getattr(worker, "parent_task_id", None) or "").strip()
            batch_id = ""
            if parent_task_id.startswith("parallel-batch:"):
                batch_id = parent_task_id.removeprefix("parallel-batch:")
            metadata = getattr(worker, "metadata", {})
            if not batch_id and isinstance(metadata, dict):
                batch_id = str(metadata.get("batch_id") or "").strip()
            if batch_id:
                workers_by_batch.setdefault(batch_id, []).append(worker)

        for host in hosts:
            if owner_id is not None and getattr(host, "owner_id", None) != owner_id:
                continue
            metadata = getattr(host, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            task_id = str(getattr(host, "task_id", "") or "").strip()
            canonical_batch_id = (
                task_id.removeprefix("parallel-batch:")
                if task_id.startswith("parallel-batch:")
                else ""
            )
            recorded_batch_id = str(metadata.get("batch_id") or "").strip()
            if canonical_batch_id and recorded_batch_id not in {"", canonical_batch_id}:
                continue
            batch_id = canonical_batch_id or recorded_batch_id
            if not batch_id:
                continue
            recorded_tenant = str(metadata.get("tenant_id") or "").strip() or None
            if tenant_id is not None and recorded_tenant != tenant_id:
                continue
            if batch_id in snapshots:
                continue
            snapshots[batch_id] = _build_recovery_snapshot_from_host_record(
                host,
                workers_by_batch.get(batch_id, []),
            )

        return sorted(
            snapshots.values(),
            key=lambda item: (str(item.created_at or ""), item.batch_id),
            reverse=True,
        )[:clean_limit]

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

    # Ownership helpers (get_batch_owner/get_batch_tenant,
    # get_task_owner/get_task_tenant, list_batch_ids_for_owner,
    # cancel_all_for_owner) live in
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
        # Close the in-memory batch first so scheduler threads stop submitting
        # work while the pools drain. A durable aggregate host row is settled
        # through the same terminal path, which makes an orderly service
        # shutdown distinguishable from a crash/restart.
        with self._lock:
            self._closed = True
            now = _now()
            for batch in self._batches.values():
                if batch.completed_at is not None:
                    continue
                for entry in batch.tasks.values():
                    if entry.status in _TERMINAL_TASK_STATUSES:
                        continue
                    entry.cancel_event.set()
                    entry.started_at = entry.started_at or now
                    entry.completed_at = now
                    entry.error = "orchestrator_shutdown"
                    entry.status = "cancelled" if entry.status == "pending" else "failed"
                    entry.worker_state = "released"
                    self._publish_task_update_locked(
                        batch,
                        entry,
                        phase="orchestrator_shutdown",
                        message=f"{entry.subagent_name} stopped during orchestrator shutdown",
                    )
                self._maybe_close_batch_locked(batch)
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
