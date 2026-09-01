from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger("echo.budget.iteration")


def _publish_extended_event() -> None:
    from runtime.platform.process.eventbus import IterationExtended, publish_event

    publish_event(
        IterationExtended(
            event_type="iteration.extended",
            reason="auto_extend",
        ),
        logger=_LOG,
    )


class IterationBudgetExceeded(Exception):
    def __init__(self, current: int, limit: int, reason: str = "") -> None:
        self.current = current
        self.limit = limit
        self.reason = reason
        super().__init__(f"Iteration budget exceeded: {current}/{limit} ({reason})")


@dataclass
class IterationBudgetConfig:
    max_iterations: int = 50
    warn_at_percent: float = 0.8
    hard_limit: bool = True
    auto_extend: bool = True
    extend_by_percent: float = 0.5
    max_extensions: int = 3
    progress_window: int = 5
    progress_threshold: float = 0.6


@dataclass
class IterationRecord:
    tool_name: str
    success: bool
    unique: bool


class SmartIterationBudget:
    def __init__(self, config: IterationBudgetConfig | None = None) -> None:
        self.config = config or IterationBudgetConfig()
        self._count: int = 0
        self._effective_limit: int = self.config.max_iterations
        self._extensions_used: int = 0
        self._history: list[IterationRecord] = []

    def tick(
        self,
        tool_name: str = "",
        success: bool = True,
        is_unique: bool = True,
    ) -> tuple[int, str]:
        self._count += 1
        self._history.append(
            IterationRecord(
                tool_name=tool_name,
                success=success,
                unique=is_unique,
            )
        )
        if len(self._history) > 100:
            self._history = self._history[-100:]

        if self._count > self._effective_limit:
            if self.config.auto_extend and self._is_making_progress():
                extended = self._try_extend()
                if extended:
                    _LOG.info(
                        "budget auto-extended: %d -> %d (progress detected)",
                        self._effective_limit
                        - int(self.config.max_iterations * self.config.extend_by_percent),
                        self._effective_limit,
                    )
                    return self._count, "extended"
            if self.config.hard_limit:
                reason = "loop_detected" if not self._is_making_progress() else "budget_exhausted"
                raise IterationBudgetExceeded(self._count, self._effective_limit, reason)
            return self._count, "over_budget"

        warn_threshold = int(self._effective_limit * self.config.warn_at_percent)
        if self._count >= warn_threshold:
            return self._count, "warning"
        return self._count, "ok"

    def _is_making_progress(self) -> bool:
        window = self.config.progress_window
        if len(self._history) < window:
            return True

        recent = self._history[-window:]
        successes = sum(1 for r in recent if r.success)
        success_rate = successes / len(recent)

        if success_rate < self.config.progress_threshold:
            return False

        unique_tools = len(set(r.tool_name for r in recent if r.tool_name))
        if unique_tools <= 1 and not all(r.success for r in recent):
            return False

        unique_calls = sum(1 for r in recent if r.unique)
        return not unique_calls < len(recent) * 0.3

    def _try_extend(self) -> bool:
        if self._extensions_used >= self.config.max_extensions:
            return False
        extension = max(1, int(self.config.max_iterations * self.config.extend_by_percent))
        self._effective_limit += extension
        self._extensions_used += 1
        return True

    @property
    def count(self) -> int:
        return self._count

    @property
    def effective_limit(self) -> int:
        return self._effective_limit

    @property
    def remaining(self) -> int:
        return max(0, self._effective_limit - self._count)

    @property
    def percent_used(self) -> float:
        return self._count / self._effective_limit if self._effective_limit > 0 else 1.0

    @property
    def extensions_used(self) -> int:
        return self._extensions_used

    def is_exceeded(self) -> bool:
        return self._count >= self._effective_limit

    def is_stuck(self) -> bool:
        if len(self._history) < self.config.progress_window:
            return False
        return not self._is_making_progress()

    def reset(self) -> None:
        self._count = 0
        self._effective_limit = self.config.max_iterations
        self._extensions_used = 0
        self._history.clear()

    def status(self) -> dict[str, Any]:
        return {
            "count": self._count,
            "effective_limit": self._effective_limit,
            "original_limit": self.config.max_iterations,
            "remaining": self.remaining,
            "percent_used": round(self.percent_used, 3),
            "extensions_used": self._extensions_used,
            "max_extensions": self.config.max_extensions,
            "is_stuck": self.is_stuck(),
            "is_making_progress": self._is_making_progress() if self._history else True,
        }


# Backward-compatible alias
IterationBudget = SmartIterationBudget

__all__ = [
    "IterationBudget",
    "IterationBudgetConfig",
    "IterationBudgetExceeded",
    "IterationRecord",
    "SmartIterationBudget",
]
