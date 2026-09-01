from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from fastapi import HTTPException

from ._openai_gateway_router_helpers import _evict_idle_rate_buckets


class _PerActorRateLimiter:
    """Per-actor rate limiting for /v1/chat/completions.

    A planner + LLM round-trip runs per call, so a small bot can burn quota
    fast. Caps (a) concurrent in-flight completions per actor (semaphore)
    and (b) calls/minute per actor (sliding window). Anonymous callers
    (require_auth=False) are all bucketed under a single shared key — they
    collectively can't exceed one actor's allotment.

    Bucket dicts are bounded against a rotating-IP flood: buckets whose
    sliding window has gone empty (no call in the last 60s) are pruned on a
    cadence (or when a hard cap is blown past), so an attacker rotating
    source IPs can't grow the dicts unbounded.
    """

    def __init__(
        self,
        *,
        concurrent_limit: int = 4,
        per_min_limit: int = 30,
    ) -> None:
        self._semaphores: dict[str, threading.Semaphore] = {}
        self._windows: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._concurrent = max(1, int(concurrent_limit))
        self._per_min = max(1, int(per_min_limit))
        # Without pruning, one bucket per distinct anon:<ip> key accumulates
        # forever. Amortized: swept at most once per interval (or when we
        # blow past a hard cap) so it never becomes O(buckets) per request
        # under a rotating-IP flood.
        self._max_buckets = 4096
        self._sweep_interval = 30.0
        self._last_sweep = [0.0]

    def _prune_locked(self, now: float) -> None:
        # Caller holds _lock. Cheap gate first: sweep on a cadence, or sooner
        # if we've blown past the hard cap — but never more than ~once/sec,
        # so a sustained flood of *active* IPs (where a sweep frees nothing)
        # can't turn this into O(buckets)/request.
        elapsed = now - self._last_sweep[0]
        if elapsed < 1.0:
            return
        over_cap = len(self._windows) > self._max_buckets
        if not over_cap and elapsed < self._sweep_interval:
            return
        self._last_sweep[0] = now
        # Only keys with an empty window (no call in 60s) are dropped, so in
        # the normal case no slot is held. The one edge case — a single
        # completion running >60s — would at worst reset that key's
        # concurrency cap once; the later release() lands on an orphaned
        # semaphore, which is harmless.
        _evict_idle_rate_buckets(self._windows, self._semaphores, now - 60.0)

    def _bucket_key(self, actor: str | None, request: Any) -> str:
        if actor:
            return f"actor:{actor}"
        # Fall back to the client IP for anonymous calls. When even that's
        # unknown (rare), share a single anonymous bucket.
        try:
            host = getattr(getattr(request, "client", None), "host", None) or "anon"
        except Exception:  # noqa: BLE001 — best-effort; fail-open
            host = "anon"
        return f"anon:{host}"

    def acquire(self, actor: str | None, request: Any) -> threading.Semaphore:
        key = self._bucket_key(actor, request)
        with self._lock:
            sem = self._semaphores.get(key)
            if sem is None:
                sem = threading.Semaphore(self._concurrent)
                self._semaphores[key] = sem
            window = self._windows.get(key)
            if window is None:
                window = deque()
                self._windows[key] = window
            # Sliding 60s window. Drop stale calls.
            now = time.monotonic()
            cutoff = now - 60.0
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= self._per_min:
                raise HTTPException(
                    429,
                    f"rate limit: max {self._per_min} completions/min per actor",
                )
            window.append(now)
            # Bound the bucket dicts against a rotating-IP flood.
            self._prune_locked(now)
        # Acquire outside the lock so a saturated actor doesn't block
        # everyone else.
        if not sem.acquire(blocking=False):
            raise HTTPException(
                429,
                f"rate limit: max {self._concurrent} concurrent completions per actor",
            )
        return sem

    def release(self, sem: threading.Semaphore) -> None:
        sem.release()
