"""Pydantic models for the Anthropic Managed Agents compat layer.

Mirrors the wire shapes documented at
``platform.claude.com/docs/en/managed-agents/``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# ── Session ──────────────────────────────────────────────────


class SessionStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    TERMINATED = "terminated"


class UsageInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class CreateSessionRequest(BaseModel):
    agent: str | dict[str, Any] | None = None
    environment_id: str | None = None
    title: str | None = None
    vault_ids: list[str] = Field(default_factory=list)


class SessionResponse(BaseModel):
    id: str
    status: SessionStatus = SessionStatus.IDLE
    title: str | None = None
    agent_id: str | None = None
    created_at: str | None = None
    usage: UsageInfo = Field(default_factory=UsageInfo)


# ── Events (inbound: client → server) ────────────────────────


class UserMessageContent(BaseModel):
    type: Literal["text"] = "text"
    text: str = ""


class UserMessageEvent(BaseModel):
    type: Literal["user.message"] = "user.message"
    content: list[UserMessageContent] = Field(default_factory=list)


class UserInterruptEvent(BaseModel):
    type: Literal["user.interrupt"] = "user.interrupt"


class UserToolConfirmationEvent(BaseModel):
    type: Literal["user.tool_confirmation"] = "user.tool_confirmation"
    tool_use_id: str
    result: Literal["allow", "deny"]
    deny_message: str | None = None


class UserCustomToolResultEvent(BaseModel):
    type: Literal["user.custom_tool_result"] = "user.custom_tool_result"
    custom_tool_use_id: str
    content: list[UserMessageContent] = Field(default_factory=list)


# Discriminated union for inbound events
InboundEvent = (
    UserMessageEvent | UserInterruptEvent | UserToolConfirmationEvent | UserCustomToolResultEvent
)


class SendEventsRequest(BaseModel):
    events: list[dict[str, Any]]


# ── Events (outbound: server → client) ───────────────────────


def _new_event_id() -> str:
    return f"evt_{uuid4().hex[:24]}"


class StopReason(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str = "end_turn"
    event_ids: list[str] = Field(default_factory=list)


class OutboundEvent(BaseModel):
    """Base for all server-emitted events."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=_new_event_id)
    type: str
    processed_at: str | None = Field(
        default_factory=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


class SessionStatusEvent(OutboundEvent):
    type: str = "session.status_idle"
    status: SessionStatus = SessionStatus.IDLE
    stop_reason: StopReason | None = None


class SessionErrorEvent(OutboundEvent):
    type: str = "session.error"
    error: dict[str, Any] = Field(default_factory=dict)


class AgentMessageEvent(OutboundEvent):
    type: str = "agent.message"
    content: list[dict[str, Any]] = Field(default_factory=list)


class AgentThinkingEvent(OutboundEvent):
    type: str = "agent.thinking"
    content: list[dict[str, Any]] = Field(default_factory=list)


class AgentToolUseEvent(OutboundEvent):
    type: str = "agent.tool_use"
    tool_name: str = ""
    tool_use_id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


class AgentToolResultEvent(OutboundEvent):
    type: str = "agent.tool_result"
    tool_use_id: str = ""
    status: str = "success"
    output: str = ""


class AgentCustomToolUseEvent(OutboundEvent):
    type: str = "agent.custom_tool_use"
    tool_name: str = ""
    tool_use_id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AgentCustomToolUseEvent",
    "AgentMessageEvent",
    "AgentThinkingEvent",
    "AgentToolResultEvent",
    "AgentToolUseEvent",
    "CreateSessionRequest",
    "InboundEvent",
    "OutboundEvent",
    "SendEventsRequest",
    "SessionErrorEvent",
    "SessionResponse",
    "SessionStatus",
    "SessionStatusEvent",
    "StopReason",
    "UsageInfo",
    "UserCustomToolResultEvent",
    "UserInterruptEvent",
    "UserMessageEvent",
    "UserToolConfirmationEvent",
]
