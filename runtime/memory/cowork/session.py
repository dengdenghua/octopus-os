"""Unified collaboration session — one read-model over the cowork substrate.

Codex flagged that ``cowork group`` (thread_id), ``team room`` (room_id) and
``team tasks`` (room_id) drifted as three separate sources of truth. This is
the first refactor step toward one model: the **cowork thread is the canonical
session**, and a Team Room is an optional *linked surface* of it (recorded as a
first-class ``room_link`` event, so the link is event-sourced like everything
else — no side store).

``CollaborationSession`` composes the existing stores (roster/mode/room link
from the group log, the shared blackboard, async tasks, presence) into one
object so consumers read a session instead of stitching three endpoints. It's a
read-model: no data migration, no big-bang — the strangler seam that later
flows route through.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CollaborationSession:
    """The unified view of a collaboration thread."""

    session_id: str  # = thread_id (canonical)
    room_id: str | None  # linked Team Room surface, if any
    mode: str
    roster: list[dict[str, Any]]
    blackboard: dict[str, Any]
    tasks: list[dict[str, Any]]
    presence: list[dict[str, Any]]
    room_messages: list[dict[str, Any]]  # linked room's recent transcript, if any
    room_participants: list[dict[str, Any]]  # linked room's participant config, if any
    room_tasks: list[dict[str, Any]]  # linked room's team tasks (the 3rd source), if any
    # Linked Workspace (``{"id", "name", "mount_type"}``) when a
    # ``workspace_link`` event has been folded into the group state. ``None``
    # when the thread has no workspace bound to it.
    workspace: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "room_id": self.room_id,
            "mode": self.mode,
            "roster": self.roster,
            "blackboard": self.blackboard,
            "tasks": self.tasks,
            "presence": self.presence,
            "room_messages": self.room_messages,
            "room_participants": self.room_participants,
            "room_tasks": self.room_tasks,
            "workspace": self.workspace,
        }


def resolve_session(
    group_store: Any,
    thread_id: str,
    *,
    async_store: Any = None,
    presence_store: Any = None,
    room_message_store: Any = None,
    room_messages_provider: Any = None,
    room_participants_provider: Any = None,
    room_tasks_provider: Any = None,
) -> CollaborationSession:
    """Fold the canonical thread into one session view. ``async_store`` /
    ``presence_store`` are reused if given, else built from the group store's
    base dir; presence is omitted when no store is available. When the session
    has a linked room, its recent transcript (``room_message_store``), participant
    config (``room_participants_provider``) and **team tasks**
    (``room_tasks_provider`` — the third source of truth Codex flagged) are folded
    in too, so one session view covers all three surfaces."""
    state = group_store.state(thread_id)

    if async_store is None:
        from runtime.memory.cowork.async_work import AsyncWorkStore

        async_store = AsyncWorkStore(base_dir=group_store.base_dir, group_store=group_store)
    tasks = [t.to_dict() for t in async_store.list(thread_id)]

    presence: list[dict[str, Any]] = []
    if presence_store is not None:
        from runtime.memory.cowork.presence import group_presence

        presence = [m.to_dict() for m in group_presence(group_store, presence_store, thread_id)]

    room_messages: list[dict[str, Any]] = []
    room_participants: list[dict[str, Any]] = []
    room_tasks: list[dict[str, Any]] = []
    if state.room_id:
        if room_messages_provider is not None:
            try:
                room_messages = room_messages_provider(state.room_id) or []
            except Exception:  # noqa: BLE001 — linked-room transcript is best-effort
                room_messages = []
        elif room_message_store is not None:
            try:
                room_messages = room_message_store.history(state.room_id, limit=50)
            except Exception:  # noqa: BLE001 — linked-room transcript is best-effort
                room_messages = []
        if room_participants_provider is not None:
            try:
                room_participants = room_participants_provider(state.room_id) or []
            except Exception:  # noqa: BLE001 — linked-room roster is best-effort
                room_participants = []
        if room_tasks_provider is not None:
            try:
                room_tasks = room_tasks_provider(state.room_id) or []
            except Exception:  # noqa: BLE001 — linked-room team tasks are best-effort
                room_tasks = []

    return CollaborationSession(
        session_id=thread_id,
        room_id=state.room_id,
        mode=state.mode,
        roster=[m.to_dict() for m in state.roster],
        blackboard=group_store.blackboard_snapshot(thread_id),
        tasks=tasks,
        presence=presence,
        room_messages=room_messages,
        room_participants=room_participants,
        room_tasks=room_tasks,
        workspace=state.workspace,
    )


def link_room(group_store: Any, thread_id: str, room_id: str, *, actor: str = "system") -> Any:
    """Link a Team Room to this session (event-sourced ``room_link``), so the
    two surfaces stop drifting. Returns the folded state."""
    from runtime.memory.cowork.group import MemberEvent

    group_store.append(
        thread_id,
        MemberEvent(action="room_link", actor=actor, target_id=room_id),
    )
    return group_store.state(thread_id)
