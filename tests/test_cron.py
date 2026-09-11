"""Implementation note."""

from __future__ import annotations

import threading
import time
from datetime import datetime

import pytest

from runtime.adapters.scheduler import (
    BackgroundRunner,
    CronExpression,
    CronParseError,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestParseSuccess:
    def test_all_wildcards(self):
        c = CronExpression.parse("* * * * *")
        assert len(c.minutes) == 60
        assert len(c.hours) == 24
        assert len(c.days) == 31
        assert len(c.months) == 12
        assert len(c.weekdays) == 7

    def test_fixed_values(self):
        c = CronExpression.parse("0 9 * * 1")
        assert c.minutes == frozenset({0})
        assert c.hours == frozenset({9})
        assert c.weekdays == frozenset({1})

    def test_step_from_wildcard(self):
        c = CronExpression.parse("*/15 * * * *")
        assert c.minutes == frozenset({0, 15, 30, 45})

    def test_step_from_range(self):
        c = CronExpression.parse("0-30/10 * * * *")
        assert c.minutes == frozenset({0, 10, 20, 30})

    def test_step_from_single(self):
        """Implementation note."""
        c = CronExpression.parse("5/10 * * * *")
        assert c.minutes == frozenset({5, 15, 25, 35, 45, 55})

    def test_list(self):
        c = CronExpression.parse("0,15,30,45 * * * *")
        assert c.minutes == frozenset({0, 15, 30, 45})

    def test_range(self):
        c = CronExpression.parse("0 9-17 * * *")
        assert c.hours == frozenset(range(9, 18))

    def test_complex_mixed(self):
        c = CronExpression.parse("0 0 1,15 * 1-5")
        assert c.minutes == frozenset({0})
        assert c.hours == frozenset({0})
        assert c.days == frozenset({1, 15})
        assert c.weekdays == frozenset({1, 2, 3, 4, 5})


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestParseFailure:
    def test_too_few_fields(self):
        with pytest.raises(CronParseError, match="5 fields"):
            CronExpression.parse("* * *")

    def test_too_many_fields(self):
        with pytest.raises(CronParseError, match="5 fields"):
            CronExpression.parse("* * * * * *")

    def test_out_of_range_minute(self):
        with pytest.raises(CronParseError, match="out of range"):
            CronExpression.parse("60 * * * *")

    def test_out_of_range_hour(self):
        with pytest.raises(CronParseError, match="out of range"):
            CronExpression.parse("* 24 * * *")

    def test_out_of_range_day(self):
        with pytest.raises(CronParseError, match="out of range"):
            CronExpression.parse("* * 32 * *")

    def test_inverted_range(self):
        with pytest.raises(CronParseError, match="inverted"):
            CronExpression.parse("30-5 * * * *")

    def test_bad_step(self):
        with pytest.raises(CronParseError, match="bad step"):
            CronExpression.parse("*/abc * * * *")

    def test_zero_step(self):
        with pytest.raises(CronParseError, match="step must be > 0"):
            CronExpression.parse("*/0 * * * *")

    def test_named_month_rejected(self):
        with pytest.raises(CronParseError, match="unsupported"):
            CronExpression.parse("0 0 * JAN *")


# ═══════════════════════════════════════════════════════════
# matches()
# ═══════════════════════════════════════════════════════════


class TestMatches:
    def test_wildcard_matches_all(self):
        c = CronExpression.parse("* * * * *")
        assert c.matches(datetime(2026, 4, 18, 12, 34))
        assert c.matches(datetime(2030, 1, 1, 0, 0))

    def test_specific_time(self):
        c = CronExpression.parse("30 9 * * *")
        assert c.matches(datetime(2026, 1, 1, 9, 30))
        assert not c.matches(datetime(2026, 1, 1, 9, 31))
        assert not c.matches(datetime(2026, 1, 1, 10, 30))

    def test_weekday_monday(self):
        """Implementation note."""
        c = CronExpression.parse("0 9 * * 1")
        assert c.matches(datetime(2026, 4, 20, 9, 0))  # Implementation note.
        assert not c.matches(datetime(2026, 4, 21, 9, 0))  # Implementation note.

    def test_weekday_sunday(self):
        """Implementation note."""
        c = CronExpression.parse("0 9 * * 0")
        assert c.matches(datetime(2026, 4, 19, 9, 0))
        assert not c.matches(datetime(2026, 4, 20, 9, 0))

    def test_weekday_range_workdays(self):
        """Implementation note."""
        c = CronExpression.parse("0 9 * * 1-5")
        for d in range(20, 25):  # Implementation note.
            assert c.matches(datetime(2026, 4, d, 9, 0))
        assert not c.matches(datetime(2026, 4, 25, 9, 0))  # Implementation note.
        assert not c.matches(datetime(2026, 4, 19, 9, 0))  # Implementation note.

    def test_every_15_minutes(self):
        c = CronExpression.parse("*/15 * * * *")
        for m in (0, 15, 30, 45):
            assert c.matches(datetime(2026, 4, 18, 12, m))
        for m in (1, 14, 16, 29, 44, 59):
            assert not c.matches(datetime(2026, 4, 18, 12, m))


# ═══════════════════════════════════════════════════════════
# next_after()
# ═══════════════════════════════════════════════════════════


class TestNextAfter:
    def test_every_minute(self):
        c = CronExpression.parse("* * * * *")
        now = datetime(2026, 4, 18, 12, 34, 56)
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 4, 18, 12, 35)
        assert nxt.second == 0

    def test_next_matches_in_one_minute(self):
        c = CronExpression.parse("0 * * * *")  # Implementation note.
        now = datetime(2026, 4, 18, 12, 59)
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 4, 18, 13, 0)

    def test_wrap_to_next_day(self):
        c = CronExpression.parse("0 9 * * *")
        now = datetime(2026, 4, 18, 10, 0)  # Implementation note.
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 4, 19, 9, 0)

    def test_skip_weekend(self):
        """Implementation note."""
        c = CronExpression.parse("0 9 * * 1-5")
        now = datetime(2026, 4, 24, 10, 0)  # Implementation note.
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 4, 27, 9, 0)  # Implementation note.
        assert nxt.weekday() == 0  # Python Monday

    def test_wrap_to_next_month(self):
        c = CronExpression.parse("0 0 1 * *")  # Implementation note.
        now = datetime(2026, 4, 18, 0, 0)
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 5, 1, 0, 0)

    def test_wrap_to_next_year(self):
        c = CronExpression.parse("0 0 1 1 *")  # Implementation note.
        now = datetime(2026, 4, 1, 0, 0)
        nxt = c.next_after(now)
        assert nxt == datetime(2027, 1, 1, 0, 0)

    def test_every_15_minutes_alignment(self):
        c = CronExpression.parse("*/15 * * * *")
        now = datetime(2026, 4, 18, 12, 7)
        nxt = c.next_after(now)
        assert nxt == datetime(2026, 4, 18, 12, 15)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunnerIntegration:
    def test_add_cron_rejects_bad_expr(self):
        r = BackgroundRunner()
        with pytest.raises(CronParseError):
            r.add_cron("bad", "* * *", lambda: None)

    def test_add_cron_task_registered(self):
        r = BackgroundRunner()
        task = r.add_cron("hourly", "0 * * * *", lambda: None)
        assert task.is_cron
        assert task.cron_expr is not None
        assert "hourly" in r.task_names()

    def test_add_cron_duplicate_rejected(self):
        r = BackgroundRunner()
        r.add_cron("x", "* * * * *", lambda: None)
        with pytest.raises(ValueError, match="duplicate"):
            r.add_cron("x", "0 9 * * *", lambda: None)

    def test_run_on_start_fires_immediately(self):
        """Implementation note."""
        ev = threading.Event()
        r = BackgroundRunner()
        r.add_cron(
            "noon",
            "0 12 * * *",  # Implementation note.
            lambda: ev.set(),
            run_on_start=True,
        )
        r.start()
        try:
            assert ev.wait(timeout=2.0)
        finally:
            r.stop()

    def test_cron_task_reschedules_after_run(self):
        """Implementation note."""
        done = threading.Event()
        r = BackgroundRunner()
        r.add_cron(
            "anyminute",
            "* * * * *",  # Implementation note.
            lambda: done.set(),
            run_on_start=True,
        )
        r.start()
        try:
            assert done.wait(timeout=2.0)
        finally:
            r.stop()
        # Implementation note.
        task_stats = r.stats()["anyminute"]
        now = time.monotonic()
        assert task_stats.next_run_ts is not None
        assert task_stats.next_run_ts > now

    def test_cron_and_periodic_coexist(self):
        """Implementation note."""
        r = BackgroundRunner()
        r.add_periodic("p", 0.05, lambda: None)
        r.add_cron("c", "* * * * *", lambda: None)
        assert set(r.task_names()) == {"p", "c"}

    def test_cron_task_zero_interval_ok(self):
        """Implementation note."""
        r = BackgroundRunner()
        # Implementation note.
        r.add_cron("c", "0 9 * * *", lambda: None)

    def test_add_periodic_zero_interval_still_rejected(self):
        """Implementation note."""
        r = BackgroundRunner()
        with pytest.raises(ValueError, match="interval_s"):
            r.add_periodic("p", 0, lambda: None)
