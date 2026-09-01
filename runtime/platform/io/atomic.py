"""
Atomic file writes with `.bak` rollover and optional debounce.

Design:

  1. Write payload to sibling ``<path>.tmp-<pid>-<uuid>`` in the same
     directory (required: same dir = same filesystem = atomic rename).
  2. ``fsync`` the temp file so the bytes hit disk before we swap.
  3. If the target already exists, move it to ``<path>.bak`` atomically
     (``os.replace`` is atomic on POSIX; on Windows it replaces
     atomically too). Any previous ``.bak`` is overwritten.
  4. ``os.replace`` the temp onto the target. After this call returns,
     either the new version is fully visible, or the old version still
     is — readers never see a half-written file.
  5. Best-effort ``fsync`` on the directory so the rename is durable
     (POSIX only; no-op on Windows where directory fsync is not
     exposed).

Failure modes this defends against:
  - Process crash mid-write  → old version intact
  - Power loss mid-rename    → old version OR new version, never both
                                partially
  - Partial write (ENOSPC)   → old version intact; caller sees raised
                                exception, can decide to retry or not

Use ``read_json_with_backup()`` to opportunistically recover from
``.bak`` when the primary file is corrupted (e.g. manual edit gone
wrong, older bug). That makes the pair self-healing across restarts.

Not a goal:
  - Cross-host durability (that's a database)
  - Lock-free concurrent writers (use a ``threading.Lock``; see
    ``debounced_json_writer`` for a ready-made wrapper)
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any


class AtomicWriteError(OSError):
    """Raised when an atomic write fails after cleanup."""


# Per-absolute-path locks. Serializes concurrent writers to the same
# target. Necessary on Windows where `os.replace` of a file another
# thread is actively swapping can briefly fail with EACCES; on POSIX
# it's cheap insurance against interleaved .bak rotation.
#
# WeakValueDictionary: locks for paths no-one references anymore are
# reclaimed by GC, so the dict doesn't grow unbounded in long-running
# servers that touch many files.
_PATH_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve() if path.is_absolute() else path.absolute())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PATH_LOCKS[key] = lock
        return lock


@contextlib.contextmanager
def _cross_process_lock(target: Path):
    """Best-effort cross-process lock for ``target`` using a sidecar.

    ``_lock_for`` serialises writers within a single Python process.
    When uvicorn runs with ``--workers N>1`` (or two CLI invocations
    touch the same file), two processes each hold their own
    ``threading.Lock`` for the same path and do not serialise.

    We open ``<target>.lock`` (a tiny sibling file) and acquire an
    OS-level advisory lock on it via ``fcntl.flock`` on POSIX or
    ``msvcrt.locking`` on Windows. The sidecar persists across
    invocations (leaving behind a 0-byte file is fine) so subsequent
    processes can re-acquire cleanly.

    If the locking primitive is unavailable (or a platform doesn't
    support it), the context manager is a no-op — same behaviour
    as before this change, no regression.
    """
    lock_path = target.parent / (target.name + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return
    try:
        fd = os.open(
            str(lock_path),
            os.O_CREAT | os.O_RDWR,
            0o644,
        )
    except OSError:
        yield
        return
    locked = False
    try:
        if os.name == "nt":
            try:
                import msvcrt as _msvcrt

                _msvcrt.locking(fd, _msvcrt.LK_LOCK, 1)
                locked = True
            except OSError:
                locked = False
        else:
            try:
                import fcntl as _fcntl

                _fcntl.flock(fd, _fcntl.LOCK_EX)
                locked = True
            except (OSError, ImportError):
                locked = False
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt as _msvcrt

                    try:  # noqa: SIM105
                        os.lseek(fd, 0, 0)
                    except OSError:  # noqa: BLE001 — atomic-write helper cleanup; never break primary write
                        pass
                    _msvcrt.locking(fd, _msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl as _fcntl

                    _fcntl.flock(fd, _fcntl.LOCK_UN)
            except OSError:  # noqa: BLE001 — atomic write cleanup best-effort
                pass
        with contextlib.suppress(OSError):
            os.close(fd)


def _replace_with_retry(
    src: Path,
    dst: Path,
    *,
    swallow: bool = False,
    attempts: int = 5,
) -> None:
    """``os.replace(src, dst)`` with bounded retry.

    On Windows, replace can transiently fail with EACCES/EBUSY when
    the target is briefly held open by AV scanners, indexers, or
    sibling threads. The fix is dumb but reliable: retry with a
    short backoff, only on Windows-style errors.

    POSIX is rename-atomic and almost never produces this; we still
    attempt one retry to cover network filesystems.
    """
    delay = 0.005
    last_err: OSError | None = None
    for _ in range(attempts):
        try:
            os.replace(src, dst)
            return
        except OSError as exc:
            last_err = exc
            time.sleep(delay)
            delay = min(delay * 2, 0.1)
    if swallow:
        return
    assert last_err is not None
    raise last_err


def _fsync_dir(path: Path) -> None:
    # Durably commit the rename on POSIX. Not available on Windows
    # (os.open with O_DIRECTORY fails), so we silently skip.
    if os.name != "posix":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:  # noqa: BLE001 — fsync best-effort; consistency maintained by atomic rename
        pass
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def atomic_write_bytes(
    path: str | os.PathLike[str],
    data: bytes,
    *,
    keep_backup: bool = True,
    fsync: bool = True,
    mode: int | None = None,
) -> None:
    """Write ``data`` to ``path`` atomically.

    Args:
        path: Target file.
        data: Bytes to write.
        keep_backup: If ``True`` and the target exists, move it to
            ``<path>.bak`` before replacing. Previous ``.bak`` is
            overwritten.
        fsync: If ``True``, fsync temp file and parent directory. Set
            to ``False`` for hot-path writes where durability is less
            important than throughput.
        mode: If set (e.g. ``0o600``), the file is created with exactly
            these permissions — applied to the temp file *before* any
            bytes are written, so the final file is never briefly
            world-readable. This closes the TOCTOU window a caller would
            otherwise have doing ``atomic_write_json(...)`` then
            ``os.chmod(..., 0o600)`` on a secret file. ``None`` keeps the
            default (0o644 minus umask).

    Raises:
        AtomicWriteError: on any filesystem error after attempting
            cleanup of the temp file.
    """
    target = Path(path)
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f".{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
    tmp_path = parent / tmp_name

    with _lock_for(target), _cross_process_lock(target):
        try:
            # Write + fsync the temp file.
            fd = os.open(
                str(tmp_path),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode if mode is not None else 0o644,
            )
            # O_CREAT mode is masked by umask, so for a secret file force
            # the exact bits before writing — the temp (and the target it
            # is renamed onto) is thus never wider than requested.
            if mode is not None:
                with contextlib.suppress(OSError):
                    os.fchmod(fd, mode)
            # fd ownership transfers to fdopen context; if the open
            # succeeded but a subsequent write failed, fdopen's
            # ``__exit__`` already closed fd, so a re-raise is enough
            # to propagate to the outer ``except`` for cleanup.
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                if fsync:
                    with contextlib.suppress(OSError):
                        os.fsync(fh.fileno())

            # Rotate existing target → .bak (best-effort; a
            # failure here should not block the write since the
            # temp is already good and we'd rather land the new
            # version than preserve the old).
            if keep_backup and target.exists():
                bak = target.with_suffix(target.suffix + ".bak")
                _replace_with_retry(target, bak, swallow=True)

            _replace_with_retry(tmp_path, target)

            if fsync:
                _fsync_dir(parent)
        except Exception as exc:
            # Best-effort cleanup of orphaned temp file.
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:  # noqa: BLE001 — atomic write cleanup best-effort
                pass
            raise AtomicWriteError(f"atomic write to {target} failed") from exc


def atomic_write_text(
    path: str | os.PathLike[str],
    data: str,
    *,
    encoding: str = "utf-8",
    newline: str | None = "\n",
    keep_backup: bool = True,
    fsync: bool = True,
    mode: int | None = None,
) -> None:
    """Atomic text write. See ``atomic_write_bytes`` for semantics.

    If ``newline`` is not ``None`` and ``data`` does not end with it,
    the newline is appended. POSIX tools (grep, diff, git) expect a
    trailing newline; this default matches that expectation.
    """
    if newline is not None and data and not data.endswith(newline):
        data = data + newline
    atomic_write_bytes(
        path,
        data.encode(encoding),
        keep_backup=keep_backup,
        fsync=fsync,
        mode=mode,
    )


def atomic_write_json(
    path: str | os.PathLike[str],
    obj: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    keep_backup: bool = True,
    fsync: bool = True,
    default: Callable[[Any], Any] | None = None,
    mode: int | None = None,
) -> None:
    """Serialize ``obj`` to ``path`` as JSON atomically.

    Defaults mirror what the codebase was already doing in 40+ ad-hoc
    call sites: ``indent=2``, ``ensure_ascii=False``, trailing
    newline. Pass ``mode=0o600`` for secret files (tokens, keys) so they
    are created with restrictive permissions from the start.
    """
    payload = json.dumps(
        obj,
        indent=indent,
        ensure_ascii=ensure_ascii,
        sort_keys=sort_keys,
        default=default,
    )
    atomic_write_text(
        path,
        payload,
        keep_backup=keep_backup,
        fsync=fsync,
        mode=mode,
    )


def read_json_with_backup(
    path: str | os.PathLike[str],
    *,
    default: Any = None,
) -> Any:
    """Read JSON from ``path``, falling back to ``<path>.bak``.

    Returns ``default`` if neither exists or both are corrupt. Never
    raises. Corrupt primary + good backup is the case this exists
    for: after a partial write on an older codepath, or a user
    hand-editing the JSON, the ``.bak`` is our parachute.
    """
    target = Path(path)
    bak = target.with_suffix(target.suffix + ".bak")

    for candidate in (target, bak):
        try:
            with candidate.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
    return default


class _DebouncedWriter:
    """Coalesce bursts of writes to the same file into one atomic
    write after ``interval_s`` of quiet time.

    Fire-and-forget: callers do ``writer.queue(obj)`` and move on. A
    single worker thread performs the actual atomic write. If the
    process exits, ``flush()`` forces a synchronous write of any
    pending payload.

    Use this for files that get updated 10+ times/second with small
    diffs (``pause_state.json``, ``recipe_scores.json``, UI state)
    where each individual write doesn't need to be durable but losing
    the tail of the stream on crash is acceptable.

    For files where every write must land, call
    ``atomic_write_json()`` directly.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        interval_s: float = 0.5,
        indent: int | None = 2,
    ) -> None:
        self._path = Path(path)
        self._interval = interval_s
        self._indent = indent
        self._pending: Any = _SENTINEL
        self._lock = threading.Lock()
        self._last_queued_at: float = 0.0
        self._cond = threading.Condition(self._lock)
        self._stop = False
        self._worker: threading.Thread | None = None

    def queue(self, obj: Any) -> None:
        """Stage ``obj`` as the next payload to write."""
        with self._cond:
            self._pending = obj
            self._last_queued_at = time.monotonic()
            if self._worker is None or not self._worker.is_alive():
                self._stop = False
                self._worker = threading.Thread(
                    target=self._run,
                    name=f"debounced-write-{self._path.name}",
                    daemon=True,
                )
                self._worker.start()
            self._cond.notify_all()

    def flush(self) -> None:
        """Synchronously write the pending payload, if any."""
        with self._cond:
            if self._pending is _SENTINEL:
                return
            payload = self._pending
            self._pending = _SENTINEL
        atomic_write_json(self._path, payload, indent=self._indent)

    def close(self) -> None:
        """Stop the worker thread after flushing."""
        self.flush()
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=2.0)

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._stop and self._pending is _SENTINEL:
                    self._cond.wait()
                if self._stop and self._pending is _SENTINEL:
                    return
                # Wait until it has been quiet for interval_s.
                while not self._stop:
                    elapsed = time.monotonic() - self._last_queued_at
                    remaining = self._interval - elapsed
                    if remaining <= 0:
                        break
                    self._cond.wait(timeout=remaining)
                payload = self._pending
                self._pending = _SENTINEL
            # Concurrent flush() may have drained the payload between
            # our wait ending and us taking it; skip in that case.
            if payload is _SENTINEL:
                continue
            with contextlib.suppress(AtomicWriteError):
                atomic_write_json(
                    self._path,
                    payload,
                    indent=self._indent,
                )


_SENTINEL = object()


def debounced_json_writer(
    path: str | os.PathLike[str],
    *,
    interval_s: float = 0.5,
    indent: int | None = 2,
) -> _DebouncedWriter:
    """Factory for a per-file debounced writer.

    Typical usage::

        _writer = debounced_json_writer(state_path)
        # ... on every state change:
        _writer.queue(current_state)
        # ... on shutdown:
        _writer.close()
    """
    return _DebouncedWriter(path, interval_s=interval_s, indent=indent)
