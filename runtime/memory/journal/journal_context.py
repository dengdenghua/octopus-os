from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_AGENT_ID: ContextVar[str | None] = ContextVar("echo_journal_agent_id", default=None)
_CONVERSATION_ID: ContextVar[str | None] = ContextVar(
    "echo_journal_conversation_id",
    default=None,
)
_TENANT_ID: ContextVar[str | None] = ContextVar("echo_journal_tenant_id", default=None)
_OWNER_ACTOR_ID: ContextVar[str | None] = ContextVar("echo_journal_owner_actor_id", default=None)


def current_agent_id() -> str | None:
    return _AGENT_ID.get()


def current_conversation_id() -> str | None:
    return _CONVERSATION_ID.get()


def current_tenant_id() -> str | None:
    return _TENANT_ID.get()


def current_owner_actor_id() -> str | None:
    return _OWNER_ACTOR_ID.get()


@contextmanager
def journal_context(
    *,
    agent_id: str | None = None,
    conversation_id: str | None = None,
    tenant_id: str | None = None,
    owner_actor_id: str | None = None,
) -> Iterator[None]:
    token_a = _AGENT_ID.set(agent_id)
    token_c = _CONVERSATION_ID.set(conversation_id)
    token_t = _TENANT_ID.set(tenant_id)
    token_o = _OWNER_ACTOR_ID.set(owner_actor_id)
    try:
        yield
    finally:
        _AGENT_ID.reset(token_a)
        _CONVERSATION_ID.reset(token_c)
        _TENANT_ID.reset(token_t)
        _OWNER_ACTOR_ID.reset(token_o)
