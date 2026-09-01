"""Tests for pause_control enhancements: wall-time limits, GC, deduplication."""

import time
from pathlib import Path

import pytest

from runtime.core.cerebrum.pause_control import ActiveTask, PauseController


@pytest.fixture
def temp_store(tmp_path: Path) -> Path:
    return tmp_path / "pause_test.json"


@pytest.fixture
def ctrl(temp_store: Path) -> PauseController:
    return PauseController(store_path=temp_store, autoload=False)


def test_active_task_wall_time_check():
    """ActiveTask.is_wall_time_exceeded() detects wall-clock timeout."""
    task = ActiveTask(
        task_id="t1",
        started_at=time.time() - 100,
        max_wall_time_seconds=50,
    )
    assert task.is_wall_time_exceeded()

    task2 = ActiveTask(
        task_id="t2",
        started_at=time.time(),
        max_wall_time_seconds=100,
    )
    assert not task2.is_wall_time_exceeded()

    # Zero means no limit
    task3 = ActiveTask(
        task_id="t3",
        started_at=time.time() - 1000,
        max_wall_time_seconds=0,
    )
    assert not task3.is_wall_time_exceeded()


def test_register_active_with_wall_time_limit(ctrl: PauseController):
    """register_active accepts max_wall_time_seconds parameter."""
    ctrl.register_active(
        "t1",
        thread_id="th1",
        max_wall_time_seconds=300.0,
    )
    active = ctrl.list_active()
    assert len(active) == 1
    assert active[0].task_id == "t1"
    assert active[0].max_wall_time_seconds == 300.0


def test_check_active_task_limits_wall_time(ctrl: PauseController):
    """check_active_task_limits detects wall-time exceeded."""
    # Register a task that started 200s ago with 100s limit
    ctrl.register_active(
        "t1",
        thread_id="th1",
        max_wall_time_seconds=100.0,
    )
    # Manually set started_at to simulate old task
    ctrl._active["t1"].started_at = time.time() - 200

    exceeded, reason = ctrl.check_active_task_limits("t1")
    assert exceeded
    assert reason == "wall_time_limit"

    # Task within limit
    ctrl.register_active(
        "t2",
        thread_id="th2",
        max_wall_time_seconds=1000.0,
    )
    exceeded, reason = ctrl.check_active_task_limits("t2")
    assert not exceeded
    assert reason == ""


def test_gc_stale_active_tasks(ctrl: PauseController):
    """Stale active tasks older than _ACTIVE_TASK_TTL_SECONDS are GC'd."""
    # Register a fresh task
    ctrl.register_active("t1", thread_id="th1")

    # Register a stale task (simulate 25 hours old)
    ctrl.register_active("t2", thread_id="th2")
    ctrl._active["t2"].started_at = time.time() - (25 * 3600)

    # list_active triggers GC
    active = ctrl.list_active()
    task_ids = {t.task_id for t in active}

    assert "t1" in task_ids
    assert "t2" not in task_ids  # Should be GC'd


def test_request_pause_deduplication(ctrl: PauseController):
    """Requesting pause for same task_id updates existing record."""
    # First pause request
    req1 = ctrl.request_pause(
        "t1",
        reason="user_request",
        requested_by="user1",
        note="first pause",
        thread_id="th1",
    )
    assert req1.task_id == "t1"
    assert req1.note == "first pause"

    # Second pause request for same task_id should update, not duplicate
    req2 = ctrl.request_pause(
        "t1",
        reason="user_request",
        requested_by="user2",
        note="second pause",
        thread_id="th1",
    )
    assert req2.task_id == "t1"
    assert req2.note == "second pause"

    # Should have only one pending record
    pending = ctrl.list_pending()
    assert len(pending) == 1
    assert pending[0].task_id == "t1"
    assert pending[0].note == "second pause"


def test_request_pause_deduplication_preserves_timestamp(ctrl: PauseController):
    """Deduplication preserves original requested_at for TTL GC."""
    req1 = ctrl.request_pause(
        "t1",
        reason="user_request",
        requested_by="user1",
        note="first",
        thread_id="th1",
    )
    original_ts = req1.requested_at

    time.sleep(0.1)  # Ensure time advances

    req2 = ctrl.request_pause(
        "t1",
        reason="user_request",
        requested_by="user2",
        note="second",
        thread_id="th1",
    )

    # Timestamp should be preserved from original request
    assert req2.requested_at == original_ts


def test_request_pause_different_tasks_no_dedup(ctrl: PauseController):
    """Different task_ids don't get deduplicated."""
    ctrl.request_pause("t1", note="first", thread_id="th1")
    ctrl.request_pause("t2", note="second", thread_id="th2")

    pending = ctrl.list_pending()
    assert len(pending) == 2
    task_ids = {r.task_id for r in pending}
    assert task_ids == {"t1", "t2"}


def test_active_task_to_dict_includes_wall_time(ctrl: PauseController):
    """ActiveTask.to_dict() includes max_wall_time_seconds."""
    ctrl.register_active(
        "t1",
        max_wall_time_seconds=600.0,
    )
    active = ctrl.list_active()
    d = active[0].to_dict()
    assert "max_wall_time_seconds" in d
    assert d["max_wall_time_seconds"] == 600.0


def test_active_task_separates_current_context_from_cumulative_accounting(
    ctrl: PauseController,
):
    ctrl.register_active("t-context", max_tokens=150_000)
    ctrl.update_active_usage(
        "t-context",
        tokens_delta=29_000,
        input_tokens_delta=27_500,
        output_tokens_delta=1_500,
        cache_read_tokens_delta=8_000,
        current_context_tokens=27_500,
        context_capacity_tokens=100_000,
    )
    ctrl.update_active_usage(
        "t-context",
        tokens_delta=31_000,
        input_tokens_delta=29_000,
        output_tokens_delta=2_000,
        current_context_tokens=29_000,
        context_capacity_tokens=100_000,
    )

    task = ctrl.list_active()[0]
    rendered = task.to_dict()
    assert rendered["tokens_spent"] == 60_000
    assert rendered["input_tokens_spent"] == 56_500
    assert rendered["output_tokens_spent"] == 3_500
    assert rendered["cache_read_tokens"] == 8_000
    assert rendered["current_context_tokens"] == 29_000
    assert rendered["context_utilization"] == pytest.approx(0.29)


def test_resume_grant_extends_live_accounting_limits(ctrl: PauseController):
    ctrl.register_active("t-grant", max_tokens=150_000, max_usd=3.0)

    task = ctrl.extend_active_limits("t-grant", extra_tokens=100_000, extra_usd=1.5)

    assert task is not None
    assert task.max_tokens == 250_000
    assert task.max_usd == pytest.approx(4.5)


def test_resume_registration_applies_every_grant_dimension():
    from runtime.core.cerebrum.pause_control import get_pause_controller
    from runtime.core.cerebrum.react_resume import _resume_or_register_turn
    from runtime.platform.models import ParsedIntent

    controller = get_pause_controller()
    task_id = "resume-grant-all-dimensions"
    controller.clear(task_id)
    controller.unregister_active(task_id)
    controller.set_grant(
        task_id,
        extra_iterations=15,
        extra_tokens=100_000,
        extra_usd=1.25,
    )
    intent = ParsedIntent(
        raw="继续",
        intent_type="task",
        normalized_goal="继续",
        user_context={},
    )
    try:
        resumed = _resume_or_register_turn(
            object(),
            intent,
            None,
            resume_task_id=task_id,
            react_task_id=task_id,
            thread_id="thread-grant",
            max_iterations=30,
            active_max_tokens_budget=150_000,
            active_max_usd_budget=3.0,
            messages=[],
        )

        active = next(task for task in controller.list_active() if task.task_id == task_id)
        assert resumed.max_iterations == 45
        assert active.max_iterations == 45
        assert active.max_tokens == 250_000
        assert active.max_usd == pytest.approx(4.25)
    finally:
        controller.clear(task_id)
        controller.unregister_active(task_id)


def test_check_active_task_limits_unknown_task(ctrl: PauseController):
    """check_active_task_limits returns False for unknown task."""
    exceeded, reason = ctrl.check_active_task_limits("unknown")
    assert not exceeded
    assert reason == ""


def test_gc_preserves_fresh_active_tasks(ctrl: PauseController):
    """GC only removes stale tasks, not fresh ones."""
    # Register multiple fresh tasks
    for i in range(5):
        ctrl.register_active(f"t{i}", thread_id=f"th{i}")

    active = ctrl.list_active()
    assert len(active) == 5

    # All should still be present after GC
    active_after = ctrl.list_active()
    assert len(active_after) == 5


# ═══════════════════════════════════════════════════════════
# Audit T-05: mode-graded wall-clock hard cap
# ═══════════════════════════════════════════════════════════


def test_turn_wall_time_cap_defaults(monkeypatch):
    from runtime.core.cerebrum.pause_control import (
        DEFAULT_TURN_WALL_TIME_CAP_LONG_HORIZON_S,
        DEFAULT_TURN_WALL_TIME_CAP_S,
        turn_wall_time_cap_s,
    )

    monkeypatch.delenv("ECHO_TURN_WALL_TIME_CAP_S", raising=False)
    assert turn_wall_time_cap_s() == float(DEFAULT_TURN_WALL_TIME_CAP_S)
    assert turn_wall_time_cap_s(goal_mode=True) == float(DEFAULT_TURN_WALL_TIME_CAP_LONG_HORIZON_S)
    assert turn_wall_time_cap_s(research_mode=True) == float(
        DEFAULT_TURN_WALL_TIME_CAP_LONG_HORIZON_S
    )
    assert turn_wall_time_cap_s(swarm_mode=True) == float(DEFAULT_TURN_WALL_TIME_CAP_LONG_HORIZON_S)
    assert turn_wall_time_cap_s(goal_mode=True, research_mode=True) == float(
        DEFAULT_TURN_WALL_TIME_CAP_LONG_HORIZON_S
    )


def test_turn_wall_time_cap_env_override(monkeypatch):
    from runtime.core.cerebrum.pause_control import turn_wall_time_cap_s

    monkeypatch.setenv("ECHO_TURN_WALL_TIME_CAP_S", "3600")
    assert turn_wall_time_cap_s() == 3600.0
    assert turn_wall_time_cap_s(goal_mode=True) == 3600.0  # env wins over mode grading

    monkeypatch.setenv("ECHO_TURN_WALL_TIME_CAP_S", "0")
    assert turn_wall_time_cap_s() == 0.0  # 0 disables the cap


def test_resume_or_register_turn_forwards_wall_time_cap(monkeypatch):
    """The production registration point passes the cap through to
    register_active, so the per-iteration guard can auto-pause (T-05)."""
    from runtime.core.cerebrum import react_resume
    from runtime.core.cerebrum.pause_control import turn_wall_time_cap_s

    recorded: dict = {}

    class _StubPause:
        def register_active(self, task_id, **kwargs):
            recorded["kwargs"] = kwargs

        def consume_grant(self, task_id):
            return {}

        def clear(self, task_id):
            pass

    import runtime.core.cerebrum.pause_control as pause_ctrl_mod

    monkeypatch.setattr(pause_ctrl_mod, "get_pause_controller", lambda: _StubPause())

    class _Intent:
        user_context = {}

    turn = react_resume._resume_or_register_turn(
        stack=object(),
        intent=_Intent(),
        agent=object(),
        resume_task_id=None,
        react_task_id="task-t05",
        thread_id="th",
        max_iterations=30,
        active_max_tokens_budget=100_000,
        active_max_usd_budget=1.0,
        max_wall_time_seconds=turn_wall_time_cap_s(),
        messages=[],
    )
    assert recorded["kwargs"]["max_wall_time_seconds"] == float(
        __import__(
            "runtime.core.cerebrum.pause_control", fromlist=["DEFAULT_TURN_WALL_TIME_CAP_S"]
        ).DEFAULT_TURN_WALL_TIME_CAP_S
    )
    assert turn is not None

