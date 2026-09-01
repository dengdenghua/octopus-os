from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .tool_output_pruner import ToolResultPrunePolicy, prune_tool_result_text
from .tool_output_spill import maybe_spill_text

ToolCallOrigin = Literal["native", "react_compat", "planner_compat", "direct"]
ToolLifecycleKind = Literal["tool_start", "tool_end"]


@dataclass(frozen=True, slots=True)
class NormalizedToolCall:
    """Provider-agnostic tool invocation used at executor boundaries."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    origin: ToolCallOrigin = "direct"


@dataclass(frozen=True, slots=True)
class NormalizedToolResult:
    """Provider-agnostic result envelope for a completed tool call."""

    id: str
    name: str
    output: Any = None
    rendered: str = ""
    is_error: bool = False
    status: str = "success"
    error_type: str | None = None
    origin: ToolCallOrigin = "direct"


@dataclass(frozen=True, slots=True)
class NormalizedToolLifecycleEvent:
    """Shared tool lifecycle event before surface-specific rendering."""

    kind: ToolLifecycleKind
    id: str
    name: str
    iteration: int | None = None
    input: Any = None
    output: Any = None
    status: str | None = None
    is_error: bool = False
    duration_ms: int | None = None
    origin: ToolCallOrigin = "direct"
    extras: dict[str, Any] = field(default_factory=dict)


def normalize_tool_call(
    call: Any,
    *,
    origin: ToolCallOrigin = "direct",
) -> NormalizedToolCall:
    """Convert supported tool-call shapes into ``NormalizedToolCall``.

    The first migration target is native LLM tool_use calls whose payload
    uses ``id/name/input``. ReAct and planner adapters can later feed their
    parsed calls through the same function using dict payloads.
    """
    if isinstance(call, NormalizedToolCall):
        return call

    if isinstance(call, dict):
        raw_id = call.get("id") or call.get("tool_use_id") or call.get("call_id")
        raw_name = call.get("name") or call.get("tool") or call.get("sucker_id")
        raw_args = call.get("arguments")
        if raw_args is None:
            raw_args = call.get("input")
        if raw_args is None:
            raw_args = call.get("args")
    else:
        raw_id = (
            getattr(call, "id", None)
            or getattr(call, "tool_use_id", None)
            or getattr(call, "call_id", None)
        )
        raw_name = (
            getattr(call, "name", None)
            or getattr(call, "tool", None)
            or getattr(call, "sucker_id", None)
        )
        raw_args = getattr(call, "arguments", None)
        if raw_args is None:
            raw_args = getattr(call, "input", None)
        if raw_args is None:
            raw_args = getattr(call, "args", None)

    name = str(raw_name or "").strip()
    if not name:
        raise ValueError("tool call missing name")

    call_id = str(raw_id or name).strip() or name
    arguments = dict(raw_args) if isinstance(raw_args, dict) else {}
    return NormalizedToolCall(
        id=call_id,
        name=name,
        arguments=arguments,
        origin=origin,
    )


_LIFECYCLE_KNOWN_KEYS = frozenset(
    {
        "type",
        "id",
        "call_id",
        "tool_use_id",
        "tool_call_id",
        "name",
        "tool",
        "tool_name",
        "input",
        "input_preview",
        "args_preview",
        "output",
        "output_preview",
        "iteration",
        "status",
        "is_error",
        "duration_ms",
        "origin",
    }
)


def normalize_tool_lifecycle_event(
    kind: ToolLifecycleKind,
    payload: dict[str, Any],
    *,
    origin: ToolCallOrigin = "direct",
) -> NormalizedToolLifecycleEvent:
    """Normalize native/ReAct tool lifecycle payloads."""
    raw_id = (
        payload.get("id")
        or payload.get("tool_call_id")
        or payload.get("tool_use_id")
        or payload.get("call_id")
        or ""
    )
    raw_name = payload.get("name") or payload.get("tool_name") or payload.get("tool") or "tool"
    status = payload.get("status")
    is_error = bool(payload.get("is_error"))
    if isinstance(status, str) and status.lower() in {
        "error",
        "failed",
        "failure",
        "rejected",
        "cancelled",
    }:
        is_error = True
    iteration = payload.get("iteration")
    if not isinstance(iteration, int):
        iteration = None
    duration_ms = payload.get("duration_ms")
    if not isinstance(duration_ms, int):
        duration_ms = None
    extras = {key: value for key, value in payload.items() if key not in _LIFECYCLE_KNOWN_KEYS}
    return NormalizedToolLifecycleEvent(
        kind=kind,
        id=str(raw_id),
        name=str(raw_name or "tool"),
        iteration=iteration,
        input=payload.get("input", payload.get("input_preview", payload.get("args_preview"))),
        output=payload.get("output", payload.get("output_preview")),
        status=str(status) if status is not None else None,
        is_error=is_error,
        duration_ms=duration_ms,
        origin=origin,
        extras=extras,
    )


def tool_lifecycle_event_to_react_event(
    event: NormalizedToolLifecycleEvent,
) -> dict[str, Any]:
    """Render a lifecycle event using the existing ReAct stream shape."""
    base: dict[str, Any] = {
        "type": event.kind,
        "tool_call_id": event.id,
        "tool_name": event.name,
    }
    if event.iteration is not None:
        base["iteration"] = event.iteration
    if event.kind == "tool_start":
        base["input_preview"] = event.input
    else:
        base["status"] = event.status or ("error" if event.is_error else "success")
        base["output_preview"] = "" if event.output is None else str(event.output)
    if event.duration_ms is not None:
        base["duration_ms"] = event.duration_ms
    base.update(event.extras)
    return base


def tool_lifecycle_event_to_trace_payload(
    event: NormalizedToolLifecycleEvent,
) -> dict[str, Any]:
    """Render a durable trace payload while preserving legacy aliases."""
    payload: dict[str, Any] = {
        "id": event.id,
        "name": event.name,
        "tool_call_id": event.id,
        "tool": event.name,
        "origin": event.origin,
    }
    if event.iteration is not None:
        payload["iteration"] = event.iteration
    if event.kind == "tool_start":
        payload["input"] = event.input
        payload["input_preview"] = event.input
    else:
        payload["status"] = event.status or ("error" if event.is_error else "success")
        payload["is_error"] = event.is_error
        payload["output"] = event.output
        payload["output_preview"] = event.output
    if event.duration_ms is not None:
        payload["duration_ms"] = event.duration_ms
    payload.update(event.extras)
    return payload


def output_signals_error(output: Any) -> bool:
    """Return True when structured tool output reports failure."""
    if not isinstance(output, dict):
        return False
    ok = output.get("ok")
    if ok is False:
        return True
    success = output.get("success")
    if success is False:
        return True
    exit_code = output.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0:
        return True
    err = output.get("error")
    if isinstance(err, str) and err.strip() and ok is not True:
        return True
    status = output.get("status")
    return bool(isinstance(status, str) and status.lower() in ("error", "failed", "failure"))


def render_tool_output(
    output: Any,
    *,
    max_chars: int | None = None,
    prune_middle: bool = False,
    prune_policy: ToolResultPrunePolicy | None = None,
    spill_oversized: bool = False,
    tool_name: str | None = None,
    call_id: str | None = None,
) -> str:
    """Render arbitrary tool output into a bounded string.

    ``prune_middle`` (off by default) applies dsh-style head/marker/tail
    pruning before the legacy ``max_chars`` head truncation, so callers can opt
    into keeping both ends of a long tool result instead of only the head.
    ``spill_oversized`` (off by default) applies the dsh spill policy first:
    an over-cap plain-text result is saved to a session-scoped spill file and
    replaced with a bounded head/tail preview plus a locator, so the model can
    read the full output on demand instead of losing the middle. When the
    spill replacement fits the cap, the pruner below is a no-op.
    """
    if isinstance(output, str):
        rendered = output
    else:
        try:
            import json

            rendered = json.dumps(output, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = repr(output)

    if spill_oversized:
        # The call-site gate (``TOOL_RESULT_SPILL_ENABLED``) already applied the
        # master switch; inside the render funnel the policy is explicitly on so
        # the switch lives in exactly one place.
        spilled = maybe_spill_text(
            rendered,
            tool_name=tool_name or "tool",
            enabled=True,
        )
        if spilled is not None:
            rendered = spilled

    if prune_middle:
        pruned = prune_tool_result_text(
            rendered,
            policy=prune_policy,
            tool_name=tool_name,
            call_id=call_id,
        )
        if pruned is not None:
            rendered = pruned

    if max_chars is not None and max_chars >= 0 and len(rendered) > max_chars:
        rendered = (
            rendered[:max_chars] + f"\n\n...(truncated, {len(rendered) - max_chars} more chars)"
        )
    return rendered


def normalize_tool_result(
    call: Any,
    output: Any,
    *,
    is_error: bool | None = None,
    status: str = "success",
    error_type: str | None = None,
    origin: ToolCallOrigin = "direct",
    max_chars: int | None = None,
    prune_middle: bool = False,
    prune_policy: ToolResultPrunePolicy | None = None,
    spill_oversized: bool = False,
    tool_name: str | None = None,
    call_id: str | None = None,
) -> NormalizedToolResult:
    """Convert tool output into the shared result envelope."""
    normalized_call = normalize_tool_call(call, origin=origin)
    if is_error is None:
        is_error = status != "success" or output_signals_error(output)
    return NormalizedToolResult(
        id=normalized_call.id,
        name=normalized_call.name,
        output=output,
        rendered=render_tool_output(
            output,
            max_chars=max_chars,
            prune_middle=prune_middle,
            prune_policy=prune_policy,
            spill_oversized=spill_oversized,
            tool_name=tool_name,
            call_id=call_id if call_id is not None else normalized_call.id,
        ),
        is_error=bool(is_error),
        status=status,
        error_type=error_type,
        origin=normalized_call.origin,
    )


def normalize_step_tool_result(
    step: Any,
    *,
    origin: ToolCallOrigin = "direct",
    max_chars: int | None = None,
    fallback_call: Any | None = None,
    prune_middle: bool = False,
    prune_policy: ToolResultPrunePolicy | None = None,
    spill_oversized: bool = False,
    tool_name: str | None = None,
) -> NormalizedToolResult:
    """Convert an execution ``Step`` into the shared result envelope."""
    action = getattr(step, "action", None) or fallback_call
    result = getattr(step, "result", None)
    status = str(getattr(result, "status", "unknown") or "unknown")
    return normalize_tool_result(
        action,
        getattr(result, "output", None),
        status=status,
        error_type=getattr(result, "error_type", None),
        origin=origin,
        max_chars=max_chars,
        prune_middle=prune_middle,
        prune_policy=prune_policy,
        spill_oversized=spill_oversized,
        tool_name=tool_name,
    )


def normalize_task_node_tool_call(
    node: Any,
    resolved_args: dict[str, Any],
    *,
    node_index: int,
) -> NormalizedToolCall:
    """Convert a planner ``TaskNode`` into the common tool-call protocol."""
    skill_ref = getattr(node, "skill_ref", None)
    if skill_ref is None:
        node_id = str(getattr(node, "node_id", "") or f"node:{node_index}")
        raise ValueError(f"task node {node_id!r} missing skill_ref")

    node_id = str(getattr(node, "node_id", "") or f"node:{node_index}")
    return normalize_tool_call(
        {
            "id": node_id,
            "name": str(skill_ref),
            "arguments": resolved_args,
        },
        origin="planner_compat",
    )


__all__ = [
    "NormalizedToolCall",
    "NormalizedToolLifecycleEvent",
    "NormalizedToolResult",
    "ToolCallOrigin",
    "ToolLifecycleKind",
    "normalize_tool_lifecycle_event",
    "normalize_tool_call",
    "normalize_step_tool_result",
    "normalize_tool_result",
    "normalize_task_node_tool_call",
    "output_signals_error",
    "render_tool_output",
    "tool_lifecycle_event_to_react_event",
    "tool_lifecycle_event_to_trace_payload",
]
