"""Implementation note."""

from __future__ import annotations

import threading

import pytest

from runtime.adapters.scheduler import BackgroundRunner
from runtime.core.hearts import Hearts, HeartsSnapshot
from runtime.safety.budget_breaker import CircuitBreaker

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
def patched_breaker_time(clock, monkeypatch):
    monkeypatch.setattr(
        "runtime.safety.budget_breaker.breaker.time.monotonic",
        clock,
    )
    return clock


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstructor:
    def test_default_constructs(self):
        h = Hearts()
        assert isinstance(h.systemic, BackgroundRunner)
        assert h.branchial == {}
        assert h.channels() == []
        assert not h.is_running

    def test_with_systemic_and_branchial(self, patched_breaker_time):
        runner = BackgroundRunner()
        breaker = CircuitBreaker(window_seconds=60.0)
        h = Hearts(
            systemic=runner,
            branchial={"llm": breaker},
        )
        assert h.systemic is runner
        assert "llm" in h.branchial
        assert h.channels() == ["llm"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBranchialRegistration:
    def test_register_new_channel(self, patched_breaker_time):
        h = Hearts()
        breaker = CircuitBreaker(window_seconds=60.0)
        h.register_branchial("llm", breaker)
        assert h.dispatch_io("llm") is breaker

    def test_duplicate_rejected(self, patched_breaker_time):
        h = Hearts()
        h.register_branchial("llm", CircuitBreaker(window_seconds=60.0))
        with pytest.raises(ValueError, match="duplicate"):
            h.register_branchial("llm", CircuitBreaker(window_seconds=60.0))

    def test_empty_name_rejected(self, patched_breaker_time):
        h = Hearts()
        with pytest.raises(ValueError, match="non-empty"):
            h.register_branchial("", CircuitBreaker(window_seconds=60.0))

    def test_dispatch_unknown_channel(self, patched_breaker_time):
        h = Hearts()
        h.register_branchial("llm", CircuitBreaker(window_seconds=60.0))
        with pytest.raises(KeyError, match="mcp"):
            h.dispatch_io("mcp")

    def test_channels_sorted(self, patched_breaker_time):
        h = Hearts()
        h.register_branchial("z", CircuitBreaker(window_seconds=60.0))
        h.register_branchial("a", CircuitBreaker(window_seconds=60.0))
        h.register_branchial("m", CircuitBreaker(window_seconds=60.0))
        assert h.channels() == ["a", "m", "z"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLifecycle:
    def test_start_and_stop(self):
        h = Hearts()
        h.start()
        try:
            assert h.is_running
        finally:
            h.stop()
        assert not h.is_running

    def test_stop_idempotent_when_not_running(self):
        h = Hearts()
        # Implementation note.
        h.stop()
        assert not h.is_running

    def test_context_manager(self):
        with Hearts() as h:
            assert h.is_running
        assert not h.is_running

    def test_context_manager_stops_on_exception(self):
        h = Hearts()
        with pytest.raises(RuntimeError), h:
            assert h.is_running
            raise RuntimeError("boom")
        assert not h.is_running

    def test_systemic_tasks_still_work(self):
        """Implementation note."""
        ran = threading.Event()

        def cb():
            ran.set()

        runner = BackgroundRunner()
        runner.add_periodic("x", 60.0, cb, run_on_start=True)
        h = Hearts(systemic=runner)
        with h:
            assert ran.wait(timeout=2.0)


# ═══════════════════════════════════════════════════════════
# snapshot
# ═══════════════════════════════════════════════════════════


class TestSnapshot:
    def test_empty_snapshot(self):
        h = Hearts()
        snap = h.snapshot()
        assert isinstance(snap, HeartsSnapshot)
        assert snap.systemic["is_running"] is False
        assert snap.branchial == {}
        # Implementation note.
        assert snap.healthy is False

    def test_snapshot_reflects_running(self):
        h = Hearts()
        with h:
            snap = h.snapshot()
        assert snap.systemic["state"] == "running"  # Implementation note.
        # Implementation note.

    def test_snapshot_includes_task_stats(self):
        ran = threading.Event()
        count = [0]

        def cb():
            count[0] += 1
            if count[0] >= 1:
                ran.set()

        runner = BackgroundRunner()
        runner.add_periodic("probe", 0.05, cb, run_on_start=True)
        h = Hearts(systemic=runner)
        with h:
            assert ran.wait(timeout=3.0)
            snap = h.snapshot()
        assert "probe" in snap.systemic["tasks"]
        assert snap.systemic["tasks"]["probe"]["success_count"] >= 1
        assert snap.systemic["tasks"]["probe"]["error_count"] == 0

    def test_snapshot_includes_branchial(self, patched_breaker_time):
        breaker = CircuitBreaker(window_seconds=60.0)
        h = Hearts(branchial={"llm": breaker})
        snap = h.snapshot()
        assert "llm" in snap.branchial
        assert snap.branchial["llm"]["state"] == "closed"

    def test_to_dict_is_serializable(self, patched_breaker_time):
        import json

        breaker = CircuitBreaker(window_seconds=60.0)
        h = Hearts(branchial={"llm": breaker})
        with h:
            snap = h.snapshot()
        # Implementation note.
        encoded = json.dumps(snap.to_dict(), default=str)
        assert "llm" in encoded
        assert "healthy" in encoded


# ═══════════════════════════════════════════════════════════
# healthy()
# ═══════════════════════════════════════════════════════════


class TestHealthy:
    def test_not_running_is_unhealthy(self):
        h = Hearts()
        assert h.healthy() is False

    def test_running_with_no_branchial_is_healthy(self):
        with Hearts() as h:
            assert h.healthy() is True

    def test_running_with_closed_breaker_is_healthy(self, patched_breaker_time):
        h = Hearts(branchial={"llm": CircuitBreaker(window_seconds=60.0)})
        with h:
            assert h.healthy() is True

    def test_breaker_open_makes_unhealthy(self, patched_breaker_time):
        """Implementation note."""
        breaker = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=1,
            cooldown_seconds=10.0,
        )
        h = Hearts(branchial={"llm": breaker})
        with h:
            assert h.healthy() is True
            # Implementation note.
            breaker.check()
            breaker.record(success=False)
            breaker.check()
            breaker.record(success=False)
            assert breaker.state == "open"
            assert h.healthy() is False

            snap = h.snapshot()
            assert snap.healthy is False
            assert snap.branchial["llm"]["state"] == "open"

    def test_any_open_breaker_trips_healthy(self, patched_breaker_time):
        """Implementation note."""
        b_llm = CircuitBreaker(window_seconds=60.0)
        b_mcp = CircuitBreaker(
            window_seconds=60.0,
            max_errors_per_window=0,
            cooldown_seconds=5.0,
        )
        h = Hearts(branchial={"llm": b_llm, "mcp": b_mcp})
        with h:
            # Implementation note.
            b_mcp.check()
            b_mcp.record(success=False)
            assert b_mcp.state == "open"
            assert b_llm.state == "closed"
            assert h.healthy() is False
