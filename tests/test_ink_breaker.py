"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.safety.budget_breaker import BreakerModelRouter, CircuitBreaker, CircuitOpen
from runtime.sensing.model_router import (
    Message,
    MockModelRouter,
    ModelRequest,
    ModelResponse,
    ModelRouter,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def patched_time(clock, monkeypatch):
    """Implementation note."""
    monkeypatch.setattr(
        "runtime.safety.budget_breaker.breaker.time.monotonic",
        clock,
    )
    return clock


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestClosedState:
    def test_initial_closed(self, patched_time):
        b = CircuitBreaker(window_seconds=60.0)
        assert b.state == "closed"
        assert b.check() == "closed"

    def test_record_stays_closed_under_threshold(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_calls_per_window=10,
        )
        for _ in range(5):
            b.check()
            b.record(success=True)
        assert b.state == "closed"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTrips:
    def test_max_calls_trip(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_calls_per_window=3,
            cooldown_seconds=30.0,
        )
        # Implementation note.
        for _ in range(4):
            b.check()
            b.record(success=True)
        assert b.state == "open"
        # Implementation note.
        with pytest.raises(CircuitOpen):
            b.check()

    def test_max_cost_trip(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_cost_usd_per_window=0.10,
        )
        b.check()
        b.record(success=True, cost_usd=0.15)  # Implementation note.
        assert b.state == "open"

    def test_max_errors_trip(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=2,
        )
        for _ in range(3):
            b.check()
            b.record(success=False)
        assert b.state == "open"

    def test_circuit_open_carries_reason(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        b.check()
        b.record(success=False)
        try:
            b.check()
        except CircuitOpen as e:
            assert "max_errors" in e.reason
            assert e.cooldown_seconds == 5.0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSlidingWindow:
    def test_old_events_pruned(self, patched_time):
        b = CircuitBreaker(
            window_seconds=10.0,
            max_calls_per_window=3,
        )
        for _ in range(3):
            b.check()
            b.record(success=True)
        assert b.state == "closed"

        # Implementation note.
        patched_time.advance(11.0)

        # Implementation note.
        b.check()
        b.record(success=True)
        assert b.state == "closed"
        assert b.snapshot()["calls_in_window"] == 1


# ═══════════════════════════════════════════════════════════
# cooldown → half_open → closed / open
# ═══════════════════════════════════════════════════════════


class TestHalfOpen:
    def test_cooldown_transitions_to_half_open(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        b.check()
        b.record(success=False)
        assert b.state == "open"

        # Implementation note.
        patched_time.advance(4.0)
        with pytest.raises(CircuitOpen):
            b.check()

        # Implementation note.
        patched_time.advance(2.0)
        state = b.check()
        assert state == "half_open"

    def test_half_open_probe_success_resets(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        b.check()
        b.record(success=False)
        patched_time.advance(6.0)
        b.check()  # half_open
        b.record(success=True)  # Implementation note.
        assert b.state == "closed"
        assert b.snapshot()["calls_in_window"] >= 1

    def test_half_open_probe_failure_re_opens(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        b.check()
        b.record(success=False)
        patched_time.advance(6.0)
        b.check()  # Implementation note.
        b.record(success=False)  # Implementation note.
        assert b.state == "open"
        # Implementation note.
        with pytest.raises(CircuitOpen):
            b.check()

    def test_half_open_rejects_concurrent_probes(self, patched_time):
        """Implementation note."""
        b = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        b.check()
        b.record(success=False)
        patched_time.advance(6.0)
        b.check()  # Implementation note.
        with pytest.raises(CircuitOpen, match="probe in flight"):
            b.check()  # Implementation note.


# ═══════════════════════════════════════════════════════════
# reset
# ═══════════════════════════════════════════════════════════


class TestReset:
    def test_reset_clears_state(self, patched_time):
        b = CircuitBreaker(
            window_seconds=60.0,
            max_calls_per_window=1,
        )
        b.check()
        b.record(success=True)
        b.check()
        b.record(success=True)
        assert b.state == "open"

        b.reset()
        assert b.state == "closed"
        assert b.snapshot()["calls_in_window"] == 0


# ═══════════════════════════════════════════════════════════
# BreakerModelRouter
# ═══════════════════════════════════════════════════════════


class _FailingInner(ModelRouter):
    def __init__(self) -> None:
        self.call_count = 0

    def call(self, request: ModelRequest) -> ModelResponse:
        self.call_count += 1
        raise RuntimeError("always fails")


def _req() -> ModelRequest:
    return ModelRequest(
        model="x",
        messages=[Message(role="user", content="hi")],
    )


class TestBreakerModelRouter:
    def test_normal_pass_through(self, patched_time):
        inner = MockModelRouter(response="ok")
        breaker = CircuitBreaker(window_seconds=60.0)
        router = BreakerModelRouter(inner=inner, breaker=breaker)

        resp = router.call(_req())
        assert resp.text == "ok"
        assert breaker.state == "closed"

    def test_inner_failures_trip_breaker(self, patched_time):
        inner = _FailingInner()
        breaker = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=2,
            cooldown_seconds=10.0,
        )
        router = BreakerModelRouter(inner=inner, breaker=breaker)

        # Implementation note.
        for _ in range(3):
            with pytest.raises(RuntimeError, match="always fails"):
                router.call(_req())
        assert breaker.state == "open"

        # Implementation note.
        with pytest.raises(CircuitOpen):
            router.call(_req())
        assert inner.call_count == 3  # Implementation note.

    def test_cost_accumulates_from_response(self, patched_time):
        inner = MockModelRouter(response="x" * 100)
        breaker = CircuitBreaker(
            window_seconds=60.0,
            max_cost_usd_per_window=1e-5,
            cooldown_seconds=10.0,
        )
        router = BreakerModelRouter(inner=inner, breaker=breaker)

        # Implementation note.
        # Implementation note.
        router.call(_req())
        snap = breaker.snapshot()
        assert snap["cost_in_window_usd"] > 0
        # Implementation note.
        with pytest.raises(CircuitOpen):
            router.call(_req())

    def test_half_open_via_router(self, patched_time):
        """Implementation note."""
        inner = MockModelRouter(response="ok")
        breaker = CircuitBreaker(
            window_seconds=60.0,
            max_calls_per_window=1,
            cooldown_seconds=5.0,
        )
        router = BreakerModelRouter(inner=inner, breaker=breaker)

        router.call(_req())
        router.call(_req())  # Implementation note.
        assert breaker.state == "open"
        with pytest.raises(CircuitOpen):
            router.call(_req())

        # Implementation note.
        patched_time.advance(6.0)
        # Implementation note.
        resp = router.call(_req())
        assert resp.text == "ok"
        assert breaker.state == "closed"
