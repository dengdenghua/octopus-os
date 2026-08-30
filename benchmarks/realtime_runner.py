"""Realtime WebSocket adapter for the behavioral evaluation harness.

The adapter speaks the production JSON-RPC protocol rather than the retired
SSE endpoints. One connection and one new thread are used per trial so
``run_suite(..., k=3)`` has isolated transport state by default.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

ApprovalAction = Literal["accept", "decline"]
ApprovalResponder = Callable[[str, dict[str, Any]], dict[str, Any]]
EventObserver = Callable[[dict[str, Any]], None]
WorkspaceResolver = str | Path | Callable[[], str | Path]
ContextOverridesResolver = dict[str, Any] | Callable[[Path], dict[str, Any]]


class RealtimeEndpointError(RuntimeError):
    """A transport/setup failure that must not be scored as agent behavior."""

    def __init__(
        self,
        message: str,
        *,
        category: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code

    def to_event(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "type": "infrastructure",
            "category": self.category,
            "message": str(self),
        }
        if self.status_code is not None:
            error["status_code"] = self.status_code
        return {"kind": "infrastructure_error", "error": error}


async def probe_realtime_endpoint(
    url: str,
    *,
    token: str | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    """Verify reachability and authentication before creating eval fixtures."""

    headers = {"Authorization": f"Bearer {token}"} if token else None
    try:
        async with asyncio.timeout(timeout_seconds):
            async with connect(
                url,
                additional_headers=headers,
                open_timeout=timeout_seconds,
                close_timeout=2.0,
                ping_interval=None,
                ping_timeout=None,
            ):
                return
    except RealtimeEndpointError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalized at the transport boundary
        raise _endpoint_error(exc) from exc


def _endpoint_error(exc: Exception) -> RealtimeEndpointError:
    if isinstance(exc, InvalidStatus):
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in {401, 403}:
            return RealtimeEndpointError(
                f"realtime WebSocket authentication was rejected (HTTP {status_code})",
                category="authentication",
                status_code=status_code,
            )
        category = "capacity" if status_code == 429 else "handshake"
        return RealtimeEndpointError(
            "realtime WebSocket handshake was rejected"
            + (f" (HTTP {status_code})" if status_code is not None else ""),
            category=category,
            status_code=status_code,
        )
    if isinstance(exc, TimeoutError):
        return RealtimeEndpointError(
            "realtime WebSocket preflight timed out",
            category="timeout",
        )
    if isinstance(exc, OSError):
        return RealtimeEndpointError(
            f"realtime WebSocket is unreachable: {exc}",
            category="unreachable",
        )
    return RealtimeEndpointError(
        f"realtime WebSocket preflight failed: {exc}",
        category="transport",
    )


@dataclass
class RealtimeTrialRunner:
    """Turn a production realtime session into eval-harness events."""

    url: str
    token: str | None = None
    approval_policy: str = "never"
    approval_action: ApprovalAction = "decline"
    approval_responder: ApprovalResponder | None = None
    # Explicit engine selection is expressed as the server-registered agent
    # identity. A fresh eval thread then follows the production role route.
    agent_id: str | None = None
    model: str | None = None
    topology_id: str | None = None
    workspace: WorkspaceResolver | None = None
    context_overrides: ContextOverridesResolver | None = None
    sandbox_policy: dict[str, Any] | None = None
    timeout_seconds: float = 900.0
    event_observer: EventObserver | None = None

    def __call__(self, prompt: str):
        """Synchronous ``TrialRunner`` entry point used by ``run_suite``."""

        return iter(asyncio.run(self.run(prompt)))

    async def run(self, prompt: str) -> list[dict[str, Any]]:
        request_id = uuid.uuid4().hex
        thread_id = f"eval-{uuid.uuid4().hex}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        workspace_root: Path | None = None
        if self.workspace is not None:
            workspace = self.workspace() if callable(self.workspace) else self.workspace
            workspace_root = Path(workspace).resolve()
            if not workspace_root.is_dir():
                raise ValueError(f"Echo evaluation workspace does not exist: {workspace_root}")
        input_metadata: dict[str, Any] = {
            "source": "behavioral-eval",
            "isolatedTrial": True,
        }
        selected_agent = str(self.agent_id or "").strip()
        if selected_agent:
            # The top-level value is inspected before the free-form context by
            # the production resolver, so backend selection cannot be
            # shadowed even for a trial that has no workspace context.
            input_metadata["agent_id"] = selected_agent
        if workspace_root is not None:
            context: dict[str, Any] = {
                "mode": "code",
                "capability_mode": "code",
            }
            overrides = self.context_overrides
            if callable(overrides):
                overrides = overrides(workspace_root)
            context.update(overrides or {})
            if selected_agent:
                # Backend identity is part of the comparison contract, not an
                # arbitrary context override.  Set it last so a resolver
                # cannot silently turn a requested Codex trial into native (or
                # vice versa).
                context["agent_id"] = selected_agent
            # A caller may select the appropriate work surface, but it cannot
            # weaken trial isolation or redirect the workspace.
            context["workspace_scope"] = "project"
            context["workspace_path"] = str(workspace_root)
            input_metadata["context"] = context
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [
                {
                    "type": "text",
                    "text": prompt,
                    "metadata": input_metadata,
                }
            ],
            "approvalPolicy": self.approval_policy,
        }
        if self.model:
            params["model"] = self.model
        if self.topology_id:
            params["topologyId"] = self.topology_id
        if workspace_root is not None:
            params["cwd"] = str(workspace_root)
        if self.sandbox_policy is not None:
            params["sandboxPolicy"] = self.sandbox_policy
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "turn/start",
            "params": params,
        }
        events: list[dict[str, Any]] = []
        text_delta_seen = False
        started_at = time.monotonic()
        connected = False
        try:
            async with asyncio.timeout(self.timeout_seconds):
                async with connect(
                    self.url,
                    additional_headers=headers,
                    open_timeout=min(self.timeout_seconds, 30.0),
                    close_timeout=5.0,
                    max_size=16 * 1024 * 1024,
                    # Default keepalive pings (20s/20s) turn a transient
                    # server stall — GC pause, CPU saturation, a long tool
                    # running on the same box — into a spurious 1011
                    # disconnect mid-turn. Keep liveness detection but give
                    # the server a generous pong grace period; the trial's
                    # own timeout still bounds the whole turn.
                    ping_interval=30.0,
                    ping_timeout=90.0,
                ) as websocket:
                    connected = True
                    await websocket.send(json.dumps(request, ensure_ascii=False))
                    async for raw_message in websocket:
                        payload = json.loads(raw_message)
                        if not isinstance(payload, dict):
                            self._record(
                                events,
                                {"kind": "protocol_error", "error": "non-object message"},
                            )
                            continue
                        if "method" in payload and "id" in payload:
                            await websocket.send(
                                json.dumps(self._approval_response(payload), ensure_ascii=False)
                            )
                            self._record(
                                events,
                                {
                                    "kind": "approval_request",
                                    "method": str(payload.get("method") or ""),
                                    "params": payload.get("params") or {},
                                },
                            )
                            continue
                        if payload.get("id") == request_id:
                            if payload.get("error") is not None:
                                self._record(events, _runtime_error_event(payload["error"]))
                            else:
                                result = payload.get("result") or {}
                                turn = result.get("turn") if isinstance(result, dict) else None
                                if not text_delta_seen:
                                    self._record(events, *_final_text_events(turn))
                                self._record(events, {"kind": "turn_result", "turn": turn})
                            break
                        method = str(payload.get("method") or "")
                        params_value = payload.get("params")
                        notification_params = params_value if isinstance(params_value, dict) else {}
                        mapped = _notification_events(method, notification_params)
                        if any(row.get("kind") == "text_delta" for row in mapped):
                            text_delta_seen = True
                        self._record(events, *mapped)
        except TimeoutError:
            if not connected:
                self._record(events, _endpoint_error(TimeoutError()).to_event())
            else:
                self._record(
                    events,
                    {
                        "kind": "error",
                        "error": {
                            "type": "timeout",
                            "message": f"turn exceeded {self.timeout_seconds:g}s",
                            "timeout_seconds": self.timeout_seconds,
                            "elapsed_seconds": round(time.monotonic() - started_at, 3),
                            "event_count_before_error": len(events),
                            "last_event_kind": events[-1]["kind"] if events else None,
                        },
                    },
                )
        except Exception as exc:  # noqa: BLE001 - emit a score-safe transport event
            self._record(events, _endpoint_error(exc).to_event())
        return events

    def _record(self, events: list[dict[str, Any]], *new_events: dict[str, Any]) -> None:
        for event in new_events:
            events.append(event)
            if self.event_observer is not None:
                self.event_observer(event)

    def _approval_response(self, request: dict[str, Any]) -> dict[str, Any]:
        method = str(request.get("method") or "")
        params = request.get("params")
        safe_params = params if isinstance(params, dict) else {}
        result = (
            self.approval_responder(method, safe_params)
            if self.approval_responder is not None
            else {"action": self.approval_action}
        )
        if not isinstance(result, dict):
            raise TypeError("approval_responder must return a JSON object")
        return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def _notification_events(method: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    if method == "thread/tokenUsage/updated":
        usage = params.get("tokenUsage")
        return [{"kind": "token_usage", "usage": usage if isinstance(usage, dict) else {}}]
    if method == "item/agentMessage/delta":
        return [{"kind": "text_delta", "delta": str(params.get("delta") or "")}]
    if method == "item/reasoning/textDelta":
        return [{"kind": "reasoning_delta", "delta": str(params.get("delta") or "")}]
    if method == "item/commandExecution/outputDelta":
        return [
            {
                "kind": "tool_output",
                "item_id": params.get("itemId"),
                "delta": str(params.get("delta") or ""),
            }
        ]
    if method in {"item/started", "item/completed"}:
        item = params.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "")
            if item_type in {"commandExecution", "mcpToolCall", "fileChange", "subagent"}:
                return [
                    {
                        "kind": "tool_start" if method == "item/started" else "tool_end",
                        "tool_name": _tool_name(item),
                        "item": item,
                    }
                ]
            if item_type == "error" and method == "item/completed":
                return [_runtime_error_event(item.get("errorInfo") or item)]
        return [{"kind": "item_event", "method": method, "item": item}]
    return [{"kind": "protocol_event", "method": method, "params": params}]


def _tool_name(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or "")
    if item_type == "mcpToolCall":
        return str(item.get("tool") or "mcp_tool")
    if item_type == "commandExecution":
        return "command_execution"
    if item_type == "fileChange":
        return "file_change"
    if item_type == "subagent":
        return "subagent"
    return item_type or "unknown"


def _runtime_error_event(error: Any) -> dict[str, Any]:
    """Separate provider/control-plane outages from scored agent failures."""

    rendered = (
        json.dumps(error, ensure_ascii=False, sort_keys=True)
        if isinstance(error, (dict, list))
        else str(error)
    )
    lowered = rendered.lower()
    provider_markers = (
        "http_401",
        "http_402",
        "http_403",
        "http_429",
        "incorrect api key",
        "invalid api key",
        "authentication failed",
        "unauthorized",
        "insufficient balance",
        "insufficient funds",
        "insufficient quota",
        "payment required",
        "credit balance",
        "余额不足",
        "billing details",
        "account is suspended",
        "rate limit",
        "rate_limit",
        "provider unavailable",
        "service unavailable",
        "http_502",
        "http_503",
        "http_504",
    )
    if any(marker in lowered for marker in provider_markers):
        return {
            "kind": "infrastructure_error",
            "error": {
                "type": "infrastructure",
                "category": "provider_unavailable",
                "message": rendered,
            },
        }
    return {"kind": "error", "error": error}


def _final_text_events(turn: Any) -> list[dict[str, Any]]:
    if not isinstance(turn, dict):
        return []
    items = turn.get("items")
    if not isinstance(items, list):
        return []
    return [
        {"kind": "text_delta", "delta": str(item.get("text") or "")}
        for item in items
        if isinstance(item, dict)
        and item.get("type") == "agentMessage"
        and str(item.get("text") or "")
    ]


__all__ = [
    "ApprovalAction",
    "ApprovalResponder",
    "EventObserver",
    "RealtimeEndpointError",
    "RealtimeTrialRunner",
    "WorkspaceResolver",
    "probe_realtime_endpoint",
]


