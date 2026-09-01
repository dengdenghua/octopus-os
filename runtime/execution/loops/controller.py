from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from runtime.execution.loops._controller_attempt import LoopControllerAttemptMixin
from runtime.execution.loops._controller_helpers import (
    _PRODUCT_LOOP_MODES,
    _VERIFIED_LOOP_MODES,
    _attempt_exception_category,
    _attempt_exception_error_text,
    _attempt_exception_repairable,
    _now_iso,
    _resolve_workspace_path,
    _unsupported_mode_result,
)
from runtime.execution.loops._controller_prompt import LoopControllerPromptMixin
from runtime.execution.loops._controller_recovery import LoopControllerRecoveryMixin
from runtime.execution.loops._controller_supervisor import LoopControllerSupervisorMixin
from runtime.execution.loops._controller_trace import LoopControllerTraceMixin
from runtime.execution.loops._controller_verify import LoopControllerVerifyMixin
from runtime.execution.loops.models import (
    LoopAttempt,
    LoopRun,
    LoopRunStatus,
)
from runtime.execution.loops.recovery import build_loop_run_checkpoint
from runtime.execution.loops.store import LoopRunStore
from runtime.execution.loops.verifiers import (
    LoopVerifierRegistry,
    build_default_loop_verifier_registry,
)
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.task_supervisor import TaskRunStatus, TaskSupervisor
from runtime.platform.runtime_policy.workspaces import WorkspaceManager
from runtime.safety.approval.cancellation import CancellationToken


class LoopController(
    LoopControllerRecoveryMixin,
    LoopControllerVerifyMixin,
    LoopControllerSupervisorMixin,
    LoopControllerTraceMixin,
    LoopControllerPromptMixin,
    LoopControllerAttemptMixin,
):
    def __init__(
        self,
        *,
        store: LoopRunStore,
        stack: Any,
        workspace_manager: WorkspaceManager,
        verifier_registry: LoopVerifierRegistry | None = None,
        review_queue: ReviewQueue | None = None,
        candidate_registry_path: str | Path | None = None,
        trace_store: AgentTraceStore | None = None,
        task_supervisor: TaskSupervisor | None = None,
        react_runner: Any = None,
    ) -> None:
        self.store = store
        self.stack = stack
        self.workspace_manager = workspace_manager
        self.verifier_registry = verifier_registry or build_default_loop_verifier_registry()
        self.review_queue = review_queue
        self.candidate_registry_path = (
            Path(candidate_registry_path) if candidate_registry_path is not None else None
        )
        self.trace_store = trace_store
        self.task_supervisor = task_supervisor
        self.react_runner = react_runner
        self._lock = threading.Lock()
        self._executing: set[str] = set()

    def execute(
        self,
        run_id: str,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> LoopRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in {
            LoopRunStatus.COMPLETED,
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            return run
        with self._lock:
            if run_id in self._executing:
                current = self.store.get(run_id)
                if current is None:
                    raise KeyError(run_id)
                return current
            self._executing.add(run_id)
        try:
            return self._execute_locked(run_id, cancellation_token=cancellation_token)
        finally:
            with self._lock:
                self._executing.discard(run_id)

    def request_cancel(
        self,
        run_id: str,
        *,
        reason: str = "cancelled by operator",
    ) -> LoopRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in {
            LoopRunStatus.COMPLETED,
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            return run
        cancel_reason = str(reason or "").strip() or "cancelled by operator"
        requested = self.store.mutate(
            run_id,
            lambda current, cancel_reason=cancel_reason: current.model_copy(
                update={
                    "cancel_requested_at": current.cancel_requested_at or _now_iso(),
                    "cancel_reason": cancel_reason,
                    "last_error": cancel_reason,
                }
            ),
        )
        with self._lock:
            executing = run_id in self._executing
        if executing:
            return requested
        if not self._supervisor_heartbeat(run_id):
            return requested
        return self._cancel_run(run_id, cancel_reason)

    def restart(
        self,
        run_id: str,
        *,
        goal: str | None = None,
        thread_id: str | None = None,
        workspace_path: str | None = None,
        reuse_workspace: bool = True,
        policy: Any = None,
    ) -> LoopRun:
        source = self.store.get(run_id)
        if source is None:
            raise KeyError(run_id)
        self._ensure_restartable(source)
        return self._spawn_child_run(
            source,
            goal=goal,
            thread_id=thread_id,
            workspace_path=workspace_path,
            reuse_workspace=reuse_workspace,
            policy=policy,
            resume_checkpoint_id=None,
        )

    def resume(
        self,
        run_id: str,
        *,
        goal: str | None = None,
        thread_id: str | None = None,
        workspace_path: str | None = None,
        reuse_workspace: bool = True,
        policy: Any = None,
    ) -> LoopRun:
        source = self.store.get(run_id)
        if source is None:
            raise KeyError(run_id)
        if source.status not in {
            LoopRunStatus.FAILED,
            LoopRunStatus.CANCELLED,
            LoopRunStatus.INTERRUPTED,
        }:
            raise ValueError("loop run is not resumable")
        return self._spawn_child_run(
            source,
            goal=goal,
            thread_id=thread_id,
            workspace_path=workspace_path,
            reuse_workspace=reuse_workspace,
            policy=policy,
            resume_checkpoint_id=build_loop_run_checkpoint(source)["id"],
        )

    def _execute_locked(
        self,
        run_id: str,
        *,
        cancellation_token: CancellationToken | None = None,
    ) -> LoopRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if cancelled := self._check_for_cancellation(
            run_id,
            cancellation_token=cancellation_token,
        ):
            return cancelled
        if run.mode not in _VERIFIED_LOOP_MODES | _PRODUCT_LOOP_MODES:
            verifier_result = _unsupported_mode_result(run.mode)
            run = self.store.mutate(
                run_id,
                lambda current: current.model_copy(
                    update={
                        "status": LoopRunStatus.FAILED,
                        "completed_at": _now_iso(),
                        "last_error": verifier_result.summary,
                        "last_verifier_result": verifier_result,
                    }
                ),
            )
            return self._finalize_learning(run)
        requires_verifier = run.mode in _VERIFIED_LOOP_MODES
        workspace_path = _resolve_workspace_path(run, self.workspace_manager)
        supervisor_run = run.model_copy(update={"workspace_path": workspace_path})
        if not self._supervisor_start(supervisor_run):
            return self._latest_run(run_id)
        run = self.store.mutate(
            run_id,
            lambda current: current.model_copy(
                update={
                    "workspace_path": workspace_path,
                    "started_at": current.started_at or _now_iso(),
                }
            ),
        )
        self._record_trace_run_started(run)
        run = self._recover_interrupted_attempts(run_id)
        if run.status == LoopRunStatus.INTERRUPTED:
            return self._finalize_learning(run)
        terminal = self._recover_verified_terminal_run(run_id)
        if terminal is not None:
            return terminal
        run = self._latest_run(run_id)
        if run.status == LoopRunStatus.REPAIRING and not self._supervisor_transition(
            run,
            TaskRunStatus.REPAIRING,
        ):
            return self._latest_run(run_id)
        pending_verification = self._pending_verification_attempt(run)
        if pending_verification is not None:
            if not requires_verifier:
                run = self.store.mutate(
                    run_id,
                    lambda current: current.model_copy(
                        update={
                            "status": (
                                LoopRunStatus.COMPLETED
                                if pending_verification.success is not False
                                else LoopRunStatus.FAILED
                            ),
                            "completed_at": current.completed_at or _now_iso(),
                            "last_error": (
                                ""
                                if pending_verification.success is not False
                                else pending_verification.error
                                or "runner did not complete successfully"
                            ),
                        }
                    ),
                )
                return self._finalize_learning(run)
            terminal = self._verify_attempt(
                run_id,
                pending_verification.attempt_index,
                workspace_path,
                cancellation_token=cancellation_token,
            )
            if terminal is not None:
                return terminal
            run = self._latest_run(run_id)
        if self._attempts_exhausted_without_terminal(run):
            run = self._fail_exhausted_after_recovery(run_id)
            return self._finalize_learning(run)
        if cancelled := self._check_for_cancellation(
            run_id,
            cancellation_token=cancellation_token,
        ):
            return cancelled
        max_attempts = run.policy.max_attempts
        for attempt_index in range(len(run.attempts) + 1, max_attempts + 1):
            if cancelled := self._check_for_cancellation(
                run_id,
                cancellation_token=cancellation_token,
            ):
                return cancelled
            run = self.store.mutate(
                run_id,
                lambda current: current.model_copy(
                    update={
                        "status": LoopRunStatus.RUNNING,
                        "last_error": "",
                    }
                ),
            )
            if not self._supervisor_transition(run, TaskRunStatus.RUNNING):
                return self._latest_run(run_id)
            prompt = self._build_attempt_prompt(run)
            attempt = LoopAttempt(
                attempt_index=attempt_index,
                prompt=prompt,
            )
            run = self.store.mutate(
                run_id,
                lambda current, attempt=attempt: current.model_copy(
                    update={"attempts": [*current.attempts, attempt]}
                ),
            )
            try:
                react_result = self._run_attempt(
                    run,
                    prompt,
                    workspace_path,
                    attempt_index=attempt_index,
                    cancellation_token=cancellation_token,
                )
            except Exception as exc:
                if not self._supervisor_heartbeat(run_id):
                    return self._latest_run(run_id)
                failure_category = _attempt_exception_category(exc)
                error_text = _attempt_exception_error_text(
                    exc,
                    category=failure_category,
                )
                run = self._record_attempt_exception(
                    run_id,
                    attempt_index,
                    error_text,
                    category=failure_category,
                    effect_summary={
                        "schema": "echo.loop.attempt_effect_summary.v2",
                        "emitted_by": (
                            "react_runtime_exception_contract"
                            if _attempt_exception_repairable(failure_category)
                            else "unknown"
                        ),
                        "complete": _attempt_exception_repairable(failure_category),
                        "sealed": _attempt_exception_repairable(failure_category),
                        "total_tool_count": 0,
                        "read_only_effect_count": 0,
                        "workspace_write_effect_count": 0,
                        "local_state_effect_count": 0,
                        "external_effect_count": 0,
                        "indeterminate_effect_count": 0,
                        "unsealed_receipt_count": 0,
                        "unknown_effect_count": (
                            0 if _attempt_exception_repairable(failure_category) else 1
                        ),
                    },
                )
                if (
                    not _attempt_exception_repairable(failure_category)
                    or attempt_index >= max_attempts
                ):
                    if not self._supervisor_heartbeat(run_id):
                        return self._latest_run(run_id)
                    run = self.store.mutate(
                        run_id,
                        lambda current, error_text=error_text: current.model_copy(
                            update={
                                "status": LoopRunStatus.FAILED,
                                "completed_at": _now_iso(),
                                "last_error": error_text,
                            }
                        ),
                    )
                    return self._finalize_learning(run)
                run = self.store.mutate(
                    run_id,
                    lambda current, error_text=error_text: current.model_copy(
                        update={
                            "status": LoopRunStatus.REPAIRING,
                            "last_error": error_text,
                        }
                    ),
                )
                if not self._supervisor_transition(run, TaskRunStatus.REPAIRING):
                    return self._latest_run(run_id)
                continue
            if not self._supervisor_heartbeat(run_id):
                return self._latest_run(run_id)
            run = self._record_attempt_result(run_id, attempt_index, react_result)
            if cancelled := self._check_for_cancellation(
                run_id,
                cancellation_token=cancellation_token,
                latest_result=react_result,
            ):
                return cancelled
            if not requires_verifier:
                product_attempt_succeeded = react_result is not None and react_result.success
                product_attempt_error = (
                    "" if product_attempt_succeeded else "runner did not complete successfully"
                )
                run = self.store.mutate(
                    run_id,
                    lambda current, product_attempt_succeeded=product_attempt_succeeded, product_attempt_error=product_attempt_error: (
                        current.model_copy(
                            update={
                                "status": (
                                    LoopRunStatus.COMPLETED
                                    if product_attempt_succeeded
                                    else LoopRunStatus.FAILED
                                ),
                                "completed_at": _now_iso(),
                                "last_error": product_attempt_error,
                            }
                        )
                    ),
                )
                return self._finalize_learning(run)
            terminal = self._verify_attempt(
                run_id,
                attempt_index,
                workspace_path,
                cancellation_token=cancellation_token,
            )
            if terminal is not None:
                return terminal
        final_run = self.store.get(run_id)
        if final_run is None:
            raise KeyError(run_id)
        return final_run
