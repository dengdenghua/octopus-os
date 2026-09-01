from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CompletionOutcome = Literal[
    "completed",
    "completed_with_warning",
    "partial",
    "blocked_on_user",
    "paused",
    "cancelled",
    "failed",
]


@dataclass(frozen=True)
class CompletionDecision:
    """Canonical semantic outcome for one ReAct turn.

    The runtime historically derived the same outcome independently for the
    result object, ``react_completed`` event, completion receipt, and UI.  A
    single decision prevents those projections from disagreeing (for example,
    a cleaned final answer being green in the event but failed in the receipt).
    """

    outcome: CompletionOutcome
    reason: str
    # Compatibility delivery signal consumed by existing turn clients.  For a
    # partial result this is true because a usable answer was delivered; the
    # outcome (and receipt.ready) still make clear that work is incomplete.
    success: bool
    terminal: bool = True
    resumable: bool = False
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "reason": self.reason,
            "success": self.success,
            "terminal": self.terminal,
            "resumable": self.resumable,
            "retryable": self.retryable,
        }


def decide_completion(
    *,
    terminated_reason: str,
    effective_success: bool,
    blocked_on_user: bool = False,
) -> CompletionDecision:
    """Resolve raw loop signals into one stable, protocol-facing outcome."""

    reason = str(terminated_reason or "error")
    if reason == "paused":
        return CompletionDecision(
            outcome="paused",
            reason=reason,
            success=False,
            resumable=True,
        )
    if reason == "cancelled":
        return CompletionDecision(
            outcome="cancelled",
            reason=reason,
            success=False,
        )
    if blocked_on_user:
        return CompletionDecision(
            outcome="blocked_on_user",
            reason=reason,
            success=False,
            resumable=True,
        )
    if effective_success and reason == "final_answer_with_warning":
        return CompletionDecision(
            outcome="completed_with_warning",
            reason=reason,
            success=True,
        )
    if effective_success and reason == "max_iter":
        return CompletionDecision(
            outcome="partial",
            reason=reason,
            success=True,
            resumable=True,
        )
    # A completeness guard can exhaust its bounded retries after the model has
    # already performed useful work but keeps emitting a future-work sentence.
    # Preserve the distinction in the raw termination reason while exposing a
    # resumable partial result to the UI instead of a terminal red failure.
    if reason == "guard_impasse" and effective_success:
        return CompletionDecision(
            outcome="partial",
            reason=reason,
            success=True,
            resumable=True,
            retryable=True,
        )
    if effective_success and reason == "final_answer":
        return CompletionDecision(
            outcome="completed",
            reason=reason,
            success=True,
        )
    return CompletionDecision(
        outcome="failed",
        reason=reason,
        success=False,
        retryable=reason in {"error", "model_stall", "max_iter"},
    )
