"""Sub-agent threading foundation.

The end-state model treats a dispatched sub-agent as its OWN thread (half a
conversation) rather than a fresh single-shot that shares the parent's
thread identity. This module gives a sub-agent a real child thread id plus its
lineage, and binds the run's :class:`Session` so the child is addressable
while coordination stays continuous with the parent.

Two roots are deliberately decoupled, because the event bus and the
blackboard resolve scopes differently and conflating them would split the
board:

- ``root_thread_id`` — the lineage ROOT thread id (thread-stable). The event
  bus publishes under this key, so the Workbench subscribes to
  ``/stream/{root_thread_id}`` once for the whole task lineage.
- ``blackboard_root_turn_id`` — the coordination turn id the child SHARES
  with its parent (the parent's ``turn_id``). The blackboard resolves to this,
  so a threaded child keeps reading/writing the SAME board as the parent
  without changing the parent's per-turn board semantics.

The child thread record (when the thread store is reachable) carries lineage
metadata (``parent_thread_id`` / ``root_thread_id``), so descendants inherit
the same root and share the same bus stream + board.
"""

from __future__ import annotations

import dataclasses
import uuid
from typing import Any

try:
    from runtime.platform.process.session import Session
except ImportError:  # pragma: no cover - optional
    Session = Any  # type: ignore[assignment,misc]


class SubagentThreadBinding:
    """Resolved thread identity + coordination roots for one sub-agent call."""

    __slots__ = (
        "child_thread_id",
        "root_thread_id",
        "parent_thread_id",
        "parent_turn_id",
        "persisted",
    )

    def __init__(
        self,
        *,
        child_thread_id: str,
        root_thread_id: str,
        parent_thread_id: str,
        parent_turn_id: str,
        persisted: bool = False,
    ) -> None:
        self.child_thread_id = child_thread_id
        self.root_thread_id = root_thread_id
        self.parent_thread_id = parent_thread_id
        self.parent_turn_id = parent_turn_id
        self.persisted = persisted

    def to_metadata(self) -> dict[str, str]:
        """Lineage metadata to stamp onto the child Session.

        Empty values are omitted so we never stamp useless blank keys when the
        parent had no thread/turn id (e.g. raw unit-test sessions).
        """
        meta: dict[str, str] = {}
        for key, value in (
            ("root_thread_id", self.root_thread_id),
            ("parent_thread_id", self.parent_thread_id),
            ("blackboard_root_turn_id", self.parent_turn_id),
        ):
            if value:
                meta[key] = value
        return meta


def _resolve_lineage_root(
    parent_thread_id: str,
    parent_metadata: dict[str, Any] | None,
) -> str:
    """Return the lineage ROOT thread id for a child.

    Children inherit the parent's ``root_thread_id`` when present (so a
    grandchild keeps pointing at the original root); otherwise the parent
    thread id IS the root.
    """
    meta = parent_metadata or {}
    inherited = meta.get("root_thread_id")
    if isinstance(inherited, str) and inherited.strip():
        return inherited.strip()
    return parent_thread_id


def forge_subagent_thread(
    session: Any,
    *,
    agent_id: str = "",
    role: str = "",
    persist: bool = True,
) -> SubagentThreadBinding:
    """Create a real child thread identity + lineage for a sub-agent.

    The child gets its own ``thread_id`` so it is an independent, addressable
    thread; the coordination roots are resolved from the parent so the bus
    stream and blackboard stay continuous. Best-effort: when the parent has no
    thread id, or the thread store is unreachable, the child identity is
    synthetic (ephemeral, not persisted) but the roots still resolve.
    """
    parent_thread_id = ""
    parent_turn_id = ""
    parent_metadata: dict[str, Any] = {}
    if session is not None:
        parent_thread_id = str(
            getattr(session, "thread_id", None) or getattr(session, "conversation_id", None) or ""
        ).strip()
        parent_turn_id = str(getattr(session, "turn_id", None) or "").strip()
        meta = getattr(session, "metadata", None)
        if isinstance(meta, dict):
            parent_metadata = meta

    child_thread_id = uuid.uuid4().hex
    root_thread_id = _resolve_lineage_root(parent_thread_id, parent_metadata)
    binding = SubagentThreadBinding(
        child_thread_id=child_thread_id,
        root_thread_id=root_thread_id,
        parent_thread_id=parent_thread_id,
        parent_turn_id=parent_turn_id,
        persisted=False,
    )

    if not parent_thread_id or not persist:
        return binding

    persisted = _persist_child_thread(binding, agent_id=agent_id, role=role)
    binding.persisted = persisted
    return binding


def _persist_child_thread(
    binding: SubagentThreadBinding,
    *,
    agent_id: str,
    role: str,
) -> bool:
    """Best-effort persistence of the child thread record via the store."""
    try:
        from runtime.execution.suckers.history_skill import _resolve_store

        store = _resolve_store()
        if store is None or not hasattr(store, "create"):
            return False
        metadata: dict[str, Any] = {
            **binding.to_metadata(),
        }
        if agent_id:
            metadata["subagent_role"] = role or agent_id
        store.create(
            metadata=metadata,
            values={"title": f"subagent · {role or agent_id or 'task'}"},
            status="idle",
        )
        return True
    except Exception:  # noqa: BLE001 - persistence is best-effort
        return False


def bind_subagent_session(
    session: Any,
    binding: SubagentThreadBinding,
    *,
    extra_metadata: dict[str, Any] | None = None,
    flip_thread_id: bool = False,
) -> Any:
    """Return a copy of ``session`` stamped with the sub-agent lineage roots.

    The child KEEPS the parent's ``thread_id`` / ``conversation_id`` /
    ``turn_id`` by default so journal/trace attribution stays on the parent
    conversation (the Workbench links the child's events back to the main
    thread). ``flip_thread_id=True`` opts into the independent-thread identity
    (the child gets its own thread id) — the future "sub-agent = its own
    conversation" step. ``extra_metadata`` (e.g. a locked write root) is
    merged last so callers can layer their own keys.
    """
    if session is None:
        session = Session()
    meta: dict[str, Any] = {**(session.metadata or {})}
    meta.update(binding.to_metadata())
    if extra_metadata:
        meta.update(extra_metadata)
    if flip_thread_id:
        return dataclasses.replace(
            session,
            thread_id=binding.child_thread_id,
            conversation_id=binding.child_thread_id,
            metadata=meta,
        )
    return dataclasses.replace(session, metadata=meta)


__all__ = [
    "SubagentThreadBinding",
    "bind_subagent_session",
    "forge_subagent_thread",
]
