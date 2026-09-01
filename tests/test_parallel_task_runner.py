"""Audit T-09: ParallelTaskRunner cancel actually stops the in-flight loop."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.misc.parallel_runner import (
    ParallelTask,
    ParallelTaskRunner,
    TaskStatus,
    create_parallel_task_router,
)


def test_cancel_stops_inflight_task_and_keeps_cancelled(monkeypatch) -> None:
    """cancel() must reach the running react loop via the ambient token and
    the terminal state must stay CANCELLED (not COMPLETED)."""
    import runtime.core.cerebrum.react_loop as react_loop_mod

    registered = threading.Event()
    cancel_observed = threading.Event()

    def _blocking_run_react_loop(**kwargs):
        from runtime.safety.approval.cancellation import current_cancellation_token

        current_cancellation_token().on_cancelled(lambda reason: cancel_observed.set())
        registered.set()
        cancel_observed.wait(5)  # block until cancel() fires the token
        return {"terminated_reason": "cancelled"}

    monkeypatch.setattr(react_loop_mod, "run_react_loop", _blocking_run_react_loop)

    runner = ParallelTaskRunner(max_workers=1, stack=SimpleNamespace())
    task = ParallelTask(prompt="do the thing")
    runner.submit(task)

    assert registered.wait(3), "worker never installed the cancellation token"
    assert runner.cancel(task.id) is True
    assert cancel_observed.wait(3), "cancel never reached the running loop"
    # Wait for the worker to settle and keep the CANCELLED terminal state.
    end = time.monotonic() + 5
    while time.monotonic() < end and task.id in runner._sources:
        time.sleep(0.02)
    assert task.status is TaskStatus.CANCELLED
    assert "cancelled" in (task.result or "").lower()
    runner.shutdown(wait=True)


def test_cancel_while_running_keeps_cancelled_terminal(monkeypatch) -> None:
    """Cancelling while the task is RUNNING keeps the CANCELLED terminal
    state — the loop's normal completion path must not overwrite it."""
    import runtime.core.cerebrum.react_loop as react_loop_mod

    hold = threading.Event()

    def _fake(**kwargs):
        hold.wait(5)
        return "done"

    monkeypatch.setattr(react_loop_mod, "run_react_loop", _fake)

    runner = ParallelTaskRunner(max_workers=1, stack=SimpleNamespace())
    task = ParallelTask(prompt="x")
    runner.submit(task)
    end = time.monotonic() + 5
    while time.monotonic() < end and task.status is not TaskStatus.RUNNING:
        time.sleep(0.02)
    assert task.status is TaskStatus.RUNNING
    assert runner.cancel(task.id) is True
    hold.set()  # let the worker finish; the terminal state must stay CANCELLED
    end = time.monotonic() + 5
    while time.monotonic() < end and task.status is TaskStatus.RUNNING:
        time.sleep(0.02)
    assert task.status is TaskStatus.CANCELLED
    runner.shutdown(wait=True)


def test_cancel_before_worker_start_uses_preinstalled_source(monkeypatch) -> None:
    import runtime.core.cerebrum.react_loop as react_loop_mod

    first_started = threading.Event()
    release_first = threading.Event()
    executed: list[str] = []

    def _fake(*, intent, **_kwargs):
        executed.append(intent.raw)
        if intent.raw == "first":
            first_started.set()
            release_first.wait(5)
        return "done"

    monkeypatch.setattr(react_loop_mod, "run_react_loop", _fake)
    runner = ParallelTaskRunner(max_workers=1, stack=SimpleNamespace())
    first = runner.submit(ParallelTask(prompt="first"))
    assert first_started.wait(3)
    queued = runner.submit(ParallelTask(prompt="queued"))

    assert queued.id in runner._sources
    assert runner.cancel(queued.id) is True
    release_first.set()
    end = time.monotonic() + 5
    while time.monotonic() < end and first.status is TaskStatus.RUNNING:
        time.sleep(0.01)

    assert queued.status is TaskStatus.CANCELLED
    assert queued.started_at is None
    assert executed == ["first"]
    runner.shutdown(wait=True)


def test_terminal_task_history_is_bounded(monkeypatch) -> None:
    import runtime.core.cerebrum.react_loop as react_loop_mod

    monkeypatch.setattr(react_loop_mod, "run_react_loop", lambda **_kwargs: "done")
    runner = ParallelTaskRunner(
        max_workers=2,
        stack=SimpleNamespace(),
        max_retained_terminal=3,
    )
    tasks = [runner.submit(ParallelTask(prompt=str(index))) for index in range(12)]
    end = time.monotonic() + 5
    while time.monotonic() < end and any(
        task.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED) for task in tasks
    ):
        time.sleep(0.01)

    retained = runner.list_tasks()
    assert len(retained) == 3
    assert all(task.status is TaskStatus.COMPLETED for task in retained)
    assert runner._sources == {}
    assert runner._futures == {}
    runner.shutdown(wait=True)


def test_runner_shutdown_cancels_running_and_queued_work(monkeypatch) -> None:
    import runtime.core.cerebrum.react_loop as react_loop_mod

    running = threading.Event()
    cancelled = threading.Event()

    def _blocking(**_kwargs):
        from runtime.safety.approval.cancellation import current_cancellation_token

        current_cancellation_token().on_cancelled(lambda _reason: cancelled.set())
        running.set()
        cancelled.wait(5)
        return "cancelled"

    monkeypatch.setattr(react_loop_mod, "run_react_loop", _blocking)
    runner = ParallelTaskRunner(max_workers=1, stack=SimpleNamespace())
    first = runner.submit(ParallelTask(prompt="running"))
    assert running.wait(3)
    queued = runner.submit(ParallelTask(prompt="queued"))

    runner.shutdown(wait=True)

    assert cancelled.is_set()
    assert first.status is TaskStatus.CANCELLED
    assert queued.status is TaskStatus.CANCELLED
    assert runner._closed is True
    assert runner._sources == {}
    assert runner._futures == {}


def test_router_lifespan_shuts_down_app_local_runner(monkeypatch) -> None:
    calls: list[bool] = []
    original = ParallelTaskRunner.shutdown

    def _spy(self: ParallelTaskRunner, *, wait: bool = True) -> None:
        if not self._closed:
            calls.append(wait)
        original(self, wait=wait)

    monkeypatch.setattr(ParallelTaskRunner, "shutdown", _spy)
    app = FastAPI()
    app.include_router(create_parallel_task_router(stack=SimpleNamespace()))

    with TestClient(app):
        pass

    assert calls == [False]

