"""Per-skill rate limiter — runaway-loop protection for LLM agents.

When an LLM gets stuck in a loop (e.g. repeatedly calling
``web_search`` with the same query because the result doesn't answer
its need), the agent can burn through budget and produce nothing.
This module adds a cheap per-skill token bucket so a misbehaving
planner is forced to diversify.

Design
------
Pure token-bucket, in-memory, per-process. Each skill gets its own
bucket keyed by ``skill_name`` — optionally further keyed by
``caller`` (e.g. arm_id) so two unrelated arms don't starve each
other.

Default limits:
  * capacity = 20 calls
  * refill = 20 calls / 60 s

Values are tunable at construction time. A limiter rejects a call
by returning ``(False, retry_after_seconds)`` — the caller decides
whether to back off, raise, or route to a different skill.

Integration
-----------
Wire this in at the Beak (``ToolExecutor.execute_step``) layer so
every skill invocation passes through the gate before the handler
runs. Suggested placement: right after the ``CapabilityPermissions``
check and before the ``TrustEngine`` call — rate limits are cheaper
to evaluate than immune verdicts so short-circuiting here saves
work on the common-case "not rate-limited".

Thread-safety
-------------
All state access is guarded by a single mutex. For the expected
call volume (a few hundred calls/minute) contention is negligible.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

_LOG = logging.getLogger("echo.suckers.rate_limit")

# ── defaults ─────────────────────────────────────────────────
DEFAULT_CAPACITY = 20
DEFAULT_REFILL_RATE = 20.0 / 60.0  # 20 tokens per 60 s


@dataclass
class _Bucket:
    capacity: float
    tokens: float
    refill_per_sec: float
    last_refill: float

    def refill(self, now: float) -> None:
        if now <= self.last_refill:
            return
        delta = (now - self.last_refill) * self.refill_per_sec
        self.tokens = min(self.capacity, self.tokens + delta)
        self.last_refill = now

    def try_consume(self, now: float, n: float = 1.0) -> tuple[bool, float]:
        """Attempt to take ``n`` tokens. Returns (ok, retry_after_seconds)."""
        self.refill(now)
        if self.tokens >= n:
            self.tokens -= n
            return True, 0.0
        # Compute seconds until enough tokens are available.
        deficit = n - self.tokens
        if self.refill_per_sec <= 0:
            return False, float("inf")
        return False, deficit / self.refill_per_sec


class SkillRateLimiter:
    """Per-(skill, caller) token bucket rate limiter.

    Parameters
    ----------
    capacity:
        Maximum burst size. Calls are allowed at up to ``capacity``
        per refill window without throttling.
    refill_per_sec:
        Refill rate in tokens / second. The default is 20 / 60 s
        (one call roughly every 3 seconds in steady state).
    overrides:
        Per-skill overrides ``{"web_search": (10, 0.1), ...}`` where
        each tuple is ``(capacity, refill_per_sec)``.
    clock:
        Injectable clock for tests. Defaults to ``time.monotonic``.
    """

    def __init__(
        self,
        *,
        capacity: int = DEFAULT_CAPACITY,
        refill_per_sec: float = DEFAULT_REFILL_RATE,
        overrides: dict[str, tuple[int, float]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._capacity = float(capacity)
        self._refill_per_sec = float(refill_per_sec)
        self._overrides = dict(overrides or {})
        self._clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()
        # Stats — exposed via ``stats()`` for observability.
        self._calls_total = 0
        self._throttled_total = 0

    # ── public API ──────────────────────────────────────────

    def try_acquire(
        self,
        skill_name: str,
        *,
        caller: str = "",
        cost: float = 1.0,
    ) -> tuple[bool, float]:
        """Try to reserve one token for a ``skill_name`` call.

        Returns ``(True, 0.0)`` when allowed, ``(False, retry_s)`` when
        throttled. ``retry_s`` is the wait until enough tokens refill.

        ``caller`` segments the bucket so arms don't starve each other
        — pass an arm_id, session_id, or empty string to share one
        bucket across all callers.
        """
        if not skill_name:
            return True, 0.0
        now = self._clock()
        key = (skill_name, caller)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                cap, rate = self._overrides.get(
                    skill_name,
                    (self._capacity, self._refill_per_sec),
                )
                bucket = _Bucket(
                    capacity=float(cap),
                    tokens=float(cap),
                    refill_per_sec=float(rate),
                    last_refill=now,
                )
                self._buckets[key] = bucket
            self._calls_total += 1
            ok, retry = bucket.try_consume(now, n=cost)
            if not ok:
                self._throttled_total += 1
                _LOG.info(
                    "rate_limit: throttled skill=%s caller=%s retry_in=%.2fs",
                    skill_name,
                    caller,
                    retry,
                )
            return ok, retry

    def reset(self, skill_name: str | None = None, caller: str = "") -> None:
        """Refill a specific bucket or all buckets.

        Without arguments, wipes every bucket — useful between tests.
        With ``skill_name`` + ``caller``, refills just that bucket
        (e.g. an operator override after a false-positive throttle).
        """
        with self._lock:
            if skill_name is None:
                self._buckets.clear()
                return
            key = (skill_name, caller)
            if key in self._buckets:
                bucket = self._buckets[key]
                bucket.tokens = bucket.capacity
                bucket.last_refill = self._clock()

    def stats(self) -> dict[str, int | float | dict[str, float]]:
        """Return aggregate counters + per-bucket token levels.

        Shape::

            {
                "calls_total": int,
                "throttled_total": int,
                "throttle_ratio": float,
                "buckets": {
                    "skill@caller": tokens_available,
                    ...
                }
            }
        """
        with self._lock:
            now = self._clock()
            for b in self._buckets.values():
                b.refill(now)
            total = self._calls_total
            ratio = self._throttled_total / total if total > 0 else 0.0
            return {
                "calls_total": total,
                "throttled_total": self._throttled_total,
                "throttle_ratio": ratio,
                "buckets": {
                    f"{name}@{caller}": round(b.tokens, 3)
                    for (name, caller), b in self._buckets.items()
                },
            }

    def set_override(
        self,
        skill_name: str,
        capacity: int,
        refill_per_sec: float,
    ) -> None:
        """Install / update a per-skill limit override.

        Existing buckets for that skill are reset so the new limits
        apply immediately instead of on next bucket creation.
        """
        with self._lock:
            self._overrides[skill_name] = (capacity, refill_per_sec)
            for key in list(self._buckets):
                if key[0] == skill_name:
                    del self._buckets[key]


__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_REFILL_RATE",
    "SkillRateLimiter",
]
