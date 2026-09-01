from __future__ import annotations

import threading
from concurrent.futures import Future
from typing import Any

from runtime.execution.loops.controller import LoopController
from runtime.execution.loops.store import LoopRunStore
from runtime.platform.process.session_executor import SessionExecutor
from runtime.safety.approval.cancellation import CancellationSource


class LoopRunDispatcher:
    def __init__(
        self,
        *,
        controller: LoopController,
        store: LoopRunStore,
        max_workers: int = 2,
        max_queued: int = 8,
    ) -> None:
        self.controller = controller
        self.store = store
        self._max_workers = max(1, int(max_workers))
        self._max_queued = max(0, int(max_queued))
        self._executor = SessionExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="loop-run",
        )
        self._lock = threading.Lock()
        self._futures: dict[str, Future] = {}
        self._sources: dict[str, CancellationSource] = {}

    def submit(self, run_id: str) -> bool:
        """Dispatch a run. Returns True when accepted; False when the queue is
        full (audit T-16) — the caller maps that to a 429. An already-running
        run is a no-op that still returns True."""
        with self._lock:
            current = self._futures.get(run_id)
            if current is not None and not current.done():
                return True
            pending = sum(1 for f in self._futures.values() if not f.done())
            # Approximate the queued depth: futures beyond the active workers.
            queued = max(0, pending - self._max_workers)
            if queued >= self._max_queued:
                return False
            source = CancellationSource()
            self._sources[run_id] = source
            future = self._executor.submit(
                self.controller.execute,
                run_id,
                cancellation_token=source.token,
            )
            self._futures[run_id] = future
            future.add_done_callback(lambda _done, run_id=run_id: self._forget(run_id))
            return True

    def is_running(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
            return future is not None and not future.done()

    def cancel(
        self,
        run_id: str,
        *,
        reason: str = "cancelled by operator",
    ) -> dict[str, Any]:
        cancel_reason = str(reason or "").strip() or "cancelled by operator"
        with self._lock:
            future = self._futures.get(run_id)
            source = self._sources.get(run_id)
            source_cancelled = source.cancel(reason=cancel_reason) if source is not None else False
            future_cancelled = future.cancel() if future is not None else False
        run = self.controller.request_cancel(run_id, reason=cancel_reason)
        return {
            "run": run,
            "reason": cancel_reason,
            "source_cancelled": source_cancelled,
            "future_cancelled": future_cancelled,
        }

    def _forget(self, run_id: str) -> None:
        with self._lock:
            current = self._futures.get(run_id)
            if current is not None and current.done():
                self._futures.pop(run_id, None)
            self._sources.pop(run_id, None)
