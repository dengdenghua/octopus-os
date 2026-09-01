"""Device lock — prevent concurrent Arm access to the same Android device.

When two arms (e.g. mobile_operator_arm + mobile_browser_operator_arm)
try to drive the same phone simultaneously, results are unpredictable.
This module provides a simple async lock keyed by device_id.

Usage:
    from runtime.safety.approval.device_lock import acquire_device

    async with acquire_device("pixel-8-pro"):
        await pool.call_tool("pixel-8-pro", "android.tap", {"x": 100, "y": 200})
    # lock released automatically
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field


@dataclass
class _LockEntry:
    holder: str  # arm_id or caller identifier
    acquired_at: float = field(default_factory=time.time)


class DeviceLockManager:
    """Manages per-device async locks."""

    DEFAULT_TIMEOUT_S = 60.0

    def __init__(self) -> None:
        self._locks: dict[str, _LockEntry] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _get_semaphore(self, device_id: str) -> asyncio.Semaphore:
        if device_id not in self._semaphores:
            self._semaphores[device_id] = asyncio.Semaphore(1)
        return self._semaphores[device_id]

    @asynccontextmanager
    async def acquire(
        self,
        device_id: str,
        holder: str = "unknown",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> AsyncIterator[None]:
        """Acquire exclusive access to a device. Raises TimeoutError on contention."""
        sem = self._get_semaphore(device_id)
        try:
            await asyncio.wait_for(sem.acquire(), timeout=timeout_s)
        except TimeoutError:
            current = self._locks.get(device_id)
            holder_info = (
                f" (held by {current.holder} for {time.time() - current.acquired_at:.1f}s)"
                if current
                else ""
            )
            raise TimeoutError(
                f"Device {device_id} lock contention{holder_info}. Timeout after {timeout_s}s."
            ) from None

        self._locks[device_id] = _LockEntry(holder=holder)
        try:
            yield
        finally:
            self._locks.pop(device_id, None)
            sem.release()

    def is_locked(self, device_id: str) -> bool:
        return device_id in self._locks

    def current_holder(self, device_id: str) -> str | None:
        entry = self._locks.get(device_id)
        return entry.holder if entry else None

    def list_locked(self) -> dict[str, str]:
        return {did: entry.holder for did, entry in self._locks.items()}


# ── Global singleton ────────────────────────────────────

_manager: DeviceLockManager | None = None


def get_device_lock_manager() -> DeviceLockManager:
    global _manager
    if _manager is None:
        _manager = DeviceLockManager()
    return _manager


@asynccontextmanager
async def acquire_device(
    device_id: str,
    holder: str = "unknown",
    timeout_s: float = 60.0,
) -> AsyncIterator[None]:
    """Convenience shortcut: acquire_device("pixel-8", holder="mobile_arm")."""
    mgr = get_device_lock_manager()
    async with mgr.acquire(device_id, holder=holder, timeout_s=timeout_s):
        yield
