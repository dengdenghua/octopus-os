"""Waiting-user escalation watchdog — side-channel notifications when an
operator approval blocks longer than a threshold.

Design constraints:
  * Pure side channel: sink callbacks are best-effort and must never raise
    into the control-session / approval path.
  * One action may escalate at most ``max_reminders`` times; the first fires
    after ``timeout_seconds``, later ones after ``repeat_after_seconds`` each.
  * Thread-safe; no dependency on the channel adapters so the unit can be
    tested without network or config.
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class EscalationPolicy:
    timeout_seconds: float = 300.0
    repeat_after_seconds: float = 600.0
    max_reminders: int = 2

    @classmethod
    def from_mapping(cls, value: object) -> EscalationPolicy:
        if not isinstance(value, dict):
            return cls()
        allowed = {"timeout_seconds", "repeat_after_seconds", "max_reminders"}
        data: dict[str, Any] = {
            k: v for k, v in value.items() if k in allowed and isinstance(v, (int, float))
        }
        if "max_reminders" in data:
            data["max_reminders"] = max(0, int(data["max_reminders"]))
        return cls(**data)


class WaitingEscalationWatchdog:
    """Tracks actions in ``waiting_user`` and calls a sink when they stall.

    The sink receives a dict payload and runs synchronously; callers should
    wrap slow/remote sinks in their own executor if needed. Exceptions raised
    by the sink are swallowed and logged at debug level — the watchdog never
    breaks the approval flow.
    """

    def __init__(
        self,
        sink: Callable[[dict[str, Any]], None] | None = None,
        policy: EscalationPolicy | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sink = sink
        self._policy = policy or EscalationPolicy()
        self._clock = clock
        self._lock = threading.Lock()
        self._waiting: dict[str, dict[str, Any]] = {}

    @property
    def policy(self) -> EscalationPolicy:
        return self._policy

    def on_waiting(self, action_id: str, *, detail: dict[str, Any] | None = None) -> None:
        """Record that ``action_id`` entered waiting_user (first time only)."""
        if not action_id:
            return
        with self._lock:
            if action_id not in self._waiting:
                self._waiting[action_id] = {
                    "action_id": action_id,
                    "started_at": self._clock(),
                    "detail": detail or {},
                    "reminders_sent": 0,
                }

    def on_resolved(self, action_id: str) -> None:
        """Clear the action when the operator responds or it leaves waiting."""
        with self._lock:
            self._waiting.pop(action_id, None)

    def poll(self) -> list[dict[str, Any]]:
        """Fire the sink for any action past its reminder schedule.

        Returns the payloads fired in this poll. Call this from the control
        session's maintenance hook; it is also safe to call on a timer.
        """
        fired: list[dict[str, Any]] = []
        now = self._clock()
        with self._lock:
            for action_id, item in list(self._waiting.items()):
                elapsed = now - float(item["started_at"])
                sent = int(item["reminders_sent"])
                due_at = self._policy.timeout_seconds + sent * self._policy.repeat_after_seconds
                if elapsed < due_at or sent >= self._policy.max_reminders:
                    continue
                item["reminders_sent"] = sent + 1
                fired.append(
                    {
                        "action_id": action_id,
                        "waited_seconds": round(elapsed, 1),
                        "reminder": sent + 1,
                        "detail": dict(item.get("detail") or {}),
                    }
                )
        if not fired:
            return []
        sink = self._sink
        if sink is None:
            return fired
        for payload in fired:
            with contextlib.suppress(Exception):
                # Best-effort side channel: never break control flow.
                sink(payload)
        return fired

    def __len__(self) -> int:
        with self._lock:
            return len(self._waiting)
