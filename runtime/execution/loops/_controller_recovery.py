from __future__ import annotations

from typing import Any

from runtime.execution.loops._controller_helpers import (
    _ACTIVE_LOOP_STATUSES,
    _attempt_execution_completed,
    _now_iso,
    _runner_incomplete_after_verification_error,
    _truncate_text,
    _verifier_error_text,
    _verifier_failure_repairable,
)
from runtime.execution.loops.models import (
    LoopAttempt,
    LoopRun,
    LoopRunStatus,
)


class LoopControllerRecoveryMixin:
    @staticmethod
    def _ensure_restartable(run: LoopRun) -> None:
        if run.status in {
            LoopRunStatus.PENDING,
            LoopRunStatus.RUNNING,
            LoopRunStatus.VERIFYING,
            LoopRunStatus.REPAIRING,
        }:
            raise ValueError("loop run is still active")

    def _spawn_child_run(
        self,
        source: LoopRun,
        *,
        goal: str | None,
        thread_id: str | None,
        workspace_path: str | None,
        reuse_workspace: bool,
        policy: Any,
        resume_checkpoint_id: str | None,
    ) -> LoopRun:
        next_goal = str(goal or "").strip() or source.goal
        next_thread_id = thread_id if thread_id is not None else source.thread_id
        next_workspace_path = (
            workspace_path
            if workspace_path is not None
            else source.workspace_path
            if reuse_workspace
            else None
        )
        next_policy = (
            policy.model_copy(deep=True)
            if policy is not None
            else source.policy.model_copy(deep=True)
        )
        child = LoopRun(
            tenant_id=source.tenant_id,
            owner_id=source.owner_id,
            parent_run_id=source.run_id,
            origin_run_id=source.origin_run_id or source.run_id,
            resume_checkpoint_id=resume_checkpoint_id,
            goal=next_goal,
            mode=source.mode,
            thread_id=next_thread_id,
            workspace_path=next_workspace_path,
            policy=next_policy,
        )
        return self.store.create(child)

    def _recover_interrupted_attempts(self, run_id: str) -> LoopRun:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in _ACTIVE_LOOP_STATUSES:
            return run
        if not any(
            not attempt.completed_at and str(attempt.status or "") == "running"
            for attempt in run.attempts
        ):
            return run
        return self.store.mutate(run_id, self._recover_interrupted_attempts_for_current)

    def _recover_interrupted_attempts_for_current(self, current: LoopRun) -> LoopRun:
        if current.status not in _ACTIVE_LOOP_STATUSES:
            return current
        reason = "previous loop attempt interrupted before completion"
        recovery_reason = "previous loop attempt recovered from half-written completion"
        recovered = False
        interrupted = False
        attempts: list[LoopAttempt] = []
        for attempt in current.attempts:
            if attempt.completed_at or str(attempt.status or "") != "running":
                attempts.append(attempt)
                continue
            if recovered_status := self._recoverable_attempt_status(attempt):
                recovered = True
                attempts.append(
                    attempt.model_copy(
                        update={
                            "completed_at": attempt.completed_at or _now_iso(),
                            "status": recovered_status,
                            "success": True if recovered_status == "completed" else attempt.success,
                            "terminated_reason": attempt.terminated_reason or recovery_reason,
                            "final_answer": _truncate_text(attempt.final_answer),
                            "error": "",
                        }
                    )
                )
                continue
            interrupted = True
            attempts.append(
                attempt.model_copy(
                    update={
                        "completed_at": attempt.completed_at or _now_iso(),
                        "status": "interrupted",
                        "success": False,
                        "terminated_reason": reason,
                        "error": attempt.error or reason,
                    }
                )
            )
        if not recovered and not interrupted:
            return current
        return current.model_copy(
            update={
                "status": (LoopRunStatus.VERIFYING if recovered else LoopRunStatus.INTERRUPTED),
                "completed_at": (
                    current.completed_at or _now_iso()
                    if interrupted and not recovered
                    else current.completed_at
                ),
                "last_error": reason if interrupted and not recovered else "",
                "attempts": attempts,
            }
        )

    @staticmethod
    def _recoverable_attempt_status(attempt: LoopAttempt) -> str | None:
        if attempt.success is True and not str(attempt.error or "").strip():
            return "completed"
        has_completion_snapshot = bool(
            str(attempt.final_answer or "").strip() or attempt.completion_receipt
        )
        if has_completion_snapshot and not str(attempt.error or "").strip():
            return "needs_verify"
        return None

    def _recover_verified_terminal_run(self, run_id: str) -> LoopRun | None:
        run = self.store.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status not in _ACTIVE_LOOP_STATUSES:
            return None
        attempt = self._latest_verified_attempt(run)
        if attempt is None:
            return None
        if attempt.verifier_result is None:
            return None
        should_finalize = False

        def _mutate(current: LoopRun) -> LoopRun:
            nonlocal should_finalize
            if current.status not in _ACTIVE_LOOP_STATUSES:
                return current
            current_attempt = self._latest_verified_attempt(current)
            if current_attempt is None or current_attempt.verifier_result is None:
                return current
            verifier_result = current_attempt.verifier_result
            if verifier_result.passed:
                should_finalize = True
                if not _attempt_execution_completed(
                    current_attempt,
                    allow_legacy_runner=self.react_runner is not None,
                ):
                    error_text = _runner_incomplete_after_verification_error()
                    return current.model_copy(
                        update={
                            "status": LoopRunStatus.FAILED,
                            "completed_at": current.completed_at or _now_iso(),
                            "last_error": error_text,
                            "last_verifier_result": verifier_result,
                            "attempts": [
                                attempt.model_copy(update={"status": "failed", "error": error_text})
                                if attempt.attempt_index == current_attempt.attempt_index
                                else attempt
                                for attempt in current.attempts
                            ],
                        }
                    )
                return current.model_copy(
                    update={
                        "status": LoopRunStatus.COMPLETED,
                        "completed_at": current.completed_at or _now_iso(),
                        "last_error": "",
                        "last_verifier_result": verifier_result,
                    }
                )
            error_text = _verifier_error_text(verifier_result)
            if _verifier_failure_repairable(verifier_result) and (
                current_attempt.attempt_index < current.policy.max_attempts
            ):
                return current.model_copy(
                    update={
                        "status": LoopRunStatus.REPAIRING,
                        "last_error": error_text,
                        "last_verifier_result": verifier_result,
                    }
                )
            should_finalize = True
            return current.model_copy(
                update={
                    "status": LoopRunStatus.FAILED,
                    "completed_at": current.completed_at or _now_iso(),
                    "last_error": error_text,
                    "last_verifier_result": verifier_result,
                }
            )

        recovered = self.store.mutate(run_id, _mutate)
        if should_finalize:
            return self._finalize_learning(recovered)
        return None

    @staticmethod
    def _latest_verified_attempt(run: LoopRun) -> LoopAttempt | None:
        if not run.attempts:
            return None
        attempt = run.attempts[-1]
        return attempt if attempt.verifier_result is not None else None

    @staticmethod
    def _pending_verification_attempt(run: LoopRun) -> LoopAttempt | None:
        if run.status not in {LoopRunStatus.RUNNING, LoopRunStatus.VERIFYING}:
            return None
        for attempt in reversed(run.attempts):
            if attempt.completed_at and attempt.verifier_result is None:
                if attempt.status in {"completed", "needs_verify"}:
                    return attempt
                if attempt.success is True and not attempt.error:
                    return attempt
                return None
        return None

    @staticmethod
    def _attempts_exhausted_without_terminal(run: LoopRun) -> bool:
        if run.status not in _ACTIVE_LOOP_STATUSES:
            return False
        return len(run.attempts) >= run.policy.max_attempts

    def _fail_exhausted_after_recovery(self, run_id: str) -> LoopRun:
        reason = "loop attempts exhausted after recovering interrupted state"
        return self.store.mutate(
            run_id,
            lambda current, reason=reason: current.model_copy(
                update={
                    "status": LoopRunStatus.FAILED,
                    "completed_at": current.completed_at or _now_iso(),
                    "last_error": current.last_error or reason,
                }
            ),
        )
