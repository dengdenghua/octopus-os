"""Scheduling + sub-agent launching mixin for ``ParallelAgentOrchestrator``.

Extracted from ``orchestrator.py`` (2026-08) to keep that file under the
god-file threshold. This mixin encapsulates the worker-pool scheduling loop,
timeout/expiry handling, worker-generation replacement, process-isolation
launching, and stage-change event publishing. It is a mixin so it can reach
orchestrator instance state (``self._lock``, ``self._pool``, ``self._batches``)
without duplicating the state on the main class.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from ._orchestrator_models import (
    _TERMINAL_TASK_STATUSES,
    _aggregate,
    _build_coordination_summary,
    _iso,
    _now,
    _route_decision,
    _timeout_policy,
    _unrunnable_plan_issues,
)
from .helpers import (
    authorize_dependency_file_handoffs as _authorize_dependency_file_handoffs,
)
from .helpers import (
    deps_terminal_success as _deps_terminal_success,
)
from .helpers import (
    preview as _preview,
)
from .models import BatchStreamEvent
from .process_worker import (
    close_process_messages,
    poll_process_message,
    process_runner_compatible,
    spawn_process_runner,
    terminate_process,
)


class _SchedulerMixin:
    def _run_task(self, entry: Any, context: dict[str, Any]) -> None:
        if entry.cancel_event.is_set():
            with self._lock:
                if entry.status == "pending":
                    entry.status = "cancelled"
                    entry.completed_at = _now()
                    batch = self._batches[entry.batch_id]
                    self._publish_task_update_locked(
                        batch,
                        entry,
                        phase="cancelled",
                        message=f"{entry.subagent_name} cancelled before start",
                    )
                    self._maybe_close_batch_locked(batch)
            return

        route_decision = _route_decision(entry.subagent_name, context)
        with self._lock:
            entry.route_decision = route_decision
        if route_decision.get("action") == "block":
            with self._lock:
                if entry.status == "pending":
                    entry.status = "failed"
                    entry.started_at = _now()
                    entry.completed_at = entry.started_at
                    entry.error = (
                        "subagent_route_blocked: "
                        f"{route_decision.get('reason') or 'blocked by routing policy'}"
                    )
                    batch = self._batches[entry.batch_id]
                    self._publish_task_update_locked(
                        batch,
                        entry,
                        phase="subagent_route_blocked",
                        message=f"{entry.subagent_name} blocked by fitness routing",
                    )
                    self._maybe_close_batch_locked(batch)
            return

        with self._lock:
            if entry.status in _TERMINAL_TASK_STATUSES:
                return
            entry.status = "running"
            entry.worker_state = "running"
            entry.started_at = _now()
            batch = self._batches[entry.batch_id]
            self._publish_task_update_locked(
                batch,
                entry,
                phase="started",
                message=f"{entry.subagent_name} started working",
            )

        run_context = dict(context)
        run_context["subagent_route_decision"] = route_decision
        with self._lock:
            batch = self._batches[entry.batch_id]
            run_context["runtime_session_metadata"] = batch.runtime_session_metadata
            run_context["file_write_owner"] = entry.task_id
            if entry.work_contract is not None:
                run_context["work_contract"] = entry.work_contract.model_dump()
            _authorize_dependency_file_handoffs(batch, entry, run_context)
        run_context["emit_tool_event"] = self._make_tool_event_emitter(entry)

        output: str | None = None
        error: str | None = None
        isolation = str(
            run_context.get("subagent_worker_isolation") or self._default_worker_isolation
        ).strip()
        isolation_reason: str | None = None
        if isolation == "auto":
            compatible = process_runner_compatible(runner=self._runner, context=run_context)
            isolation = "process" if compatible else "thread"
            if not compatible:
                isolation_reason = "auto_fallback_unpicklable_runner_or_context"
        entry.worker_isolation = "process" if isolation == "process" else "thread"
        entry.worker_isolation_reason = isolation_reason
        if entry.worker_isolation == "process":
            output, error = self._invoke_process_runner(entry, run_context)
        else:
            try:
                output = self._runner(
                    entry.description,
                    subagent_name=entry.subagent_name,
                    context=run_context,
                    cancel_event=entry.cancel_event,
                )
            except TypeError:
                try:
                    output = self._runner(
                        entry.description,
                        subagent_name=entry.subagent_name,
                        context=run_context,
                    )
                except Exception as e:  # noqa: BLE001
                    error = f"{type(e).__name__}: {e}"
            except Exception as e:  # noqa: BLE001
                error = f"{type(e).__name__}: {e}"

        with self._lock:
            if entry.status in {"cancelled", "timed_out"}:
                entry.late_result_ignored_at = _now()
                return
            entry.completed_at = _now()
            if entry.cancel_event.is_set() and error is None:
                entry.status = "cancelled"
            elif error is not None:
                entry.status = "failed"
                entry.error = error
            else:
                cleaned_output = output or ""
                if not cleaned_output.strip():
                    entry.status = "failed"
                    entry.error = "empty_result_contract_violation"
                    entry.result = cleaned_output
                else:
                    entry.status = "completed"
                    entry.result = cleaned_output
            entry.worker_state = "released"
            batch = self._batches[entry.batch_id]
            self._publish_task_update_locked(
                batch,
                entry,
                phase="failed" if entry.status == "failed" else entry.status,
                message=f"{entry.subagent_name} {entry.status}",
                result_preview=_preview(entry.result),
            )
            self._maybe_close_batch_locked(batch)

    def _invoke_process_runner(
        self,
        entry: Any,
        run_context: dict[str, Any],
    ) -> tuple[str | None, str | None]:
        try:
            process, process_cancel, messages = spawn_process_runner(
                runner=self._runner,
                description=entry.description,
                subagent_name=entry.subagent_name,
                context=run_context,
            )
        except Exception as exc:  # noqa: BLE001 - isolation boundary fails closed
            return None, f"process_isolation_start_failed:{type(exc).__name__}: {exc}"

        with self._lock:
            entry.worker_process = process
            entry.process_cancel_event = process_cancel
            entry.process_messages = messages
            entry.worker_state = "process_running"

        output: str | None = None
        error: str | None = None
        emitter = run_context.get("emit_tool_event")
        try:
            while process.is_alive():
                if entry.cancel_event.is_set():
                    process_cancel.set()
                message = poll_process_message(messages)
                if message is None:
                    continue
                kind, payload = message
                if kind == "result":
                    output = str(payload or "")
                elif kind == "error":
                    error = str(payload or "process worker failed")
                elif kind == "tool_event" and callable(emitter) and isinstance(payload, dict):
                    emitter(**payload)
            process.join(timeout=0.1)
            for _ in range(4):
                message = poll_process_message(messages, timeout=0.01)
                if message is None:
                    break
                kind, payload = message
                if kind == "result":
                    output = str(payload or "")
                elif kind == "error":
                    error = str(payload or "process worker failed")
                elif kind == "tool_event" and callable(emitter) and isinstance(payload, dict):
                    emitter(**payload)
            if output is None and error is None and process.exitcode not in {0, None}:
                error = f"process_worker_exit:{process.exitcode}"
        finally:
            with self._lock:
                entry.worker_process = None
                entry.process_cancel_event = None
                entry.process_messages = None
            close_process_messages(messages)
        return output, error

    def _schedule_batch(self, batch_id: str, context: dict[str, Any]) -> None:
        while True:
            with self._lock:
                batch = self._batches.get(batch_id)
                if batch is None or batch.completed_at is not None:
                    return
                self._expire_tasks_locked(batch, context)
                if batch.completed_at is not None:
                    return
                ready = [
                    entry
                    for entry in batch.tasks.values()
                    if entry.status == "pending"
                    and entry.future is None
                    and _deps_terminal_success(batch, entry)
                ]
                blocked_failed = [
                    entry
                    for entry in batch.tasks.values()
                    if entry.status == "pending"
                    and any(
                        batch.tasks[dep].status
                        in {
                            "failed",
                            "cancelled",
                            "timed_out",
                        }
                        for dep in entry.depends_on
                        if dep in batch.tasks
                    )
                ]
                for entry in blocked_failed:
                    entry.status = "cancelled"
                    entry.completed_at = _now()
                    entry.error = "dependency_failed"
                    self._publish_task_update_locked(
                        batch,
                        entry,
                        phase="dependency_blocked",
                        message=f"{entry.subagent_name} blocked by dependency",
                    )
                if blocked_failed:
                    self._maybe_close_batch_locked(batch)
                if not ready and not blocked_failed:
                    self._fail_stalled_pending_tasks_locked(batch)
                if ready:
                    ready.sort(key=lambda e: (-e.priority, e.task_id))
                    for entry in ready:
                        try:
                            entry.submitted_at = _now()
                            entry.worker_generation = self._pool_generation
                            entry.worker_state = "submitted"
                            entry.future = self._pool.submit(
                                self._run_task,
                                entry,
                                context,
                            )
                        except RuntimeError:
                            return

            if not ready:
                time.sleep(0.01)

    def _expire_tasks_locked(
        self,
        batch: Any,
        context: dict[str, Any],
    ) -> None:
        now = _now()
        policy = batch.timeout_policy or _timeout_policy(context)
        task_timeout_s = policy["task_timeout_s"]
        queue_timeout_s = policy["queue_timeout_s"]
        cancel_grace_s = policy["cancel_grace_s"]
        changed = False
        for entry in batch.tasks.values():
            phase = ""
            message = ""
            if (
                entry.status == "running"
                and entry.cancel_requested_at is not None
                and (now - entry.cancel_requested_at).total_seconds() >= cancel_grace_s
            ):
                entry.status = "cancelled"
                entry.error = "cancel_grace_exceeded"
                phase = "cancel_forced"
                message = f"{entry.subagent_name} cancelled after grace period"
            elif (
                entry.status == "running"
                and entry.started_at is not None
                and (now - entry.started_at).total_seconds() >= task_timeout_s
            ):
                entry.cancel_event.set()
                entry.status = "timed_out"
                entry.error = "runner_timeout"
                phase = "timed_out"
                message = f"{entry.subagent_name} exceeded the task timeout"
            elif (
                entry.status == "pending"
                and entry.future is not None
                and entry.submitted_at is not None
                and (now - entry.submitted_at).total_seconds() >= queue_timeout_s
            ):
                entry.cancel_event.set()
                entry.future.cancel()
                entry.status = "timed_out"
                entry.error = "queue_timeout"
                phase = "queue_timed_out"
                message = f"{entry.subagent_name} exceeded the dispatch queue timeout"
            if not phase:
                continue
            entry.completed_at = now
            if phase in {"timed_out", "cancel_forced"}:
                self._replace_stuck_worker_generation_locked(
                    batch,
                    entry,
                    reason=entry.error or phase,
                    now=now,
                )
            self._publish_task_update_locked(
                batch,
                entry,
                phase=phase,
                message=message,
            )
            changed = True
        if changed:
            self._maybe_close_batch_locked(batch)

    def _replace_stuck_worker_generation_locked(
        self,
        batch: Any,
        entry: Any,
        *,
        reason: str,
        now: datetime,
    ) -> None:
        """Quarantine a non-cooperative worker generation and restore capacity.

        Python cannot safely kill a running thread. The authoritative pool is
        therefore replaced without waiting, queued tasks are migrated, and
        late results from the retired generation are ignored because their
        task already has a terminal state.
        """

        generation = entry.worker_generation
        future = entry.future
        if generation is None or future is None or future.done():
            entry.worker_state = "released"
            return
        if entry.worker_process is not None:
            if entry.process_cancel_event is not None:
                entry.process_cancel_event.set()
            terminated = terminate_process(entry.worker_process)
            entry.worker_state = "process_terminated" if terminated else "process_kill_failed"
            entry.replacement_generation = generation
            batch.worker_replacements.append(
                {
                    "event": "process_worker_terminated",
                    "retired_generation": generation,
                    "replacement_generation": generation,
                    "created_replacement": False,
                    "trigger_task_id": entry.task_id,
                    "reason": reason,
                    "at": _iso(now),
                    "terminated": terminated,
                    "quarantined_task_ids": [entry.task_id],
                    "migrated_task_ids": [],
                }
            )
            return
        entry.worker_state = "quarantined"

        existing = next(
            (
                row
                for row in batch.worker_replacements
                if int(row.get("retired_generation", -1)) == generation
            ),
            None,
        )
        if existing is not None:
            quarantined = existing.setdefault("quarantined_task_ids", [])
            if entry.task_id not in quarantined:
                quarantined.append(entry.task_id)
            entry.replacement_generation = int(
                existing.get("replacement_generation", self._pool_generation)
            )
            return

        limit = int(batch.timeout_policy.get("worker_replacement_limit") or 0)
        if len(batch.worker_replacements) >= limit:
            entry.worker_state = "quarantined_limit_reached"
            batch.worker_replacements.append(
                {
                    "event": "replacement_limit_reached",
                    "retired_generation": generation,
                    "replacement_generation": self._pool_generation,
                    "trigger_task_id": entry.task_id,
                    "reason": reason,
                    "at": _iso(now),
                    "quarantined_task_ids": [entry.task_id],
                    "migrated_task_ids": [],
                }
            )
            return

        created_replacement = False
        if generation == self._pool_generation:
            retired_pool = self._pool
            self._pool_generation += 1
            self._pool = ThreadPoolExecutor(
                max_workers=self._max_concurrency,
                thread_name_prefix=f"parallel-agent-g{self._pool_generation}",
            )
            self._retired_pools[generation] = retired_pool
            retired_pool.shutdown(wait=False, cancel_futures=True)
            created_replacement = True

        replacement_generation = self._pool_generation
        entry.replacement_generation = replacement_generation
        migrated = self._migrate_queued_generation_locked(generation)
        batch.worker_replacements.append(
            {
                "event": "worker_generation_replaced",
                "retired_generation": generation,
                "replacement_generation": replacement_generation,
                "created_replacement": created_replacement,
                "trigger_task_id": entry.task_id,
                "reason": reason,
                "at": _iso(now),
                "quarantined_task_ids": [entry.task_id],
                "migrated_task_ids": migrated,
            }
        )

    def _migrate_queued_generation_locked(self, generation: int) -> list[str]:
        migrated: list[str] = []
        for current_batch in self._batches.values():
            for candidate in current_batch.tasks.values():
                if (
                    candidate.status != "pending"
                    or candidate.worker_generation != generation
                    or candidate.future is None
                ):
                    continue
                if not candidate.future.cancel():
                    continue
                candidate.future = None
                candidate.submitted_at = None
                candidate.worker_generation = None
                candidate.worker_state = "migrated"
                migrated.append(candidate.task_id)
        return migrated

    def _maybe_close_batch_locked(self, batch: Any) -> None:
        if batch.completed_at is not None:
            return
        if any(t.status in ("pending", "running") for t in batch.tasks.values()):
            return
        batch.completed_at = _now()
        batch.aggregated_content = _aggregate(batch)
        total, completed, failed, cancelled = batch.counts()
        status = batch.derived_status()
        # Audit T-12: terminal row so the journal no longer shows the batch
        # as running (startup sweep folds anything left running as
        # interrupted).
        from .helpers import journal_batch_lifecycle

        journal_batch_lifecycle(
            batch.batch_id,
            status=status,
            detail=(
                f"parallel batch finished "
                f"(completed={completed} failed={failed} cancelled={cancelled})"
            ),
        )
        self._publish_stage_change_locked(
            batch,
            stage="final_report",
            status=status,
            progress=1.0,
            message="Agent results integrated",
        )
        ev = BatchStreamEvent(
            type="batch_complete",
            batch_id=batch.batch_id,
            lane="timeline",
            status=status,
            payload={
                "status": status,
                "total_tasks": total,
                "completed_tasks": completed,
                "failed_tasks": failed,
                "cancelled_tasks": cancelled,
                "completion_receipt": batch.completion_receipt(),
                "coordination_summary": _build_coordination_summary(batch),
            },
        )
        self._broadcast_locked(batch, ev)
        self._prune_completed_batches_locked()

    def _fail_unrunnable_plan_locked(self, batch: Any) -> bool:
        issues = _unrunnable_plan_issues(batch)
        if not issues:
            return False
        error = "invalid_work_plan:" + ";".join(issues)
        now = _now()
        for entry in batch.tasks.values():
            if entry.status in _TERMINAL_TASK_STATUSES:
                continue
            entry.cancel_event.set()
            entry.status = "failed"
            entry.error = error
            entry.started_at = entry.started_at or now
            entry.completed_at = now
            self._publish_task_update_locked(
                batch,
                entry,
                phase="invalid_work_plan",
                message=f"{entry.subagent_name} blocked by invalid work plan",
            )
        self._maybe_close_batch_locked(batch)
        return True

    def _fail_stalled_pending_tasks_locked(self, batch: Any) -> bool:
        if batch.completed_at is not None:
            return False
        active = any(
            entry.status == "running"
            or (entry.status == "pending" and entry.future is not None and not entry.future.done())
            for entry in batch.tasks.values()
        )
        stalled = [
            entry
            for entry in batch.tasks.values()
            if entry.status == "pending" and entry.future is None
        ]
        if active or not stalled:
            return False

        issues = _unrunnable_plan_issues(batch)
        error = "invalid_work_plan:" + ";".join(issues) if issues else "dependency_unresolvable"
        now = _now()
        for entry in stalled:
            entry.status = "failed"
            entry.error = error
            entry.started_at = entry.started_at or now
            entry.completed_at = now
            self._publish_task_update_locked(
                batch,
                entry,
                phase="dependency_unresolvable",
                message=f"{entry.subagent_name} dependency graph stalled",
            )
        self._maybe_close_batch_locked(batch)
        return True

    def _make_tool_event_emitter(
        self,
        entry: Any,
    ) -> Any:
        def emit_tool_event(
            *,
            tool_name: str,
            status: str | None = None,
            input_preview: str | None = None,
            output_preview: str | None = None,
            artifact_paths: list[str] | None = None,
            message: str | None = None,
            payload: dict[str, object] | None = None,
        ) -> None:
            with self._lock:
                batch = self._batches.get(entry.batch_id)
                if batch is None:
                    return
                ev = BatchStreamEvent(
                    type="tool_call",
                    batch_id=batch.batch_id,
                    task_id=entry.task_id,
                    lane="computer",
                    status=status,
                    subagent_name=entry.subagent_name,
                    tool_name=tool_name,
                    tool_input_preview=input_preview,
                    tool_output_preview=output_preview,
                    artifact_paths=artifact_paths or [],
                    node_ids=[entry.task_id],
                    payload=payload or {},
                    message=message,
                    description=entry.description,
                )
                self._broadcast_locked(batch, ev)

        return emit_tool_event
