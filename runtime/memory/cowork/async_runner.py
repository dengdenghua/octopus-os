"""The loop that makes async coworkers actually work.

Polls the thread's pending tasks, builds each one's context (history sliced by
the assignee's grant + the shared blackboard), runs the agent, posts the result
to the board, and records the outcome into competence memory. Can run once
(``drain``) or as a background daemon.

How an agent is *run* is injected (``execute``) — the production wiring passes a
bridge to the ephemeral sub-agent runner at bootstrap (same pattern as
``set_ephemeral_role_runner``); tests pass a stub. So this whole loop is testable
without an LLM and never touches the realtime streaming path.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from runtime.memory.cowork.async_work import AsyncTask, AsyncWorkStore
from runtime.memory.cowork.context_view import resolve_view, slice_messages
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.nominate import CompetenceStore, tokenize

_LOG = logging.getLogger("echo.cowork.async_runner")

# execute(task, context) -> result text. ``context`` carries the grant-sliced
# history, the shared blackboard, and the roster.
Executor = Callable[[AsyncTask, dict[str, Any]], str]
HistoryProvider = Callable[[str], list[Any]]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class AsyncWorkRunner:
    """Drives pending async tasks to completion via an injected ``execute``."""

    def __init__(
        self,
        store: AsyncWorkStore,
        group_store: GroupStore,
        execute: Executor,
        *,
        competence: CompetenceStore | None = None,
        history_provider: HistoryProvider | None = None,
        recover_stale_seconds: float = 900.0,
        max_attempts: int = 3,
    ) -> None:
        self._store = store
        self._groups = group_store
        self._execute = execute
        self._competence = competence
        self._history = history_provider or (lambda _tid: [])
        self._recover_stale_seconds = max(0.0, float(recover_stale_seconds))
        self._max_attempts = max(1, int(max_attempts))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._total_ticks = 0
        self._total_failures = 0
        self._consecutive_failures = 0
        self._last_tick_at: str | None = None
        self._last_success_at: str | None = None
        self._last_failure_at: str | None = None
        self._last_error: str | None = None
        self._last_recovered: dict[str, int] = {"requeued": 0, "failed": 0}
        self._last_ran_count = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def status(self) -> dict[str, Any]:
        """Operational health snapshot for UI/API diagnostics."""
        with self._state_lock:
            return {
                "running": self.running,
                "recover_stale_seconds": self._recover_stale_seconds,
                "max_attempts": self._max_attempts,
                "total_ticks": self._total_ticks,
                "total_failures": self._total_failures,
                "consecutive_failures": self._consecutive_failures,
                "last_tick_at": self._last_tick_at,
                "last_success_at": self._last_success_at,
                "last_failure_at": self._last_failure_at,
                "last_error": self._last_error,
                "last_recovered": dict(self._last_recovered),
                "last_ran_count": self._last_ran_count,
            }

    def _record_tick_result(
        self,
        *,
        success: bool,
        recovered: dict[str, int] | None = None,
        ran_count: int = 0,
        error: str | None = None,
    ) -> None:
        now = _now_iso()
        with self._state_lock:
            self._total_ticks += 1
            self._last_tick_at = now
            self._last_recovered = dict(recovered or {"requeued": 0, "failed": 0})
            self._last_ran_count = int(ran_count)
            if success:
                self._consecutive_failures = 0
                self._last_error = None
                self._last_success_at = now
                return
            self._total_failures += 1
            self._consecutive_failures += 1
            self._last_error = error or "tick failed"
            self._last_failure_at = now

    def _build_context(self, task: AsyncTask) -> dict[str, Any]:
        state = self._groups.state(task.thread_id)
        msgs = self._history(task.thread_id)
        view = resolve_view(state, task.assignee, max(0, len(msgs) - 1))
        history = slice_messages(view, msgs) if view else []
        return {
            "history": history,
            "blackboard": self._groups.blackboard_snapshot(task.thread_id),
            "roster": [m.id for m in state.roster],
            "grant_scope": view.scope if view else None,
        }

    def _record_competence(self, assignee: str, prompt: str, success: bool) -> None:
        if not self._competence:
            return
        for tag in list(tokenize(prompt))[:5]:
            self._competence.record(assignee, tag, success)

    def run_one(self, task: AsyncTask) -> bool:
        """Claim → execute → complete (or fail) one task. False if not claimable."""
        if not self._store.claim(task.task_id):
            return False
        try:
            result = self._execute(task, self._build_context(task))
        except Exception as exc:  # noqa: BLE001 — a failed task must not kill the loop
            self._store.fail(task.task_id, f"{type(exc).__name__}: {exc}")
            self._record_competence(task.assignee, task.prompt, success=False)
            _LOG.warning("async task %s failed: %s", task.task_id, exc)
            return True
        self._store.complete(task.task_id, result)
        self._record_competence(task.assignee, task.prompt, success=True)
        return True

    def drain(self, thread_id: str) -> int:
        """Run every currently-pending task in a thread. Returns how many ran."""
        ran = 0
        for task in self._store.pending(thread_id):
            if self.run_one(task):
                ran += 1
        return ran

    def recover_stale(self) -> dict[str, int]:
        """Requeue abandoned working tasks before polling pending work."""
        recovered = self._store.recover_stale_working(
            max_age_seconds=self._recover_stale_seconds,
            max_attempts=self._max_attempts,
        )
        if recovered.get("requeued") or recovered.get("failed"):
            _LOG.warning("async runner recovered stale tasks: %s", recovered)
        return recovered

    def drain_all(self) -> int:
        self.recover_stale()
        return sum(self.drain(tid) for tid in self._store.threads_with_pending())

    def tick_once(self) -> int:
        """Run one recover+drain tick and record runner health."""
        try:
            recovered = self.recover_stale()
            ran = 0
            for thread_id in self._store.threads_with_pending():
                ran += self.drain(thread_id)
        except Exception as exc:  # noqa: BLE001 — tick health must capture store/context failures
            self._record_tick_result(
                success=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            _LOG.warning("async runner tick error: %s", exc, exc_info=True)
            return 0
        self._record_tick_result(success=True, recovered=recovered, ran_count=ran)
        return ran

    # ── background daemon ────────────────────────────────────────────────────
    def start(self, *, poll_seconds: float = 5.0) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, args=(poll_seconds,), name="cowork-async-runner", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    def _loop(self, poll_seconds: float) -> None:
        while not self._stop.wait(timeout=poll_seconds):
            self.tick_once()
