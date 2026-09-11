"""Implementation note."""

from __future__ import annotations

import threading
import time

import pytest

from runtime.adapters.scheduler import BackgroundRunner

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLifecycle:
    def test_initial_state_idle(self):
        r = BackgroundRunner()
        assert r.state == "idle"
        assert not r.is_running

    def test_start_transitions_running(self):
        r = BackgroundRunner()
        r.start()
        try:
            assert r.state == "running"
            assert r.is_running
        finally:
            r.stop()
        assert r.state == "stopped"
        assert not r.is_running

    def test_start_idempotent(self):
        r = BackgroundRunner()
        r.start()
        r.start()  # Implementation note.
        try:
            assert r.is_running
        finally:
            r.stop()

    def test_cannot_restart_after_stop(self):
        r = BackgroundRunner()
        r.start()
        r.stop()
        with pytest.raises(RuntimeError, match="cannot restart"):
            r.start()

    def test_context_manager(self):
        with BackgroundRunner() as r:
            assert r.is_running
        assert r.state == "stopped"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegistration:
    def test_duplicate_name_rejected(self):
        r = BackgroundRunner()
        r.add_periodic("a", 10.0, lambda: None)
        with pytest.raises(ValueError, match="duplicate"):
            r.add_periodic("a", 5.0, lambda: None)

    def test_zero_interval_rejected(self):
        r = BackgroundRunner()
        with pytest.raises(ValueError, match="interval_s"):
            r.add_periodic("a", 0, lambda: None)

    def test_negative_jitter_rejected(self):
        r = BackgroundRunner()
        with pytest.raises(ValueError, match="jitter_s"):
            r.add_periodic("a", 1.0, lambda: None, jitter_s=-0.1)

    def test_task_names_listed(self):
        r = BackgroundRunner()
        r.add_periodic("a", 10.0, lambda: None)
        r.add_periodic("b", 10.0, lambda: None)
        assert sorted(r.task_names()) == ["a", "b"]

    def test_remove_returns_true_when_found(self):
        r = BackgroundRunner()
        r.add_periodic("a", 10.0, lambda: None)
        assert r.remove("a") is True
        assert r.remove("a") is False
        assert r.task_names() == []

    def test_cannot_add_after_stop(self):
        r = BackgroundRunner()
        r.start()
        r.stop()
        with pytest.raises(RuntimeError, match="stopped"):
            r.add_periodic("a", 1.0, lambda: None)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestExecution:
    def test_run_on_start_triggers_immediately(self):
        ev = threading.Event()

        def cb():
            ev.set()

        r = BackgroundRunner()
        r.add_periodic("x", 60.0, cb, run_on_start=True)
        r.start()
        try:
            assert ev.wait(timeout=2.0), "run_on_start should fire < 2s"
        finally:
            r.stop()

    def test_periodic_repeats(self):
        calls: list[float] = []
        done = threading.Event()

        def cb():
            calls.append(time.monotonic())
            if len(calls) >= 3:
                done.set()

        r = BackgroundRunner()
        r.add_periodic("x", 0.05, cb, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0), f"should reach 3 calls, got {len(calls)}"
        finally:
            r.stop()
        assert len(calls) >= 3

    def test_multiple_tasks_independent(self):
        a_count = 0
        b_count = 0
        lock = threading.Lock()
        done = threading.Event()

        def cb_a():
            nonlocal a_count
            with lock:
                a_count += 1
                if a_count >= 2 and b_count >= 2:
                    done.set()

        def cb_b():
            nonlocal b_count
            with lock:
                b_count += 1
                if a_count >= 2 and b_count >= 2:
                    done.set()

        r = BackgroundRunner()
        r.add_periodic("a", 0.04, cb_a, run_on_start=True)
        r.add_periodic("b", 0.04, cb_b, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0)
        finally:
            r.stop()
        assert a_count >= 2
        assert b_count >= 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrorIsolation:
    def test_callback_exception_swallowed_and_logged(self, capsys):
        calls = 0
        ok_done = threading.Event()

        def bad():
            raise RuntimeError("boom")

        def ok():
            nonlocal calls
            calls += 1
            if calls >= 2:
                ok_done.set()

        r = BackgroundRunner()
        r.add_periodic("bad", 0.04, bad, run_on_start=True)
        r.add_periodic("ok", 0.04, ok, run_on_start=True)
        r.start()
        try:
            assert ok_done.wait(timeout=3.0)
        finally:
            r.stop()
        # Implementation note.
        assert calls >= 2

        # Implementation note.
        st = r.stats()
        assert st["bad"].error_count >= 1
        assert "boom" in st["bad"].last_error

    def test_bad_task_continues_being_scheduled(self):
        attempts = 0
        done = threading.Event()

        def bad():
            nonlocal attempts
            attempts += 1
            if attempts >= 3:
                done.set()
            raise RuntimeError("always fails")

        r = BackgroundRunner()
        r.add_periodic("bad", 0.04, bad, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0)
        finally:
            r.stop()
        # Implementation note.
        assert attempts >= 3


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStats:
    def test_success_count_tracks_runs(self):
        ran = threading.Event()
        count = [0]

        def cb():
            count[0] += 1
            if count[0] >= 2:
                ran.set()

        r = BackgroundRunner()
        r.add_periodic("x", 0.04, cb, run_on_start=True)
        r.start()
        try:
            assert ran.wait(timeout=3.0)
        finally:
            r.stop()
        st = r.stats()["x"]
        assert st.success_count >= 2
        assert st.error_count == 0
        assert st.last_run_ts is not None
        assert st.last_duration_s is not None
        assert st.last_error is None

    def test_next_run_ts_advances(self):
        done = threading.Event()
        count = [0]

        def cb():
            count[0] += 1
            if count[0] >= 1:
                done.set()

        r = BackgroundRunner()
        r.add_periodic("x", 0.05, cb, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0)
        finally:
            r.stop()
        st = r.stats()["x"]
        assert st.last_run_ts is not None
        assert st.next_run_ts is not None
        # Implementation note.
        assert st.next_run_ts > st.last_run_ts


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStopResponsiveness:
    def test_stop_returns_quickly_without_tasks(self):
        r = BackgroundRunner()
        r.start()
        t0 = time.monotonic()
        r.stop(timeout=2.0)
        elapsed = time.monotonic() - t0
        # Implementation note.
        assert elapsed < 1.5

    def test_stop_waits_for_current_callback(self):
        """Implementation note."""
        inside = threading.Event()
        release = threading.Event()

        def slow():
            inside.set()
            release.wait(timeout=2.0)

        r = BackgroundRunner()
        r.add_periodic("slow", 0.01, slow, run_on_start=True)
        r.start()
        assert inside.wait(timeout=2.0)
        # Implementation note.
        release.set()
        r.stop(timeout=3.0)
        assert not r.is_running


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIntegrationPatterns:
    def test_schedules_learn_from_journal_pattern(self):
        """Implementation note."""
        from runtime.memory.journal import InMemoryJournal
        from runtime.safety.recovery import RuleExtractor

        journal = InMemoryJournal()
        call_count = [0]
        done = threading.Event()

        def reflect_tick():
            # Implementation note.
            RuleExtractor(journal).extract()
            call_count[0] += 1
            if call_count[0] >= 3:
                done.set()

        r = BackgroundRunner()
        r.add_periodic("reflect_job", 0.04, reflect_tick, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0)
        finally:
            r.stop()
        assert call_count[0] >= 3
        st = r.stats()["reflect_job"]
        assert st.success_count >= 3
        assert st.error_count == 0
