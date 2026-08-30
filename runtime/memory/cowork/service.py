"""Membership service — the actor-agnostic write path.

The HTTP router attributes mutations to the authenticated *human*. But a real
team also has members pulling in *other* members: "this needs a DB expert, I'll
grab @db-agent." ``MemberEvent.actor`` is already a free-form id, so an agent's
delegation tool can call these helpers with ``actor=<its own id>`` to assemble
the team it needs — agent-initiated membership, no special case.

Thin wrappers over ``GroupStore.append`` so both the router and in-process agent
tools share one validated write path.
"""

from __future__ import annotations

from runtime.memory.cowork.group import (
    VALID_MODES,
    ContextGrant,
    MemberEvent,
    normalize_group_mode,
)
from runtime.memory.cowork.group_store import GroupStore


def invite_member(
    store: GroupStore,
    thread_id: str,
    *,
    actor: str,
    target_id: str,
    kind: str = "agent",
    role: str = "participant",
    grant: ContextGrant | None = None,
    at_message: int | None = None,
) -> MemberEvent:
    """Pull a member into the thread. ``actor`` may be a human OR an agent id —
    that's the whole agent-initiated-invite feature."""
    if not target_id:
        raise ValueError("target_id is required")
    return store.append(
        thread_id,
        MemberEvent(
            action="invite",
            actor=actor or "system",
            target_id=target_id,
            target_kind="human" if kind == "human" else "agent",
            role="observer" if role == "observer" else "participant",
            grant=grant or ContextGrant(),
            at_message=at_message,
        ),
    )


def remove_member(store: GroupStore, thread_id: str, *, actor: str, target_id: str) -> MemberEvent:
    return store.append(
        thread_id,
        MemberEvent(action="leave", actor=actor or "system", target_id=target_id),
    )


def set_mode(store: GroupStore, thread_id: str, *, actor: str, mode: str) -> MemberEvent:
    normalized_mode = normalize_group_mode(mode)
    if normalized_mode is None:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
    return store.append(
        thread_id,
        MemberEvent(action="mode", actor=actor or "system", mode=normalized_mode),
    )
