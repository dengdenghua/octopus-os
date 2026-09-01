#!/usr/bin/env python3
"""Small stdio MCP bridge for the Echo REC HTTP capability."""

from __future__ import annotations

import json
import os
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SERVER_INFO = {"name": "Echo Recorder", "version": "1.2.0"}
TOOLS = [
    {
        "name": "recording_start",
        "description": "Start or resume an explicitly approved Echo recording for a task.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thread_id", "name"],
            "properties": {
                "thread_id": {"type": "string"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "provider": {
                    "type": "string",
                    "enum": ["hybrid", "human", "agent"],
                    "default": "hybrid",
                },
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "recording_status",
        "description": "Read the current Echo recording status without polling.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thread_id"],
            "properties": {"thread_id": {"type": "string"}},
        },
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "recording_append_events",
        "description": "Append a bounded batch of provider events to an active recording.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thread_id", "events"],
            "properties": {
                "thread_id": {"type": "string"},
                "events": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {"type": "object"},
                },
            },
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "recording_provider_status",
        "description": "Check whether the Chrome browser relay provider is online and which tab it can record.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "annotations": {"readOnlyHint": True, "idempotentHint": True},
    },
    {
        "name": "recording_stop",
        "description": "Stop the active recording and return its workflow or skill result.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["thread_id"],
            "properties": {
                "thread_id": {"type": "string"},
                "use_llm": {"type": "boolean", "default": True},
            },
        },
        "annotations": {"readOnlyHint": False, "idempotentHint": True},
    },
]


def _base_url() -> str:
    return os.environ.get("ECHO_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    token = os.environ.get("ECHO_AUTH_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(_base_url() + path, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - localhost plugin bridge
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Echo REC HTTP {exc.code}: {detail[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Echo REC backend unavailable: {exc.reason}") from exc


def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
    thread_id = str(arguments.get("thread_id") or "")
    if name == "recording_start":
        return _request("POST", "/api/teach-repeat/record/start", arguments)
    if name == "recording_status":
        from urllib.parse import quote

        return _request(
            "GET",
            f"/api/teach-repeat/record/status?thread_id={quote(thread_id, safe='')}",
        )
    if name == "recording_append_events":
        return _request("POST", "/api/teach-repeat/record/events", arguments)
    if name == "recording_provider_status":
        relay = _request("GET", "/api/browser/relay/status")
        return {
            "agent": {"available": True},
            "browser": {
                "available": bool(relay.get("connected")),
                "state": relay.get("connection_state", "offline"),
                "extension_version": relay.get("extension_version", ""),
                "active_tab": relay.get("active_tab"),
            },
        }
    if name == "recording_stop":
        return _request("POST", "/api/teach-repeat/record/stop", arguments)
    raise RuntimeError(f"Unknown tool: {name}")


def _response(request_id: Any, result: Any = None, error: Any = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    return payload


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        protocol = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
        return _response(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params") or {}
        try:
            result = _call_tool(str(params.get("name") or ""), params.get("arguments") or {})
            return _response(
                request_id,
                {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]},
            )
        except Exception as exc:  # noqa: BLE001 - MCP must return structured errors
            return _response(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
    if request_id is not None:
        return _response(request_id, error={"code": -32601, "message": "Method not found"})
    return None


def main() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _handle(message)
        except Exception as exc:  # noqa: BLE001
            response = _response(None, error={"code": -32700, "message": str(exc)})
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

