"""Anthropic Managed Agents REST + SSE router.

Exposes the ``/v1/sessions`` surface so the official ``anthropic``
Python/TS SDK (``client.beta.sessions.*``) can connect to
echo-agent as a self-hosted backend.

Required beta header: ``anthropic-beta: managed-agents-2026-04-01``
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

try:
    from fastapi import APIRouter, HTTPException, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Request = object  # type: ignore[assignment, misc]
    StreamingResponse = None  # type: ignore[assignment, misc]

from .event_adapter import (
    requires_action_event,
    turn_completed_event,
    turn_started_event,
)
from .models import (
    AgentCustomToolUseEvent,
    AgentMessageEvent,
    AgentThinkingEvent,
    CreateSessionRequest,
    OutboundEvent,
    SendEventsRequest,
    SessionStatus,
    _new_event_id,
)
from .session_manager import SessionManager

_logger = logging.getLogger(__name__)

_BETA_HEADER = "managed-agents-2026-04-01"
_APPROVAL_TIMEOUT_DEFAULT = 600.0


def _adapt_notify(method_str: str, params: dict[str, Any]) -> list[OutboundEvent]:
    """Map a realtime ``emitter.notify(method, params)`` into Anthropic events.

    v1 maps the streaming TEXT deltas — the bulk of what an SDK client renders
    token-by-token. Item-level tool events (``item/started`` / ``item/completed``
    carrying realtime item models) are a follow-up: they need the item-model
    mapping and are skipped here rather than emitted malformed.
    """
    if method_str == "item/agentMessage/delta":
        delta = params.get("delta") or ""
        if delta:
            return [AgentMessageEvent(content=[{"type": "text", "text": delta}])]
    elif method_str == "item/reasoning/textDelta":
        delta = params.get("delta") or ""
        if delta:
            return [
                AgentThinkingEvent(
                    content=[{"type": "thinking", "thinking": delta}],
                )
            ]
    return []


class _SseEmitter:
    """A realtime ``EventEmitter`` that fans turn events out to a session's SSE
    subscribers and bridges interrupt / approval back from inbound POST events.

    Created per turn and pinned on ``SessionState.active_emitter`` so the
    ``POST /events`` handler can reach the in-flight turn:
    ``user.interrupt`` → :meth:`request_interrupt`; ``user.tool_confirmation``
    → :meth:`resolve_approval`.
    """

    def __init__(
        self,
        manager: SessionManager,
        session_id: str,
        *,
        approval_timeout: float = _APPROVAL_TIMEOUT_DEFAULT,
    ) -> None:
        self._manager = manager
        self._session_id = session_id
        self._approval_timeout = approval_timeout
        self._interrupted: set[str] = set()
        self._pending: dict[str, asyncio.Future] = {}

    @property
    def interrupted(self) -> bool:
        return bool(self._interrupted)

    # ── EventEmitter: streaming ──────────────────────────────
    async def notify(self, method: Any, params: dict[str, Any]) -> None:
        method_str = getattr(method, "value", None) or str(method)
        for evt in _adapt_notify(method_str, params or {}):
            await self._manager.publish(self._session_id, evt)

    # ── EventEmitter: approval ───────────────────────────────
    async def request_approval(
        self,
        method: Any,
        params: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> Any:
        params = params or {}
        item_id = str(
            params.get("itemId") or params.get("toolCallId") or _new_event_id(),
        )
        # Surface the request over SSE so the client knows to confirm, then go
        # idle with stop_reason=requires_action (Anthropic protocol).
        await self._manager.publish(
            self._session_id,
            AgentCustomToolUseEvent(
                tool_name=str(params.get("tool") or ""),
                tool_use_id=item_id,
                input={
                    "args_preview": str(
                        params.get("argsPreview") or params.get("detail") or "",
                    ),
                },
            ),
        )
        await self._manager.publish(
            self._session_id,
            requires_action_event(blocking_event_ids=[item_id]),
        )
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[item_id] = fut
        try:
            return await asyncio.wait_for(
                fut,
                timeout=timeout or self._approval_timeout,
            )
        except TimeoutError:
            # Fail-closed: no confirmation arrived → DENY (the old MVP stub
            # auto-accepted, silently bypassing on-request approval).
            return {"action": "decline"}
        finally:
            self._pending.pop(item_id, None)

    def resolve_approval(self, tool_use_id: str, *, allow: bool) -> bool:
        """Resolve a pending approval from an inbound user.tool_confirmation.

        Returns True if a matching pending request was resolved.
        """
        fut = self._pending.get(tool_use_id)
        if fut is None or fut.done():
            return False
        fut.set_result({"action": "accept" if allow else "decline"})
        return True

    # ── EventEmitter: interrupt registry ─────────────────────
    def is_turn_interrupted(self, turn_id: str) -> bool:
        return "*" in self._interrupted or turn_id in self._interrupted

    def get_interrupt_reason(self, turn_id: str) -> str | None:
        if "*" in self._interrupted:
            return "连接断开或后端重启"
        if turn_id in self._interrupted:
            return "用户停止了任务"
        return None

    def register_turn(self, turn_id: str) -> None:
        self._interrupted.discard(turn_id)

    def unregister_turn(self, turn_id: str) -> None:
        self._interrupted.discard(turn_id)

    def request_interrupt(self, turn_id: str = "*") -> None:
        """Mark the in-flight turn interrupted. Defaults to '*' (any turn) so a
        client interrupt lands even if it races the turn's register_turn()."""
        self._interrupted.add(turn_id)


async def _start_turn_with_claim(
    runtime: Any,
    params: dict[str, Any],
    emitter: Any,
) -> Any:
    """Run the Anthropic compatibility turn under the canonical OS claim."""

    from runtime.memory.threads.event_log import EventLog, thread_log_path
    from runtime.platform.process.thread_turn_claim import (
        ThreadTurnClaimUnavailable,
        acquire_thread_turn_claim,
    )
    from runtime.sensing.gateway._realtime_claim_aware_emitter import (
        _ClaimAwareEmitter,
    )
    from runtime.sensing.gateway._realtime_thread_delete_probe import (
        assert_thread_accepts_runtime_writes,
    )

    thread_id = params.get("threadId")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ThreadTurnClaimUnavailable("anthropic turn has no authoritative thread id")
    logs_root = getattr(runtime, "_logs_root", None)
    if logs_root is None:
        raise ThreadTurnClaimUnavailable("realtime runtime has no authoritative logs root")
    claim = acquire_thread_turn_claim(logs_root, thread_id)
    try:
        assert_thread_accepts_runtime_writes(runtime, thread_id)
        guarded_emitter = _ClaimAwareEmitter(
            emitter,
            claim,
            log=EventLog(thread_log_path(logs_root, thread_id)),
            runtime=runtime,
        )
        return await runtime.start_turn(params, guarded_emitter)
    finally:
        claim.release()


def create_anthropic_compat_router(
    *,
    stack: Any,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
    agent_registry: Any = None,
) -> Any:
    """Build the ``/v1/sessions`` FastAPI router."""
    if not FASTAPI_AVAILABLE:
        raise RuntimeError("fastapi required for anthropic compat layer")

    router = APIRouter(tags=["anthropic-compat"])
    manager = SessionManager()

    # ── Auth helper ──────────────────────────────────────────

    def _auth(request: Request) -> str | None:
        # Verify beta header presence.
        beta = request.headers.get("anthropic-beta") or ""
        if _BETA_HEADER not in beta:
            raise HTTPException(
                400,
                f"missing required header: anthropic-beta: {_BETA_HEADER}",
            )
        try:
            from runtime.sensing.gateway.openai_gateway import _resolve_actor

            return _resolve_actor(
                request,
                identity_store,
                require_auth,
                jwt_secret=jwt_secret,
                jwt_issuer=jwt_issuer,
                jwt_audience=jwt_audience,
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            if require_auth:
                raise HTTPException(401, "auth required") from exc
            return None

    def _owned_or_404(state: Any, actor: str | None) -> None:
        # Ownership gate · a session may only be read or driven by the actor
        # who created it. ``list_sessions`` already scopes by creator_actor;
        # the per-session routes must do the same, or any authenticated caller
        # could read/drive another user's session by guessing its id. We raise
        # 404 (not 403) so a non-owner can't even confirm the session exists.
        # Single-user/dev mode (require_auth off → actor is None) skips the gate.
        if actor is not None and state.creator_actor is not None and state.creator_actor != actor:
            raise HTTPException(404, "session not found")

    # ── POST /v1/sessions ────────────────────────────────────

    @router.post("/v1/sessions")
    async def create_session(request: Request) -> dict[str, Any]:
        actor = _auth(request)
        try:
            body = CreateSessionRequest.model_validate(await request.json())
        except Exception as exc:
            raise HTTPException(400, f"invalid body: {exc}") from exc
        agent_id = (
            body.agent
            if isinstance(body.agent, str)
            else (body.agent.get("id") if isinstance(body.agent, dict) else None)
        )
        state = await manager.create(
            agent_id=agent_id,
            title=body.title,
            actor=actor,
        )
        return manager.to_response(state).model_dump(mode="json")

    # ── GET /v1/sessions/{id} ────────────────────────────────

    @router.get("/v1/sessions/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        actor = _auth(request)
        state = await manager.get(session_id)
        if state is None:
            raise HTTPException(404, "session not found")
        _owned_or_404(state, actor)
        return manager.to_response(state).model_dump(mode="json")

    # ── GET /v1/sessions ─────────────────────────────────────

    @router.get("/v1/sessions")
    async def list_sessions(request: Request) -> list[dict[str, Any]]:
        actor = _auth(request)
        states = await manager.list_sessions(actor=actor)
        return [manager.to_response(s).model_dump(mode="json") for s in states[:50]]

    # ── POST /v1/sessions/{id}/events ────────────────────────

    @router.post("/v1/sessions/{session_id}/events")
    async def send_events(session_id: str, request: Request) -> dict[str, Any]:
        actor = _auth(request)
        state = await manager.get(session_id)
        if state is None:
            raise HTTPException(404, "session not found")
        _owned_or_404(state, actor)
        try:
            body = SendEventsRequest.model_validate(await request.json())
        except Exception as exc:
            raise HTTPException(400, f"invalid body: {exc}") from exc

        for raw_event in body.events:
            event_type = raw_event.get("type", "")
            if event_type == "user.message":
                # Extract text from content blocks.
                content_blocks = raw_event.get("content") or []
                text_parts = [
                    b.get("text", "")
                    for b in content_blocks
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                text = "\n".join(t for t in text_parts if t)
                if not text:
                    continue
                # Fire a turn via CerebrumRuntime.
                asyncio.create_task(
                    _run_turn(session_id, state, text, actor),
                )
            elif event_type == "user.interrupt":
                emitter = getattr(state, "active_emitter", None)
                if emitter is not None:
                    # "*" interrupts whatever turn is in flight; the ReAct loop
                    # polls is_turn_interrupted() cooperatively and bails out.
                    emitter.request_interrupt()
            elif event_type == "user.tool_confirmation":
                emitter = getattr(state, "active_emitter", None)
                tool_use_id = raw_event.get("tool_use_id") or ""
                allow = raw_event.get("result") == "allow"
                if emitter is not None and tool_use_id:
                    emitter.resolve_approval(tool_use_id, allow=allow)
            elif event_type == "user.custom_tool_result":
                # Custom-tool results need the custom-tool round-trip (the agent
                # must have an outstanding custom_tool_use). Not yet wired —
                # left explicit rather than silently swallowed.
                _logger.debug(
                    "anthropic compat: user.custom_tool_result not yet wired",
                )
            else:
                _logger.debug("anthropic compat: unknown event type %s", event_type)

        return {"ok": True}

    # ── GET /v1/sessions/{id}/events/stream ──────────────────

    @router.get("/v1/sessions/{session_id}/events/stream")
    async def stream_events(session_id: str, request: Request) -> Any:
        actor = _auth(request)
        state = await manager.get(session_id)
        if state is None:
            raise HTTPException(404, "session not found")
        _owned_or_404(state, actor)
        queue = await manager.subscribe(session_id)
        if queue is None:
            raise HTTPException(404, "session not found")

        async def _sse_generator():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    except TimeoutError:
                        # Keepalive comment.
                        yield ": keepalive\n\n"
                        continue
                    if event is None:
                        break
                    payload = event.model_dump(mode="json")
                    yield f"data: {json.dumps(payload)}\n\n"
            finally:
                await manager.unsubscribe(session_id, queue)

        return StreamingResponse(
            _sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── GET /v1/sessions/{id}/events ─────────────────────────

    @router.get("/v1/sessions/{session_id}/events")
    async def list_events(session_id: str, request: Request) -> list[dict[str, Any]]:
        actor = _auth(request)
        state = await manager.get(session_id)
        if state is None:
            raise HTTPException(404, "session not found")
        _owned_or_404(state, actor)
        return [e.model_dump(mode="json") for e in state.history[-200:]]

    # ── Internal: run a turn ─────────────────────────────────

    async def _run_turn(
        session_id: str,
        state: Any,
        text: str,
        actor: str | None,
    ) -> None:
        """Drive a ReAct turn and publish events to SSE subscribers."""
        await manager.set_status(session_id, SessionStatus.RUNNING)
        await manager.publish(session_id, turn_started_event())

        try:
            from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

            runtime: CerebrumRuntime | None = getattr(stack, "_realtime_runtime", None)
            if runtime is None:
                # Fallback: build a minimal CerebrumRuntime from the stack.
                runtime = CerebrumRuntime(stack=stack)

            # Build TurnParams-compatible dict.
            params = {
                "threadId": state.thread_id,
                "input": [{"type": "text", "text": text, "metadata": {}}],
                "approvalPolicy": "on-request",
            }
            if state.agent_id:
                params["input"][0]["metadata"] = {"agent_id": state.agent_id}

            # Emitter that streams turn events to SSE subscribers and bridges
            # interrupt / approval from inbound POST events. Pinned on the
            # session so POST /events can reach this in-flight turn.
            emitter = _SseEmitter(manager, session_id)
            state.active_emitter = emitter
            await _start_turn_with_claim(runtime, params, emitter)

            await manager.publish(
                session_id,
                turn_completed_event(interrupted=emitter.interrupted),
            )

        except Exception as exc:
            _logger.exception("anthropic compat: turn failed")
            from .models import SessionErrorEvent

            await manager.publish(
                session_id,
                SessionErrorEvent(error={"message": str(exc)}),
            )
        finally:
            state.active_emitter = None
            await manager.set_status(session_id, SessionStatus.IDLE)

    return router
