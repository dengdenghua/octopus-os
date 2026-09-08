"""Process lock for one Echo appliance state directory.

The running appliance holds an exclusive lock. Offline backup tooling asks for
the same lock, so a second runtime cannot start and a backup cannot take a torn
snapshot while Agent, audit or credential writers are active.
"""

from __future__ import annotations

import contextlib
import errno
import os
import stat
from pathlib import Path
from typing import Any

LOCK_FILENAME = ".echo-state.lock"


class StateLockError(RuntimeError):
    pass


class StateDirectoryLock:
    def __init__(self, descriptor: int, path: Path, *, directory_guard: Any = None) -> None:
        self._descriptor = descriptor
        self.path = path
        self._directory_guard = directory_guard

    @classmethod
    def acquire(
        cls,
        state_dir: Path | str,
        *,
        exclusive: bool,
        create: bool = False,
        purpose: str | None = None,
    ) -> StateDirectoryLock:
        if os.name == "nt":
            return cls._acquire_windows(
                state_dir, exclusive=exclusive, create=create, purpose=purpose
            )
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - unsupported non-Windows platform
            raise StateLockError("state locking requires a Unix host") from exc

        root = Path(state_dir)
        if create:
            root.mkdir(parents=True, exist_ok=True)
        if not root.is_dir():
            raise StateLockError(f"state directory does not exist: {root}")
        path = root / LOCK_FILENAME
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise StateLockError(f"cannot open private state lock: {path}") from exc

        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise StateLockError("state lock is not a regular file")
            os.fchmod(descriptor, 0o600)
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            try:
                fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EAGAIN}:
                    mode = purpose or ("backup/restore" if exclusive else "runtime")
                    raise StateLockError(
                        f"state directory is already in use; cannot start {mode}"
                    ) from exc
                raise
        except Exception:
            os.close(descriptor)
            raise
        return cls(descriptor, path)

    @classmethod
    def _acquire_windows(
        cls, state_dir: Path | str, *, exclusive: bool, create: bool, purpose: str | None
    ) -> StateDirectoryLock:
        from appliance.windows_state import (
            lock_descriptor,
            open_private_file,
            private_state_directory,
        )

        guard = private_state_directory(state_dir, create=create, protect=False)
        descriptor = -1
        try:
            root = guard.__enter__()
            path = root / LOCK_FILENAME
            descriptor = open_private_file(path, lock_file=True)
            try:
                lock_descriptor(descriptor, exclusive=exclusive)
            except OSError as exc:
                if getattr(exc, "winerror", None) in {32, 33, 158}:
                    mode = purpose or ("backup/restore" if exclusive else "runtime")
                    raise StateLockError(
                        f"state directory is already in use; cannot start {mode}"
                    ) from exc
                raise
            return cls(descriptor, path, directory_guard=guard)
        except BaseException as exc:
            if descriptor >= 0:
                os.close(descriptor)
            guard.__exit__(None, None, None)
            if isinstance(exc, (StateLockError, KeyboardInterrupt, SystemExit)):
                raise
            raise StateLockError("cannot open private state lock") from exc

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        with contextlib.suppress(OSError):
            os.close(descriptor)
        guard, self._directory_guard = self._directory_guard, None
        if guard is not None:
            guard.__exit__(None, None, None)

    def __enter__(self) -> StateDirectoryLock:
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()

    def __del__(self) -> None:  # pragma: no cover - deterministic callers use context managers
        self.release()


__all__ = ["LOCK_FILENAME", "StateDirectoryLock", "StateLockError"]
