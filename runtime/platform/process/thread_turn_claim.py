"""Strict cross-process ownership for one active turn per thread.

The realtime gateway already serializes turns with an in-process
``KeyedLock``.  Multiple Uvicorn workers (or multiple server processes)
each own a different asyncio lock, though, so that guard cannot preserve a
thread's causal ordering across processes.

This module adds the authority boundary missing from that design: a
non-blocking OS advisory lock whose file descriptor remains open for the
whole turn.  The lock is released by the kernel when the process exits, so
there is no TTL and no stale-heartbeat takeover window.  JSON stored in the
lock file is diagnostics only; it can provide an active turn id to a losing
request, but it is never used to decide ownership.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

_CLAIM_DIRECTORY = ".thread-turn-locks"
_METADATA_SENTINEL = b"0"


class ThreadTurnClaimError(RuntimeError):
    """Base class for authoritative thread-turn claim failures."""


class ThreadTurnClaimConflict(ThreadTurnClaimError):
    """Another process currently owns the thread's turn claim."""

    def __init__(
        self,
        thread_id: str,
        *,
        active_turn_id: str | None = None,
        holder_id: str | None = None,
        claim_epoch: str | None = None,
    ) -> None:
        super().__init__(f"thread {thread_id!r} already has an active turn")
        self.thread_id = thread_id
        self.active_turn_id = active_turn_id
        self.holder_id = holder_id
        # Opaque execution incarnation used by cross-process control
        # messages.  Unlike ``holder_id`` it contains no hostname/PID and is
        # safe to persist in the per-thread audit log.
        self.claim_epoch = claim_epoch


class ThreadTurnClaimUnavailable(ThreadTurnClaimError):
    """The platform/filesystem could not provide an authoritative lock."""


class ThreadTurnClaim:
    """An acquired claim that owns ``fd`` until :meth:`release` is called."""

    def __init__(
        self,
        *,
        fd: int,
        path: Path,
        thread_id: str,
        holder_id: str,
        claim_epoch: str,
        acquired_at: float,
    ) -> None:
        self._fd: int | None = fd
        self._path = path
        self.thread_id = thread_id
        self.holder_id = holder_id
        self.claim_epoch = claim_epoch
        self.acquired_at = acquired_at
        self.active_turn_id: str | None = None
        self._state_lock = threading.Lock()
        # ``release`` belongs to the foreground owner and remains idempotent.
        # Short-lived retained references can keep the descriptor locked while
        # one background EventLog append crosses the foreground turn boundary.
        self._owner_released = False
        self._retained_references = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def released(self) -> bool:
        with self._state_lock:
            return self._owner_released

    def retain_if_live(self) -> _ThreadTurnClaimReference | None:
        """Atomically borrow a live foreground claim for one bounded write.

        The returned reference owns its own idempotent ``release``. Once the
        foreground owner releases, new borrows fail even if an earlier borrow
        is still draining; this prevents a background watcher from extending a
        turn forever by chaining references.
        """

        with self._state_lock:
            if self._owner_released or self._fd is None:
                return None
            self._retained_references += 1
        return _ThreadTurnClaimReference(self)

    def bind_turn(self, turn_id: str) -> bool:
        """Attach the active turn id and opaque epoch to the held claim.

        The advisory lock remains the sole authority.  A metadata write
        failure returns ``False`` without releasing or weakening the claim;
        realtime callers fail closed because cross-worker control cannot
        safely address a holder whose metadata was not published.
        """

        clean_turn_id = str(turn_id or "").strip()
        if not clean_turn_id:
            return False
        with self._state_lock:
            fd = self._fd
            if fd is None or self._owner_released:
                return False
            self.active_turn_id = clean_turn_id
            return _write_metadata(
                fd,
                thread_id=self.thread_id,
                holder_id=self.holder_id,
                claim_epoch=self.claim_epoch,
                acquired_at=self.acquired_at,
                turn_id=clean_turn_id,
            )

    def release(self) -> None:
        """Release foreground ownership, idempotently.

        The OS descriptor closes after the final already-retained background
        reference drains. Repeated foreground cleanup calls never consume a
        retained reference.
        """

        with self._state_lock:
            if self._owner_released:
                return
            self._owner_released = True
            fd = self._detach_fd_if_unreferenced_locked()
        _close_locked_fd(fd)

    def _release_retained_reference(self) -> None:
        with self._state_lock:
            if self._retained_references <= 0:
                return
            self._retained_references -= 1
            fd = self._detach_fd_if_unreferenced_locked()
        _close_locked_fd(fd)

    def _detach_fd_if_unreferenced_locked(self) -> int | None:
        if not self._owner_released or self._retained_references:
            return None
        fd = self._fd
        self._fd = None
        return fd

    def __enter__(self) -> ThreadTurnClaim:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.release()


class _ThreadTurnClaimReference:
    """One idempotent retained reference to an acquired thread claim."""

    def __init__(self, claim: ThreadTurnClaim) -> None:
        self._claim: ThreadTurnClaim | None = claim
        self._state_lock = threading.Lock()

    def release(self) -> None:
        with self._state_lock:
            claim = self._claim
            self._claim = None
        if claim is not None:
            claim._release_retained_reference()

    def __enter__(self) -> _ThreadTurnClaimReference:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.release()


def acquire_thread_turn_claim(
    logs_root: str | os.PathLike[str],
    thread_id: str,
) -> ThreadTurnClaim:
    """Try once to claim ``thread_id``; never wait and never use a TTL.

    Raises:
        ThreadTurnClaimConflict: another live descriptor owns the claim.
        ThreadTurnClaimUnavailable: no authoritative OS lock can be obtained.
    """

    clean_thread_id = str(thread_id or "").strip()
    if not clean_thread_id:
        raise ValueError("thread_id is required")

    root = Path(logs_root)
    claim_root = root / _CLAIM_DIRECTORY
    try:
        claim_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ThreadTurnClaimUnavailable("thread turn claim directory is unavailable") from exc

    digest = hashlib.sha256(clean_thread_id.encode("utf-8")).hexdigest()
    path = claim_root / f"{digest}.lock"
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ThreadTurnClaimUnavailable("thread turn claim file is unavailable") from exc

    try:
        with suppress(AttributeError, OSError):
            os.fchmod(fd, 0o600)
        # The lock is authority; tightening an already-open diagnostic
        # sidecar is defense in depth on platforms that support fchmod.
        _try_lock(fd)
    except ThreadTurnClaimConflict:
        try:
            os.close(fd)
        finally:
            metadata = _read_metadata(path)
        raise ThreadTurnClaimConflict(
            clean_thread_id,
            active_turn_id=_clean_optional_text(metadata.get("turnId")),
            holder_id=_clean_optional_text(metadata.get("holderId")),
            claim_epoch=_clean_optional_text(metadata.get("claimEpoch")),
        ) from None
    except Exception:
        with suppress(OSError):
            os.close(fd)
        raise

    acquired_at = time.time()
    holder_id = f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:12]}"
    claim_epoch = uuid4().hex
    claim = ThreadTurnClaim(
        fd=fd,
        path=path,
        thread_id=clean_thread_id,
        holder_id=holder_id,
        claim_epoch=claim_epoch,
        acquired_at=acquired_at,
    )
    _write_metadata(
        fd,
        thread_id=clean_thread_id,
        holder_id=holder_id,
        claim_epoch=claim_epoch,
        acquired_at=acquired_at,
        turn_id=None,
    )
    return claim


def _close_locked_fd(fd: int | None) -> None:
    if fd is None:
        return
    try:
        _unlock(fd)
    finally:
        with suppress(OSError):
            os.close(fd)


def _try_lock(fd: int) -> None:
    if os.name == "posix":
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - POSIX always has it in supported builds
            raise ThreadTurnClaimUnavailable("POSIX advisory locks are unavailable") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ThreadTurnClaimConflict("") from exc
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}:
                raise ThreadTurnClaimConflict("") from exc
            raise ThreadTurnClaimUnavailable("POSIX advisory lock failed") from exc
        return

    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        try:
            import msvcrt
        except ImportError as exc:
            raise ThreadTurnClaimUnavailable("Windows advisory locks are unavailable") from exc
        try:
            if os.fstat(fd).st_size == 0:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, _METADATA_SENTINEL)
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(  # type: ignore[attr-defined]
                fd,
                msvcrt.LK_NBLCK,  # type: ignore[attr-defined]
                1,
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise ThreadTurnClaimConflict("") from exc
            raise ThreadTurnClaimUnavailable("Windows advisory lock failed") from exc
        return

    raise ThreadTurnClaimUnavailable(f"OS advisory locks unsupported on {os.name!r}")


def _unlock(fd: int) -> None:
    if os.name == "posix":
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except (ImportError, OSError):
            pass
        return
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        try:
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(  # type: ignore[attr-defined]
                fd,
                msvcrt.LK_UNLCK,  # type: ignore[attr-defined]
                1,
            )
        except (ImportError, OSError):
            pass


def _write_metadata(
    fd: int,
    *,
    thread_id: str,
    holder_id: str,
    claim_epoch: str,
    acquired_at: float,
    turn_id: str | None,
) -> bool:
    payload = json.dumps(
        {
            "threadId": thread_id,
            "holderId": holder_id,
            "claimEpoch": claim_epoch,
            "acquiredAt": acquired_at,
            "turnId": turn_id,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    data = _METADATA_SENTINEL + payload
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        offset = 0
        while offset < len(data):
            written = os.write(fd, data[offset:])
            if written <= 0:
                return False
            offset += written
        os.ftruncate(fd, len(data))
        return True
    except OSError:
        return False


def _read_metadata(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    if raw.startswith(_METADATA_SENTINEL):
        raw = raw[len(_METADATA_SENTINEL) :]
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clean_optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = [
    "ThreadTurnClaim",
    "ThreadTurnClaimConflict",
    "ThreadTurnClaimError",
    "ThreadTurnClaimUnavailable",
    "acquire_thread_turn_claim",
]
