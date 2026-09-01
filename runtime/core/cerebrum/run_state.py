from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

RunState = Literal["pending", "running", "completed", "failed", "cancelled", "partial"]

_SUCCESS = {"completed", "success"}
_RUNNING = {"pending", "running", "queued", "started"}
_FAILED = {"failed", "error", "timed_out", "timeout"}
_CANCELLED = {"cancelled", "canceled", "dependency_blocked"}


@dataclass(frozen=True)
class RunStateSummary:
    state: RunState
    total: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    running: int = 0
    unknown: int = 0
    terminal: bool = False
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "total": self.total,
            "completed": self.completed,
            "failed": self.failed,
            "cancelled": self.cancelled,
            "running": self.running,
            "unknown": self.unknown,
            "terminal": self.terminal,
            "reasons": list(self.reasons),
        }


def converge_run_state(statuses: list[str] | tuple[str, ...]) -> RunStateSummary:
    total = len(statuses)
    if total == 0:
        return RunStateSummary(state="completed", terminal=True, reasons=("empty",))

    normalized = [_normalize_status(status) for status in statuses]
    counts = Counter(normalized)
    completed = counts["completed"]
    failed = counts["failed"]
    cancelled = counts["cancelled"]
    running = counts["running"]
    unknown = counts["unknown"]

    if running or unknown:
        state: RunState = "running"
        terminal = False
    elif completed == total:
        state = "completed"
        terminal = True
    elif cancelled == total:
        state = "cancelled"
        terminal = True
    elif failed == total:
        state = "failed"
        terminal = True
    else:
        state = "partial"
        terminal = True

    reasons: list[str] = []
    if failed:
        reasons.append("has_failed")
    if cancelled:
        reasons.append("has_cancelled")
    if running:
        reasons.append("has_running")
    if unknown:
        reasons.append("has_unknown")

    return RunStateSummary(
        state=state,
        total=total,
        completed=completed,
        failed=failed,
        cancelled=cancelled,
        running=running,
        unknown=unknown,
        terminal=terminal,
        reasons=tuple(reasons),
    )


def _normalize_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in _SUCCESS:
        return "completed"
    if value in _RUNNING:
        return "running"
    if value in _FAILED:
        return "failed"
    if value in _CANCELLED:
        return "cancelled"
    return "unknown"
