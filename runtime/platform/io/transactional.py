"""Crash-safe, cross-process transactions for small local JSON files.

The ordinary atomic writers protect readers from truncated files, but a
read-modify-write sequence needs one stable lock spanning both the read and the
replace.  This module provides that boundary for security- and lifecycle-state
files shared by multiple registry instances or server workers.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import weakref
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar


class TransactionalFileError(RuntimeError):
    """A state transaction could not be performed safely."""


_T = TypeVar("_T")


@dataclass(frozen=True)
class JsonMutation(Generic[_T]):
    """Result returned by a JSON transaction callback."""

    value: _T
    changed: bool = True


_PATH_LOCKS: weakref.WeakValueDictionary[str, Any] = weakref.WeakValueDictionary()
_PATH_LOCKS_GUARD = threading.Lock()
_THREAD_STATE = threading.local()


def _canonical(path: Path) -> tuple[str, Path]:
    target = path.expanduser().resolve(strict=False)
    return os.path.normcase(str(target)), target


def _path_lock(key: str) -> Any:
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


@contextmanager
def _os_lock(target: Path) -> Iterator[None]:
    """Acquire a stable sidecar lock, failing closed if that is impossible."""

    lock_path = target.parent / f".{target.name}.transaction.lock"
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise TransactionalFileError(f"cannot open state lock: {lock_path}") from exc

    locked = False
    try:
        if os.name == "nt":
            try:
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
            except (ImportError, OSError) as exc:
                raise TransactionalFileError(f"cannot lock state file: {target}") from exc
        elif os.name == "posix":
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            except (ImportError, OSError) as exc:
                raise TransactionalFileError(f"cannot lock state file: {target}") from exc
        else:
            raise TransactionalFileError(f"no supported state-file lock for platform {os.name!r}")
        locked = True
        yield
    finally:
        if locked:
            with contextlib.suppress(ImportError, OSError):
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


@contextmanager
def path_transaction(path: str | os.PathLike[str]) -> Iterator[Path]:
    """Hold one process-local and OS-level lock for ``path``.

    Re-entry from the same thread is supported without trying to acquire the OS
    lock twice.  Callers must use the unlocked write helpers in this module (or
    :func:`mutate_json_file`) while inside the transaction.
    """

    key, target = _canonical(Path(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = _path_lock(key)
    with lock:
        depths = getattr(_THREAD_STATE, "depths", None)
        if depths is None:
            depths = {}
            _THREAD_STATE.depths = depths
        depth = int(depths.get(key, 0))
        depths[key] = depth + 1
        try:
            if depth:
                yield target
            else:
                with _os_lock(target):
                    yield target
        finally:
            if depth:
                depths[key] = depth
            else:
                depths.pop(key, None)


def _fsync_parent(directory: Path) -> None:
    """Durably persist a directory entry on POSIX; Windows has no equivalent."""

    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, flags)
    except OSError as exc:
        raise TransactionalFileError(f"cannot open state directory: {directory}") from exc
    try:
        os.fsync(fd)
    except OSError as exc:
        raise TransactionalFileError(f"cannot fsync state directory: {directory}") from exc
    finally:
        os.close(fd)


def _atomic_replace_unlocked(path: Path, payload: bytes, *, mode: int | None) -> None:
    """Replace ``path`` durably.  The caller must hold ``path_transaction``."""

    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        if mode is not None and hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if mode is not None:
            path.chmod(mode)
        _fsync_parent(path.parent)
    except Exception:
        if fd >= 0:
            os.close(fd)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()
        raise


def _read_json_unlocked(path: Path, default_factory: Callable[[], Any]) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except FileNotFoundError:
        return default_factory()
    except (OSError, json.JSONDecodeError) as exc:
        raise TransactionalFileError(f"state file is unreadable or corrupt: {path}") from exc


def read_json_file(
    path: str | os.PathLike[str],
    *,
    default_factory: Callable[[], Any],
    validate: Callable[[Any], None],
    mode: int | None = None,
) -> Any:
    """Read and validate JSON while excluding concurrent replacements."""

    with path_transaction(path) as target:
        data = _read_json_unlocked(target, default_factory)
        validate(data)
        if mode is not None and target.exists():
            target.chmod(mode)
        return data


def mutate_json_file(
    path: str | os.PathLike[str],
    *,
    default_factory: Callable[[], Any],
    validate: Callable[[Any], None],
    mutate: Callable[[Any], JsonMutation[_T]],
    mode: int | None = None,
    indent: int | None = 1,
) -> _T:
    """Run a locked read-modify-replace transaction on one JSON file."""

    with path_transaction(path) as target:
        data = _read_json_unlocked(target, default_factory)
        validate(data)
        outcome = mutate(data)
        if outcome.changed:
            validate(data)
            payload = json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8") + b"\n"
            _atomic_replace_unlocked(target, payload, mode=mode)
        return outcome.value


def create_file_exclusive(
    path: str | os.PathLike[str],
    payload: bytes,
    *,
    mode: int = 0o600,
) -> bool:
    """Create one durable file without replacing an existing winner."""

    with path_transaction(path) as target:
        try:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        except FileExistsError:
            return False
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _fsync_parent(target.parent)
            return True
        except Exception:
            if fd >= 0:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
            raise


__all__ = [
    "JsonMutation",
    "TransactionalFileError",
    "create_file_exclusive",
    "mutate_json_file",
    "path_transaction",
    "read_json_file",
]
