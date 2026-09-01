"""Tests for PauseController resource limit checks."""

from __future__ import annotations

import time

from runtime.core.cerebrum.pause_control import PauseController


def test_check_wall_time_limit_not_exceeded(tmp_path):
    """Task within wall-time limit should not be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_wall_time_seconds=60.0,  # 1 minute limit
    )

    exceeded, reason = pc.check_active_task_limits("task1")
    assert not exceeded
    assert reason == ""


def test_check_wall_time_limit_exceeded(tmp_path):
    """Task exceeding wall-time limit should be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)

    # Register task then manually adjust start time
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_wall_time_seconds=60.0,  # 1 minute limit
    )
    # Backdating start time to simulate elapsed time
    pc._active["task1"].started_at = time.time() - 61.0

    exceeded, reason = pc.check_active_task_limits("task1")
    assert exceeded
    assert reason == "wall_time_limit"


def test_check_wall_time_zero_means_no_limit(tmp_path):
    """max_wall_time_seconds=0 means no limit."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_wall_time_seconds=0.0,  # No limit
    )
    # Backdating to simulate long run
    pc._active["task1"].started_at = time.time() - 3600.0

    exceeded, reason = pc.check_active_task_limits("task1")
    assert not exceeded


def test_check_token_budget_not_exceeded(tmp_path):
    """Task within token budget should not be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_tokens=10000,
    )
    pc._active["task1"].tokens_spent = 8000

    exceeded, reason = pc.check_active_task_limits("task1")
    assert not exceeded
    assert reason == ""


def test_check_token_budget_exceeded(tmp_path):
    """Task exceeding token budget should be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_tokens=10000,
    )
    pc._active["task1"].tokens_spent = 10001

    exceeded, reason = pc.check_active_task_limits("task1")
    assert exceeded
    assert reason == "token_budget_exceeded"


def test_check_cost_budget_not_exceeded(tmp_path):
    """Task within cost budget should not be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_usd=0.50,
    )
    pc._active["task1"].cost_usd = 0.45

    exceeded, reason = pc.check_active_task_limits("task1")
    assert not exceeded


def test_check_cost_budget_exceeded(tmp_path):
    """Task exceeding cost budget should be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_usd=0.50,
    )
    pc._active["task1"].cost_usd = 0.51

    exceeded, reason = pc.check_active_task_limits("task1")
    assert exceeded
    assert reason == "cost_budget_exceeded"


def test_check_iteration_limit_not_exceeded(tmp_path):
    """Task within iteration limit should not be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_iterations=30,
    )
    pc._active["task1"].current_iteration = 25

    exceeded, reason = pc.check_active_task_limits("task1")
    assert not exceeded


def test_check_iteration_limit_exceeded(tmp_path):
    """Task exceeding iteration limit should be flagged."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_iterations=30,
    )
    pc._active["task1"].current_iteration = 30

    exceeded, reason = pc.check_active_task_limits("task1")
    assert exceeded
    assert reason == "iteration_limit_exceeded"


def test_check_multiple_limits_wall_time_first(tmp_path):
    """When multiple limits exceeded, wall-time is checked first."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)
    pc.register_active(
        "task1",
        thread_id="thread1",
        max_wall_time_seconds=60.0,
        max_tokens=10000,
        max_usd=0.50,
        max_iterations=30,
    )
    # Exceed all limits
    pc._active["task1"].started_at = time.time() - 61.0
    pc._active["task1"].tokens_spent = 10001
    pc._active["task1"].cost_usd = 0.51
    pc._active["task1"].current_iteration = 30

    exceeded, reason = pc.check_active_task_limits("task1")
    assert exceeded
    # Wall-time is checked first
    assert reason == "wall_time_limit"


def test_check_unknown_task_returns_false(tmp_path):
    """Checking limits for unknown task should return False."""
    pc = PauseController(store_path=tmp_path / "pause.json", autoload=False)

    exceeded, reason = pc.check_active_task_limits("nonexistent")
    assert not exceeded
    assert reason == ""

