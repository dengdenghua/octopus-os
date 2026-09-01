"""Job registry watchdog timeout backstop tests.

Validates that the sweeper thread enforces watchdog_timeout_s deadlines and
force-fails timed-out jobs before the external watchdog script kills the
entire process.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from runtime.execution.jobs.registry import LocalJobRegistry
from runtime.execution.jobs.types import JobHooks, JobOutcome, JobStart


class _HangingProducer:
    """Producer that never settles its done future, simulating a stuck job."""

    def __init__(self) -> None:
        self.done_future: asyncio.Future[JobOutcome] | None = None

    def start(
        self,
        *,
        kind: str = "test",
        label: str = "stuck",
        owner: str | None = None,
        watchdog_timeout_s: int | None = None,
    ) -> JobStart:
        def run() -> JobHooks:
            self.done_future = asyncio.get_running_loop().create_future()
            return JobHooks(
                cancel=lambda _reason: None,
                done=self.done_future,
                read_output=None,
            )

        return JobStart(
            kind=kind,
            label=label,
            owner=owner,
            run=run,
            watchdog_timeout_s=watchdog_timeout_s,
        )


@pytest.mark.asyncio
async def test_watchdog_timeout_force_fails_stuck_job() -> None:
    """A job that exceeds its watchdog_timeout_s is force-failed by the sweeper."""
    registry = LocalJobRegistry()
    registry.attach_controller("test")
    producer = _HangingProducer()
    registry.start(producer.start(watchdog_timeout_s=1))

    # Job starts running.
    snapshot = registry.get("test-1")
    assert snapshot.status == "running"

    # Wait for sweeper to detect and fail the job.
    # Sweeper runs every 5s, so we need to wait at least 1s (deadline) + 5s (scan) = 6s+.
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        snapshot = registry.get("test-1")
        if snapshot.status == "failed":
            break
        await asyncio.sleep(0.1)

    assert snapshot.status == "failed"
    assert "watchdog timeout" in (snapshot.detail or "").lower()
    assert snapshot.finished_at is not None


@pytest.mark.asyncio
async def test_watchdog_timeout_zero_disables_deadline() -> None:
    """A job with watchdog_timeout_s=0 or None runs without a deadline."""
    registry = LocalJobRegistry()
    registry.attach_controller("test")

    # None case - job runs without being force-failed
    producer_none = _HangingProducer()
    registry.start(producer_none.start(watchdog_timeout_s=None))

    # Zero case - job runs without being force-failed
    producer_zero = _HangingProducer()
    registry.start(producer_zero.start(kind="nolimit", watchdog_timeout_s=0))

    # Both jobs remain running after 0.5s (not force-failed).
    await asyncio.sleep(0.5)
    assert registry.get("test-1").status == "running"
    assert registry.get("nolimit-1").status == "running"


@pytest.mark.asyncio
async def test_watchdog_does_not_fail_settled_job() -> None:
    """A job that settles before its deadline is not touched by the sweeper."""
    registry = LocalJobRegistry()
    registry.attach_controller("test")

    class _SettlingProducer:
        def __init__(self) -> None:
            self.done_future: asyncio.Future[JobOutcome] | None = None

        def start(self, *, watchdog_timeout_s: int | None = None) -> JobStart:
            def run() -> JobHooks:
                self.done_future = asyncio.get_running_loop().create_future()
                return JobHooks(
                    cancel=lambda _reason: None,
                    done=self.done_future,
                    read_output=None,
                )

            return JobStart(
                kind="fast",
                label="completes",
                run=run,
                watchdog_timeout_s=watchdog_timeout_s,
            )

    producer = _SettlingProducer()
    registry.start(producer.start(watchdog_timeout_s=2))

    # Settle the job immediately.
    assert producer.done_future is not None
    producer.done_future.get_loop().call_soon_threadsafe(
        lambda: producer.done_future.set_result(JobOutcome(status="completed", output="ok"))
    )

    # Wait for settlement to propagate.
    for _ in range(100):
        snapshot = registry.get("fast-1")
        if snapshot.status == "completed":
            break
        await asyncio.sleep(0.01)

    assert snapshot.status == "completed"
    assert snapshot.detail != "watchdog timeout"

    # Wait past the original deadline to confirm sweeper doesn't re-fail.
    await asyncio.sleep(2.5)
    final = registry.get("fast-1")
    assert final.status == "completed"


@pytest.mark.asyncio
async def test_sweeper_self_retires_when_no_deadlines_remain() -> None:
    """The sweeper thread self-retires after idle scans, then restarts for new deadlines."""
    registry = LocalJobRegistry()
    registry.attach_controller("test")

    # Start a job with a long deadline (won't expire during test).
    producer = _HangingProducer()
    registry.start(producer.start(watchdog_timeout_s=100))

    # Give sweeper time to start (it's lazy-started on first deadline job).
    await asyncio.sleep(0.1)

    # Settle the job to remove all deadlines.
    assert producer.done_future is not None
    producer.done_future.get_loop().call_soon_threadsafe(
        lambda: producer.done_future.set_result(JobOutcome(status="completed", output="done"))
    )

    # Wait for settlement.
    for _ in range(100):
        if registry.get("test-1").status == "completed":
            break
        await asyncio.sleep(0.01)

    assert registry.get("test-1").status == "completed"

    # Now verify the sweeper still works by starting a new deadline job.
    # The sweeper either is still in its idle countdown or has self-retired
    # and will be restarted. Either way, a short-deadline job should expire.
    producer2 = _HangingProducer()
    registry.start(producer2.start(kind="test2", watchdog_timeout_s=1))

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        snapshot = registry.get("test2-1")
        if snapshot.status == "failed":
            break
        await asyncio.sleep(0.1)

    assert snapshot.status == "failed"
    assert "watchdog timeout" in (snapshot.detail or "").lower()

