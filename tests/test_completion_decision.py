"""Direct unit tests for the canonical completion decision.

``decide_completion`` is the single source of truth for a ReAct turn's
semantic outcome. These tests pin every branch of the decision table plus
the serialized shape consumed by turn clients, so the projections (result
object, ``react_completed`` event, completion receipt, UI) cannot drift.
"""

import pytest

from runtime.core.cerebrum.completion_decision import (
    CompletionDecision,
    decide_completion,
)

# terminated_reason, effective_success, blocked_on_user,
# expected_outcome, success, resumable, retryable
_OUTCOME_ROWS = [
    ("final_answer", True, False, "completed", True, False, False),
    ("final_answer_with_warning", True, False, "completed_with_warning", True, False, False),
    ("max_iter", True, False, "partial", True, True, False),
    ("some_reason", False, True, "blocked_on_user", False, True, False),
    ("paused", False, False, "paused", False, True, False),
    ("cancelled", False, False, "cancelled", False, False, False),
    ("model_stall", False, False, "failed", False, False, True),
    ("error", False, False, "failed", False, False, True),
    ("max_iter", False, False, "failed", False, False, True),
    ("unknown_reason", False, False, "failed", False, False, False),
]


@pytest.mark.parametrize(
    (
        "terminated_reason",
        "effective_success",
        "blocked_on_user",
        "outcome",
        "success",
        "resumable",
        "retryable",
    ),
    _OUTCOME_ROWS,
)
def test_decide_completion_outcome_table(
    terminated_reason: str,
    effective_success: bool,
    blocked_on_user: bool,
    outcome: str,
    success: bool,
    resumable: bool,
    retryable: bool,
) -> None:
    decision = decide_completion(
        terminated_reason=terminated_reason,
        effective_success=effective_success,
        blocked_on_user=blocked_on_user,
    )

    assert decision.outcome == outcome
    assert decision.success is success
    assert decision.terminal is True
    assert decision.resumable is resumable
    assert decision.retryable is retryable
    assert decision.reason == terminated_reason


def test_decide_completion_defaults_reason_to_error() -> None:
    decision = decide_completion(
        terminated_reason="",
        effective_success=False,
    )

    assert decision.outcome == "failed"
    assert decision.reason == "error"
    assert decision.retryable is True


def test_decide_completion_paused_wins_over_blocked() -> None:
    # ``paused`` is checked before ``blocked_on_user`` in the decision table;
    # pin that priority so a user-wait pause cannot degrade into a resumable
    # blocked state with the wrong reason.
    decision = decide_completion(
        terminated_reason="paused",
        effective_success=False,
        blocked_on_user=True,
    )

    assert decision.outcome == "paused"
    assert decision.reason == "paused"
    assert decision.resumable is True
    assert decision.retryable is False


def test_decide_completion_returns_frozen_decision() -> None:
    decision = decide_completion(
        terminated_reason="final_answer",
        effective_success=True,
    )

    assert isinstance(decision, CompletionDecision)
    # dataclasses.FrozenInstanceError subclasses AttributeError.
    with pytest.raises(AttributeError, match="cannot assign to field"):
        decision.outcome = "failed"  # type: ignore[misc]


def test_completion_decision_to_dict_shape() -> None:
    decision = CompletionDecision(
        outcome="partial",
        reason="max_iter",
        success=True,
        terminal=True,
        resumable=True,
        retryable=False,
    )

    assert decision.to_dict() == {
        "outcome": "partial",
        "reason": "max_iter",
        "success": True,
        "terminal": True,
        "resumable": True,
        "retryable": False,
    }


def test_guard_impasse_with_clean_trajectory_is_resumable_partial() -> None:
    decision = decide_completion(
        terminated_reason="guard_impasse",
        effective_success=True,
    )
    assert decision.outcome == "partial"
    assert decision.success is True
    assert decision.resumable is True
    assert decision.retryable is True


def test_guard_impasse_without_evidence_remains_failed() -> None:
    decision = decide_completion(
        terminated_reason="guard_impasse",
        effective_success=False,
    )
    assert decision.outcome == "failed"
    assert decision.success is False

