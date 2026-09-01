from __future__ import annotations

import contextlib
import json
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Lease:
    scope: str
    holder_id: str
    acquired_at: float
    expires_at: float
    fencing_token: int = 0  # Implementation note.

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())


class Coordinator(Protocol):
    holder_id: str  # Implementation note.

    def acquire_lease(self, scope: str, *, ttl: float) -> Lease | None:
        pass

    def renew_lease(self, lease: Lease, *, ttl: float) -> Lease | None:
        pass

    def release_lease(self, lease: Lease) -> bool:
        pass

    def current_lease(self, scope: str) -> Lease | None:
        pass


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _default_holder_id() -> str:
    import uuid

    try:
        host = socket.gethostname()
    except OSError:  # noqa: BLE001
        host = "unknown"
    return f"{host}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class InMemoryCoordinator:
    def __init__(self, holder_id: str | None = None) -> None:
        self.holder_id = holder_id or _default_holder_id()
        self._leases: dict[str, Lease] = {}
        self._counter: int = 0
        self._lock = threading.RLock()

    def acquire_lease(self, scope: str, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        now = time.time()
        with self._lock:
            existing = self._leases.get(scope)
            if existing is not None and existing.expires_at > now:  # noqa: SIM102
                if existing.holder_id != self.holder_id:
                    return None
            self._counter += 1
            lease = Lease(
                scope=scope,
                holder_id=self.holder_id,
                acquired_at=now,
                expires_at=now + ttl,
                fencing_token=self._counter,
            )
            self._leases[scope] = lease
            return lease

    def renew_lease(self, lease: Lease, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        now = time.time()
        with self._lock:
            current = self._leases.get(lease.scope)
            if current is None or current.holder_id != lease.holder_id:
                return None
            if current.fencing_token != lease.fencing_token:
                return None
            if current.expires_at <= now:
                return None
            new_lease = Lease(
                scope=lease.scope,
                holder_id=self.holder_id,
                acquired_at=current.acquired_at,
                expires_at=now + ttl,
                fencing_token=current.fencing_token,
            )
            self._leases[lease.scope] = new_lease
            return new_lease

    def release_lease(self, lease: Lease) -> bool:
        with self._lock:
            current = self._leases.get(lease.scope)
            if current is None or current.holder_id != lease.holder_id:
                return False
            if current.fencing_token != lease.fencing_token:
                return False
            del self._leases[lease.scope]
            return True

    def current_lease(self, scope: str) -> Lease | None:
        now = time.time()
        with self._lock:
            lease = self._leases.get(scope)
            if lease is None:
                return None
            if lease.expires_at <= now:
                del self._leases[scope]
                return None
            return lease


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class FileLockCoordinator:
    def __init__(
        self,
        lock_dir: str | Path,
        holder_id: str | None = None,
    ) -> None:
        self.holder_id = holder_id or _default_holder_id()
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._counter_path = self.lock_dir / ".fencing_counter"

    def _lease_path(self, scope: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in scope)
        return self.lock_dir / f"{safe}.lease"

    def _next_fencing_token(self) -> int:
        try:
            with self._counter_path.open("r+", encoding="utf-8") as f:
                raw = f.read().strip()
                n = int(raw) if raw else 0
                n += 1
                f.seek(0)
                f.truncate()
                f.write(str(n))
                return n
        except FileNotFoundError:
            self._counter_path.write_text("1", encoding="utf-8")
            return 1

    def _exclusive_op(self, path: Path, op):
        f = None
        try:
            f = path.open("a+b")
            _os_lock(f)
            try:
                return op(f)
            finally:
                _os_unlock(f)
        finally:
            if f is not None:
                f.close()

    def _read_lease_from(self, f) -> dict | None:
        f.seek(0)
        raw = f.read()
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _write_lease_to(self, f, data: dict) -> None:
        f.seek(0)
        f.truncate()
        f.write(json.dumps(data).encode("utf-8"))
        f.flush()
        with contextlib.suppress(OSError):
            os.fsync(f.fileno())

    def acquire_lease(self, scope: str, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        path = self._lease_path(scope)
        now = time.time()

        def _op(f):
            current = self._read_lease_from(f)
            if current is not None and current.get("expires_at", 0) > now:  # noqa: SIM102
                if current.get("holder_id") != self.holder_id:
                    return None
            token = self._next_fencing_token()
            data = {
                "scope": scope,
                "holder_id": self.holder_id,
                "acquired_at": now,
                "expires_at": now + ttl,
                "fencing_token": token,
            }
            self._write_lease_to(f, data)
            return Lease(**data)

        return self._exclusive_op(path, _op)

    def renew_lease(self, lease: Lease, *, ttl: float) -> Lease | None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        path = self._lease_path(lease.scope)
        now = time.time()

        def _op(f):
            current = self._read_lease_from(f)
            if current is None:
                return None
            if current.get("holder_id") != lease.holder_id:
                return None
            if current.get("fencing_token") != lease.fencing_token:
                return None
            if current.get("expires_at", 0) <= now:
                return None
            data = {
                "scope": lease.scope,
                "holder_id": self.holder_id,
                "acquired_at": current.get("acquired_at", now),
                "expires_at": now + ttl,
                "fencing_token": lease.fencing_token,
            }
            self._write_lease_to(f, data)
            return Lease(**data)

        return self._exclusive_op(path, _op)

    def release_lease(self, lease: Lease) -> bool:
        path = self._lease_path(lease.scope)

        def _op(f):
            current = self._read_lease_from(f)
            if current is None:
                return False
            if current.get("holder_id") != lease.holder_id:
                return False
            if current.get("fencing_token") != lease.fencing_token:
                return False
            f.seek(0)
            f.truncate()
            return True

        result = self._exclusive_op(path, _op)
        try:
            if path.exists() and path.stat().st_size == 0:
                path.unlink()
        except OSError:  # noqa: BLE001 — coordinator file cleanup best-effort
            pass
        return result

    def current_lease(self, scope: str) -> Lease | None:
        path = self._lease_path(scope)
        if not path.exists():
            return None
        now = time.time()

        def _op(f):
            current = self._read_lease_from(f)
            if current is None:
                return None
            if current.get("expires_at", 0) <= now:
                return None
            return Lease(**current)

        return self._exclusive_op(path, _op)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


if os.name == "nt":  # Windows
    import msvcrt

    def _os_lock(f) -> None:
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                return
            except OSError:
                time.sleep(0.01)

    def _os_unlock(f) -> None:
        try:
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:  # noqa: BLE001 — coordinator file cleanup best-effort
            pass

else:  # Unix-like
    import fcntl

    def _os_lock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _os_unlock(f) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class LeaderGuard:
    coordinator: Coordinator
    scope: str
    ttl: float
    _lease: Lease | None = field(default=None, init=False)
    is_leader: bool = field(default=False, init=False)

    def __enter__(self) -> LeaderGuard:
        self._lease = self.coordinator.acquire_lease(self.scope, ttl=self.ttl)
        self.is_leader = self._lease is not None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._lease is not None:
            with contextlib.suppress(Exception):
                self.coordinator.release_lease(self._lease)
            self._lease = None
        self.is_leader = False
