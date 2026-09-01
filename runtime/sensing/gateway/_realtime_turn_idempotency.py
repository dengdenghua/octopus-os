"""Durable ``userItemId`` replay helpers for realtime turn startup."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.memory.threads.event_log import EventLog, thread_log_path
from runtime.protocol import ItemType, JsonRpcErrorCode, Turn, TurnParams
from runtime.sensing.gateway._realtime_gateway_types import _RpcError


def turn_for_user_item_id(
    logs_root: Path | None,
    thread_id: str,
    user_item_id: str,
) -> Turn | None:
    """Return the durable turn that already owns one client item id."""

    if logs_root is None:
        return None
    path = thread_log_path(logs_root, thread_id)
    if not path.is_file():
        return None
    owning_turn_id: str | None = None
    # Filter raw lines by the opaque id first; only an actual retry pays for
    # full Pydantic replay of a long thread.
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if user_item_id not in line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, TypeError, ValueError):
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                params = payload.get("params")
                if (
                    event.get("event") == "turn_started"
                    and isinstance(params, dict)
                    and params.get("userItemId") == user_item_id
                ):
                    owning_turn_id = str(event.get("turnId") or "") or owning_turn_id
                item = payload.get("item")
                if not isinstance(item, dict) or item.get("id") != user_item_id:
                    continue
                if item.get("type") != ItemType.USER_MESSAGE.value:
                    raise _RpcError(
                        JsonRpcErrorCode.INVALID_PARAMS,
                        "userItemId already belongs to a non-user timeline item",
                    )
                owning_turn_id = str(event.get("turnId") or "") or owning_turn_id
    except OSError as exc:
        raise _RpcError(
            JsonRpcErrorCode.INTERNAL_ERROR,
            "unable to verify userItemId uniqueness",
        ) from exc
    if owning_turn_id is None:
        return None
    for turn in reversed(EventLog(path).replay()):
        if turn.id == owning_turn_id:
            return turn
    raise _RpcError(
        JsonRpcErrorCode.INTERNAL_ERROR,
        "userItemId owner is missing from the durable thread replay",
    )


def turn_input_text(params: TurnParams) -> str:
    return "\n".join(
        str(block.get("text"))
        for block in params.input
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


def existing_user_item_text(turn: Turn) -> str | None:
    for item in turn.items:
        if item.type == ItemType.USER_MESSAGE:
            return str(getattr(item, "text", ""))
    if turn.params is not None:
        return turn_input_text(turn.params)
    return None


__all__ = ["existing_user_item_text", "turn_for_user_item_id", "turn_input_text"]
