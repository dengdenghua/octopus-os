"""Application lifecycle wiring for the optional agent filesystem watcher."""

from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)


def register_agent_watcher_lifecycle(app: Any, *, registry: Any, runtime: Any) -> None:
    """Start the watcher with the app and always reap it on shutdown."""

    app.state.agent_watcher = None

    def start() -> None:
        if getattr(app.state, "agent_watcher", None) is not None:
            return
        try:
            from runtime.execution.agents.loader import default_agents_root
            from runtime.execution.agents.watcher import start_agent_watcher

            app.state.agent_watcher = start_agent_watcher(
                agents_root=default_agents_root(),
                registry=registry,
                runtime=runtime,
            )
        except (ImportError, AttributeError, TypeError, OSError) as exc:
            _log.warning(
                "agent watcher failed to start (%s) · manual reload still works",
                exc,
            )

    def stop() -> None:
        observer = getattr(app.state, "agent_watcher", None)
        app.state.agent_watcher = None
        if observer is None:
            return
        try:
            observer.stop()
            observer.join(timeout=2.0)
        except (AttributeError, RuntimeError, OSError) as exc:
            _log.warning("agent watcher failed to stop cleanly (%s)", exc)

    app.router.add_event_handler("startup", start)
    app.router.add_event_handler("shutdown", stop)


__all__ = ["register_agent_watcher_lifecycle"]
