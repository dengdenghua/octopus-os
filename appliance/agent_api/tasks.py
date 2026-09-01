"""Agent task lifecycle compatibility surface consumed by Echo OS."""

from __future__ import annotations

from typing import Any

from runtime.platform.process.task_supervisor import (
    TaskLeaseConflict,
)
from runtime.platform.process.task_supervisor import (
    task_lease_health as _task_lease_health,
)


def task_lease_health(task: Any) -> dict[str, Any]:
    health = _task_lease_health(task)
    return dict(health) if isinstance(health, dict) else {}


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


__all__ = ["TaskLeaseConflict", "resume_checkpoint_metadata", "task_lease_health"]
