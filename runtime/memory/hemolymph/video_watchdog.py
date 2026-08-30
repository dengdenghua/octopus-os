"""Auto incremental indexing for the local video library.

A lightweight background thread periodically rescans watched directories for
videos and runs :func:`build_video_index` in ``incremental`` mode. Because the
index is mtime-keyed, only new or changed files are re-keyframed — the whole
library is never rebuilt on every tick. This closes the gap where the UI and
the agent both required a manual "rebuild index" action.

The watcher is entirely optional: it is started explicitly (e.g. from a CLI
flag or the media router) and self-gates when video indexing is disabled.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.video_watchdog")

# Directories that are known to be watched, keyed by normalized path.
_watchers: dict[tuple[str, str], VideoWatcher] = {}
_lock = threading.Lock()


class VideoWatcher:
    """Background scanner that incrementally indexes a directory's videos."""

    def __init__(
        self,
        directory: str | Path,
        *,
        interval_sec: float = 60.0,
        include_faces: bool = True,
        db_path: str | Path | None = None,
        max_files: int = 100,
    ) -> None:
        self.directory = str(Path(directory).expanduser().resolve())
        self.interval_sec = max(10.0, float(interval_sec))
        self.include_faces = include_faces
        self.db_path = db_path
        self.max_files = max_files
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="video-watchdog", daemon=True)
        self._thread.start()
        _LOG.info("video watchdog started for %s", self.directory)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        _LOG.info("video watchdog stopped for %s", self.directory)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._scan_once()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("video watchdog scan failed: %s", exc)
            self._stop.wait(self.interval_sec)

    def _scan_once(self) -> dict[str, Any]:
        from runtime.memory.hemolymph import video_semantic_index as _vidx

        result = _vidx.build_video_index(
            self.directory,
            db_path=self.db_path,
            include_faces=self.include_faces,
            include_transcript=False,
            max_files=self.max_files,
            incremental=True,
        )
        if result and result.get("ok") and result.get("videos_indexed"):
            _LOG.info(
                "video watchdog incremental index: %s new/changed file(s)",
                result.get("videos_indexed"),
            )
        return result or {}


def start_watching(
    directory: str | Path,
    *,
    interval_sec: float = 60.0,
    include_faces: bool = True,
    db_path: str | Path | None = None,
    max_files: int = 100,
) -> VideoWatcher:
    """Start a watcher for ``directory`` (idempotent per directory)."""
    normalized_directory = str(Path(directory).expanduser().resolve())
    normalized_db_path = str(Path(db_path).expanduser().resolve()) if db_path else ""
    key = (normalized_directory, normalized_db_path)
    with _lock:
        existing = _watchers.get(key)
        if existing is not None:
            return existing
        watcher = VideoWatcher(
            normalized_directory,
            interval_sec=interval_sec,
            include_faces=include_faces,
            db_path=db_path,
            max_files=max_files,
        )
        _watchers[key] = watcher
        watcher.start()
        return watcher


def stop_watching(directory: str | Path, db_path: str | Path | None = None) -> bool:
    """Stop and remove the watcher for ``directory``. Returns True if stopped."""
    return _stop_watching(directory, db_path=db_path)


def _stop_watching(directory: str | Path, db_path: str | Path | None = None) -> bool:
    normalized_directory = str(Path(directory).expanduser().resolve())
    normalized_db_path = str(Path(db_path).expanduser().resolve()) if db_path else ""
    key = (normalized_directory, normalized_db_path)
    with _lock:
        watcher = _watchers.pop(key, None)
        if watcher is None and not normalized_db_path:
            matching = [item for item in _watchers if item[0] == normalized_directory]
            if len(matching) == 1:
                watcher = _watchers.pop(matching[0])
    if watcher is None:
        return False
    watcher.stop()
    return True


def stop_all() -> None:
    with _lock:
        watchers = list(_watchers.values())
        _watchers.clear()
    for watcher in watchers:
        watcher.stop()


__all__ = [
    "VideoWatcher",
    "start_watching",
    "stop_watching",
    "stop_all",
]
