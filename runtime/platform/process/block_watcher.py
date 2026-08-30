"""BlockWatcher — development-time hot reload for composition blocks.

Design doc: ``docs/architecture/blocks.md`` §7 (P4 · 开发期热重载).

Watches a plugin/block directory and reconciles the loaded composition state
with what is on disk:

* **added** block   → load it (services bound through the hub's ServiceBus);
* **changed** block → unload + reload (fresh import, fresh services);
* **removed** block → unload (services unbound).

Reload is unload-then-load, so a stale ``sys.modules`` entry is purged first.
This is a development tool: a *changed consumer* whose provider also changed
is reloaded independently (leaf-first semantics); full dependency-aware
transactional reload is deliberately out of scope here.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

_LOG = logging.getLogger(__name__)


class BlockWatcher:
    def __init__(
        self,
        plugin_dir: str | Path,
        hub: Any,
        *,
        interval: float = 1.0,
    ) -> None:
        self._dir = Path(plugin_dir)
        self._hub = hub
        self._interval = max(0.1, float(interval))
        self._mtimes: dict[str, float] = {}

    # ── reconciliation ───────────────────────────────────────

    def _on_disk(self) -> dict[str, Path]:
        """Map block name → directory for every on-disk block manifest."""
        found: dict[str, Path] = {}
        for item in sorted(self._dir.iterdir()):
            if not item.is_dir():
                continue
            if not (item / "plugin.yaml").exists():
                continue
            found[item.name] = item
        return found

    @staticmethod
    def _mtime(plugin_dir: Path) -> float | None:
        """Latest mtime across the manifest + entrypoint (the reload trigger)."""
        stamps = [
            path.stat().st_mtime
            for path in (plugin_dir / "plugin.yaml", plugin_dir / "__init__.py")
            if path.exists()
        ]
        return max(stamps) if stamps else None

    def scan(self) -> dict[str, list[str]]:
        """One reconciliation pass; returns ``{"loaded", "reloaded", "unloaded"}``.

        Idempotent: with no on-disk change it returns three empty lists.
        """
        result: dict[str, list[str]] = {"loaded": [], "reloaded": [], "unloaded": []}
        on_disk = self._on_disk()

        # Removed blocks: unload what we tracked and is gone from disk.
        for name in list(self._mtimes):
            if name not in on_disk:
                if self._hub.get_plugin(name) is not None:
                    self._hub.unload(name)
                    result["unloaded"].append(name)
                self._mtimes.pop(name, None)

        for name, plugin_dir in on_disk.items():
            mtime = self._mtime(plugin_dir)
            if mtime is None:
                continue
            previous = self._mtimes.get(name)
            loaded = self._hub.get_plugin(name) is not None

            if not loaded:
                sys.modules.pop(name, None)
                if self._hub.load(name) is not None:
                    self._mtimes[name] = mtime
                    result["loaded"].append(name)
            elif previous is None:
                # Already loaded before the watcher started — baseline only.
                self._mtimes[name] = mtime
            elif previous != mtime:
                sys.modules.pop(name, None)
                self._hub.unload(name)
                if self._hub.load(name) is not None:
                    self._mtimes[name] = mtime
                    result["reloaded"].append(name)
        return result

    # ── loop ─────────────────────────────────────────────────

    def run(self, stop_event: threading.Event | None = None) -> None:
        """Poll until ``stop_event`` is set (or forever when not provided)."""
        while stop_event is None or not stop_event.is_set():
            try:
                self.scan()
            except Exception as exc:  # noqa: BLE001 — a bad block must not kill the watcher
                _LOG.warning("BlockWatcher scan failed: %s", exc)
            time.sleep(self._interval)
