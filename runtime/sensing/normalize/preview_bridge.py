from __future__ import annotations

import fnmatch
import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)


class PreviewRefreshBridge:
    def __init__(
        self,
        *,
        bus: Any,
        journal: Any,
        target: str = "",
        debounce_ms: int = 300,
        path_include: list[str] | None = None,
        path_exclude: list[str] | None = None,
    ) -> None:
        self.bus = bus
        self.journal = journal
        self.target = target
        self.debounce_ms = max(debounce_ms, 0)
        self.path_include = list(path_include or [])
        self.path_exclude = list(path_exclude or [])

        self._last_emit_ms: float = 0.0
        self._pending_path: str | None = None
        self._pending_reason: str = ""
        self._lock = threading.RLock()
        self._timer: threading.Timer | None = None
        self._subscribed_type: Any = None
        self._active = False

    def start(self) -> None:
        if self._active:
            return
        from runtime.sensing.normalize.events import FileChanged

        self.bus.subscribe(FileChanged, self._on_file_changed)
        self._subscribed_type = FileChanged
        self._active = True

    def stop(self) -> None:
        if self._active and self._subscribed_type is not None:
            try:
                self.bus.unsubscribe(
                    self._subscribed_type,
                    self._on_file_changed,
                )
            except (TypeError, ValueError, AttributeError, OSError):  # noqa: BLE001
                logger.debug("preview bridge unsubscribe failed", exc_info=True)
            self._subscribed_type = None
            self._active = False
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _matches(self, path: str) -> bool:
        for pat in self.path_exclude:
            if fnmatch.fnmatch(path, pat):
                return False
        if not self.path_include:
            return True
        return any(fnmatch.fnmatch(path, p) for p in self.path_include)

    def _on_file_changed(self, event: Any) -> None:
        path = getattr(event, "path", "") or ""
        if not self._matches(path):
            return
        change_type = getattr(event, "change_type", "modified")
        reason = f"{path}:{change_type}"

        now_ms = time.time() * 1000
        with self._lock:
            if now_ms - self._last_emit_ms >= self.debounce_ms:
                self._last_emit_ms = now_ms
                self._emit_refresh(path, reason)
                return
            self._pending_path = path
            self._pending_reason = reason
            if self._timer is None or not self._timer.is_alive():
                self._timer = threading.Timer(
                    self.debounce_ms / 1000,
                    self._flush,
                )
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            path = self._pending_path or ""
            reason = self._pending_reason
            self._pending_path = None
            self._pending_reason = ""
            self._timer = None
        if path:
            self._last_emit_ms = time.time() * 1000
            self._emit_refresh(path, reason)

    def _emit_refresh(self, path: str, reason: str) -> None:
        if not hasattr(self.journal, "write_preview_refresh"):
            return
        try:
            self.journal.write_preview_refresh(
                target=self.target,
                trigger_path=path,
                reason=reason,
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.exception("preview bridge write_preview_refresh failed: %s", exc)
