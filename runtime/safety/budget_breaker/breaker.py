from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

CircuitState = Literal["closed", "open", "half_open"]


class CircuitOpen(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        opened_at: float,
        cooldown_seconds: float,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.opened_at = opened_at
        self.cooldown_seconds = cooldown_seconds


@dataclass
class _Event:
    ts: float
    success: bool
    cost_usd: float


class CircuitBreaker:
    def __init__(
        self,
        *,
        window_seconds: float = 60.0,
        max_calls_per_window: int | None = None,
        max_cost_usd_per_window: float | None = None,
        max_errors_per_window: int | None = None,
        cooldown_seconds: float = 30.0,
    ) -> None:
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self.window_seconds = window_seconds
        self.max_calls = max_calls_per_window
        self.max_cost = max_cost_usd_per_window
        self.max_errors = max_errors_per_window
        self.cooldown_seconds = cooldown_seconds

        self._lock = threading.Lock()
        self._events: deque[_Event] = deque()
        self._state: CircuitState = "closed"
        self._opened_at: float = 0.0
        self._open_reason: str = ""
        self._probe_pending: bool = False

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    def snapshot(self) -> dict:
        with self._lock:
            self._prune(time.monotonic())
            total_cost = sum(e.cost_usd for e in self._events)
            errors = sum(1 for e in self._events if not e.success)
            return {
                "state": self._state,
                "calls_in_window": len(self._events),
                "cost_in_window_usd": round(total_cost, 6),
                "errors_in_window": errors,
                "opened_at": self._opened_at if self._state != "closed" else None,
                "reason": self._open_reason if self._state != "closed" else None,
            }

    # ─── check / record ─────────────────────────────

    def check(self) -> CircuitState:
        now = time.monotonic()
        with self._lock:
            self._prune(now)

            if self._state == "open":
                if now - self._opened_at >= self.cooldown_seconds:
                    self._state = "half_open"
                    self._probe_pending = False
                    return "half_open"
                raise CircuitOpen(
                    self._open_reason,
                    opened_at=self._opened_at,
                    cooldown_seconds=self.cooldown_seconds,
                )

            if self._state == "half_open":
                raise CircuitOpen(
                    "half_open probe in flight",
                    opened_at=self._opened_at,
                    cooldown_seconds=self.cooldown_seconds,
                )

            return "closed"

    def record(self, *, success: bool, cost_usd: float = 0.0) -> None:
        now = time.monotonic()
        with self._lock:
            self._events.append(_Event(ts=now, success=success, cost_usd=cost_usd))
            self._prune(now)

            if self._state == "half_open":
                if success:
                    self._state = "closed"
                    self._opened_at = 0.0
                    self._open_reason = ""
                else:
                    self._trip(reason=f"half_open probe failed at {now:.1f}", now=now)
                return

            if self._state == "closed":
                tripped = self._evaluate_thresholds(now)
                if tripped is not None:
                    self._trip(reason=tripped, now=now)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._state = "closed"
            self._opened_at = 0.0
            self._open_reason = ""
            self._probe_pending = False

    def _prune(self, now: float) -> None:
        threshold = now - self.window_seconds
        while self._events and self._events[0].ts < threshold:
            self._events.popleft()

    def _evaluate_thresholds(self, now: float) -> str | None:
        if self.max_calls is not None and len(self._events) > self.max_calls:
            return f"max_calls ({len(self._events)} > {self.max_calls}/{self.window_seconds}s)"
        if self.max_cost is not None:
            total = sum(e.cost_usd for e in self._events)
            if total > self.max_cost:
                return f"max_cost (${total:.4f} > ${self.max_cost:.4f}/{self.window_seconds}s)"
        if self.max_errors is not None:
            errors = sum(1 for e in self._events if not e.success)
            if errors > self.max_errors:
                return f"max_errors ({errors} > {self.max_errors}/{self.window_seconds}s)"
        return None

    def _trip(self, *, reason: str, now: float) -> None:
        self._state = "open"
        self._opened_at = now
        self._open_reason = reason
        self._probe_pending = False
