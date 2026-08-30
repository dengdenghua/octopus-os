"""Process tree management and graceful shutdown utilities.

Handles cross-platform process termination with:
- Signal cascade: SIGINT → 3s grace → SIGKILL (Unix)
- Process tree termination (child processes included)
- Windows taskkill integration for full process group cleanup
- Timeout-aware graceful shutdown
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import signal

_logger = logging.getLogger(__name__)

_GRACE_PERIOD_SEC = 3.0


class ProcessTreeManager:
    """Manages cross-platform process tree lifecycle.

    Usage:
        manager = ProcessTreeManager()
        proc = await asyncio.create_subprocess_exec(...)
        manager.track(proc.pid, proc)
        await manager.terminate_tree()
    """

    def __init__(
        self,
        grace_period: float = _GRACE_PERIOD_SEC,
    ) -> None:
        self._grace_period = grace_period
        self._tracked: dict[int, asyncio.subprocess.Process] = {}
        self._terminated_pids: set[int] = set()

    def track(
        self,
        pid: int,
        process: asyncio.subprocess.Process,
    ) -> None:
        """Track a process by its PID for lifecycle management."""
        self._tracked[pid] = process

    def untrack(self, pid: int) -> None:
        self._tracked.pop(pid, None)
        self._terminated_pids.discard(pid)

    async def terminate_graceful(
        self,
        pid: int,
        process: asyncio.subprocess.Process,
    ) -> int:
        """Terminate a process gracefully with signal cascade.

        Returns the exit code, or -1 if the process couldn't be killed.
        """
        if process.returncode is not None:
            return process.returncode

        if platform.system() == "Windows":
            return await self._terminate_windows(pid, process)
        return await self._terminate_unix(pid, process)

    async def terminate_all(self) -> list[int]:
        """Terminate all tracked processes gracefully.

        Returns list of exit codes.
        """
        codes = []
        for pid, proc in list(self._tracked.items()):
            code = await self.terminate_graceful(pid, proc)
            codes.append(code)
        self._tracked.clear()
        self._terminated_pids.clear()
        return codes

    async def _terminate_unix(
        self,
        pid: int,
        process: asyncio.subprocess.Process,
    ) -> int:
        """Unix: SIGINT → grace → SIGKILL."""
        if pid in self._terminated_pids:
            return process.returncode if process.returncode is not None else -1

        try:
            self._terminated_pids.add(pid)
            try:
                os.killpg(pid, signal.SIGINT)
            except ProcessLookupError:
                _logger.debug("process %d already exited", pid)
                return process.returncode if process.returncode is not None else -1

            _logger.debug("sent SIGINT to process group %d", pid)

            try:
                await asyncio.wait_for(
                    process.wait(),
                    timeout=self._grace_period,
                )
                _logger.info("process %d exited after SIGINT", pid)
                return process.returncode if process.returncode is not None else -1
            except TimeoutError:
                _logger.warning(
                    "process %d did not exit after SIGINT, sending SIGKILL",
                    pid,
                )
                os.killpg(pid, signal.SIGKILL)
                await process.wait()
                return process.returncode if process.returncode is not None else -1
        except Exception as exc:
            _logger.error("failed to terminate process %d: %s", pid, exc)
            return -1

    async def _terminate_windows(
        self,
        pid: int,
        process: asyncio.subprocess.Process,
    ) -> int:
        """Windows: taskkill /T /F to kill entire process tree."""
        if pid in self._terminated_pids:
            return process.returncode if process.returncode is not None else -1

        try:
            self._terminated_pids.add(pid)
            taskkill_path = os.path.join(
                os.environ.get("SYSTEMROOT", "C:\\Windows"),
                "System32",
                "taskkill.exe",
            )

            kill_proc = await asyncio.create_subprocess_exec(
                taskkill_path,
                "/pid",
                str(pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await kill_proc.wait()

            await asyncio.wait_for(
                process.wait(),
                timeout=5.0,
            )
            _logger.info("process %d terminated via taskkill", pid)
            return process.returncode if process.returncode is not None else -1
        except TimeoutError:
            _logger.warning("taskkill for pid %d timed out", pid)
            return -1
        except Exception as exc:
            _logger.error("failed to terminate Windows process %d: %s", pid, exc)
            return -1

    @property
    def active_count(self) -> int:
        """Number of currently tracked (non-terminated) processes."""
        return len(self._tracked) - len(self._terminated_pids)
