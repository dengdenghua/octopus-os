"""Shared state and helpers for realtime cerebrum tests.

Module-level mutable state (_SCRIPT, _LAST_*) is intentionally kept here
as a single-source singleton — both conftest.py's fake_stream and test
files' _set_script mutate the same objects.
"""

from __future__ import annotations

from typing import Any

from runtime.protocol import (
    JsonRpcRequest,
    JsonRpcResponse,
    Notification,
    decode_message,
    encode_message,
)

# ─── module-level mutable state (singleton) ────────────────

_SCRIPT: list[dict[str, Any]] = []
_SCRIPT_POP_ONCE: bool = False
_LAST_STREAM_ARGS: dict[str, Any] = {}
_LAST_STREAM_KWARGS: dict[str, Any] = {}
_LAST_SESSION: dict[str, Any] = {}


# ─── helpers ───────────────────────────────────────────────


def set_script(events: list[dict[str, Any]]) -> None:
    """Replace the fake react_loop script."""
    global _SCRIPT_POP_ONCE
    _SCRIPT.clear()
    _SCRIPT.extend(events)
    _SCRIPT_POP_ONCE = False
    _LAST_STREAM_KWARGS.clear()
    _LAST_SESSION.clear()


def drive(ws: Any, params: dict[str, Any], approve: bool = True) -> dict[str, Any]:
    """Drive a WebSocket turn/start round-trip and collect notifications."""
    ws.send_text(encode_message(JsonRpcRequest(id=1, method="turn/start", params=params)))
    notifications: list[Notification] = []
    response: JsonRpcResponse | None = None
    while True:
        msg = decode_message(ws.receive_text())
        if isinstance(msg, JsonRpcRequest):
            ws.send_text(
                encode_message(
                    JsonRpcResponse(
                        id=msg.id, result={"action": "accept" if approve else "decline"}
                    )
                )
            )
            continue
        if isinstance(msg, Notification):
            notifications.append(msg)
            continue
        if isinstance(msg, JsonRpcResponse) and msg.id == 1:
            response = msg
            break
    assert response is not None
    return {"response": response, "notifications": notifications}

