from __future__ import annotations

import contextlib
import fnmatch
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from ..events import FileChanged
from ..sensor import EnvSensor, SensorStatus

logger = logging.getLogger(__name__)


class FileWatcherSensor(EnvSensor):
    def __init__(
        self,
        *,
        paths: list[str | Path],
        patterns: list[str] | None = None,
        ignore_patterns: list[str] | None = None,
        recursive: bool = True,
        debounce_ms: int = 200,
        poll_interval_seconds: float = 2.0,
        sensor_id: str = "file_watcher",
        force_polling: bool = False,  # Implementation note.
    ) -> None:
        super().__init__()
        if not paths:
            raise ValueError("paths required")
        if debounce_ms < 0:
            raise ValueError("debounce_ms must be >= 0")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        self.sensor_id = sensor_id
        self.paths = [Path(p).resolve() for p in paths]
        self.patterns = list(patterns) if patterns else []
        self.ignore_patterns = list(ignore_patterns) if ignore_patterns else []
        self.recursive = recursive
        self.debounce_ms = debounce_ms
        self.poll_interval = poll_interval_seconds
        self.force_polling = force_polling

        self._bg_thread: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._watchdog_observer: Any = None
        self._snapshot: dict[str, float] = {}  # path → mtime
        self._last_emit_per_path: dict[str, float] = {}

    def _matches(self, path: str) -> bool:
        name = os.path.basename(path)
        if self.ignore_patterns:
            for pat in self.ignore_patterns:
                if fnmatch.fnmatch(name, pat):
                    return False
        if not self.patterns:
            return True
        return any(fnmatch.fnmatch(name, p) for p in self.patterns)

    def _debounced(self, path: str) -> bool:
        now = time.time() * 1000
        last = self._last_emit_per_path.get(path, 0.0)
        if now - last < self.debounce_ms:
            return False
        self._last_emit_per_path[path] = now
        return True

    def _emit(self, path: str, change_type: str, old_path: str = "") -> None:
        if not self._matches(path):
            return
        if not self._debounced(path):
            return
        size = -1
        if change_type != "deleted":
            with contextlib.suppress(OSError):
                size = os.path.getsize(path)
        try:
            self._publish(
                FileChanged(
                    path=path,
                    change_type=change_type,  # type: ignore[arg-type]
                    old_path=old_path,
                    size_bytes=size,
                )
            )
        except Exception as e:  # noqa: BLE001
            self._last_error = f"{type(e).__name__}: {e}"
            logger.exception("FileWatcherSensor emit failed")

    def start(self) -> None:
        with self._lock:
            if self._running:
                return
            self._running = True
        self._stop_evt.clear()

        if not self.force_polling and self._try_start_watchdog():
            return
        self._start_polling()

    def stop(self) -> None:
        with self._lock:
            if not self._running:
                return
            self._running = False
        self._stop_evt.set()

        obs = self._watchdog_observer
        self._watchdog_observer = None
        if obs is not None:
            try:
                obs.stop()
                obs.join(timeout=2.0)
            except (OSError, TypeError, ValueError, RuntimeError):  # noqa: BLE001
                pass

        t = self._bg_thread
        self._bg_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    def status(self) -> SensorStatus:
        base = super().status()
        meta = {
            "backend": "watchdog" if self._watchdog_observer else "polling",
            "paths": [str(p) for p in self.paths],
            "patterns": list(self.patterns),
        }
        return SensorStatus(
            sensor_id=base.sensor_id,
            running=base.running,
            events_emitted=base.events_emitted,
            last_emit_at=base.last_emit_at,
            last_error=base.last_error,
            metadata=meta,
        )

    def _try_start_watchdog(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return False

        sensor = self

        class _H(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    sensor._emit(event.src_path, "created")

            def on_modified(self, event):
                if not event.is_directory:
                    sensor._emit(event.src_path, "modified")

            def on_deleted(self, event):
                if not event.is_directory:
                    sensor._emit(event.src_path, "deleted")

            def on_moved(self, event):
                if not event.is_directory:
                    sensor._emit(event.dest_path, "moved", old_path=event.src_path)

        obs = Observer()
        for p in self.paths:
            if not p.exists():
                logger.warning("FileWatcher path not found: %s", p)
                continue
            obs.schedule(_H(), str(p), recursive=self.recursive)
        obs.start()
        self._watchdog_observer = obs
        return True

    def _start_polling(self) -> None:
        self._snapshot = self._scan_mtimes()
        self._bg_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"skin-{self.sensor_id}",
        )
        self._bg_thread.start()

    def _poll_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                current = self._scan_mtimes()
                self._diff_and_emit(self._snapshot, current)
                self._snapshot = current
            except (OSError, ValueError, TypeError) as exc:
                self._last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("FileWatcherSensor poll loop error")
            self._stop_evt.wait(self.poll_interval)

    def _scan_mtimes(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for root in self.paths:
            if root.is_file():
                with contextlib.suppress(OSError):
                    out[str(root)] = root.stat().st_mtime
                continue
            if not root.is_dir():
                continue
            try:
                iterator = root.rglob("*") if self.recursive else root.glob("*")
                for p in iterator:
                    if p.is_file():
                        with contextlib.suppress(OSError):
                            out[str(p)] = p.stat().st_mtime
            except OSError:  # noqa: BLE001 — stat failed (e.g. file deleted mid-scan); skip path
                pass
        return out

    def _diff_and_emit(self, old: dict[str, float], new: dict[str, float]) -> None:
        old_set = set(old)
        new_set = set(new)
        for path in new_set - old_set:
            self._emit(path, "created")
        for path in old_set - new_set:
            self._emit(path, "deleted")
        for path in new_set & old_set:
            if new[path] > old[path] + 0.001:  # 1ms tolerance
                self._emit(path, "modified")
