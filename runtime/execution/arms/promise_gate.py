"""Promise gate — async concurrency control via chained promises.

Serializes asynchronous operations so that only one can execute at
a time, while allowing callers to queue up behind the current
operation.  Prevents parallel tool calls from racing against
shared workspace state when the executor is already inside a
critical section.

Key patterns
~~~~~~~~~~~~
- **Gate**: A promise chain that ensures operations run sequentially.
- **Acquired lock state**: Track whether the current session owns the lock.
- **Session isolation**: Different sessions can be rejected if one owns the lock.

Usage
~~~~~
    gate = PromiseGate()

    async def critical_operation(session_id: str):
        async with gate.enter(session_id):
            # Only one session can be here at a time
            await do_something()
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

_logger = logging.getLogger(__name__)


class GateError(Exception):
    """Raised when a gate entry is rejected."""


class PromiseGate:
    """A promise chain gate for serializing async operations.

    Ensures that only one async operation can hold the gate at a time.
    Additional callers are queued and will execute after the current
    one completes.

    Attributes:
        _lock: asyncio.Lock for mutual exclusion.
        _owner_session_id: The session ID that currently owns the gate.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner_session_id: str | None = None

    @property
    def is_locked(self) -> bool:
        return self._lock.locked()

    @property
    def owner_session_id(self) -> str | None:
        return self._owner_session_id

    async def acquire(self, session_id: str | None = None) -> None:
        """Acquire the gate, waiting if another operation holds it.

        Args:
            session_id: Optional session identifier. If provided and
                the gate is owned by a different session, a GateError
                is raised.

        Raises:
            GateError: If the gate is owned by a different session.
        """
        # Check session isolation before waiting
        if self._lock.locked() and (
            session_id and self._owner_session_id and session_id != self._owner_session_id
        ):
            raise GateError(
                f"Session '{session_id}' cannot acquire gate owned by '{self._owner_session_id}'"
            )

        await self._lock.acquire()
        self._owner_session_id = session_id

    def release(self) -> None:
        """Release the gate, allowing the next queued operation to proceed."""
        self._owner_session_id = None
        if self._lock.locked():
            self._lock.release()

    @asynccontextmanager
    async def enter(self, session_id: str | None = None):
        """Context manager for gate entry and automatic release.

        Usage:
            async with gate.enter(session_id):
                await critical_operation()
        """
        await self.acquire(session_id)
        try:
            yield
        finally:
            self.release()

    async def reset(self) -> None:
        """Reset the gate to its initial unlocked state.

        Use with caution — this may leave pending operations in an
        undefined state.
        """
        while self._lock.locked():
            self._lock.release()
        self._owner_session_id = None


class SessionLock:
    """Session-based lock with file-system persistence.

    Extends PromiseGate with file-based lock persistence so that
    locks survive process crashes and can be cleaned up on restart.

    Attributes:
        _gate: The underlying PromiseGate.
        _lock_file_path: Path to the lock file.
    """

    def __init__(
        self,
        lock_file_path: str | None = None,
    ) -> None:
        self._gate = PromiseGate()
        self._lock_file_path = lock_file_path

    async def acquire(self, session_id: str) -> bool:
        """Acquire the session lock.

        Returns:
            True if the lock was acquired, False if another session
            already owns it.
        """
        try:
            await self._gate.acquire(session_id)
            if self._lock_file_path:
                self._write_lock_file(session_id)
            return True
        except GateError:
            return False

    def release(self, session_id: str | None = None) -> None:
        """Release the session lock."""
        self._gate.release()
        if self._lock_file_path:
            self._remove_lock_file()

    @asynccontextmanager
    async def session(self, session_id: str):
        """Context manager for session lock."""
        acquired = await self.acquire(session_id)
        if not acquired:
            raise GateError(
                "Another session is currently using the resource. "
                "Only one session can run at a time."
            )
        try:
            yield
        finally:
            self.release(session_id)

    def _write_lock_file(self, session_id: str) -> None:
        """Write lock file for crash recovery."""
        import json
        import os

        lock_data = {
            "session_id": session_id,
            "pid": os.getpid(),
        }
        try:
            with open(self._lock_file_path, "w") as f:
                json.dump(lock_data, f)
        except OSError:
            _logger.warning("failed to write lock file: %s", self._lock_file_path)

    def _remove_lock_file(self) -> None:
        """Remove lock file."""
        import os

        if self._lock_file_path:
            try:
                if os.path.exists(self._lock_file_path):
                    os.unlink(self._lock_file_path)
            except OSError:
                _logger.warning("failed to remove lock file: %s", self._lock_file_path)

    @classmethod
    def cleanup_stale_lock(cls, lock_file_path: str) -> bool:
        """Remove a lock file if the owning process is no longer running.

        Returns:
            True if a stale lock was cleaned up, False otherwise.
        """
        import json
        import os

        if not os.path.exists(lock_file_path):
            return False

        try:
            with open(lock_file_path) as f:
                lock_data = json.load(f)

            pid = lock_data.get("pid")
            if pid and not cls._is_process_running(pid):
                os.unlink(lock_file_path)
                return True
        except (json.JSONDecodeError, OSError):
            try:
                os.unlink(lock_file_path)
                return True
            except OSError:  # noqa: BLE001 — lock file cleanup best-effort
                pass

        return False

    @staticmethod
    def _is_process_running(pid: int) -> bool:
        """Check if a process with the given PID is running."""
        import os

        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
