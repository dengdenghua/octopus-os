"""Data models + pure helpers for ``ParallelAgentOrchestrator``.

Extracted from ``orchestrator.py`` (2026-08) to keep that file under the
god-file threshold. This module holds the internal task/batch entry dataclasses,
timeout/risk helpers, and the pure builder functions that produce wire-format
observability/coordination/recovery views. Nothing here reads or writes
orchestrator state, so it sits naturally outside the class.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from runtime.core.cerebrum.completion_receipt import build_completion_receipt
from runtime.core.cerebrum.run_state import converge_run_state
from runtime.execution.misc.file_write_leases import (
    file_write_lease_snapshot,
)

from .helpers import (
    preview as _preview,
)
from .models import (
    BatchPlan,
    BatchRecoverySnapshot,
    BatchRecoveryTask,
    BatchResult,
    TaskResult,
    WorkContract,
)

_log = logging.getLogger(__name__)


TaskRunner = Callable[..., str]
_TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled", "timed_out"}
_UNRUNNABLE_PLAN_ISSUE_PREFIXES = ("dependency_cycle:", "unknown_dependency:")


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _now() -> datetime:
    return datetime.now(UTC)


def _context_risk_level(context: dict[str, Any]) -> str:
    for key in (
        "task_risk_level",
        "risk_level",
        "approval_risk_level",
        "quality_risk_level",
    ):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "low"


def _timeout_setting(
    context: dict[str, Any],
    key: str,
    *,
    default: float,
    maximum: float,
) -> float:
    try:
        value = float(context.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(0.01, min(value, maximum))


def _timeout_policy(context: dict[str, Any]) -> dict[str, float]:
    return {
        "task_timeout_s": _timeout_setting(
            context,
            "subagent_task_timeout_s",
            default=900.0,
            maximum=3600.0,
        ),
        "queue_timeout_s": _timeout_setting(
            context,
            "subagent_queue_timeout_s",
            default=60.0,
            maximum=900.0,
        ),
        "cancel_grace_s": _timeout_setting(
            context,
            "subagent_cancel_grace_s",
            default=5.0,
            maximum=60.0,
        ),
        "worker_replacement_limit": _timeout_setting(
            context,
            "subagent_worker_replacement_limit",
            default=8.0,
            maximum=32.0,
        ),
    }


def _route_decision(agent_id: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        from runtime.safety.evolution.subagent_routing import (
            decide_subagent_route,
        )

        decision = decide_subagent_route(
            role=agent_id,
            risk_level=_context_risk_level(context),
            review_queue_path=context.get("review_queue_path"),
            subagent_policy_path=context.get("subagent_policy_path"),
            enabled=bool(context.get("enable_subagent_fitness_routing", True)),
        )
        return decision.to_dict()
    except Exception as exc:  # noqa: BLE001
        _log.debug(
            "parallel subagent fitness routing skipped · agent_id=%s error=%s",
            agent_id,
            exc,
        )
        return {
            "schema": "echo.subagent_route_decision.v1",
            "role": agent_id,
            "action": "allow",
            "reason": "subagent fitness routing unavailable",
            "risk_level": _context_risk_level(context),
            "verdict": "unknown",
            "score": None,
            "confidence": 0.0,
            "evidence_item_ids": [],
        }


@dataclass
class _TaskEntry:
    task_id: str
    batch_id: str
    description: str
    subagent_name: str
    depends_on: list[str]
    priority: int
    write_paths: list[str] = field(default_factory=list)

    status: str = "pending"
    result: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    submitted_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    completed_at: datetime | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None
    work_contract: WorkContract | None = None
    route_decision: dict[str, Any] | None = None
    worker_generation: int | None = None
    worker_state: str = "pending"
    replacement_generation: int | None = None
    late_result_ignored_at: datetime | None = None
    worker_isolation: str = "thread"
    worker_isolation_reason: str | None = None
    worker_process: Any = None
    process_cancel_event: Any = None
    process_messages: Any = None

    def duration(self) -> float | None:
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None

    def to_wire(self) -> TaskResult:
        return TaskResult(
            task_id=self.task_id,
            batch_id=self.batch_id,
            description=self.description,
            status=self.status,
            result=self.result,
            error=self.error,
            started_at=_iso(self.started_at),
            completed_at=_iso(self.completed_at),
            duration_seconds=self.duration(),
            subagent_name=self.subagent_name,
            work_contract=self.work_contract,
            worker_generation=self.worker_generation,
            worker_state=self.worker_state,
            replacement_generation=self.replacement_generation,
            late_result_ignored_at=_iso(self.late_result_ignored_at),
            worker_isolation=self.worker_isolation,
            worker_isolation_reason=self.worker_isolation_reason,
        )


@dataclass
class _BatchEntry:
    batch_id: str
    tasks: dict[str, _TaskEntry]
    created_at: datetime
    completed_at: datetime | None = None
    aggregation_strategy: str | None = None
    aggregated_content: str | None = None
    conflicts: list[str] = field(default_factory=list)
    plan: BatchPlan | None = None
    runtime_session_metadata: dict[str, Any] = field(default_factory=dict)
    timeout_policy: dict[str, float] = field(default_factory=dict)
    worker_replacements: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[tuple[asyncio.Queue, asyncio.AbstractEventLoop]] = field(default_factory=list)
    event_log: list[Any] = field(default_factory=list)
    event_sequence: int = 0
    event_log_dropped_count: int = 0
    artifact_paths_by_task: dict[str, list[str]] = field(default_factory=dict)
    # Owner enforcement: set by dispatch() from the calling actor's id.
    # ``None`` means "no owner recorded" — for batches created before
    # ownership tracking was added, or in single-user dev mode where
    # require_auth is off. Endpoints treat ``None`` as "visible to
    # everyone" so legacy state isn't suddenly hidden.
    owner_id: str | None = None

    # ── counters ──
    def derived_status(self) -> str:
        return converge_run_state([t.status for t in self.tasks.values()]).state

    def validation_issues(self) -> list[str]:
        return list(self.plan.validation_issues) if self.plan is not None else []

    def validation_warnings(self) -> list[str]:
        return list(self.plan.validation_warnings) if self.plan is not None else []

    def artifact_count(self) -> int:
        return sum(len(paths) for paths in self.artifact_paths_by_task.values())

    def completion_receipt(self) -> dict[str, object]:
        return build_completion_receipt(
            [t.status for t in self.tasks.values()],
            contract_issues=self.validation_issues(),
            contract_warnings=self.validation_warnings(),
            artifact_count=self.artifact_count(),
            output_present=bool(self.aggregated_content),
        ).to_dict()

    def counts(self) -> tuple[int, int, int, int]:
        """(total, completed, failed, cancelled)."""
        total = len(self.tasks)
        completed = sum(1 for t in self.tasks.values() if t.status == "completed")
        failed = sum(1 for t in self.tasks.values() if t.status in ("failed", "timed_out"))
        cancelled = sum(1 for t in self.tasks.values() if t.status == "cancelled")
        return total, completed, failed, cancelled

    def to_wire(self) -> BatchResult:
        total, completed, failed, cancelled = self.counts()
        coordination_summary = _build_coordination_summary(self)
        return BatchResult(
            batch_id=self.batch_id,
            status=self.derived_status(),
            total_tasks=total,
            completed_tasks=completed,
            failed_tasks=failed,
            cancelled_tasks=cancelled,
            created_at=_iso(self.created_at),
            completed_at=_iso(self.completed_at),
            results=[t.to_wire() for t in self.tasks.values()],
            aggregated_content=self.aggregated_content,
            aggregation_strategy=self.aggregation_strategy,
            conflicts=list(self.conflicts),
            plan=self.plan,
            event_log=list(self.event_log),
            event_log_truncated=self.event_log_dropped_count > 0,
            event_log_dropped_count=self.event_log_dropped_count,
            completion_receipt=self.completion_receipt(),
            file_write_observability=file_write_lease_snapshot(
                self.runtime_session_metadata,
            ),
            coordination_summary=coordination_summary,
            worker_observability=_build_worker_observability(self),
        )


def _aggregate(batch: _BatchEntry) -> str | None:
    strategy = batch.aggregation_strategy or "concat"
    parts: list[str] = []
    for t in batch.tasks.values():
        if t.status == "completed" and t.result:
            parts.append(f"[{t.subagent_name}] {t.result}")
    if not parts:
        return None
    if strategy == "concat":
        return "\n\n".join(parts)
    return "\n\n".join(parts)


def _unrunnable_plan_issues(batch: _BatchEntry) -> list[str]:
    return [
        issue
        for issue in batch.validation_issues()
        if issue.startswith(_UNRUNNABLE_PLAN_ISSUE_PREFIXES)
    ]


def _build_worker_observability(batch: _BatchEntry) -> dict[str, object]:
    replacements = [dict(row) for row in batch.worker_replacements]
    quarantined = sorted(
        entry.task_id
        for entry in batch.tasks.values()
        if entry.worker_state.startswith("quarantined")
        or entry.worker_state in {"process_terminated", "process_kill_failed"}
    )
    late_results = sorted(
        entry.task_id for entry in batch.tasks.values() if entry.late_result_ignored_at is not None
    )
    migrated = sorted(
        {str(task_id) for row in replacements for task_id in row.get("migrated_task_ids", [])}
    )
    generations = [
        generation
        for entry in batch.tasks.values()
        for generation in (entry.worker_generation, entry.replacement_generation)
        if generation is not None
    ]
    return {
        "schema": "echo.parallel_worker_observability.v1",
        "authoritative_generation": max(generations, default=0),
        "replacement_count": sum(
            1
            for row in replacements
            if row.get("event") in {"worker_generation_replaced", "process_worker_terminated"}
        ),
        "generation_replacement_count": sum(
            1 for row in replacements if row.get("event") == "worker_generation_replaced"
        ),
        "process_termination_count": sum(
            1 for row in replacements if row.get("event") == "process_worker_terminated"
        ),
        "replacement_limit_reached_count": sum(
            1 for row in replacements if row.get("event") == "replacement_limit_reached"
        ),
        "quarantined_task_count": len(quarantined),
        "migrated_task_count": len(migrated),
        "late_result_ignored_count": len(late_results),
        "quarantined_task_ids": quarantined,
        "migrated_task_ids": migrated,
        "late_result_ignored_task_ids": late_results,
        "replacement_limit": int(batch.timeout_policy.get("worker_replacement_limit") or 0),
        "replacements": replacements,
    }


def _task_row(entry: _TaskEntry) -> dict[str, object]:
    if entry.status == "completed" and entry.result:
        action = "use_result"
    elif entry.status in {"failed", "timed_out"}:
        action = "retry_task"
    elif entry.status == "cancelled" and entry.error == "dependency_failed":
        action = "retry_after_dependency"
    elif entry.status == "cancelled":
        action = "confirm_cancelled"
    else:
        action = "wait_for_task"
    return {
        "task_id": entry.task_id,
        "subagent_name": entry.subagent_name,
        "status": entry.status,
        "recommended_action": action,
        "result_chars": len(str(entry.result or "").strip()),
        "error": entry.error,
        "depends_on": list(entry.depends_on),
        "write_paths": list(entry.write_paths),
        "duration_seconds": entry.duration(),
        "worker_generation": entry.worker_generation,
        "worker_state": entry.worker_state,
        "replacement_generation": entry.replacement_generation,
        "late_result_ignored": entry.late_result_ignored_at is not None,
        "worker_isolation": entry.worker_isolation,
        "worker_isolation_reason": entry.worker_isolation_reason,
    }


def _primary_task_id(batch: _BatchEntry) -> str | None:
    for entry in batch.tasks.values():
        if entry.status == "completed" and str(entry.result or "").strip():
            return entry.task_id
    return None


def _coordination_next_action(
    *,
    receipt: dict[str, object],
    failed_task_ids: list[str],
    cancelled_task_ids: list[str],
    conflict_count: int,
    output_present: bool,
) -> str:
    if conflict_count > 0:
        return "review_file_write_conflicts"
    if failed_task_ids and output_present:
        return "use_completed_outputs_and_retry_failed_tasks"
    if failed_task_ids:
        return "retry_failed_tasks"
    if cancelled_task_ids and output_present:
        return "use_completed_outputs_and_requeue_cancelled_tasks"
    if cancelled_task_ids:
        return "requeue_cancelled_tasks"
    if receipt.get("ready") is True:
        return "use_aggregated_result"
    if output_present:
        return "review_partial_outputs"
    return "rerun_with_clearer_task_split"


def _build_coordination_summary(batch: _BatchEntry) -> dict[str, object]:
    """Machine-readable task-level arbitration for a parallel batch."""
    rows = [_task_row(entry) for entry in batch.tasks.values()]
    failed_task_ids = [
        str(row["task_id"]) for row in rows if row["status"] in {"failed", "timed_out"}
    ]
    cancelled_task_ids = [str(row["task_id"]) for row in rows if row["status"] == "cancelled"]
    dependency_blocked_task_ids = [
        entry.task_id for entry in batch.tasks.values() if entry.error == "dependency_failed"
    ]
    receipt = batch.completion_receipt()
    file_obs = file_write_lease_snapshot(batch.runtime_session_metadata)
    conflict_count = int(file_obs.get("conflict_count") or 0)
    primary_task_id = _primary_task_id(batch)
    output_present = bool(batch.aggregated_content)
    next_action = _coordination_next_action(
        receipt=receipt,
        failed_task_ids=failed_task_ids,
        cancelled_task_ids=cancelled_task_ids,
        conflict_count=conflict_count,
        output_present=output_present,
    )
    return {
        "schema": "echo.parallel_batch_coordination.v1",
        "batch_id": batch.batch_id,
        "status": batch.derived_status(),
        "ready": bool(receipt.get("ready")),
        "primary_task_id": primary_task_id,
        "recommended_next_action": next_action,
        "completed_task_ids": [str(row["task_id"]) for row in rows if row["status"] == "completed"],
        "failed_task_ids": failed_task_ids,
        "cancelled_task_ids": cancelled_task_ids,
        "dependency_blocked_task_ids": dependency_blocked_task_ids,
        "conflict_count": conflict_count,
        "contract_issue_count": len(batch.validation_issues()),
        "contract_warning_count": len(batch.validation_warnings()),
        "output_present": output_present,
        "aggregation_strategy": batch.aggregation_strategy or "concat",
        "tasks": rows,
        "checkpoint": {
            "batch_id": batch.batch_id,
            "after_sequence": batch.event_sequence,
        },
    }


def _build_recovery_snapshot(
    batch: _BatchEntry,
) -> BatchRecoverySnapshot:
    total, completed, failed, cancelled = batch.counts()
    run_state = converge_run_state([t.status for t in batch.tasks.values()])
    artifacts_by_task: dict[str, list[str]] = {task_id: [] for task_id in batch.tasks}
    event_types: dict[str, int] = {}
    first_sequence: int | None = None
    last_sequence: int | None = None
    for task_id, paths in batch.artifact_paths_by_task.items():
        bucket = artifacts_by_task.setdefault(task_id, [])
        bucket.extend(path for path in paths if path not in bucket)
    for event in batch.event_log:
        event_types[event.type] = event_types.get(event.type, 0) + 1
        sequence = event.sequence or 0
        if sequence > 0:
            first_sequence = sequence if first_sequence is None else min(first_sequence, sequence)
            last_sequence = sequence if last_sequence is None else max(last_sequence, sequence)
        if event.task_id and event.artifact_paths:
            bucket = artifacts_by_task.setdefault(event.task_id, [])
            for path in event.artifact_paths:
                if path not in bucket:
                    bucket.append(path)

    all_artifacts: list[str] = []
    for paths in artifacts_by_task.values():
        for path in paths:
            if path not in all_artifacts:
                all_artifacts.append(path)

    failed_task_ids = [
        entry.task_id for entry in batch.tasks.values() if entry.status in {"failed", "timed_out"}
    ]
    cancelled_task_ids = [
        entry.task_id for entry in batch.tasks.values() if entry.status == "cancelled"
    ]
    pending_task_ids = [
        entry.task_id for entry in batch.tasks.values() if entry.status == "pending"
    ]
    running_task_ids = [
        entry.task_id for entry in batch.tasks.values() if entry.status == "running"
    ]
    blocked_by_dependency = [
        entry.task_id for entry in batch.tasks.values() if entry.error == "dependency_failed"
    ]
    rerunnable_task_ids = [
        entry.task_id
        for entry in batch.tasks.values()
        if entry.status in {"failed", "cancelled", "timed_out", "pending"}
    ]

    return BatchRecoverySnapshot(
        batch_id=batch.batch_id,
        status=run_state.state,
        terminal=run_state.terminal,
        resume_available=bool(rerunnable_task_ids),
        created_at=_iso(batch.created_at),
        completed_at=_iso(batch.completed_at),
        task_count=total,
        completed_tasks=completed,
        failed_tasks=failed,
        cancelled_tasks=cancelled,
        running_tasks=sum(1 for entry in batch.tasks.values() if entry.status == "running"),
        pending_tasks=sum(1 for entry in batch.tasks.values() if entry.status == "pending"),
        tasks=[
            BatchRecoveryTask(
                task_id=entry.task_id,
                status=entry.status,
                subagent_name=entry.subagent_name,
                depends_on=list(entry.depends_on),
                priority=entry.priority,
                write_paths=list(entry.write_paths),
                description_preview=_preview(entry.description, max_chars=180),
                result_preview=_preview(entry.result, max_chars=260),
                error=entry.error,
                submitted_at=_iso(entry.submitted_at),
                cancel_requested_at=_iso(entry.cancel_requested_at),
                started_at=_iso(entry.started_at),
                completed_at=_iso(entry.completed_at),
                duration_seconds=entry.duration(),
                artifact_paths=artifacts_by_task.get(entry.task_id, []),
                work_contract=entry.work_contract,
                route_decision=dict(entry.route_decision or {}),
                worker_generation=entry.worker_generation,
                worker_state=entry.worker_state,
                replacement_generation=entry.replacement_generation,
                late_result_ignored_at=_iso(entry.late_result_ignored_at),
                worker_isolation=entry.worker_isolation,
                worker_isolation_reason=entry.worker_isolation_reason,
            )
            for entry in batch.tasks.values()
        ],
        dag={task_id: list(entry.depends_on) for task_id, entry in batch.tasks.items()},
        plan=batch.plan,
        event_sequence={
            "event_count": len(batch.event_log),
            "event_log_limit_reached": batch.event_log_dropped_count > 0,
            "dropped_event_count": batch.event_log_dropped_count,
            "first_sequence": first_sequence,
            "last_sequence": last_sequence,
            "next_after_sequence": last_sequence or 0,
            "types": event_types,
        },
        artifact_paths=all_artifacts,
        conflicts=list(batch.conflicts),
        completion_receipt=batch.completion_receipt(),
        file_write_observability=file_write_lease_snapshot(
            batch.runtime_session_metadata,
        ),
        coordination_summary=_build_coordination_summary(batch),
        worker_observability=_build_worker_observability(batch),
        recovery_hints={
            "rerunnable_task_ids": rerunnable_task_ids,
            "failed_task_ids": failed_task_ids,
            "cancelled_task_ids": cancelled_task_ids,
            "pending_task_ids": pending_task_ids,
            "running_task_ids": running_task_ids,
            "blocked_by_dependency": blocked_by_dependency,
            "checkpoint": {
                "batch_id": batch.batch_id,
                "after_sequence": last_sequence or 0,
            },
            "timeout_policy": dict(batch.timeout_policy),
        },
        safety={
            "raw_subagent_outputs_included": False,
            "event_payloads_included": False,
            "owner_id_included": False,
            "result_preview_max_chars": 260,
            "description_preview_max_chars": 180,
            "late_terminal_results_ignored": True,
            "stuck_worker_generations_replaced": True,
            "worker_replacement_limit": int(
                batch.timeout_policy.get("worker_replacement_limit") or 0
            ),
        },
    )
