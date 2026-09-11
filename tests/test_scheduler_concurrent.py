"""Implementation note."""

from __future__ import annotations

import threading
import time

import pytest

from runtime.adapters.scheduler import BackgroundRunner

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruction:
    def test_max_workers_must_be_positive(self):
        with pytest.raises(ValueError, match="max_workers"):
            BackgroundRunner(max_workers=0)
        with pytest.raises(ValueError, match="max_workers"):
            BackgroundRunner(max_workers=-1)

    def test_default_max_workers_is_1(self):
        r = BackgroundRunner()
        assert r.max_workers == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConcurrentExecution:
    def test_two_slow_callbacks_run_in_parallel(self):
        """Implementation note."""
        barrier = threading.Barrier(2, timeout=2.0)
        a_done = threading.Event()
        b_done = threading.Event()

        def slow_a():
            barrier.wait()  # Implementation note.
            time.sleep(0.08)
            a_done.set()

        def slow_b():
            barrier.wait()
            time.sleep(0.08)
            b_done.set()

        r = BackgroundRunner(max_workers=4)
        r.add_periodic("a", interval_s=5.0, callback=slow_a, run_on_start=True)
        r.add_periodic("b", interval_s=5.0, callback=slow_b, run_on_start=True)

        t0 = time.monotonic()
        r.start()
        try:
            assert a_done.wait(timeout=2.0)
            assert b_done.wait(timeout=2.0)
        finally:
            r.stop()
        elapsed = time.monotonic() - t0
        # Implementation note.
        assert elapsed < 0.7, f"too slow · not concurrent: {elapsed:.3f}s"

    def test_slow_callback_does_not_delay_others(self):
        """Implementation note."""
        fast_count = [0]
        fast_done_3 = threading.Event()
        slow_started = threading.Event()

        def fast():
            fast_count[0] += 1
            if fast_count[0] >= 3:
                fast_done_3.set()

        def slow():
            slow_started.set()
            time.sleep(0.3)

        r = BackgroundRunner(max_workers=4)
        r.add_periodic("slow", interval_s=5.0, callback=slow, run_on_start=True)
        r.add_periodic("fast", interval_s=0.05, callback=fast, run_on_start=True)

        r.start()
        try:
            assert slow_started.wait(timeout=1.0)
            # Implementation note.
            assert fast_done_3.wait(timeout=1.0), (
                f"fast only ran {fast_count[0]} times · slow is blocking"
            )
        finally:
            r.stop()
        # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestInFlightCounting:
    def test_in_flight_rises_and_falls(self):
        release = threading.Event()
        started_count = [0]
        started_count_lock = threading.Lock()

        def waiting():
            with started_count_lock:
                started_count[0] += 1
            release.wait(timeout=2.0)

        r = BackgroundRunner(max_workers=4)
        r.add_periodic("w", interval_s=0.05, callback=waiting, run_on_start=True)
        r.start()
        try:
            # Implementation note.
            for _ in range(50):
                if started_count[0] >= 2:
                    break
                time.sleep(0.02)
            assert r.stats()["w"].in_flight >= 2
            release.set()
        finally:
            r.stop()
        # Implementation note.
        assert r.stats()["w"].in_flight == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStopDrainsPool:
    def test_stop_waits_for_in_flight_callbacks(self):
        callback_completed = threading.Event()

        def slow():
            time.sleep(0.2)
            callback_completed.set()

        r = BackgroundRunner(max_workers=2)
        r.add_periodic("s", interval_s=5.0, callback=slow, run_on_start=True)
        r.start()
        # Implementation note.
        time.sleep(0.08)
        r.stop(timeout=2.0)
        # Implementation note.
        assert callback_completed.is_set()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrorIsolationInPool:
    def test_bad_callback_in_worker_does_not_crash_pool(self):
        ok_count = [0]
        ok_done_2 = threading.Event()

        def bad():
            raise RuntimeError("boom")

        def ok():
            ok_count[0] += 1
            if ok_count[0] >= 2:
                ok_done_2.set()

        r = BackgroundRunner(max_workers=4)
        r.add_periodic("bad", interval_s=0.04, callback=bad, run_on_start=True)
        r.add_periodic("ok", interval_s=0.04, callback=ok, run_on_start=True)
        r.start()
        try:
            assert ok_done_2.wait(timeout=2.0)
        finally:
            r.stop()
        st = r.stats()
        assert st["bad"].error_count >= 1
        assert "boom" in st["bad"].last_error
        assert st["ok"].success_count >= 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_serial_mode_preserves_ordering(self):
        """Implementation note."""
        calls: list[str] = []
        done = threading.Event()

        def a():
            calls.append("a-start")
            time.sleep(0.05)
            calls.append("a-end")
            if calls.count("a-end") >= 2:
                done.set()

        r = BackgroundRunner(max_workers=1)
        r.add_periodic("a", interval_s=0.02, callback=a, run_on_start=True)
        r.start()
        try:
            assert done.wait(timeout=3.0)
        finally:
            r.stop()
        # Implementation note.
        # Implementation note.
        for i in range(0, len(calls) - 1, 2):
            if i + 1 < len(calls):
                assert calls[i] == "a-start"
                assert calls[i + 1] == "a-end"

    def test_no_pool_allocated_in_serial_mode(self):
        r = BackgroundRunner(max_workers=1)
        r.start()
        try:
            # Implementation note.
            assert r._pool is None
        finally:
            r.stop()
