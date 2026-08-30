"""Pydantic wire models for the persistent team tasks API.

These models are imported by ``team_tasks_router`` and kept in a
separate submodule so the router module stays focused on the routing
and task-lifecycle logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskAssigneeWire(BaseModel):
    """Who the task is assigned to. Polymorphic so a task can target
    AI roster members AND human participants in the same field."""

    model_config = ConfigDict(extra="ignore")

    kind: str  # "agent" | "participant"
    ref: str  # agent name (for kind=agent) or participant id (for kind=participant)


class TeamTaskWire(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    room_id: str
    title: str
    description: str = ""
    sop_template: str = ""  # empty = freeform; otherwise meta-skill name
    status: str = "pending"  # pending | running | done | failed | cancelled
    assignees: list[TaskAssigneeWire] = Field(default_factory=list)
    created_by: str | None = None  # actor / participant id
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    produced_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateTeamTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    room_id: str
    title: str
    description: str = ""
    sop_template: str = ""
    assignees: list[TaskAssigneeWire] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class UpdateTeamTaskRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    title: str | None = None
    description: str | None = None
    status: str | None = None
    assignees: list[TaskAssigneeWire] | None = None
    sop_template: str | None = None


__all__ = [
    "TaskAssigneeWire",
    "TeamTaskWire",
    "CreateTeamTaskRequest",
    "UpdateTeamTaskRequest",
]
