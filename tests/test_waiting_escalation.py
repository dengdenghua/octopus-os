from __future__ import annotations

import threading
import time
from typing import Any

from runtime.sensing.gateway.computer_control_session import _sync_escalation
from runtime.sensing.gateway.computer_router_state import ComputerRouterState
from runtime.sensing.gateway.waiting_escalation import (
    EscalationPolicy,
    WaitingEscalationWatchdog,
)


class _Clock:
    """Manually-advanceable clock for deterministic tests."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_watchdog_fires_after_timeout() -> None:
    clock = _Clock()
    fired: list[dict[str, Any]] = []
    watchdog = WaitingEscalationWatchdog(
        sink=fired.append,
        policy=EscalationPolicy(timeout_seconds=300, repeat_after_seconds=600, max_reminders=2),
        clock=clock,
    )

    watchdog.on_waiting("action-1", detail={"action": "click"})
    assert watchdog.poll() == []

    clock.advance(299)
    assert watchdog.poll() == []

    clock.advance(2)
    payloads = watchdog.poll()
    assert len(payloads) == 1
    assert payloads[0]["action_id"] == "action-1"
    assert payloads[0]["reminder"] == 1
    assert payloads[0]["waited_seconds"] == 301
    assert payloads[0]["detail"] == {"action": "click"}
    assert fired == payloads


def test_watchdog_respects_max_reminders() -> None:
    clock = _Clock()
    fired: list[dict[str, Any]] = []
    watchdog = WaitingEscalationWatchdog(
        sink=fired.append,
        policy=EscalationPolicy(timeout_seconds=60, repeat_after_seconds=60, max_reminders=2),
        clock=clock,
    )

    watchdog.on_waiting("action-1")
    clock.advance(61)
    watchdog.poll()
    clock.advance(61)
    watchdog.poll()
    clock.advance(61)
    watchdog.poll()

    assert len(fired) == 2
    assert [p["reminder"] for p in fired] == [1, 2]


def test_watchdog_clears_on_resolved() -> None:
    clock = _Clock()
    fired: list[dict[str, Any]] = []
    watchdog = WaitingEscalationWatchdog(
        sink=fired.append,
        policy=EscalationPolicy(timeout_seconds=60, repeat_after_seconds=60, max_reminders=2),
        clock=clock,
    )

    watchdog.on_waiting("action-1")
    watchdog.on_resolved("action-1")
    clock.advance(120)
    assert watchdog.poll() == []
    assert len(watchdog) == 0


def test_watchdog_sink_exception_does_not_raise() -> None:
    clock = _Clock()

    def boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("sink failure")

    watchdog = WaitingEscalationWatchdog(
        sink=boom,
        policy=EscalationPolicy(timeout_seconds=1, repeat_after_seconds=1, max_reminders=2),
        clock=clock,
    )
    watchdog.on_waiting("action-1")
    clock.advance(2)
    # Must not raise: escalation is best-effort by contract. The payload is
    # still returned (the reminder count advanced), but the sink exception
    # must not propagate into the caller.
    payloads = watchdog.poll()
    assert len(payloads) == 1
    assert payloads[0]["reminder"] == 1


def test_watchdog_ignores_duplicate_waiting_and_empty_action() -> None:
    clock = _Clock()
    watchdog = WaitingEscalationWatchdog(
        sink=None,
        policy=EscalationPolicy(timeout_seconds=1, repeat_after_seconds=1, max_reminders=2),
        clock=clock,
    )

    watchdog.on_waiting("action-1", detail={"attempt": 1})
    watchdog.on_waiting("action-1", detail={"attempt": 2})  # duplicate: keep first
    watchdog.on_waiting("")
    watchdog.on_waiting("action-2")
    assert len(watchdog) == 2

    clock.advance(2)
    payloads = watchdog.poll()
    assert len(payloads) == 2
    first = next(p for p in payloads if p["action_id"] == "action-1")
    second = next(p for p in payloads if p["action_id"] == "action-2")
    assert first["detail"] == {"attempt": 1}
    assert second["detail"] == {}


def test_watchdog_is_thread_safe() -> None:
    watchdog = WaitingEscalationWatchdog(
        sink=None,
        policy=EscalationPolicy(timeout_seconds=1, repeat_after_seconds=1, max_reminders=1),
        clock=time.time,
    )
    errors: list[Exception] = []

    def worker(prefix: str) -> None:
        try:
            for idx in range(200):
                action_id = f"{prefix}-{idx}"
                watchdog.on_waiting(action_id)
                watchdog.on_resolved(action_id)
                watchdog.poll()
        except Exception as exc:  # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{n}",)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(watchdog) == 0


def test_sync_escalation_records_and_clears_via_state() -> None:
    clock = _Clock()
    fired: list[dict[str, Any]] = []
    watchdog = WaitingEscalationWatchdog(
        sink=fired.append,
        policy=EscalationPolicy(timeout_seconds=60, repeat_after_seconds=60, max_reminders=2),
        clock=clock,
    )
    state = ComputerRouterState(escalation=watchdog)

    _sync_escalation(state, "action-1", "waiting_user")
    assert len(watchdog) == 1

    clock.advance(61)
    # poll is called inside _sync_escalation; the sink should have fired now.
    _sync_escalation(state, "action-1", "waiting_user")
    assert len(fired) == 1
    assert fired[0]["action_id"] == "action-1"

    _sync_escalation(state, "action-1", "done")
    assert len(watchdog) == 0


def test_sync_escalation_without_watchdog_is_noop() -> None:
    state = ComputerRouterState(escalation=None)
    _sync_escalation(state, "action-1", "waiting_user")
    assert True  # no exception is the contract

