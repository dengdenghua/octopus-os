"""Diagnostic / capability payload builders for the computer-automation
router family.

Split out of the former ~1994-line computer_router.py. Fully self-contained
pure functions — none of them touch the router's shared mutable state
(pending/lease/activity/control_sessions), they only build response
dicts from their own arguments. Used by computer_router.py's route
handlers, computer_lease.py (conflict diagnostics), and
computer_runtime_readiness.py (capability rows).
"""

from __future__ import annotations

from typing import Any


def _classify_computer_error(error: str) -> str:
    lower = error.lower()
    if any(token in lower for token in ("display", "screen", "x server", "quartz")):
        return "display_unavailable"
    if any(token in lower for token in ("permission", "accessibility", "denied", "not allowed")):
        return "permission"
    if any(token in lower for token in ("coordinate", "bounds", "out of range", "outside")):
        return "coordinate"
    if any(token in lower for token in ("timeout", "timed out", "deadline")):
        return "timeout"
    return "unknown"


def _computer_diagnostic(
    code: str,
    *,
    severity: str,
    message: str,
    recommended_action: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": "echo.computer_automation_diagnostic.v1",
        "code": code,
        "severity": severity,
        "message": message,
        "recommended_action": recommended_action,
        "metadata": metadata or {},
    }


def _execution_failure_diagnostic(
    action: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    error = str(result.get("error") or "")
    category = _classify_computer_error(error)
    action_by_category = {
        "display_unavailable": "check_display_or_permissions",
        "permission": "review_desktop_permissions",
        "coordinate": "replan_with_fresh_screenshot",
        "timeout": "retry_with_shorter_sequence",
    }
    return _computer_diagnostic(
        "action_execution_failed",
        severity="error",
        message="Desktop automation action failed.",
        recommended_action=action_by_category.get(category, "queue_replay_case"),
        metadata={
            "action": str(action.get("action") or ""),
            "error": error,
            "error_category": category,
        },
    )


def _computer_capability(
    capability_id: str,
    *,
    title: str,
    available: bool,
    mode: str,
    reason: str = "",
    recommended_action: str = "",
    critical: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": capability_id,
        "title": title,
        "available": bool(available),
        "critical": bool(critical),
        "mode": mode,
        "reason": reason,
        "recommended_action": recommended_action,
        "metadata": metadata or {},
    }


__all__: list[str] = []
