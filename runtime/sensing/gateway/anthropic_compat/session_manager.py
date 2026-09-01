"""Session lifecycle manager for the Anthropic compat layer.

Each "session" maps 1:1 to an echo-agent thread. The manager
tracks status, cumulative usage, and SSE subscriber queues.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .models import (
    OutboundEvent,
    SessionResponse,
    SessionStatus,
    UsageInfo,
)


@dataclass
class SessionState:
    session_id: str
    thread_id: str
    agent_id: str | None = None
    title: str | None = None
    status: SessionStatus = SessionStatus.IDLE
    created_at: float = field(default_factory=time.time)
    input_tokens: int = 0
    output_tokens: int = 0
    # SSE subscribers: each is an asyncio.Queue that receives
    # OutboundEvent instances. Closed by putting None.
    subscribers: list[asyncio.Queue[OutboundEvent | None]] = field(
        default_factory=list,
    )
    # Event history for GET /events replay.
    history: list[OutboundEvent] = field(default_factory=list)
    # Actor who created this session (for ownership checks).
    creator_actor: str | None = None
    # The live EventEmitter for the in-flight turn — set by the router while a
    # turn runs, cleared when it finishes. Lets POST /events reach the running
    # turn to deliver an interrupt or an approval confirmation. None when idle.
    active_emitter: Any = None


class SessionManager:
    """In-memory session registry.

    For MVP this is process-local. A production deployment with
    multiple workers would need a shared store (Redis / SQLite).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        agent_id: str | None = None,
        title: str | None = None,
        actor: str | None = None,
    ) -> SessionState:
        session_id = f"sesn_{uuid4().hex[:24]}"
        thread_id = f"thr_{uuid4().hex[:12]}"
        state = SessionState(
            session_id=session_id,
            thread_id=thread_id,
            agent_id=agent_id,
            title=title,
            creator_actor=actor,
        )
        async with self._lock:
            self._sessions[session_id] = state
        return state

    async def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    async def list_sessions(
        self,
        *,
        actor: str | None = None,
        status: SessionStatus | None = None,
    ) -> list[SessionState]:
        results = list(self._sessions.values())
        if actor:
            results = [s for s in results if s.creator_actor == actor]
        if status:
            results = [s for s in results if s.status == status]
        return sorted(results, key=lambda s: s.created_at, reverse=True)

    async def set_status(
        self,
        session_id: str,
        status: SessionStatus,
    ) -> None:
        state = self._sessions.get(session_id)
        if state:
            state.status = status

    async def add_usage(
        self,
        session_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        state = self._sessions.get(session_id)
        if state:
            state.input_tokens += input_tokens
            state.output_tokens += output_tokens

    async def subscribe(self, session_id: str) -> asyncio.Queue[OutboundEvent | None] | None:
        state = self._sessions.get(session_id)
        if state is None:
            return None
        q: asyncio.Queue[OutboundEvent | None] = asyncio.Queue(maxsize=512)
        state.subscribers.append(q)
        return q

    async def unsubscribe(self, session_id: str, q: asyncio.Queue[Any]) -> None:
        state = self._sessions.get(session_id)
        if state:
            with contextlib.suppress(ValueError):
                state.subscribers.remove(q)

    async def publish(self, session_id: str, event: OutboundEvent) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        state.history.append(event)
        dead: list[asyncio.Queue[Any]] = []
        for q in state.subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            with contextlib.suppress(ValueError):
                state.subscribers.remove(q)

    async def close_subscribers(self, session_id: str) -> None:
        state = self._sessions.get(session_id)
        if state is None:
            return
        for q in state.subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)

    def to_response(self, state: SessionState) -> SessionResponse:
        from datetime import UTC, datetime

        return SessionResponse(
            id=state.session_id,
            status=state.status,
            title=state.title,
            agent_id=state.agent_id,
            created_at=datetime.fromtimestamp(
                state.created_at,
                tz=UTC,
            ).isoformat(),
            usage=UsageInfo(
                input_tokens=state.input_tokens,
                output_tokens=state.output_tokens,
            ),
        )
