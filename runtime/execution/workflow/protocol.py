"""Host↔worker JSONL protocol for workflow runs (dsh ``protocol.ts``).

One worker process hosts one script execution. Messages are newline-
delimited JSON on stdin (host→worker) / stdout (worker→host); stderr is
diagnostics only. Scripts cannot print (``log()`` is the narration hook),
so stdout stays a clean protocol channel.
"""

from __future__ import annotations

import json
from typing import Any, Literal, TypedDict


class WorkerInit(TypedDict, total=False):
    """The worker's one-time init payload (first stdin line)."""

    runId: str
    name: str
    body: str
    args: Any
    maxTotalAgents: int
    maxConcurrentAgents: int
    maxItemsPerCall: int


class AgentStartRequest(TypedDict, total=False):
    """What a script ``agent()`` call asks the host to start."""

    prompt: str
    schema: dict[str, Any] | None
    agent: str | None  # echo role override (dsh ``provider``)
    model: str | None


class AgentStartResult(TypedDict, total=False):
    """The host's answer to one ``agent()`` call."""

    ok: bool
    output: str | None
    structured: Any
    stop_reason: str
    error: str | None
    child_id: str | None
    fatal: bool | None


# ── host → worker ────────────────────────────────────────────

HostToWorkerMessage = (
    tuple[Literal["cancel"], str]
    | tuple[Literal["agent-response"], int, AgentStartResult]
    | tuple[Literal["agent-started"], int, str | None]
)


def encode_host_message(message: HostToWorkerMessage) -> str:
    kind, *rest = message
    if kind == "cancel":
        return json.dumps({"type": "cancel", "reason": rest[0]}, ensure_ascii=False)
    if kind == "agent-started":
        seq, child_id = rest  # type: ignore[misc]
        payload: dict[str, Any] = {"type": "agent-started", "seq": seq}
        if child_id is not None:
            payload["childId"] = child_id
        return json.dumps(payload, ensure_ascii=False)
    seq, result = rest  # type: ignore[misc]
    return json.dumps(
        {"type": "agent-response", "seq": seq, "result": result},
        ensure_ascii=False,
    )


# ── worker → host ────────────────────────────────────────────

WorkerToHostMessage = (
    tuple[Literal["phase"], str]
    | tuple[Literal["log"], str]
    | tuple[Literal["agent-request"], int, AgentStartRequest]
    | tuple[Literal["agent-start"], int, str, str | None, str | None]
    | tuple[Literal["agent-end"], int, str, str | None]
    | tuple[Literal["result"], str, Any, int, str | None]
)


def encode_worker_message(message: WorkerToHostMessage) -> str:
    kind, *rest = message
    if kind == "phase":
        return json.dumps({"type": "phase", "title": rest[0]}, ensure_ascii=False)
    if kind == "log":
        return json.dumps({"type": "log", "message": rest[0]}, ensure_ascii=False)
    if kind == "agent-request":
        seq, request = rest  # type: ignore[misc]
        return json.dumps(
            {"type": "agent-request", "seq": seq, **request},
            ensure_ascii=False,
        )
    if kind == "agent-start":
        seq, label, phase, child_id = rest  # type: ignore[misc]
        payload: dict[str, Any] = {"type": "agent-start", "seq": seq, "label": label}
        if phase is not None:
            payload["phase"] = phase
        if child_id is not None:
            payload["childId"] = child_id
        return json.dumps(payload, ensure_ascii=False)
    if kind == "agent-end":
        seq, outcome, child_id = rest  # type: ignore[misc]
        payload: dict[str, Any] = {"type": "agent-end", "seq": seq, "outcome": outcome}
        if child_id is not None:
            payload["childId"] = child_id
        return json.dumps(payload, ensure_ascii=False)
    stop_reason, value, agents_started, error = rest  # type: ignore[misc]
    payload: dict[str, Any] = {
        "type": "result",
        "stopReason": stop_reason,
        "value": value,
        "agentsStarted": agents_started,
    }
    if error is not None:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False)


def decode_host_message(line: str) -> HostToWorkerMessage:
    """Parse one host→worker line; invalid lines raise ``ValueError``."""
    payload = json.loads(line)
    kind = payload.get("type")
    if kind == "cancel":
        return ("cancel", str(payload.get("reason") or "workflow cancelled"))
    if kind == "agent-started":
        return ("agent-started", int(payload["seq"]), payload.get("childId"))
    if kind == "agent-response":
        seq = int(payload["seq"])
        result: AgentStartResult = payload.get("result") or {}
        return ("agent-response", seq, result)
    raise ValueError(f"unknown host→worker message type: {kind!r}")


def decode_worker_message(line: str) -> WorkerToHostMessage:
    """Parse one worker→host line; invalid lines raise ``ValueError``."""
    payload = json.loads(line)
    kind = payload.get("type")
    if kind == "phase":
        return ("phase", str(payload["title"]))
    if kind == "log":
        return ("log", str(payload["message"]))
    if kind == "agent-request":
        return (
            "agent-request",
            int(payload["seq"]),
            {
                "prompt": str(payload.get("prompt", "")),
                "schema": payload.get("schema"),
                "agent": payload.get("agent"),
                "model": payload.get("model"),
            },
        )
    if kind == "agent-start":
        return (
            "agent-start",
            int(payload["seq"]),
            str(payload.get("label", "")),
            payload.get("phase"),
            payload.get("childId"),
        )
    if kind == "agent-end":
        return (
            "agent-end",
            int(payload["seq"]),
            str(payload.get("outcome", "completed")),
            payload.get("childId"),
        )
    if kind == "result":
        return (
            "result",
            str(payload.get("stopReason", "error")),
            payload.get("value"),
            int(payload.get("agentsStarted", 0)),
            payload.get("error"),
        )
    raise ValueError(f"unknown worker→host message type: {kind!r}")


__all__ = [
    "AgentStartRequest",
    "AgentStartResult",
    "WorkerInit",
    "decode_host_message",
    "decode_worker_message",
    "encode_host_message",
    "encode_worker_message",
]
