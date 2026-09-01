"""SlidingWindowLimiter · per-key sliding-window rate limit, self-cleaning.

A deliberately lenient anti-abuse ceiling: it caps how many events a key
(actor id, ip, …) may record per rolling ``window_s``, allowing bursts up
to ``limit`` and only refusing sustained floods. Idle buckets — no hit
within the window — are reclaimed on an amortized sweep, so the map stays
O(active keys) even under a rotating-key flood, without ever becoming
O(keys)/call.

Not internally synchronized: drive it from a single thread / event loop
(the realtime gateway calls it only from its asyncio loop). Wrap it in a
lock if you need to share it across threads.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

# How often the amortized bucket sweep may run, and the idle-cadence when
# we're under the key cap. Bounds sweep cost to ~O(keys)/second worst case.
_SWEEP_MIN_INTERVAL = 1.0
_SWEEP_IDLE_INTERVAL = 30.0


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_s: float = 60.0,
        *,
        max_keys: int = 8192,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = max(1, int(limit))
        self._window = float(window_s)
        self._max_keys = max(1, int(max_keys))
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_sweep = 0.0

    def allow(self, key: str) -> bool:
        """Record a hit for ``key`` and report whether it stays within the
        limit. Returns False (and records nothing) when the key already has
        ``limit`` hits inside the current window."""
        now = self._clock()
        cutoff = now - self._window
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        while dq and dq[0] < cutoff:
            dq.popleft()
        allowed = len(dq) < self._limit
        if allowed:
            dq.append(now)
        # Sweep AFTER the (possible) append so the current key — now with a
        # fresh timestamp — is never mistaken for idle and dropped.
        self._maybe_sweep(now, cutoff)
        return allowed

    def _maybe_sweep(self, now: float, cutoff: float) -> None:
        elapsed = now - self._last_sweep
        if elapsed < _SWEEP_MIN_INTERVAL:
            return
        over_cap = len(self._hits) > self._max_keys
        if not over_cap and elapsed < _SWEEP_IDLE_INTERVAL:
            return
        self._last_sweep = now
        # A bucket whose newest hit predates the window is fully idle —
        # drop it. O(1) per bucket (peek the tail), no per-entry trim.
        stale = [k for k, d in self._hits.items() if not d or d[-1] < cutoff]
        for k in stale:
            del self._hits[k]

    def active_keys(self) -> int:
        """Number of live buckets — for tests / observability."""
        return len(self._hits)


__all__ = ["SlidingWindowLimiter"]
