"""Filesystem watcher · auto-reloads agents on disk edits.

Watches ``agents/<id>/`` trees and calls ``AgentRegistry.replace(...)``
when any of these files change:

    * ``profile.jsonc``                  (metadata / model / avatar)
    * ``agent-core/SOUL.md``             (persona)
    * ``agent-core/IDENTITY.md``         (role / communication style)
    * ``agent-core/USER.md`` / ``MEMORY.md`` / ``HEARTBEAT.md``
    * ``agent-core/AGENTS.md`` / ``BOOTSTRAP.md``
    * ``agent-core/tool-registry.jsonc`` (arms + private_skills)
    * ``_shared/*.md``                   (shared banner) · rebuilds ALL agents

Debounced: multiple writes within 500ms collapse to a single reload.

soft-dep on ``watchdog``. If it's not installed, ``start_agent_watcher``
returns None and the caller keeps running without auto-reload — the
``POST /api/agents/<id>/reload`` endpoint still works as a manual fallback.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    WATCHDOG_AVAILABLE = True
except ImportError:  # pragma: no cover
    WATCHDOG_AVAILABLE = False
    FileSystemEvent = None  # type: ignore[assignment, misc]
    FileSystemEventHandler = object  # type: ignore[assignment, misc]
    Observer = None  # type: ignore[assignment, misc]


_log = logging.getLogger(__name__)

# Files we care about under a per-agent dir. Anything else (e.g. writes
# to MEMORY.md done BY the agent itself via remember()) triggers a reload
# too — desired: the agent's next turn will see its own fresh memory.
_WATCHED_SUFFIXES = (".md", ".jsonc", ".json", ".svg")


class _AgentReloadHandler(FileSystemEventHandler):
    """Watchdog event handler with a per-agent debounce timer."""

    def __init__(
        self,
        agents_root: Path,
        reload_one: Any,  # Callable[[str], None]
        reload_all: Any,  # Callable[[], None]
        debounce_ms: int = 500,
    ) -> None:
        super().__init__()
        self._agents_root = agents_root.resolve()
        self._shared_dir = (agents_root / "_shared").resolve()
        self._reload_one = reload_one
        self._reload_all = reload_all
        self._debounce_s = debounce_ms / 1000.0
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.suffix not in _WATCHED_SUFFIXES:
            return

        # Resolve and determine which agent this change affects.
        try:
            rel = path.resolve().relative_to(self._agents_root)
        except ValueError:
            return
        parts = rel.parts
        if not parts:
            return

        top = parts[0]
        if top == "_shared":
            # Shared banner / rules change → rebuild everyone.
            self._schedule("__all__", self._reload_all)
        else:
            # Per-agent change → rebuild just this one.
            self._schedule(top, lambda: self._reload_one(top))

    def _schedule(self, key: str, fn: Any) -> None:
        """Replace any pending timer for this key — coalesces bursts."""
        with self._lock:
            existing = self._timers.pop(key, None)
            if existing is not None:
                existing.cancel()
            timer = threading.Timer(self._debounce_s, lambda: self._fire(key, fn))
            timer.daemon = True
            self._timers[key] = timer
            timer.start()

    def _fire(self, key: str, fn: Any) -> None:
        with self._lock:
            self._timers.pop(key, None)
        try:
            fn()
            _log.info("[agent-watcher] reloaded %s", key)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "[agent-watcher] reload %s failed: %s: %s",
                key,
                type(exc).__name__,
                exc,
            )


def start_agent_watcher(
    agents_root: Path,
    registry: Any,
    runtime: Any,
) -> Any:
    """Start a daemon watchdog Observer for ``agents_root``.

    Returns the Observer (caller can `observer.stop()` for clean shutdown)
    or None when watchdog isn't installed.

    On any ``.md`` / ``.jsonc`` / ``.json`` / ``.svg`` change:

        * under ``agents/_shared/`` → ``load_all_agents()`` + replace each
        * under ``agents/<id>/``    → ``load_agent(<id>)`` + ``registry.replace()``

    Coalesced with a 500ms debounce so saving a file in VS Code
    (which often writes multiple events) only triggers one reload.
    """
    if not WATCHDOG_AVAILABLE:
        _log.info("[agent-watcher] watchdog not installed · auto-reload off")
        return None
    if not agents_root.is_dir():
        _log.warning("[agent-watcher] agents_root missing: %s", agents_root)
        return None

    from runtime.execution.agents.loader import load_agent, load_all_agents

    shared_dir = agents_root / "_shared"

    def _reload_one(agent_id: str) -> None:
        agent_dir = agents_root / agent_id
        if not (agent_dir / "profile.jsonc").exists():
            # Might be a skills/ or sessions/ write we don't care about.
            return
        new_agent = load_agent(agent_dir, runtime, shared_dir)
        registry.replace(new_agent)

    def _reload_all() -> None:
        for agent in load_all_agents(runtime, agents_root):
            registry.replace(agent)

    handler = _AgentReloadHandler(
        agents_root=agents_root,
        reload_one=_reload_one,
        reload_all=_reload_all,
    )
    observer = Observer()
    observer.schedule(handler, str(agents_root), recursive=True)
    observer.daemon = True
    observer.start()
    _log.info("[agent-watcher] watching %s", agents_root)
    return observer


__all__ = ["start_agent_watcher", "WATCHDOG_AVAILABLE"]
