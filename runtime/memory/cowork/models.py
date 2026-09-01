"""Domain models and state-machine constants for cowork plans."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PHASE_PLAN = "plan"
PHASE_WORK = "work"
PHASE_SYNTHESIZE = "synthesize"
PHASE_COMPLETE = "complete"
PHASE_FAILED = "failed"

VALID_PHASES = frozenset({PHASE_PLAN, PHASE_WORK, PHASE_SYNTHESIZE, PHASE_COMPLETE, PHASE_FAILED})

# ``failed`` is reachable from any active phase as an irreversible escape hatch.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PHASE_PLAN: frozenset({PHASE_WORK, PHASE_FAILED}),
    PHASE_WORK: frozenset({PHASE_SYNTHESIZE, PHASE_FAILED}),
    PHASE_SYNTHESIZE: frozenset({PHASE_COMPLETE, PHASE_FAILED}),
    PHASE_COMPLETE: frozenset(),
    PHASE_FAILED: frozenset(),
}

VALID_ASSIGNMENT_STATUSES = frozenset({"claimed", "in_progress", "done", "failed"})
FINAL_TASK_ID = "__final__"
_SAFE_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,239}$")


def require_task_id(value: str, *, label: str = "task_id") -> str:
    """Return a normalized safe task identifier or raise ``ValueError``."""

    task_id = str(value or "").strip()
    if not _SAFE_TASK_ID_RE.fullmatch(task_id):
        raise ValueError(f"invalid {label}: use letters, numbers, dot, underscore, or hyphen")
    return task_id


def is_safe_task_id(value: str) -> bool:
    """Return whether ``value`` is safe to use as an artifact filename stem."""

    return _SAFE_TASK_ID_RE.fullmatch(value) is not None


@dataclass
class Task:
    """One unit of work in the plan."""

    id: str
    title: str
    description: str = ""
    required_capabilities: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "required_capabilities": list(self.required_capabilities),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Task:
        return cls(
            id=require_task_id(str(raw["id"])),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            required_capabilities=[str(c) for c in (raw.get("required_capabilities") or [])],
        )


@dataclass
class Plan:
    """Top-level cowork plan persisted as ``plan.json``."""

    session_id: str
    created_at: str
    created_by: str
    phase: str
    tasks: list[Task] = field(default_factory=list)
    phase_updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "phase": self.phase,
            "tasks": [task.to_dict() for task in self.tasks],
            "phase_updated_at": self.phase_updated_at or self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Plan:
        phase = str(raw.get("phase") or PHASE_PLAN)
        if phase not in VALID_PHASES:
            phase = PHASE_PLAN
        tasks_raw = raw.get("tasks") or []
        return cls(
            session_id=str(raw["session_id"]),
            created_at=str(raw.get("created_at") or ""),
            created_by=str(raw.get("created_by") or ""),
            phase=phase,
            tasks=[
                Task.from_dict(task)
                for task in tasks_raw
                if isinstance(task, dict) and task.get("id")
            ],
            phase_updated_at=str(raw.get("phase_updated_at") or raw.get("created_at") or ""),
        )


@dataclass
class Assignment:
    """One row of ``assignments.json``."""

    agent_id: str
    claimed_at: str
    status: str = "claimed"
    artifact_ref: str | None = None
    completed_at: str | None = None
    lease_expires_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "claimed_at": self.claimed_at,
            "status": self.status,
            "artifact_ref": self.artifact_ref,
            "completed_at": self.completed_at,
            "lease_expires_at": self.lease_expires_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Assignment:
        status = str(raw.get("status") or "claimed")
        if status not in VALID_ASSIGNMENT_STATUSES:
            status = "claimed"
        return cls(
            agent_id=str(raw.get("agent_id") or ""),
            claimed_at=str(raw.get("claimed_at") or ""),
            status=status,
            artifact_ref=(
                str(raw["artifact_ref"]) if raw.get("artifact_ref") is not None else None
            ),
            completed_at=(
                str(raw["completed_at"]) if raw.get("completed_at") is not None else None
            ),
            lease_expires_at=(
                str(raw["lease_expires_at"]) if raw.get("lease_expires_at") is not None else None
            ),
        )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "Assignment",
    "FINAL_TASK_ID",
    "PHASE_COMPLETE",
    "PHASE_FAILED",
    "PHASE_PLAN",
    "PHASE_SYNTHESIZE",
    "PHASE_WORK",
    "Plan",
    "Task",
    "VALID_ASSIGNMENT_STATUSES",
    "VALID_PHASES",
    "is_safe_task_id",
    "require_task_id",
]
