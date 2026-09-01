"""Process-local provider for the background-job capability seam.

It keeps every record in memory and hands out fresh snapshots, never live
state. Registrations outlive producer and controller fibers; owner or service
disposal cancels live work and awaits compliant producers; a throwing teardown
cancel force-fails only the record and reports a possible orphan.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .types import (
    JobOutcome,
    JobRead,
    JobSnapshot,
    JobStart,
    is_terminal,
)

_log = logging.getLogger("runtime.execution.jobs.registry")

DEFAULT_MAX_CONCURRENT_JOBS_PER_OWNER = 10

JobDoneListener = Callable[[JobSnapshot, str | None], Any]
JobsChangedListener = Callable[[str | None], Any]


class _TrackedJob:
    """The registry's mutable per-job record (never handed out)."""

    __slots__ = (
        "id",
        "kind",
        "label",
        "output_limit_bytes",
        "owner",
        "notify",
        "on_settle",
        "cancel",
        "read_output",
        "status",
        "detail",
        "output",
        "started_at",
        "finished_at",
        "deadline_at",
        "reported",
        "settled",
        "loop",
        "waiters",
        "wait_resolvers",
    )

    def __init__(
        self,
        *,
        job_id: str,
        kind: str,
        label: str,
        output_limit_bytes: int | None,
        owner: str | None,
        notify: Callable[[JobSnapshot], None] | None,
        on_settle: Callable[[JobSnapshot], None] | None,
        cancel: Callable[[str | None], None],
        read_output: Callable[[], str] | None,
        loop: asyncio.AbstractEventLoop,
        watchdog_timeout_s: int | None = None,
    ) -> None:
        self.id = job_id
        self.kind = kind
        self.label = label
        self.output_limit_bytes = output_limit_bytes
        self.owner = owner
        self.notify = notify
        self.on_settle = on_settle
        self.cancel = cancel
        self.read_output = read_output
        self.status = "running"
        self.detail: str | None = None
        self.output: str | None = None
        self.started_at = int(time.time() * 1000)
        self.finished_at: int | None = None
        self.deadline_at = (
            time.monotonic() + watchdog_timeout_s
            if watchdog_timeout_s is not None and watchdog_timeout_s > 0
            else None
        )
        self.reported = False
        self.loop = loop
        self.settled: asyncio.Future[None] = loop.create_future()
        self.waiters = 0
        self.wait_resolvers: set[asyncio.Future[None]] = set()


class LocalJobRegistry:
    """The in-memory ``jobs`` registry (dsh ``jobs-local`` port).

    The registry is one process-wide instance. Ownership is a caller-supplied
    opaque key (the parent thread/session id); a job with an owner is reachable
    only by callers whose key matches, and an unowned job is open to any
    caller. Completion and change listeners are global (one composition), each
    contained so an observer cannot break a lifecycle commit that already
    happened.
    """

    def __init__(
        self,
        max_concurrent_jobs_per_owner: int = DEFAULT_MAX_CONCURRENT_JOBS_PER_OWNER,
    ) -> None:
        if (
            not isinstance(max_concurrent_jobs_per_owner, int)
            or isinstance(max_concurrent_jobs_per_owner, bool)
            or max_concurrent_jobs_per_owner < 1
        ):
            raise ValueError("max_concurrent_jobs_per_owner must be a positive integer")
        self._max = max_concurrent_jobs_per_owner
        self._store: dict[str, _TrackedJob] = {}
        self._counters: dict[str, int] = {}
        self._done_listeners: list[JobDoneListener] = []
        self._changed_listeners: list[JobsChangedListener] = []
        self._controller_tokens: set[str] = set()
        self._owner_cleanups: dict[str, Callable[[], Awaitable[None] | None]] = {}
        self._listeners_closed = False
        self._lock = threading.RLock()
        self._sweeper: threading.Thread | None = None
        self._sweeper_stop = threading.Event()

    # ── lifecycle ───────────────────────────────────────────

    def start(self, spec: JobStart) -> str:
        """Preflight, then run the producer and register the job.

        Preflight validates the declaration and the owner concurrency cap
        before ``run()`` is invoked; a throw from ``run()`` leaves nothing
        registered. Registration is complete before the done promise is
        observed, so the visible set has genuinely changed once this returns.
        Requires a running event loop (skills execute on one).
        """
        if not self._controller_tokens:
            raise RuntimeError(
                "background jobs unavailable: no job controller attached (load the jobs skills)"
            )
        if not spec.kind:
            raise ValueError("invalid job kind: expected a non-empty string")
        if not spec.label:
            raise ValueError("invalid job label: expected a non-empty string")
        if spec.output_limit_bytes is not None and (
            not isinstance(spec.output_limit_bytes, int)
            or isinstance(spec.output_limit_bytes, bool)
            or spec.output_limit_bytes <= 0
        ):
            raise ValueError(
                "invalid output_limit_bytes: expected a positive integer, "
                f"got {spec.output_limit_bytes!r}"
            )
        if spec.owner is not None and spec.owner_cleanup is not None:
            self._owner_cleanups.setdefault(spec.owner, spec.owner_cleanup)
        with self._lock:
            active = self._active_count_locked(spec.owner)
            if active >= self._max:
                raise RuntimeError(
                    f"background job limit reached for this owner (limit: "
                    f"{self._max}); use job_kill to stop an unneeded job, "
                    f"wait for it to finish, then retry"
                )
        hooks = spec.run()
        loop = asyncio.get_running_loop()
        with self._lock:
            count = self._counters.get(spec.kind, 0) + 1
            self._counters[spec.kind] = count
            job_id = f"{spec.kind}-{count}"
            job = _TrackedJob(
                job_id=job_id,
                kind=spec.kind,
                label=spec.label,
                output_limit_bytes=spec.output_limit_bytes,
                owner=spec.owner,
                notify=spec.notify,
                on_settle=spec.on_settle,
                cancel=hooks.cancel,
                read_output=hooks.read_output,
                loop=loop,
                watchdog_timeout_s=spec.watchdog_timeout_s,
            )
            self._store[job_id] = job
            if spec.watchdog_timeout_s is not None and spec.watchdog_timeout_s > 0:
                self._ensure_sweeper_locked()

        if spec.on_start is not None:
            try:
                spec.on_start(self.snapshot(job))
            except Exception:  # noqa: BLE001 - observer containment
                _log.warning("jobs: onStart observer threw for %s", job_id, exc_info=True)

        def _on_done(task: asyncio.Task[Any]) -> None:
            if task.cancelled():
                self.settle(job, JobOutcome(status="failed", detail="producer done cancelled"))
                return
            error = task.exception()
            if error is not None:
                # Contain a producer contract violation (``done`` rejected) so
                # cleanup and waiters cannot hang.
                _log.warning(
                    "jobs: job %s producer done promise rejected (producer contract violation): %s",
                    job_id,
                    error,
                )
                self.settle(job, JobOutcome(status="failed", detail=str(error)))
                return
            self.settle(job, task.result())

        asyncio.ensure_future(hooks.done).add_done_callback(_on_done)
        self._notify_changed(spec.owner)
        return job_id

    def list(self, caller: str | None = None) -> list[JobSnapshot]:
        with self._lock:
            return [
                self.snapshot(job)
                for job in self._store.values()
                if job.owner is None or job.owner == caller
            ]

    def get(self, job_id: str, caller: str | None = None) -> JobSnapshot:
        job = self._expect(job_id)
        self._assert_access(job, caller)
        return self.snapshot(job)

    def read(self, job_id: str, caller: str | None = None) -> JobRead:
        """Consume output since the previous read (stream kinds) or the
        terminal output (final-output kinds, idempotent). A terminal read
        marks the job reported."""
        job = self._expect(job_id)
        self._assert_access(job, caller)
        if job.read_output is not None:
            text = job.read_output()
        elif is_terminal(job.status):
            text = job.output or ""
        else:
            text = ""
        if is_terminal(job.status):
            job.reported = True
        return JobRead(text=text, snapshot=self.snapshot(job))

    def kill(
        self,
        job_id: str,
        caller: str | None = None,
        reason: str | None = None,
    ) -> str:
        """Request termination. ``cancel`` runs first so a throw leaves both
        lifecycle and notice state unchanged."""
        job = self._expect(job_id)
        self._assert_access(job, caller)
        if is_terminal(job.status):
            job.reported = True
            return "already-finished"
        job.cancel(reason)
        job.status = "stopping"
        job.reported = True
        self._notify_changed(job.owner)
        return "requested"

    async def wait(
        self,
        job_id: str,
        timeout_ms: int,
        caller: str | None = None,
    ) -> JobSnapshot:
        """Wait up to ``timeout_ms`` for a terminal status. A timed-out wait
        returns the current snapshot and leaves the job alive (the timeout is
        a success, not an error)."""
        job = self._expect(job_id)
        self._assert_access(job, caller)
        if not isinstance(timeout_ms, int) or timeout_ms <= 0:
            raise ValueError(
                "invalid wait timeout: expected a positive number of "
                f"milliseconds, got {timeout_ms!r}"
            )
        if not is_terminal(job.status):
            loop = asyncio.get_running_loop()
            future = loop.create_future()
            with self._lock:
                job.waiters += 1
                job.wait_resolvers.add(future)
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout_ms / 1000.0)
            except TimeoutError:  # noqa: BLE001 — wait timeout returns the current snapshot
                pass
            finally:
                with self._lock:
                    job.wait_resolvers.discard(future)
                    job.waiters -= 1
        if is_terminal(job.status):
            job.reported = True
        return self.snapshot(job)

    # ── observation ────────────────────────────────────────

    def on_job_done(self, listener: JobDoneListener) -> Callable[[], None]:
        """Completion callback; returned promises are observed but not
        awaited. Returns an unregister callable."""
        with self._lock:
            self._done_listeners.append(listener)
            token = listener

        def _unregister() -> None:
            with self._lock:
                self._done_listeners[:] = [
                    item for item in self._done_listeners if item is not token
                ]

        return _unregister

    def on_jobs_changed(self, listener: JobsChangedListener) -> Callable[[], None]:
        """Observation callback for a change to one owner's visible set. The
        owner key is ``None`` for an unowned job change. Returns an
        unregister callable."""
        with self._lock:
            self._changed_listeners.append(listener)
            token = listener

        def _unregister() -> None:
            with self._lock:
                self._changed_listeners[:] = [
                    item for item in self._changed_listeners if item is not token
                ]

        return _unregister

    def attach_controller(self, name: str) -> Callable[[], None]:
        """One token per call keeps duplicate labels independently
        disposable. Producers may start work only while a controller is
        attached."""
        with self._lock:
            token = f"{name}#{len(self._controller_tokens)}"
            self._controller_tokens.add(token)

        def _detach() -> None:
            with self._lock:
                self._controller_tokens.discard(token)

        return _detach

    # ── teardown ───────────────────────────────────────────

    async def dispose_owned(self, owner: str) -> None:
        """Cancel, await terminal records, and drop every job owned by one
        exact owner lifecycle."""
        with self._lock:
            owned = [job for job in self._store.values() if job.owner == owner]
        self._cancel_for_teardown(owned, "owner disposed")
        await asyncio.gather(*(job.settled for job in owned), return_exceptions=True)
        with self._lock:
            for job in owned:
                self._store.pop(job.id, None)
        # Removal is the one visible-set change no per-job record carries.
        if owned:
            self._notify_changed(owner)

    async def dispose_all(self) -> None:
        """Close listeners, cancel live jobs, await settlement, and run the
        retained owner cleanups."""
        self._listeners_closed = True
        self._sweeper_stop.set()
        with self._lock:
            all_jobs = list(self._store.values())
        self._cancel_for_teardown(all_jobs, "jobs service disposed")
        await asyncio.gather(*(job.settled for job in all_jobs), return_exceptions=True)
        with self._lock:
            emptied = {job.owner for job in all_jobs}
            self._store.clear()
        for owner in emptied:
            self._notify_changed(owner)
        cleanups = list(self._owner_cleanups.values())
        self._owner_cleanups.clear()
        for cleanup in cleanups:
            try:
                result = cleanup()
                if result is not None:
                    await result
            except Exception:  # noqa: BLE001 — teardown containment
                _log.warning("jobs: owner cleanup threw during teardown", exc_info=True)

    # ── internals ──────────────────────────────────────────

    def _cancel_for_teardown(self, jobs: list[_TrackedJob], reason: str) -> None:
        """Cancel jobs during teardown with per-job containment. Teardown
        cancellation is a kill without a caller, so it claims the terminal
        report the same way ``kill()`` does; a throwing cancel force-fails the
        record and reports a possible orphan."""
        for job in jobs:
            if is_terminal(job.status):
                continue
            job.reported = True
            try:
                job.cancel(reason)
                job.status = "stopping"
                self._notify_changed(job.owner)
            except Exception as error:  # noqa: BLE001 — teardown containment
                detail = f"cancel threw during teardown; work may be orphaned: {error}"
                _log.warning(
                    "jobs: cancel of %s threw during teardown; job record "
                    "forced failed and work may be orphaned: %s",
                    job.id,
                    error,
                )
                self.settle(job, JobOutcome(status="failed", detail=detail))

    def _active_count_locked(self, owner: str | None) -> int:
        return sum(
            1
            for job in self._store.values()
            if job.owner == owner and (job.status == "running" or job.status == "stopping")
        )

    # ── stuck-job watchdog ─────────────────────────────────

    _SWEEP_INTERVAL_S = 5.0
    _SWEEP_IDLE_EXIT_SCANS = 12

    def _ensure_sweeper_locked(self) -> None:
        """Lazily start the deadline sweeper. It force-fails jobs whose
        producer never settled, so a stuck worker cannot pin its owner's
        concurrency slot forever. The thread self-retires after a run of
        idle scans with nothing to watch and is restarted on demand."""
        if self._sweeper is not None and self._sweeper.is_alive():
            return

        def _sweep_loop() -> None:
            idle = 0
            while not self._sweeper_stop.wait(self._SWEEP_INTERVAL_S):
                expired: list[_TrackedJob] = []
                watching = False
                with self._lock:
                    for job in self._store.values():
                        if job.deadline_at is None:
                            continue
                        watching = True
                        if not is_terminal(job.status) and time.monotonic() >= job.deadline_at:
                            expired.append(job)
                if not watching:
                    idle += 1
                    if idle >= self._SWEEP_IDLE_EXIT_SCANS:
                        return
                    continue
                idle = 0
                for job in expired:
                    try:
                        job.cancel("watchdog timeout")
                    except Exception as error:  # noqa: BLE001 - containment
                        _log.warning("jobs: watchdog cancel of %s threw: %s", job.id, error)
                    self.settle(
                        job,
                        JobOutcome(
                            status="failed",
                            detail="watchdog timeout: producer never settled",
                        ),
                    )
                    _log.warning(
                        "jobs: job %s (%s) force-failed by watchdog timeout",
                        job.id,
                        job.kind,
                    )

        self._sweeper_stop.clear()
        self._sweeper = threading.Thread(
            target=_sweep_loop,
            daemon=True,
            name="jobs-watchdog",
        )
        self._sweeper.start()

    def _expect(self, job_id: str) -> _TrackedJob:
        job = self._store.get(job_id)
        if job is None:
            raise LookupError(f"unknown job {job_id}")
        return job

    def _assert_access(self, job: _TrackedJob, caller: str | None) -> None:
        if job.owner is not None and job.owner != caller:
            raise PermissionError(f"job {job.id} belongs to another session")

    def snapshot(self, job: _TrackedJob) -> JobSnapshot:
        """Project a fresh read-only snapshot from the mutable record."""
        return JobSnapshot(
            id=job.id,
            kind=job.kind,
            label=job.label,
            output_limit_bytes=job.output_limit_bytes,
            owner_session=job.owner,
            status=job.status,
            detail=job.detail,
            started_at=job.started_at,
            finished_at=job.finished_at,
            reported=job.reported,
        )

    def _notify_changed(self, owner: str | None) -> None:
        with self._lock:
            listeners = list(self._changed_listeners)
        for listener in listeners:
            try:
                listener(owner)
            except Exception:  # noqa: BLE001 — observer containment
                _log.warning("jobs: onJobsChanged listener threw", exc_info=True)

    @staticmethod
    def _resolve_future(future: asyncio.Future[None]) -> None:
        """Resolve a future from whichever thread calls settle."""
        try:
            loop = future.get_loop()
            if loop.is_closed():
                return
            loop.call_soon_threadsafe(_set_future_result, future)
        except RuntimeError:  # noqa: BLE001 — loop torn down mid-settle
            pass

    def settle(self, job: _TrackedJob, outcome: JobOutcome) -> None:
        """Record the first terminal outcome, release waiters, then announce
        completion. First-wins preserves a teardown force-failure against late
        producer settlement. Pending waits mark the job reported before
        listeners run; the completion notice is announced last because it may
        open a model turn synchronously."""
        with self._lock:
            if is_terminal(job.status):
                return
            job.status = outcome.status
            job.detail = outcome.detail
            job.output = outcome.output
            job.finished_at = int(time.time() * 1000)
            if job.waiters > 0:
                job.reported = True
            snapshot = self.snapshot(job)
            wait_resolvers = list(job.wait_resolvers)
            job.wait_resolvers.clear()
        for future in wait_resolvers:
            self._resolve_future(future)
        self._resolve_future(job.settled)
        self._notify_changed(job.owner)
        if self._listeners_closed:
            return
        if job.on_settle is not None:
            try:
                job.on_settle(snapshot)
            except Exception:  # noqa: BLE001 — observer containment
                _log.warning("jobs: onSettle observer threw for %s", job.id, exc_info=True)
        if not snapshot.reported and job.notify is not None:
            try:
                job.notify(snapshot)
            except Exception:  # noqa: BLE001 — notice containment
                _log.warning("jobs: completion notice threw for %s", job.id, exc_info=True)
        with self._lock:
            listeners = list(self._done_listeners)
        for listener in listeners:
            try:
                listener(snapshot, job.owner)
            except Exception:  # noqa: BLE001 — listener containment
                _log.warning("jobs: onJobDone listener threw for %s", job.id, exc_info=True)


def _set_future_result(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


__all__ = [
    "DEFAULT_MAX_CONCURRENT_JOBS_PER_OWNER",
    "JobDoneListener",
    "JobsChangedListener",
    "LocalJobRegistry",
]
