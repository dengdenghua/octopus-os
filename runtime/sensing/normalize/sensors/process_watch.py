from __future__ import annotations

import logging
import os
import threading
from typing import Literal

from ..events import ProcessStateChanged
from ..sensor import EnvSensor

logger = logging.getLogger(__name__)


ProcessState = Literal["started", "stopped", "crashed", "running"]


class ProcessWatchSensor(EnvSensor):
    def __init__(
        self,
        *,
        pid: int,
        name: str = "",
        poll_interval_seconds: float = 5.0,
        sensor_id: str = "",
    ) -> None:
        super().__init__()
        if pid <= 0:
            raise ValueError("pid must be > 0")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        self.sensor_id = sensor_id or f"proc_{pid}"
        self.pid = pid
        self.name = name or str(pid)
        self.poll_interval = poll_interval_seconds

        self._bg_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._last_state: ProcessState | None = None

    def is_alive(self) -> bool:
        try:
            if os.name == "nt":
                import ctypes

                process_query_information = 0x0400
                h = ctypes.windll.kernel32.OpenProcess(
                    process_query_information,
                    False,
                    self.pid,
                )
                if not h:
                    return False
                try:
                    code = ctypes.c_ulong()
                    ctypes.windll.kernel32.GetExitCodeProcess(
                        h,
                        ctypes.byref(code),
                    )
                    return code.value == 259
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
            else:
                os.kill(self.pid, 0)
                return True
        except (OSError, ProcessLookupError):
            return False
        except Exception as e:  # noqa: BLE001
            self._last_error = f"{type(e).__name__}: {e}"
            return False

    def check_once(self) -> ProcessStateChanged | None:
        alive = self.is_alive()
        new_state: ProcessState = "running" if alive else "stopped"

        if self._last_state is None:
            self._last_state = new_state
            return None

        if new_state == self._last_state:
            return None

        if self._last_state == "running" and new_state == "stopped":
            change: ProcessState = "stopped"
        elif self._last_state == "stopped" and new_state == "running":
            change = "started"
        else:
            change = new_state

        self._last_state = new_state
        evt = ProcessStateChanged(
            name=self.name,
            pid=self.pid,
            state=change,
        )
        try:
            self._publish(evt)
        except Exception as e:  # noqa: BLE001
            self._last_error = f"{type(e).__name__}: {e}"
        return evt

    # ─── lifecycle ────────────────────────────────

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._stop_evt.clear()

        self._last_state = "running" if self.is_alive() else "stopped"

        self._bg_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"skin-{self.sensor_id}",
        )
        self._bg_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_evt.set()
        t = self._bg_thread
        self._bg_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                self.check_once()
            except (OSError, TypeError, ValueError, RuntimeError):
                logger.exception("ProcessWatchSensor poll loop error")
            self._stop_evt.wait(self.poll_interval)
