"""Journal-to-realtime item projections for sub-agent workbench lanes.

The subscription and stream-driving lifecycle remains in
``_realtime_react_stream_drive``.  This module only parses journal events,
builds protocol items, and emits those items on the driver's asyncio loop.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
from typing import Any

from runtime.memory.threads.event_log import EventLog
from runtime.protocol import (
    ItemMarker,
    ItemStatus,
    McpToolCallItem,
    McpToolProgress,
    ServerMethod,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter

_AGENT_LIFECYCLE_MARKERS: frozenset[str] = frozenset(
    {
        ItemMarker.SUBAGENT_SPAWNED.value,
        ItemMarker.SUBAGENT_FINISHED.value,
    }
)


def _parse_lifecycle_preview(preview: Any) -> dict[str, Any]:
    """Parse the bridge's JSON preview blob back into a dict.

    ``bridge.py`` serialises the spawn/finish payloads into
    ``args_preview`` / ``output_preview`` JSON strings before writing the
    journal event; the WS item needs them as a dict again.
    """
    if not isinstance(preview, str) or not preview.strip():
        return {}
    try:
        parsed = json.loads(preview)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _subagent_lifecycle_item_from_journal(event: Any) -> McpToolCallItem | None:
    """Synthesise a marker ``McpToolCallItem`` from a journal SubTool event.

    Fires only for the sub-agent lifecycle markers the bridge mirrors onto
    the journal (``__subagent_spawned__`` / ``__subagent_finished__``);
    every other journal event returns ``None``.
    """
    kind = getattr(event, "event_type", None)
    if kind not in ("sub_tool_start", "sub_tool_end"):
        return None
    tool_name = getattr(event, "tool_name", "") or ""
    if tool_name not in _AGENT_LIFECYCLE_MARKERS:
        return None
    spawned = kind == "sub_tool_start"
    preview = (
        getattr(event, "args_preview", None) if spawned else getattr(event, "output_preview", None)
    )
    payload = _parse_lifecycle_preview(preview)
    parent_id = getattr(event, "parent_tool_use_id", None)
    if parent_id:
        payload["parent_tool_use_id"] = str(parent_id)
    event_id = str(getattr(event, "event_id", "") or "")
    if len(event_id) > 16:
        event_id = event_id.replace("-", "")[:16]
    created = getattr(event, "ts", None)
    if spawned:
        return McpToolCallItem(
            id=f"subagent_spawn_{event_id}" if event_id else "subagent_spawn",
            server="runtime",
            tool=ItemMarker.SUBAGENT_SPAWNED.value,
            arguments=payload,
            status=ItemStatus.IN_PROGRESS,
            created_at=created,
        )
    ok = bool(payload.get("ok", True))
    return McpToolCallItem(
        id=f"subagent_finish_{event_id}" if event_id else "subagent_finish",
        server="runtime",
        tool=ItemMarker.SUBAGENT_FINISHED.value,
        arguments={"parent_tool_use_id": str(parent_id)} if parent_id else {},
        result=payload,
        status=ItemStatus.COMPLETED if ok else ItemStatus.FAILED,
        created_at=created,
    )


def _subagent_tool_item_from_journal(
    event: Any,
    *,
    identity: dict[str, Any] | None = None,
    started_item: McpToolCallItem | None = None,
) -> McpToolCallItem | None:
    """Lift one real child tool step into the durable parent turn.

    Lifecycle markers are handled by ``_subagent_lifecycle_item_from_journal``;
    this maps every other ``sub_tool_start/end`` pair onto one stable MCP item
    so the workbench can show the child's actual operations live and replay
    them after reconnect/restart.
    """
    kind = getattr(event, "event_type", None)
    if kind not in ("sub_tool_start", "sub_tool_end"):
        return None
    tool_name = str(getattr(event, "tool_name", "") or "")
    if not tool_name or tool_name in _AGENT_LIFECYCLE_MARKERS:
        return None
    role = str(getattr(event, "role_id", "") or "")
    raw_call_id = str(getattr(event, "tool_call_id", "") or "")
    identity = {
        **(identity or {}),
        "agent_id": getattr(event, "agent_id", "") or (identity or {}).get("agent_id"),
        "codename": getattr(event, "codename", "") or (identity or {}).get("codename"),
        "avatar": getattr(event, "avatar", "") or (identity or {}).get("avatar"),
    }
    stable_source = (
        f"{identity.get('agent_id') or identity.get('codename') or role}:"
        f"{raw_call_id or getattr(event, 'event_id', '')}:{tool_name}"
    )
    stable_id = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()[:16]
    parent_id = getattr(event, "parent_tool_use_id", None)
    arguments: dict[str, Any] = dict(started_item.arguments if started_item is not None else {})
    arguments.update(
        {
            "agent_id": identity.get("agent_id") or role,
            "sub_agent_role": identity.get("role") or role,
            "subagent_codename": identity.get("codename"),
            "subagent_avatar": identity.get("avatar"),
            "parent_tool_use_id": str(parent_id) if parent_id else None,
            "iteration": int(getattr(event, "iteration", 0) or 0),
        }
    )
    # Keep payloads public and bounded: the journal emitter already caps this
    # preview and never writes environment/session internals.
    if kind == "sub_tool_start":
        preview = getattr(event, "args_preview", None)
        parsed = _parse_lifecycle_preview(preview)
        arguments["input"] = parsed if parsed else ({"preview": preview} if preview else {})
        return McpToolCallItem(
            id=f"subagent_tool_{stable_id}",
            server="subagent",
            tool=tool_name,
            arguments=arguments,
            status=ItemStatus.IN_PROGRESS,
            created_at=getattr(event, "ts", None),
        )
    is_error = bool(getattr(event, "is_error", False))
    output_preview = str(getattr(event, "output_preview", "") or "")
    return McpToolCallItem(
        id=f"subagent_tool_{stable_id}",
        server="subagent",
        tool=tool_name,
        arguments=arguments,
        result={
            "output_preview": output_preview,
            "status": "error" if is_error else "success",
        },
        error=output_preview if is_error and output_preview else None,
        duration_ms=int(getattr(event, "duration_ms", 0) or 0),
        status=ItemStatus.FAILED if is_error else ItemStatus.COMPLETED,
        created_at=(
            started_item.created_at if started_item is not None else getattr(event, "ts", None)
        ),
    )


def _subagent_progress_item_from_journal(
    event: Any,
    *,
    identity: dict[str, Any] | None = None,
    accumulated: str,
) -> McpToolCallItem | None:
    """Represent public child text deltas as one incrementally updated item."""
    if getattr(event, "event_type", None) != "sub_text_delta" or not accumulated:
        return None
    identity = {
        **(identity or {}),
        "agent_id": getattr(event, "agent_id", "") or (identity or {}).get("agent_id"),
        "codename": getattr(event, "codename", "") or (identity or {}).get("codename"),
        "avatar": getattr(event, "avatar", "") or (identity or {}).get("avatar"),
    }
    role = str(getattr(event, "role_id", "") or "")
    parent_id = getattr(event, "parent_tool_use_id", None)
    lane = str(
        getattr(event, "session_id", "")
        or identity.get("codename")
        or identity.get("agent_id")
        or role
        or "agent"
    )
    stable_id = hashlib.sha256(f"{lane}:{parent_id or ''}:progress".encode()).hexdigest()[:16]
    return McpToolCallItem(
        id=f"subagent_progress_{stable_id}",
        server="subagent",
        tool="__subagent_progress__",
        arguments={
            "agent_id": identity.get("agent_id") or role,
            "sub_agent_role": identity.get("role") or role,
            "subagent_codename": identity.get("codename"),
            "subagent_avatar": identity.get("avatar"),
            "parent_tool_use_id": str(parent_id) if parent_id else None,
            "round": int(getattr(event, "round", 0) or 0),
        },
        progress=McpToolProgress(
            label="子智能体输出",
            status="running",
            preview=accumulated,
        ),
        status=ItemStatus.IN_PROGRESS,
        created_at=getattr(event, "ts", None),
    )


def _subagent_lifecycle_matches(event: Any, task_id: str) -> bool:
    """True when ``event`` is this turn's sub-agent lifecycle marker."""
    if not task_id:
        return False
    return str(getattr(event, "task_id", None) or "") == str(task_id)


async def _emit_subagent_lifecycle_item(
    turn: Any,
    log: EventLog,
    emitter: EventEmitter,
    item: McpToolCallItem,
    *,
    terminal: bool,
) -> None:
    """Append + notify a synthesised lifecycle item on the driver's loop.

    Runs on the same asyncio loop as the react driver's consumer so
    ``turn.items`` is only mutated there — the same no-race rule
    ``_start_orchestrator_bridge`` documents.
    """
    existing_index = next(
        (index for index, existing in enumerate(turn.items) if existing.id == item.id),
        None,
    )
    if existing_index is None:
        turn.items.append(item)
    else:
        turn.items[existing_index] = item
    method = ServerMethod.ITEM_COMPLETED if terminal else ServerMethod.ITEM_STARTED
    logged = (
        log.item_completed(turn.thread_id, turn.id, item)
        if terminal
        else log.item_started(turn.thread_id, turn.id, item)
    )
    with contextlib.suppress(Exception):
        await emitter.notify(
            method,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
                "eventId": logged.event_id,
            },
        )


async def _emit_subagent_progress_item(
    turn: Any,
    log: EventLog,
    emitter: EventEmitter,
    item: McpToolCallItem,
    *,
    started: bool,
) -> None:
    """Start or incrementally update one durable child-output item."""
    if started:
        await _emit_subagent_lifecycle_item(
            turn,
            log,
            emitter,
            item,
            terminal=False,
        )
        return
    existing_index = next(
        (index for index, existing in enumerate(turn.items) if existing.id == item.id),
        None,
    )
    if existing_index is None:
        await _emit_subagent_lifecycle_item(
            turn,
            log,
            emitter,
            item,
            terminal=False,
        )
        return
    turn.items[existing_index] = item
    progress = item.progress.model_dump(by_alias=True, mode="json") if item.progress else {}
    logged = log.item_delta(
        turn.thread_id,
        turn.id,
        item.id,
        "mcpToolProgress",
        progress,
    )
    with contextlib.suppress(Exception):
        await emitter.notify(
            ServerMethod.ITEM_MCP_TOOL_CALL_PROGRESS,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "itemId": item.id,
                "progress": progress,
                "eventId": logged.event_id,
            },
        )


__all__ = [
    "_emit_subagent_lifecycle_item",
    "_emit_subagent_progress_item",
    "_parse_lifecycle_preview",
    "_subagent_lifecycle_item_from_journal",
    "_subagent_lifecycle_matches",
    "_subagent_progress_item_from_journal",
    "_subagent_tool_item_from_journal",
]
