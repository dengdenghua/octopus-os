"""Breakout threads — "let me grab you in a side thread", then merge back.

A thread can spin off a child thread with a subset of members and a context grant
from the parent; they work focused, and a summary merges back onto the parent's
shared blackboard. Because membership + board are event-sourced, a fork is just a
new thread seeded with invites, and a merge is just an attributed board write —
no new machinery.
"""

from __future__ import annotations

from runtime.memory.cowork.group import ContextGrant
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.service import invite_member


def fork(
    store: GroupStore,
    parent_thread: str,
    child_thread: str,
    *,
    actor: str,
    members: list[dict],
    grant: ContextGrant | None = None,
    at_message: int | None = None,
) -> dict:
    """Spin off ``child_thread`` from ``parent_thread`` with ``members`` (each a
    ``{"id", "kind"?, "role"?}``) and a context grant. Links the two via the
    blackboards so the UI can show the breakout and trace it back."""
    grant = grant or ContextGrant(scope="from_join")
    seeded: list[str] = []
    for m in members:
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        invite_member(
            store,
            child_thread,
            actor=actor,
            target_id=mid,
            kind=str(m.get("kind") or "agent"),
            role=str(m.get("role") or "participant"),
            grant=grant,
            at_message=0,
        )
        seeded.append(mid)
    # Cross-link via the shared boards (attributed, durable).
    store.blackboard(child_thread).write(
        "forked_from",
        {"parent": parent_thread, "at_message": at_message, "by": actor},
        writer=actor,
    )
    store.blackboard(parent_thread).write(
        f"breakout:{child_thread}",
        {"members": seeded, "at_message": at_message, "by": actor, "status": "open"},
        writer=actor,
    )
    return {"child_thread": child_thread, "members": seeded}


def merge_back(
    store: GroupStore,
    child_thread: str,
    parent_thread: str,
    *,
    actor: str,
    summary: str,
    key: str | None = None,
) -> dict:
    """Write the breakout's conclusion back onto the parent's blackboard and mark
    the breakout closed."""
    result_key = key or f"breakout:{child_thread}:summary"
    store.blackboard(parent_thread).write(result_key, summary, writer=actor)
    # Flip the breakout marker to closed if it exists.
    marker = store.blackboard(parent_thread).read(f"breakout:{child_thread}")
    if isinstance(marker, dict):
        marker["status"] = "merged"
        store.blackboard(parent_thread).write(f"breakout:{child_thread}", marker, writer=actor)
    return {"merged_into": parent_thread, "key": result_key}
