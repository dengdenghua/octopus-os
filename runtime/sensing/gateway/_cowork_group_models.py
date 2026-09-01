"""Pydantic request bodies for the cowork group HTTP API."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from runtime.memory.cowork.group import GroupMode, normalize_group_mode


def response_mode(raw_mode: str) -> GroupMode:
    """Canonicalize the legacy ``project`` wire value for HTTP callers."""

    mode = normalize_group_mode(raw_mode)
    if mode is None:
        raise HTTPException(400, "mode must be chat | cluster | swarm")
    return mode


class GrantBody(BaseModel):
    scope: str = "all"  # all | from_join | range | summary
    from_msg: int | None = None
    to_msg: int | None = None


class InviteBody(BaseModel):
    target_id: str = Field(min_length=1)
    kind: str = "agent"  # agent | human
    role: str = "participant"  # participant | observer
    grant: GrantBody = Field(default_factory=GrantBody)
    at_message: int | None = None


class ModeBody(BaseModel):
    # Canonical values: chat | cluster | swarm. ``project`` is accepted by the
    # router only as a deprecated compatibility value and normalized to chat.
    mode: str


class RosterBody(BaseModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=64)
    mode: str  # chat | cluster | swarm (legacy project normalizes to chat)


class BoardBody(BaseModel):
    key: str = Field(min_length=1)
    value: Any = None


class AssignBody(BaseModel):
    assignee: str = Field(min_length=1)
    prompt: str = Field(min_length=1)


class CompleteBody(BaseModel):
    result: str = ""
    blackboard_key: str | None = None


class BreakoutBody(BaseModel):
    child_thread: str = Field(min_length=1)
    members: list[dict] = Field(default_factory=list)
    grant: dict | None = None
    at_message: int | None = None


class MergeBody(BaseModel):
    summary: str = ""


class ReadBody(BaseModel):
    member_id: str = Field(min_length=1)
    seq: int | None = None  # default: mark read up to the current event head


class HeartbeatBody(BaseModel):
    member_id: str = Field(min_length=1)


class LinkRoomBody(BaseModel):
    room_id: str = Field(min_length=1)


class RoomMessageBody(BaseModel):
    text: str = Field(min_length=1)
    participant_id: str = ""
    display_name: str = ""
    source_message_id: str = ""
    message_type: str = ""
    entity_refs: list[dict[str, Any]] = Field(default_factory=list)
    system_card: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessageProjectActionBody(BaseModel):
    action: str = Field(min_length=1)
    action_id: str = ""
    project_id: str = ""
    milestone_id: str = ""
    item_id: str = ""
    title: str = ""
    description: str = ""
    task_type: str = "analysis"
    priority: str = "P2"
    estimate: float = 0.0
    due_at: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    assigned_role: str = ""
    assigned_agent: str = ""
    depends_on: list[str] = Field(default_factory=list)
    decision: str = ""
    rationale: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)


class EnsureRoomBody(BaseModel):
    id: str | None = None
    name: str = ""
    members: list[dict[str, Any]] = Field(default_factory=list)
    leaderId: str | None = None  # noqa: N815 - team room wire uses camelCase
    mode: str | None = None


class CollabTaskBody(BaseModel):
    title: str = Field(min_length=1)
    description: str = ""
    sop_template: str = ""
    assignees: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    run: bool = False
    room: EnsureRoomBody | None = None


__all__ = [
    "AssignBody",
    "BoardBody",
    "BreakoutBody",
    "CollabTaskBody",
    "CompleteBody",
    "EnsureRoomBody",
    "GrantBody",
    "HeartbeatBody",
    "InviteBody",
    "LinkRoomBody",
    "MergeBody",
    "MessageProjectActionBody",
    "ModeBody",
    "ReadBody",
    "RosterBody",
    "RoomMessageBody",
    "response_mode",
]
