"""P-09: BackgroundRunner.stop orphan-callback handling (audit Q-05/P-09)."""

from __future__ import annotations

import concurrent.futures.thread as _cf_thread
import logging
import threading
import time
from typing import Any

from runtime.adapters.scheduler.runner import BackgroundRunner, _DaemonThreadPoolExecutor


def test_stop_returns_while_callback_stuck(caplog) -> None:
    """stop() must not hang on a stuck in-flight callback; it warns + returns."""
    inside = threading.Event()
    release = threading.Event()

    def stuck():
        inside.set()
        release.wait(timeout=30.0)  # never released within the stop budget

    runner = BackgroundRunner(max_workers=2)
    runner.add_periodic("stuck", 0.01, stuck, run_on_start=True)
    runner.start()
    assert inside.wait(timeout=2.0)

    started = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="runtime.adapters.scheduler.runner"):
        runner.stop(timeout=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert not runner.is_running
    assert runner.state == "stopped"
    assert "still running" in caplog.text

    # Release the callback so the daemon worker can exit cleanly.
    release.set()


def test_stop_with_pool_drains_cleanly() -> None:
    """A fast callback on the worker pool stops without warnings."""
    ran = threading.Event()

    def quick():
        ran.set()

    runner = BackgroundRunner(max_workers=2)
    runner.add_periodic("quick", 0.01, quick, run_on_start=True)
    runner.start()
    assert ran.wait(timeout=2.0)
    runner.stop(timeout=2.0)
    assert runner.state == "stopped"
    assert runner.task_names() == ["quick"]


def test_daemon_pool_does_not_block_interpreter() -> None:
    """Worker threads of the pool are daemon, so a stuck callback cannot
    keep the interpreter alive after stop gives up on it."""

    runner = BackgroundRunner(max_workers=2)
    runner.start()
    pool = runner._pool
    assert pool is not None
    # Force a worker thread to spawn.
    done = threading.Event()
    pool.submit(done.set)
    assert done.wait(timeout=2.0)
    workers = list(pool._threads)
    assert workers, "pool should have spawned at least one worker thread"
    assert all(t.daemon for t in workers)
    runner.stop(timeout=2.0)


def test_daemon_pool_runs_without_legacy_initializer_attributes() -> None:
    """Python 3.14 no longer creates ``_initializer`` or ``_initargs``."""
    pool = _DaemonThreadPoolExecutor(max_workers=1)
    try:
        # Exercise the exact failure mode on Python <= 3.13; on 3.14 these
        # attributes are already absent and the deletion is a no-op.
        for attribute in ("_initializer", "_initargs"):
            if hasattr(pool, attribute):
                delattr(pool, attribute)

        future = pool.submit(lambda: "scheduler-ok")
        assert future.result(timeout=2.0) == "scheduler-ok"
        assert pool._threads
        assert all(worker.daemon for worker in pool._threads)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


def test_daemon_pool_uses_worker_context_protocol_when_available(monkeypatch) -> None:
    """The 3.14 worker receives ``(executor_ref, context, queue)``."""
    pool = _DaemonThreadPoolExecutor(max_workers=1)
    expected_context = object()
    received: list[tuple[Any, object, object]] = []
    worker_ran = threading.Event()

    def create_worker_context() -> object:
        return expected_context

    def context_worker(executor_reference, context, work_queue) -> None:
        received.append((executor_reference, context, work_queue))
        worker_ran.set()

    monkeypatch.setattr(pool, "_create_worker_context", create_worker_context, raising=False)
    monkeypatch.setattr(_cf_thread, "_worker", context_worker)

    try:
        pool._adjust_thread_count()
        assert worker_ran.wait(timeout=2.0)
        assert len(received) == 1
        executor_reference, context, work_queue = received[0]
        assert executor_reference() is pool
        assert context is expected_context
        assert work_queue is pool._work_queue

        workers = list(pool._threads)
        assert len(workers) == 1
        assert workers[0].daemon
        assert _cf_thread._threads_queues[workers[0]] is pool._work_queue
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

