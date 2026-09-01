"""Turn-level session object · carries identity + context through a turn.

Motivation
----------

Before this module, every async/threaded path in the runtime had to
re-plumb the same values manually:

    * ``actor`` (the human / API identity)     — via `current_actor` ContextVar
    * ``agent`` (the persona handling the turn) — via `_resolve_agent` + kwargs
    * ``thread_id`` / ``conversation_id``       — via body / query params
    * ``budget`` / ``started_at`` / ``turn_id`` — reconstructed each time

Missed propagation = production bugs. The session has already cost us:

    * ``no current_actor set`` when planner ran in the SSE generator's
      thread and no one called ``_set_actor_ctx()``.
    * Memory / USER.md skills can't tell which agent is "active now".
    * Journal writes miss ``agent_id`` / ``conversation_id`` tags.

The Session object is a small dataclass carrying all these in one spot,
plus a ContextVar so skills / routers that can't take it as a parameter
can still look it up implicitly:

    with session_scope(Session(actor=..., agent=agent, ...)):
        ...                # inside here, current_session() returns it

Downstream call sites can either take a ``session`` keyword explicitly
(new style) or keep reading the existing ContextVars (they still work —
``session_scope`` also sets the underlying ``current_actor`` /
``current_agent_id`` vars for backward compat).

This doesn't refactor every caller at once. It introduces the shape;
downstream code migrates one handler at a time.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

if TYPE_CHECKING:  # pragma: no cover
    from runtime.execution.agents.base import Agent


# ═══════════════════════════════════════════════════════════
# ContextVars · shared with legacy callers
# ═══════════════════════════════════════════════════════════

# The master Session ContextVar. New code reads this. Its value is set
# by `session_scope()`.
_current_session: ContextVar[Session | None] = ContextVar(
    "current_session",
    default=None,
)

# Agent identity for call paths that do not receive a full Session.
_current_agent_id: ContextVar[str | None] = ContextVar(
    "current_agent_id",
    default=None,
)

# The concrete parent tool call currently executing on this context.  Session
# metadata also mirrors this value for older dispatchers, but metadata is a
# shared mutable dict and therefore cannot distinguish two parallel parent
# tool calls.  A ContextVar gives every worker its own exact causal parent.
_current_parent_tool_use_id: ContextVar[str | None] = ContextVar(
    "current_parent_tool_use_id",
    default=None,
)


# ═══════════════════════════════════════════════════════════
# Session dataclass
# ═══════════════════════════════════════════════════════════


@dataclass(slots=True)
class Session:
    """Everything a skill / router might need to know about WHO/WHEN.

    Fields
    ------
    actor :
        The human/API identity for billing, audit, and cross-request
        state (links, credits, per-user memory promotion). None for
        anonymous dev runs.
    agent :
        The `Agent` preset handling this turn. None for raw
        `/v1/chat/completions` traffic that doesn't route through
        personas.
    thread_id / conversation_id :
        Identifiers for grouping multi-turn chat. `thread_id` is the
        canonical one used by the compat router; `conversation_id` is
        kept as an alias for the older OpenAI-gateway surface.
    turn_id :
        Unique per turn (one server-side response to one user message).
        Useful when multiple parallel skills want to tag journal
        entries with a common key.
    started_at :
        Wall-clock (seconds since epoch) when the turn kicked off. Use
        for latency metrics and budget timers.
    """

    actor: str | None = None
    agent: Agent | None = None
    thread_id: str | None = None
    conversation_id: str | None = None
    turn_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def agent_id(self) -> str | None:
        return self.agent.agent_id if self.agent is not None else None

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.started_at


# ═══════════════════════════════════════════════════════════
# Scope helpers
# ═══════════════════════════════════════════════════════════


def current_session() -> Session | None:
    """Return the Session active in this thread/task, or None."""
    return _current_session.get()


def current_actor() -> str | None:
    s = _current_session.get()
    return s.actor if s else None


def current_agent_id() -> str | None:
    s = _current_session.get()
    if s is not None:
        return s.agent_id
    # Legacy fallback: pure ContextVar without a Session.
    return _current_agent_id.get()


def current_parent_tool_use_id() -> str | None:
    """Return the parent tool-use id bound to the current execution context."""
    return _current_parent_tool_use_id.get()


@contextmanager
def parent_tool_use_scope(tool_use_id: str) -> Iterator[str]:
    """Bind one tool call as the causal parent for nested delegated work."""
    token = _current_parent_tool_use_id.set(tool_use_id or None)
    try:
        yield tool_use_id
    finally:
        _current_parent_tool_use_id.reset(token)


@contextmanager
def session_scope(session: Session) -> Iterator[Session]:
    """Activate a Session for the duration of the ``with`` block.

    Also mirrors the actor onto the provider-neutral model-router context.
    """
    tok_session: Token = _current_session.set(session)
    tok_agent: Token = _current_agent_id.set(session.agent_id)

    from runtime.sensing.model_router.actor_context import current_actor as _model_actor

    model_actor_tok = _model_actor.set(session.actor)

    try:
        yield session
    finally:
        _current_session.reset(tok_session)
        _current_agent_id.reset(tok_agent)
        _model_actor.reset(model_actor_tok)


def bind_thread_session(
    session: Session,
) -> tuple[Token, Token, Token | None]:
    """Non-context-manager variant — sets the session on the CURRENT
    thread/task without auto-resetting. Use this when the caller owns
    thread lifecycle (e.g. a Starlette-spawned SSE generator) and will
    manually ``unbind_thread_session`` at the end.

    Returns ``(tok_session, tok_agent, tok_model_actor)`` — pass them to
    :func:`unbind_thread_session` in a ``finally`` to reset the ContextVars
    and avoid leaking the session onto a reused thread.
    """
    tok_session = _current_session.set(session)
    tok_agent = _current_agent_id.set(session.agent_id)
    from runtime.sensing.model_router.actor_context import current_actor as _model_actor

    tok_model_actor = _model_actor.set(session.actor)
    return tok_session, tok_agent, tok_model_actor


def unbind_thread_session(
    tok_session: Token,
    tok_agent: Token,
    tok_model_actor: Token | None = None,
) -> None:
    """Reset the ContextVars set by :func:`bind_thread_session` using its
    returned tokens. The documented partner that was previously referenced but
    never existed."""
    _current_session.reset(tok_session)
    _current_agent_id.reset(tok_agent)
    if tok_model_actor is not None:
        from runtime.sensing.model_router.actor_context import current_actor as _model_actor

        _model_actor.reset(tok_model_actor)


__all__ = [
    "Session",
    "current_session",
    "current_actor",
    "current_agent_id",
    "session_scope",
    "bind_thread_session",
    "unbind_thread_session",
]
