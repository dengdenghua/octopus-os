"""Idempotency Guard — content-hash request dedup with TTL.

Webhook retries, user double-clicks, LLM-driven multi-step plans
that accidentally replay a committed action — all benefit from an
idempotency layer that returns the previous result instead of
executing again.

Design
------
* **Keyed** — the caller supplies an ``idempotency_key`` (or lets
  the guard hash the args). Identical keys within the TTL return
  the cached result; different keys run fresh.
* **TTL** — every entry has an expiry timestamp. Expired entries
  are lazy-swept on access; a periodic ``purge()`` drops them
  eagerly for long-running processes.
* **Pending-state tracking** — when two concurrent callers arrive
  at the same key, the first runs the operation and the second
  blocks on an ``Event`` until the first commits. Prevents the
  "thundering herd" effect on cold keys.
* **Success-only caching (configurable)** — by default, exceptions
  are not cached (the next call retries fresh). Pass
  ``cache_errors=True`` to cache the exception too.

Usage
-----

Functional::

    from runtime.platform.runtime_policy.idempotency import IdempotencyGuard

    guard = IdempotencyGuard(ttl_seconds=300)

    result = guard.run(
        key="webhook:order-12345",
        fn=lambda: process_order("12345"),
    )
    # Second call with the same key within 5 minutes returns the
    # cached result without running process_order again.

Decorator::

    @guard.memoize(ttl_seconds=60)
    def expensive(user_id: str) -> dict: ...

    # Key is auto-derived from the hash of (fn name, args, kwargs).

Integration points
------------------
* Siphon webhook router — dedupe inbound webhooks by ``event_id``.
* Beak ``execute_step`` — guard against retries of committed
  write operations (by step_id + args hash).
* Ambient suggestions → action — dedupe user double-clicks on
  "accept suggestion" buttons.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

T = TypeVar("T")

# Default TTL: 10 minutes. Chosen to match the outer bound of most
# webhook retry windows without keeping cache state forever.
DEFAULT_TTL_SECONDS: float = 600.0


@dataclass
class _Entry:
    """One cache slot — holds either a result or an in-flight wait."""

    expires_at: float
    value: Any = None
    exc: BaseException | None = None
    # When set, another thread is currently running the fn. Waiters
    # block on this event and then read value/exc.
    pending: threading.Event | None = None


class IdempotencyGuard:
    """Per-key result cache with TTL and in-flight dedup.

    Parameters
    ----------
    ttl_seconds:
        Default time-to-live for cached entries. Per-call override
        via ``run(..., ttl_seconds=...)``.
    cache_errors:
        When True, a raised exception is cached and re-raised for
        subsequent callers within the TTL. Default False — errors
        retry fresh (safer for transient failures).
    clock:
        Injectable clock for tests. Defaults to ``time.monotonic``.
    max_entries:
        Soft cap on cache size. When exceeded, ``purge()`` is called
        opportunistically on the next write so expired entries are
        evicted before we exceed the cap. Further growth past the
        cap is allowed (eviction is best-effort, not LRU).
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        cache_errors: bool = False,
        clock: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self._ttl = ttl_seconds
        self._cache_errors = cache_errors
        self._clock = clock
        self._max = max_entries
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        # Stats
        self._hits = 0
        self._misses = 0
        self._waits = 0
        self._errors_cached = 0

    # ── public API ──────────────────────────────────────────

    def run(
        self,
        *,
        key: str,
        fn: Callable[..., T],
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        ttl_seconds: float | None = None,
    ) -> T:
        """Execute ``fn(*args, **kwargs)`` unless a fresh cached entry
        exists for ``key``; otherwise return (or raise) the cached
        result.

        Two simultaneous callers with the same key: the first runs,
        the second blocks on an internal event and then reads the
        first caller's result. This prevents the thundering-herd
        effect on cold keys.
        """
        if not key:
            raise ValueError("key required")
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        now = self._clock()

        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                if entry.pending is not None:
                    # Another thread is currently running. Block.
                    wait_event = entry.pending
                    self._waits += 1
                    # Fall through to waiting below.
                elif entry.expires_at > now:
                    # Fresh cached hit.
                    self._hits += 1
                    if entry.exc is not None:
                        raise entry.exc
                    return entry.value  # type: ignore[return-value]
                else:
                    # Expired. Treat as a miss — replace below.
                    wait_event = None
                    entry = None
            else:
                wait_event = None

            if entry is None or entry.pending is None and entry.expires_at <= now:
                # We own the slot.
                if len(self._entries) >= self._max:
                    self._purge_locked(now)
                entry = _Entry(
                    expires_at=now + ttl,
                    pending=threading.Event(),
                )
                self._entries[key] = entry
                owner = True
                self._misses += 1
            else:
                # We're a waiter.
                owner = False

        if not owner:
            # Release the lock and wait for the owner to finish.
            wait_event.wait(timeout=max(ttl + 1, 30.0))
            with self._lock:
                final = self._entries.get(key)
            if final is None or final.pending is not None:
                # Owner vanished or timed out. Fall through: run fresh.
                return self.run(
                    key=key,
                    fn=fn,
                    args=args,
                    kwargs=kwargs,
                    ttl_seconds=ttl_seconds,
                )
            if final.exc is not None:
                raise final.exc
            return final.value  # type: ignore[return-value]

        # We are the owner.
        try:
            result = fn(*args, **(kwargs or {}))
        except BaseException as exc:  # noqa: BLE001
            with self._lock:
                if self._cache_errors:
                    entry.exc = exc
                    entry.pending.set()  # release waiters
                    entry.pending = None
                    self._errors_cached += 1
                else:
                    # Don't cache failures — remove slot so the next
                    # caller gets a fresh attempt.
                    evt = entry.pending
                    self._entries.pop(key, None)
                    entry.pending = None
                    evt.set()
            raise
        else:
            with self._lock:
                entry.value = result
                entry.expires_at = self._clock() + ttl
                entry.pending.set()
                entry.pending = None
            return result

    def memoize(
        self,
        *,
        ttl_seconds: float | None = None,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """Decorator form — auto-hashes (fn name, args, kwargs) for key."""

        def decorator(fn: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args: Any, **kwargs: Any) -> T:
                key = compute_key(fn.__qualname__, args, kwargs)
                return self.run(
                    key=key,
                    fn=fn,
                    args=args,
                    kwargs=kwargs,
                    ttl_seconds=ttl_seconds,
                )

            wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
            return wrapper

        return decorator

    def invalidate(self, key: str) -> bool:
        """Drop a single cached entry. Returns True if found."""
        with self._lock:
            return self._entries.pop(key, None) is not None

    def clear(self) -> None:
        """Wipe every cached entry."""
        with self._lock:
            self._entries.clear()

    def purge(self) -> int:
        """Drop all expired entries. Returns count removed."""
        with self._lock:
            return self._purge_locked(self._clock())

    def stats(self) -> dict[str, int]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "waits": self._waits,
                "errors_cached": self._errors_cached,
                "size": len(self._entries),
                "hit_ratio_bp": (int(10_000 * self._hits / total) if total > 0 else 0),
            }

    # ── internals ───────────────────────────────────────────

    def _purge_locked(self, now: float) -> int:
        removed = 0
        for key in list(self._entries.keys()):
            e = self._entries[key]
            if e.pending is None and e.expires_at <= now:
                del self._entries[key]
                removed += 1
        return removed


# ── Key helpers ────────────────────────────────────────────────


def compute_key(*parts: Any) -> str:
    """Compute a stable SHA-256 key from heterogeneous parts.

    Useful when the caller wants a key derived from request body,
    user id, and a timestamp bucket::

        key = compute_key(
            "webhook",
            request_body,
            user_id,
            int(time.time() // 60),  # 1-minute bucket
        )
    """
    hasher = hashlib.sha256()
    for p in parts:
        if isinstance(p, (bytes, bytearray)):
            hasher.update(p)
        elif isinstance(p, str):
            hasher.update(p.encode("utf-8"))
        else:
            try:
                hasher.update(json.dumps(p, sort_keys=True, default=str).encode("utf-8"))
            except (TypeError, ValueError):
                hasher.update(repr(p).encode("utf-8"))
        hasher.update(b"\x00")  # separator
    return hasher.hexdigest()


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "IdempotencyGuard",
    "compute_key",
]
