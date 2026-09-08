"""Agent task lifecycle compatibility surface consumed by Echo OS."""

from __future__ import annotations

from typing import Any

from runtime.execution.request import current_execution_request as _current_execution_request
from runtime.platform.process.task_supervisor import (
    TaskLeaseConflict,
)
from runtime.platform.process.task_supervisor import (
    task_lease_health as _task_lease_health,
)


def task_lease_health(task: Any) -> dict[str, Any]:
    health = _task_lease_health(task)
    return dict(health) if isinstance(health, dict) else {}


def current_execution_request() -> Any | None:
    """Return the host-owned request bound to the current Agent worker.

    This accessor belongs to the OS-to-Agent compatibility boundary so
    appliance providers never import the runtime implementation directly.
    A legacy runtime may not expose a request context; in that case the
    provider keeps its legacy session-only behaviour.
    """

    try:
        return _current_execution_request()
    except (AttributeError, RuntimeError):
        return None


def resume_checkpoint_metadata(runtime: Any, task_id: str) -> dict[str, Any] | None:
    """Fail closed if Agent removes its temporary private recovery helper."""

    try:
        from runtime.sensing.gateway._realtime_turn_lifecycle_resume import (
            _resume_checkpoint_metadata,
        )
    except (ImportError, AttributeError):
        return None
    try:
        checkpoint = _resume_checkpoint_metadata(runtime, task_id)
    except (AttributeError, TypeError, ValueError):
        return None
    return dict(checkpoint) if isinstance(checkpoint, dict) else None


__all__ = [
    "TaskLeaseConflict",
    "current_execution_request",
    "resume_checkpoint_metadata",
    "task_lease_health",
]
