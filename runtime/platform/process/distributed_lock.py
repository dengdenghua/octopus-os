"""Distributed Lock — single primitive, multiple backends.

Generalizes the existing ``RedisCoordinator`` lease primitive into a
backend-agnostic ``DistributedLock`` so callers can write lock-aware
code without caring whether the deployment is single-process,
multi-process Redis, or future etcd. Crucially the lock returns a
**fencing token** — a monotonically-increasing integer that identifies
the holder. A late-arriving holder's writes can be rejected by
checking the token, which is the standard fix for the ``GC pause /
network partition makes the wrong holder write`` failure mode
(Martin Kleppmann's blog, 2016).

Backends
--------
``InMemoryLockBackend``
    Single-process. Uses ``threading.Lock`` + a token counter. The
    test default and the only backend a stand-alone Echo install
    needs.

``RedisLockBackend``
    Wraps the existing ``RedisCoordinator``. Production multi-pod
    deployment uses this; the K8s readiness check (this round, #3)
    can require it before declaring readiness.

Public API
----------

    from runtime.platform.process.distributed_lock import DistributedLock

    lock = DistributedLock(scope="cron:nightly_cleanup")
    with lock.acquire(ttl_seconds=60) as lease:
        if lease is None:
            return  # someone else has it
        # ... do exclusive work ...
        # ``lease.token`` is the fencing token to attach to writes

Renewal::

    lease = lock.acquire(ttl_seconds=60).__enter__()
    # ... long-running work ...
    lease = lock.renew(ttl_seconds=60)  # extends if still held

Token check::

    if not lock.is_current_token(token=write_token):
        # The lease has been re-acquired by someone newer — abort.
        raise StaleLockToken()
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

_LOG = logging.getLogger("echo.platform.distributed_lock")


# ── Lease record ─────────────────────────────────────────────


@dataclass
class Lease:
    """One acquired exclusive lease.

    ``token`` is the fencing token. Every write protected by the lock
    should carry this token so the receiver can reject stale writes
    from a holder whose lease silently expired.
    """

    scope: str
    holder_id: str
    acquired_at: float
    expires_at: float
    token: int

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.time())

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


# ── Backend protocol ─────────────────────────────────────────


class LockBackend(Protocol):
    """Anything that can acquire / renew / release a fenced lease."""

    def acquire(
        self,
        scope: str,
        *,
        holder_id: str,
        ttl_seconds: float,
    ) -> Lease | None: ...

    def renew(
        self,
        lease: Lease,
        *,
        ttl_seconds: float,
    ) -> Lease | None: ...

    def release(self, lease: Lease) -> bool: ...

    def current_token(self, scope: str) -> int | None: ...


# ── In-memory backend ────────────────────────────────────────


class InMemoryLockBackend:
    """Single-process backend. Threads coordinate via ``threading.Lock``.

    Use this for tests, local development, and any deployment that
    runs as exactly one process. Multi-pod K8s should use
    ``RedisLockBackend``.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._holders: dict[str, tuple[str, int, float]] = {}  # scope → (holder, token, expires_at)
        self._counter: dict[str, int] = {}

    def _next_token(self, scope: str) -> int:
        self._counter[scope] = self._counter.get(scope, 0) + 1
        return self._counter[scope]

    def acquire(
        self,
        scope: str,
        *,
        holder_id: str,
        ttl_seconds: float,
    ) -> Lease | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        now = time.time()
        with self._guard:
            existing = self._holders.get(scope)
            if existing is not None:
                cur_holder, cur_token, expires_at = existing
                if now < expires_at and cur_holder != holder_id:
                    return None  # someone else owns it
                if cur_holder == holder_id and now < expires_at:
                    # Re-entrant refresh — extend without bumping token.
                    new_expires = now + ttl_seconds
                    self._holders[scope] = (holder_id, cur_token, new_expires)
                    return Lease(
                        scope=scope,
                        holder_id=holder_id,
                        acquired_at=now,
                        expires_at=new_expires,
                        token=cur_token,
                    )
            # Acquire fresh — bump token monotonically.
            token = self._next_token(scope)
            self._holders[scope] = (holder_id, token, now + ttl_seconds)
            return Lease(
                scope=scope,
                holder_id=holder_id,
                acquired_at=now,
                expires_at=now + ttl_seconds,
                token=token,
            )

    def renew(
        self,
        lease: Lease,
        *,
        ttl_seconds: float,
    ) -> Lease | None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        now = time.time()
        with self._guard:
            existing = self._holders.get(lease.scope)
            if existing is None:
                return None
            cur_holder, cur_token, _ = existing
            if cur_holder != lease.holder_id or cur_token != lease.token:
                return None  # we no longer hold it
            new_expires = now + ttl_seconds
            self._holders[lease.scope] = (cur_holder, cur_token, new_expires)
            return Lease(
                scope=lease.scope,
                holder_id=cur_holder,
                acquired_at=lease.acquired_at,
                expires_at=new_expires,
                token=cur_token,
            )

    def release(self, lease: Lease) -> bool:
        with self._guard:
            existing = self._holders.get(lease.scope)
            if existing is None:
                return False
            cur_holder, cur_token, _ = existing
            if cur_holder != lease.holder_id or cur_token != lease.token:
                return False  # someone else now holds it
            del self._holders[lease.scope]
            return True

    def current_token(self, scope: str) -> int | None:
        with self._guard:
            existing = self._holders.get(scope)
            if existing is None:
                return None
            _, token, expires_at = existing
            if time.time() >= expires_at:
                return None
            return token


# ── Redis backend ────────────────────────────────────────────


class RedisLockBackend:
    """Production backend wrapping the existing ``RedisCoordinator``.

    Constructed lazily — the import only fires when the backend is
    actually used so test environments without redis don't pay a
    startup cost.
    """

    def __init__(
        self,
        coordinator: Any | None = None,
        *,
        client: Any | None = None,
        key_prefix: str = "echo:lease:",
    ) -> None:
        if coordinator is not None:
            self._coord = coordinator
        else:
            from runtime.core.hearts.redis_coordinator import RedisCoordinator

            if client is None:
                raise ValueError("client or coordinator required")
            self._coord = RedisCoordinator(client, key_prefix=key_prefix)

    @staticmethod
    def _to_lease(scope: str, raw: Any) -> Lease | None:
        if raw is None:
            return None
        return Lease(
            scope=scope,
            holder_id=getattr(raw, "holder_id", ""),
            acquired_at=getattr(raw, "acquired_at", 0.0),
            expires_at=getattr(raw, "expires_at", 0.0),
            token=int(getattr(raw, "fencing_token", 0)),
        )

    def acquire(
        self,
        scope: str,
        *,
        holder_id: str,
        ttl_seconds: float,
    ) -> Lease | None:
        # RedisCoordinator's holder_id is configured at construction.
        # If callers pass a different holder_id we honor it by
        # rebinding the coord's holder for this call.
        prev_holder = self._coord.holder_id
        try:
            self._coord.holder_id = holder_id
            raw = self._coord.acquire_lease(scope, ttl=ttl_seconds)
        finally:
            self._coord.holder_id = prev_holder
        return self._to_lease(scope, raw)

    def renew(
        self,
        lease: Lease,
        *,
        ttl_seconds: float,
    ) -> Lease | None:
        # RedisCoordinator.renew_lease takes its own Lease type.
        from runtime.core.hearts.redis_coordinator import (
            Lease as _CoordLease,
        )

        coord_lease = _CoordLease(
            scope=lease.scope,
            holder_id=lease.holder_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            fencing_token=lease.token,
        )
        prev_holder = self._coord.holder_id
        try:
            self._coord.holder_id = lease.holder_id
            raw = self._coord.renew_lease(coord_lease, ttl=ttl_seconds)
        finally:
            self._coord.holder_id = prev_holder
        return self._to_lease(lease.scope, raw)

    def release(self, lease: Lease) -> bool:
        from runtime.core.hearts.redis_coordinator import (
            Lease as _CoordLease,
        )

        coord_lease = _CoordLease(
            scope=lease.scope,
            holder_id=lease.holder_id,
            acquired_at=lease.acquired_at,
            expires_at=lease.expires_at,
            fencing_token=lease.token,
        )
        prev_holder = self._coord.holder_id
        try:
            self._coord.holder_id = lease.holder_id
            return bool(self._coord.release_lease(coord_lease))
        finally:
            self._coord.holder_id = prev_holder

    def current_token(self, scope: str) -> int | None:
        # The coordinator stores ``holder|token`` in the value.
        raw = self._coord.client.get(self._coord._key(scope))
        parsed = self._coord._parse_value(raw)
        return parsed[1] if parsed else None


# ── DistributedLock ─────────────────────────────────────────


def _default_holder_id() -> str:
    """Stable holder id ≈ host:pid:thread."""
    return f"{socket.gethostname()}:{os.getpid()}:{threading.get_ident()}"


class DistributedLock:
    """High-level, backend-agnostic exclusive lock.

    Parameters
    ----------
    scope:
        Logical lock identifier — e.g. ``"cron:nightly_cleanup"``.
    backend:
        A ``LockBackend``. Defaults to a process-local ``InMemoryLockBackend``.
    holder_id:
        Identifies this holder. Defaults to ``host:pid:thread``.
    """

    def __init__(
        self,
        scope: str,
        *,
        backend: LockBackend | None = None,
        holder_id: str | None = None,
    ) -> None:
        if not scope:
            raise ValueError("scope required")
        self.scope = scope
        self.backend = backend or InMemoryLockBackend()
        self.holder_id = holder_id or _default_holder_id()
        self._lease: Lease | None = None

    # ── core API ────────────────────────────────────────────

    def try_acquire(self, *, ttl_seconds: float) -> Lease | None:
        """Non-blocking acquire. Returns ``Lease`` or ``None``."""
        lease = self.backend.acquire(
            self.scope,
            holder_id=self.holder_id,
            ttl_seconds=ttl_seconds,
        )
        if lease is not None:
            self._lease = lease
        return lease

    def renew(self, *, ttl_seconds: float) -> Lease | None:
        """Extend the current lease. Returns ``None`` if lost."""
        if self._lease is None:
            return None
        new_lease = self.backend.renew(self._lease, ttl_seconds=ttl_seconds)
        self._lease = new_lease
        return new_lease

    def release(self) -> bool:
        """Release the current lease. Returns ``True`` if owner released."""
        if self._lease is None:
            return False
        ok = self.backend.release(self._lease)
        self._lease = None
        return ok

    def current_token(self) -> int | None:
        """Return the active fencing token for this scope, or None."""
        return self.backend.current_token(self.scope)

    def is_current_token(self, *, token: int) -> bool:
        """True if ``token`` matches the live token for this scope.

        Use to fence a write: ``if not lock.is_current_token(token=t): abort()``.
        """
        cur = self.current_token()
        return cur is not None and cur == token

    # ── context manager ────────────────────────────────────

    @contextmanager
    def acquire(self, *, ttl_seconds: float) -> Iterator[Lease | None]:
        """Context manager: acquire on enter, release on exit.

        Yields ``None`` when the lock could not be acquired so the
        caller can simply ``if lease is None: return``.
        """
        lease = self.try_acquire(ttl_seconds=ttl_seconds)
        try:
            yield lease
        finally:
            if lease is not None:
                self.release()


__all__ = [
    "DistributedLock",
    "InMemoryLockBackend",
    "Lease",
    "LockBackend",
    "RedisLockBackend",
]
