"""Persistent, thread-affine browser sessions for agent browser skills.

Playwright's *sync* API is thread-affine: a ``Page`` must be created, used, AND
closed on ONE thread. Agent tool calls span threads (the ReAct loop plus
sub-agent thread pools), yet a multi-step flow (navigate → click → type) needs
the SAME page across calls. The old per-call ``with sync_playwright()`` is safe
but stateless — every call opens a throwaway browser, so multi-step flows can't
share state.

Each logical session here gets a :class:`BrowserSessionWorker`: a daemon thread
that exclusively owns one browser + page. Callers ``submit(op)`` a callable that
runs ON the worker thread and gets the persistent page; the result comes back
through a Future. Because launch, every op, AND close all run on that one
thread, thread-affinity holds by construction — and the idle reaper closes a
session safely by just enqueueing a close (executed on the owner thread),
never touching Playwright objects cross-thread.

The page factory is injectable so the concurrency primitive is unit-testable
without a real browser.
"""

from __future__ import annotations

import atexit
import concurrent.futures
import contextlib
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

from runtime.execution.suckers.browser_launch import (
    launch_chromium,
    launch_persistent_chromium,
)

_OP_TIMEOUT_S = 60.0
_IDLE_TTL_S = 300.0
_REAP_INTERVAL_S = 30.0
_MAX_SESSIONS = 8

# A page factory returns ``(page, close_callable)``. ``close_callable`` runs on
# the worker thread at shutdown and must release every Playwright object.
PageFactory = Callable[[], "tuple[Any, Callable[[], None]]"]


def _default_page_factory(headless: bool) -> tuple[Any, Callable[[], None]]:
    import os

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    # Signed-in browser without the desktop app: when ECHO_BROWSER_PROFILE
    # points at a (pre-authenticated) user-data dir, launch a *persistent*
    # context so the agent's browser carries that profile's cookies/logins and
    # can act on signed-in sites — the capability that otherwise needed the
    # Electron bridge. Opt-in (the user points it at a profile they've logged
    # into); unset → the original stateless launch, so default behaviour and
    # the security posture are unchanged.
    profile = os.environ.get("ECHO_BROWSER_PROFILE", "").strip()
    if profile:
        context = launch_persistent_chromium(
            pw.chromium,
            user_data_dir=profile,
            headless=headless,
        )
        page = context.pages[0] if context.pages else context.new_page()

        def _close_ctx() -> None:
            try:
                context.close()
            finally:
                pw.stop()

        return page, _close_ctx

    browser = launch_chromium(pw.chromium, headless=headless)
    page = browser.new_context().new_page()

    def _close() -> None:
        try:
            browser.close()
        finally:
            pw.stop()

    return page, _close


class BrowserSessionWorker:
    """A dedicated thread owning one persistent browser page."""

    def __init__(
        self,
        *,
        page_factory: PageFactory | None = None,
        headless: bool = True,
        op_timeout_s: float = _OP_TIMEOUT_S,
    ) -> None:
        self._factory: PageFactory = page_factory or (lambda: _default_page_factory(headless))
        self._op_timeout_s = op_timeout_s
        self._q: queue.Queue[Any] = queue.Queue()
        self._lock = threading.Lock()
        self._closed = False
        self.last_used = time.monotonic()
        self._thread = threading.Thread(
            target=self._run,
            name="browser-session",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        page: Any = None
        closer: Callable[[], None] | None = None
        try:
            while True:
                item = self._q.get()
                if item is None:  # shutdown sentinel
                    break
                op, fut = item
                if fut.set_running_or_notify_cancel() is False:
                    continue
                try:
                    if page is None:
                        page, closer = self._factory()
                    fut.set_result(op(page))
                except BaseException as exc:  # noqa: BLE001 — relay to caller
                    fut.set_exception(exc)
        finally:
            if closer is not None:
                with contextlib.suppress(Exception):  # best-effort teardown
                    closer()

    def submit(self, op: Callable[[Any], Any]) -> Any:
        """Run ``op(page)`` on the worker thread; return its result (blocking)."""
        fut: concurrent.futures.Future = concurrent.futures.Future()
        # Check ``_closed`` and enqueue ATOMICALLY under the lock. close() also
        # enqueues its sentinel under the same lock, so the two can't interleave
        # — an op is never queued after the close sentinel and then dropped.
        with self._lock:
            if self._closed:
                raise RuntimeError("browser session is closed")
            self.last_used = time.monotonic()
            self._q.put((op, fut))
        try:
            return fut.result(timeout=self._op_timeout_s)
        except TimeoutError:
            # The op is still running on the worker thread and can't be
            # cancelled. Retire this worker so its possibly half-mutated page is
            # never reused; the next session call gets a fresh one.
            self.close(wait=False)
            raise

    def close(self, *, wait: bool = True, timeout: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._q.put(None)  # sentinel enqueued under the lock (see submit)
        if wait:
            self._thread.join(timeout=timeout)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used


class BrowserSessionPool:
    """Keyed pool of :class:`BrowserSessionWorker` with idle reaping + a cap."""

    def __init__(
        self,
        *,
        idle_ttl_s: float = _IDLE_TTL_S,
        reap_interval_s: float = _REAP_INTERVAL_S,
        max_sessions: int = _MAX_SESSIONS,
        page_factory: PageFactory | None = None,
    ) -> None:
        self._workers: dict[str, BrowserSessionWorker] = {}
        self._lock = threading.Lock()
        self._idle_ttl_s = idle_ttl_s
        self._max_sessions = max_sessions
        self._page_factory = page_factory
        self._stop = threading.Event()
        self._reaper = threading.Thread(
            target=self._reap_loop,
            name="browser-session-reaper",
            daemon=True,
        )
        self._reap_interval_s = reap_interval_s
        self._reaper.start()

    def get_or_create(self, key: str) -> BrowserSessionWorker:
        with self._lock:
            worker = self._workers.get(key)
            if worker is not None and not worker.closed:
                worker.last_used = time.monotonic()  # keep it off the reaper
                return worker
            if len(self._workers) >= self._max_sessions:
                self._evict_most_idle_locked()
            worker = BrowserSessionWorker(page_factory=self._page_factory)
            self._workers[key] = worker
            return worker

    def _evict_most_idle_locked(self) -> None:
        if not self._workers:
            return
        key = max(self._workers, key=lambda k: self._workers[k].idle_seconds)
        self._workers.pop(key).close(wait=False)

    def _reap_loop(self) -> None:
        while not self._stop.wait(self._reap_interval_s):
            with self._lock:
                stale = [k for k, w in self._workers.items() if w.idle_seconds > self._idle_ttl_s]
                for k in stale:
                    self._workers.pop(k).close(wait=False)

    def close_all(self) -> None:
        self._stop.set()
        with self._lock:
            for worker in self._workers.values():
                worker.close(wait=False)
            self._workers.clear()

    def active_count(self) -> int:
        with self._lock:
            return len(self._workers)


_POOL: BrowserSessionPool | None = None
_POOL_LOCK = threading.Lock()


def get_browser_session_pool() -> BrowserSessionPool:
    """Process-wide lazy singleton pool."""
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = BrowserSessionPool()
                # Stop the reaper + close all browsers cleanly at process exit
                # (daemon threads would otherwise be killed mid-teardown).
                atexit.register(_POOL.close_all)
    return _POOL
