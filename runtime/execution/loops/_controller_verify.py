from __future__ import annotations

from runtime.execution.loops._controller_helpers import (
    _attempt_execution_completed,
    _now_iso,
    _runner_incomplete_after_verification_error,
    _verifier_error_text,
    _verifier_failure_repairable,
)
from runtime.execution.loops.models import (
    LoopRun,
    LoopRunStatus,
    VerifierFinding,
    VerifierResult,
)
from runtime.platform.process.task_supervisor import TaskRunStatus
from runtime.safety.approval.cancellation import CancellationToken, scoped_cancellation


class LoopControllerVerifyMixin:
    def _verify_attempt(
        self,
        run_id: str,
        attempt_index: int,
        workspace_path: str,
        *,
        cancellation_token: CancellationToken | None,
    ) -> LoopRun | None:
        run = self.store.mutate(
            run_id,
            lambda current: current.model_copy(update={"status": LoopRunStatus.VERIFYING}),
        )
        if not self._supervisor_transition(run, TaskRunStatus.VERIFYING):
            return self._latest_run(run_id)
        if cancelled := self._check_for_cancellation(
            run_id,
            cancellation_token=cancellation_token,
        ):
            return cancelled
        if not self._supervisor_heartbeat(run_id):
            return self._latest_run(run_id)
        verifier_result = self._run_verifier(run, workspace_path, cancellation_token)
        if not self._supervisor_heartbeat(run_id):
            return self._latest_run(run_id)
        run = self._record_verifier_result(run_id, attempt_index, verifier_result)
        if cancelled := self._check_for_cancellation(
            run_id,
            cancellation_token=cancellation_token,
        ):
            return cancelled
        verified_attempt = next(
            (attempt for attempt in run.attempts if attempt.attempt_index == attempt_index),
            None,
        )
        if verifier_result.passed and (
            verified_attempt is None
            or not _attempt_execution_completed(
                verified_attempt,
                allow_legacy_runner=self.react_runner is not None,
            )
        ):
            error_text = _runner_incomplete_after_verification_error()
            if not self._supervisor_heartbeat(run_id):
                return self._latest_run(run_id)
            run = self.store.mutate(
                run_id,
                lambda current, error_text=error_text: current.model_copy(
                    update={
                        "status": LoopRunStatus.FAILED,
                        "completed_at": _now_iso(),
                        "last_error": error_text,
                        "attempts": [
                            attempt.model_copy(
                                update={
                                    "status": "failed",
                                    "error": error_text,
                                }
                            )
                            if attempt.attempt_index == attempt_index
                            else attempt
                            for attempt in current.attempts
                        ],
                    }
                ),
            )
            return self._finalize_learning(run)
        if verifier_result.passed:
            if not self._supervisor_heartbeat(run_id):
                return self._latest_run(run_id)
            run = self.store.mutate(
                run_id,
                lambda current: current.model_copy(
                    update={
                        "status": LoopRunStatus.COMPLETED,
                        "completed_at": _now_iso(),
                        "last_error": "",
                    }
                ),
            )
            return self._finalize_learning(run)

        error_text = _verifier_error_text(verifier_result)
        if not _verifier_failure_repairable(verifier_result):
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

        latest = self._latest_run(run_id)
        if attempt_index >= latest.policy.max_attempts:
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
        return None

    def _run_verifier(
        self,
        run: LoopRun,
        workspace_path: str,
        cancellation_token: CancellationToken | None,
    ) -> VerifierResult:
        try:
            with scoped_cancellation(cancellation_token or CancellationToken.none()):
                return self.verifier_registry.run(
                    run.policy.verifier_profile,
                    workspace_path,
                )
        except KeyError:
            profile = str(run.policy.verifier_profile or "").strip() or "<empty>"
            error_text = f"unknown verifier profile: {profile}"
            return VerifierResult(
                profile=profile,
                kind="verifier_error",
                failure_category="verifier_profile_unknown",
                passed=False,
                summary=error_text,
                findings=[
                    VerifierFinding(
                        name="verifier-profile",
                        passed=False,
                        category="verifier_profile_unknown",
                        exit_code=-2,
                        stderr=error_text,
                    )
                ],
            )
        except Exception as exc:
            return VerifierResult(
                profile=run.policy.verifier_profile,
                kind="verifier_error",
                failure_category="verifier_internal_error",
                passed=False,
                summary=str(exc),
                findings=[
                    VerifierFinding(
                        name="verifier-error",
                        passed=False,
                        category="verifier_internal_error",
                        exit_code=-1,
                        stderr=str(exc),
                    )
                ],
            )
