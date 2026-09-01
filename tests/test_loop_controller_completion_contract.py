from __future__ import annotations

from types import SimpleNamespace

from runtime.core.cerebrum.react_types import ReActResult, ReActStep
from runtime.execution.loops._controller_attempt import _react_result_effect_summary
from runtime.execution.loops._controller_helpers import (
    _attempt_execution_completed,
    _verifier_failure_repairable,
)
from runtime.execution.loops.controller import LoopController
from runtime.execution.loops.models import (
    LoopAttempt,
    LoopPolicy,
    LoopRun,
    LoopRunStatus,
    VerifierResult,
)
from runtime.execution.loops.store import LoopRunStore
from runtime.platform.runtime_policy.workspaces import WorkspaceManager


class _VerifierRegistry:
    def __init__(self, results: list[VerifierResult]) -> None:
        self.results = list(results)
        self.calls = 0

    def run(self, profile: str, workspace_path: str) -> VerifierResult:
        self.calls += 1
        return self.results.pop(0)


def _passed_verifier() -> VerifierResult:
    return VerifierResult(
        profile="auto",
        kind="python",
        passed=True,
        summary="all checks passed",
    )


def _execute_with_result(tmp_path, result: ReActResult) -> tuple[LoopRun, _VerifierRegistry]:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    run = LoopRun(
        goal="Repair with a coherent completion contract",
        workspace_path=str(tmp_path / "repo"),
        policy=LoopPolicy(max_attempts=1, max_iterations=1),
    )
    store.create(run)
    verifier = _VerifierRegistry([_passed_verifier()])
    controller = LoopController(
        store=store,
        stack=SimpleNamespace(name="stack"),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        verifier_registry=verifier,
        react_runner=lambda **_kwargs: result,
    )
    return controller.execute(run.run_id), verifier


def test_verifier_pass_cannot_override_runner_failure(tmp_path) -> None:
    completed, verifier = _execute_with_result(
        tmp_path,
        ReActResult(final_answer="not complete", success=False),
    )

    assert verifier.calls == 1
    assert completed.status == LoopRunStatus.FAILED
    assert "runner_incomplete_despite_verification" in completed.last_error
    assert completed.last_verifier_result is not None
    assert completed.last_verifier_result.passed is True
    assert completed.attempts[0].status == "failed"


def test_partial_completion_contract_cannot_become_completed(tmp_path) -> None:
    completed, _ = _execute_with_result(
        tmp_path,
        ReActResult(
            final_answer="partial result",
            success=True,
            completion_decision={
                "outcome": "partial",
                "reason": "max_iter",
                "success": True,
                "terminal": True,
                "resumable": True,
                "retryable": False,
            },
            completion_receipt={"ready": False},
        ),
    )

    assert completed.status == LoopRunStatus.FAILED
    assert "runner_incomplete_despite_verification" in completed.last_error


def test_completed_contract_and_verifier_pass_are_jointly_required(tmp_path) -> None:
    completed, _ = _execute_with_result(
        tmp_path,
        ReActResult(
            final_answer="done",
            success=True,
            completion_decision={
                "outcome": "completed",
                "reason": "final_answer",
                "success": True,
                "terminal": True,
                "resumable": False,
                "retryable": False,
            },
            completion_receipt={"ready": True},
        ),
    )

    assert completed.status == LoopRunStatus.COMPLETED


def test_builtin_runner_contract_is_fail_closed_but_legacy_stub_is_compatible() -> None:
    attempt = LoopAttempt(
        attempt_index=1,
        prompt="repair",
        success=True,
    )

    assert not _attempt_execution_completed(attempt, allow_legacy_runner=False)
    assert _attempt_execution_completed(attempt, allow_legacy_runner=True)


def test_loop_effect_summary_accepts_only_sealed_runtime_receipts() -> None:
    proof = {
        "schema": "echo.tool.effect_receipt.v1",
        "sealed": True,
        "emitted_by": "tool_executor",
        "tool_name": "edit_file",
        "effect_class": "workspace_write",
        "state": "committed",
    }
    result = ReActResult(
        final_answer="done",
        steps=[
            ReActStep(
                iteration=1,
                action="edit_file({})",
                action_results=[
                    {
                        "tool_name": "edit_file",
                        "ok": True,
                        "execution_source": "registered_noncanonical",
                        "effect_receipt": proof,
                    }
                ],
            )
        ],
    )

    summary = _react_result_effect_summary(result, runtime_owned=True)
    assert summary["schema"] == "echo.loop.attempt_effect_summary.v2"
    assert summary["complete"] is True
    assert summary["sealed"] is True
    assert summary["workspace_write_effect_count"] == 1
    assert summary["unknown_effect_count"] == 0

    forged = _react_result_effect_summary(result, runtime_owned=False)
    assert forged["complete"] is False
    assert forged["sealed"] is False
    assert forged["unsealed_receipt_count"] == 1
    assert forged["unknown_effect_count"] == 1


def test_recovery_cannot_promote_failed_attempt_from_stored_verifier_pass(tmp_path) -> None:
    store = LoopRunStore(tmp_path / "loop_runs.json")
    verifier_result = _passed_verifier()
    run = LoopRun(
        goal="Recover coherently",
        workspace_path=str(tmp_path / "repo"),
        status=LoopRunStatus.VERIFYING,
        policy=LoopPolicy(max_attempts=2, max_iterations=1),
        attempts=[
            LoopAttempt(
                attempt_index=1,
                prompt="Recover coherently",
                completed_at="2026-08-27T00:00:00+00:00",
                status="needs_verify",
                success=False,
                verifier_result=verifier_result,
            )
        ],
        last_verifier_result=verifier_result,
    )
    store.create(run)
    runner_calls = 0

    def runner(**_kwargs):
        nonlocal runner_calls
        runner_calls += 1
        return ReActResult(final_answer="should not run", success=True)

    controller = LoopController(
        store=store,
        stack=SimpleNamespace(name="stack"),
        workspace_manager=WorkspaceManager(tmp_path / "workspaces"),
        verifier_registry=_VerifierRegistry([]),
        react_runner=runner,
    )
    recovered = controller.execute(run.run_id)

    assert recovered.status == LoopRunStatus.FAILED
    assert runner_calls == 0
    assert "runner_incomplete_despite_verification" in recovered.last_error


def test_only_explicit_code_failure_categories_enter_model_repair() -> None:
    for category in (
        "project_manifest_error",
        "syntax_error",
        "type_error",
        "lint_failure",
        "build_failure",
        "test_failure",
    ):
        assert _verifier_failure_repairable(
            VerifierResult(profile="auto", passed=False, failure_category=category)
        )

    for category in (
        "verification_timeout",
        "verification_failure",
        "verifier_internal_error",
        "unexpected_plugin_category",
    ):
        assert not _verifier_failure_repairable(
            VerifierResult(profile="auto", passed=False, failure_category=category)
        )

