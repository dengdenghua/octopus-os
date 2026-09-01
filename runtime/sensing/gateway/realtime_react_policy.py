"""Routing policy and event translation for realtime agent streams."""

from __future__ import annotations

import os
from typing import Any, Literal, cast

from runtime.execution.tool_engine import (
    normalize_tool_lifecycle_event,
    tool_lifecycle_event_to_react_event,
)
from runtime.platform.models import ParsedIntent


def agentic_stream_event_to_react_event(
    kind: str,
    delta: Any,
    final: Any,
) -> dict[str, Any] | None:
    """Translate native tool-loop tuple events into realtime bridge events."""

    if kind == "text":
        return {"type": "text_delta", "delta": str(delta or "")}
    if kind in {"commentary", "commentary_runtime"}:
        return {
            "type": "commentary_delta",
            "delta": str(delta or ""),
            "progress_source": "runtime" if kind == "commentary_runtime" else "model",
        }
    if kind == "reasoning":
        return {"type": "thinking_delta", "delta": str(delta or "")}
    if kind in {"tool_start", "tool_end"} and isinstance(delta, dict):
        lifecycle_kind = cast(Literal["tool_start", "tool_end"], kind)
        return tool_lifecycle_event_to_react_event(
            normalize_tool_lifecycle_event(lifecycle_kind, delta, origin="native")
        )
    if kind == "stats" and isinstance(delta, dict):
        return {"type": "throughput", "usage": delta}
    if kind == "done":
        if final and not isinstance(final, str):
            return {"type": "text_delta", "delta": str(final)}
        return {"type": "react_completed"}
    if kind == "error":
        payload = delta if isinstance(delta, dict) else {}
        return {
            "type": "react_error",
            "kind": str(payload.get("kind") or "agentic_error"),
            "message": str(payload.get("message") or delta or "agentic error"),
        }
    return None


def should_use_native_tool_loop(
    stack: Any,
    intent: ParsedIntent,
    *,
    planning_mode: bool,
) -> bool:
    """Return whether a turn should use protocol-native tool calls first."""

    del planning_mode  # Planning mode is a prompt nudge, not a plan-only tier.
    flag = os.environ.get("ECHO_NATIVE_TOOL_LOOP", "1").strip().lower()
    if flag in {"0", "false", "off", "no"}:
        return False

    user_context = intent.user_context or {}
    explicit = user_context.get("native_tool_loop")
    if explicit is False:
        return False
    metadata = user_context.get("metadata")
    if isinstance(metadata, dict) and metadata.get("native_tool_loop") is False:
        return False

    from runtime.core.cerebrum.todo_protocol import context_mode

    if context_mode(user_context) == "chat":
        return False

    executor = getattr(stack, "executor", None)
    router = getattr(getattr(stack, "planner", None), "router", None)
    if executor is None or router is None or not hasattr(router, "call_stream"):
        return False

    caps = getattr(router, "capabilities", None)
    supports = getattr(caps, "supports_tool_use", None)
    if supports is True:
        return True
    if supports is False:
        return False

    primary = getattr(router, "primary", None)
    primary_caps = getattr(primary, "capabilities", None)
    return getattr(primary_caps, "supports_tool_use", None) is True


__all__ = ["agentic_stream_event_to_react_event", "should_use_native_tool_loop"]
