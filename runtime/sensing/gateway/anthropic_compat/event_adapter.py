"""Map internal ReAct loop events to Anthropic Managed Agents event shapes.

The realtime gateway emits fine-grained events from ``_drive_react``.
This adapter translates each into one or more ``OutboundEvent`` instances
that match the Anthropic wire protocol.
"""

from __future__ import annotations

from typing import Any

from .models import (
    AgentCustomToolUseEvent,
    AgentMessageEvent,
    AgentThinkingEvent,
    AgentToolResultEvent,
    AgentToolUseEvent,
    OutboundEvent,
    SessionErrorEvent,
    SessionStatus,
    SessionStatusEvent,
    StopReason,
    _new_event_id,
)


def turn_started_event() -> OutboundEvent:
    return SessionStatusEvent(
        id=_new_event_id(),
        type="session.status_running",
        status=SessionStatus.RUNNING,
    )


def turn_completed_event(*, interrupted: bool = False) -> OutboundEvent:
    return SessionStatusEvent(
        id=_new_event_id(),
        type="session.status_idle",
        status=SessionStatus.IDLE,
        stop_reason=StopReason(type="interrupted" if interrupted else "end_turn"),
    )


def requires_action_event(*, blocking_event_ids: list[str]) -> OutboundEvent:
    return SessionStatusEvent(
        id=_new_event_id(),
        type="session.status_idle",
        status=SessionStatus.IDLE,
        stop_reason=StopReason(
            type="requires_action",
            event_ids=blocking_event_ids,
        ),
    )


def adapt_react_event(event: dict[str, Any]) -> list[OutboundEvent]:
    """Convert a single ReAct event dict into Anthropic-shaped events.

    Returns a list because some internal events map to multiple
    outbound events (e.g. tool_approval_request → custom_tool_use +
    session.status_idle).
    """
    kind = event.get("kind") or event.get("type") or ""
    results: list[OutboundEvent] = []

    if kind == "text_delta":
        delta = event.get("delta", "")
        if delta:
            results.append(
                AgentMessageEvent(
                    content=[{"type": "text", "text": delta}],
                )
            )

    elif kind == "thinking_delta":
        delta = event.get("delta", "")
        if delta:
            results.append(
                AgentThinkingEvent(
                    content=[{"type": "thinking", "thinking": delta}],
                )
            )

    elif kind == "tool_start":
        tool_name = event.get("tool_name", "")
        tool_call_id = event.get("tool_call_id", _new_event_id())
        input_preview = event.get("input_preview") or {}
        results.append(
            AgentToolUseEvent(
                tool_name=tool_name,
                tool_use_id=tool_call_id,
                input=input_preview
                if isinstance(input_preview, dict)
                else {"preview": str(input_preview)},
            )
        )

    elif kind == "tool_end":
        tool_call_id = event.get("tool_call_id", "")
        status = event.get("status", "success")
        output = event.get("output_preview", "") or ""
        results.append(
            AgentToolResultEvent(
                tool_use_id=tool_call_id,
                status=status,
                output=str(output)[:4000],
            )
        )

    elif kind == "tool_approval_request":
        # Maps to custom_tool_use (the agent is asking the client to
        # confirm before proceeding). The session then goes idle with
        # stop_reason.type="requires_action".
        tool_name = event.get("tool_name", "")
        tool_call_id = event.get("tool_call_id", _new_event_id())
        args_preview = event.get("args_preview") or event.get("detail") or ""
        evt = AgentCustomToolUseEvent(
            tool_name=tool_name,
            tool_use_id=tool_call_id,
            input={"args_preview": str(args_preview)},
        )
        results.append(evt)
        results.append(requires_action_event(blocking_event_ids=[evt.id]))

    elif kind == "react_error":
        message = event.get("message", "unknown error")
        results.append(
            SessionErrorEvent(
                error={"message": str(message), "kind": event.get("kind", "")},
            )
        )

    elif kind == "throughput":
        # Usage update — not an Anthropic event type, but we track it
        # internally for the session usage counter. Return empty.
        pass

    # react_step_complete, react_cancelled, tool_output_delta — no
    # direct Anthropic mapping; silently skip.

    return results
