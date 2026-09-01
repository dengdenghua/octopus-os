"""Bridge a Workspace into a cowork group.

A Workspace (mount + members, persisted by ``WorkspaceStore``) is bound to a
cowork thread via an event-sourced ``workspace_link`` MemberEvent — the same
pattern ``link_room`` uses for Team Rooms. Once linked, the workspace's member
roster is mirrored into the group as invite/leave events so the thread sees a
live view of "who can touch this mount."

Mapping rules (``sync_workspace_members_to_group``):
  - workspace members not yet in the group  → ``invite`` event
  - group members no longer in the workspace → ``leave`` event
  - role: ``owner``/``editor`` → ``participant`` (can act)
          ``reviewer``/``viewer`` → ``observer`` (read-only)

ContextGrant per role (the privacy seam — what slice of thread history a
newly-invited member may see):
  - ``owner``/``editor`` → ``scope=all``           (full history)
  - ``reviewer``         → ``scope=from_join``     (from join onward)
  - ``viewer``           → ``scope=summary``        (summary only, no raw history)

Both helpers are pure orchestrators over the existing stores; no new storage
engine is introduced.
"""

from __future__ import annotations

import time

from runtime.memory.cowork.group import ContextGrant, MemberEvent, MemberRole
from runtime.memory.cowork.group_store import GroupStore
from runtime.workspace.store import WorkspaceStore

__all__ = [
    "broadcast_file_written",
    "grant_for_workspace_role",
    "sync_workspace_members_to_group",
    "link_workspace_to_group",
    "map_workspace_role",
]


# Workspace roles that grant write/execute access in the cowork group. Everything
# else (``reviewer``, ``viewer``) maps to ``observer`` — they can see the
# collaboration but the responders filter excludes them from acting.
_WRITE_ROLES = frozenset({"owner", "editor"})


def map_workspace_role(workspace_role: str) -> MemberRole:
    """Translate a Workspace member role into a cowork group role.

    ``owner``/``editor`` → ``participant`` (can act on the thread);
    ``reviewer``/``viewer`` → ``observer`` (read-only). Any unknown role
    defaults to ``observer`` so an accidental misconfiguration never hands
    out write access.
    """
    return "participant" if workspace_role in _WRITE_ROLES else "observer"


def grant_for_workspace_role(workspace_role: str) -> ContextGrant:
    """Translate a Workspace member role into a ContextGrant.

    The grant decides what slice of the thread's history a newly-invited
    member is allowed to see — see ``runtime.memory.cowork.group.visible_message_range``.

      - ``owner`` / ``editor`` → ``scope=all``           (full history)
      - ``reviewer``           → ``scope=from_join``      (from join onward)
      - ``viewer``             → ``scope=summary``         (no raw history)
      - unknown role           → ``scope=summary``         (fail-safe)
    """
    if workspace_role in _WRITE_ROLES:
        return ContextGrant(scope="all")
    if workspace_role == "reviewer":
        return ContextGrant(scope="from_join")
    return ContextGrant(scope="summary")


def sync_workspace_members_to_group(
    workspace_store: WorkspaceStore,
    group_store: GroupStore,
    workspace_id: str,
    thread_id: str,
    *,
    actor: str = "",
) -> None:
    """Mirror the workspace's member roster into the cowork group.

    New members (in workspace, not in group) get an ``invite`` event; members
    that left the workspace get a ``leave`` event. Role changes for existing
    members are *not* replayed here — re-inviting would just re-stamp
    ``joined_at_message`` — callers that need a role refresh should leave + re-
    invite explicitly. The actor id on every emitted event is the ``actor``
    argument (defaults to ``"workspace:<workspace_id>"``) so audit trails can
    attribute the change to the workspace sync.
    """
    ws_members = workspace_store.list_members(workspace_id)
    state = group_store.state(thread_id)

    ws_member_ids = {m.member_id for m in ws_members}
    group_member_ids = {m.id for m in state.roster}

    attribution = actor or f"workspace:{workspace_id}"

    # Invite workspace members that aren't yet in the group.
    for wm in ws_members:
        if wm.member_id in group_member_ids:
            continue
        group_store.append(
            thread_id,
            MemberEvent(
                action="invite",
                actor=attribution,
                target_id=wm.member_id,
                target_kind="agent",
                role=map_workspace_role(wm.role),
                grant=grant_for_workspace_role(wm.role),
            ),
        )

    # Remove group members that are no longer in the workspace.
    for member_id in group_member_ids - ws_member_ids:
        group_store.append(
            thread_id,
            MemberEvent(
                action="leave",
                actor=attribution,
                target_id=member_id,
            ),
        )


def link_workspace_to_group(
    workspace_store: WorkspaceStore,
    group_store: GroupStore,
    workspace_id: str,
    thread_id: str,
    *,
    actor: str = "system",
) -> None:
    """Bind ``workspace_id`` to the cowork thread ``thread_id``.

    Sends a ``workspace_link`` MemberEvent carrying the workspace's id, name
    and mount_type, then syncs the workspace's member roster into the group
    (see ``sync_workspace_members_to_group``). Re-calling on an already-linked
    thread re-stamps the link (the latest ``workspace_link`` wins in
    ``fold_state``) and re-runs the membership diff — both are idempotent.
    """
    ws = workspace_store.get_workspace(workspace_id)
    if ws is None:
        raise ValueError(f"workspace {workspace_id!r} does not exist")

    group_store.append(
        thread_id,
        MemberEvent(
            action="workspace_link",
            actor=actor,
            target_id=workspace_id,
            workspace={
                "id": ws.id,
                "name": ws.name,
                "mount_type": ws.mount_type,
            },
        ),
    )

    sync_workspace_members_to_group(
        workspace_store,
        group_store,
        workspace_id,
        thread_id,
        actor=f"workspace:{workspace_id}",
    )


def broadcast_file_written(
    group_store: GroupStore,
    thread_id: str,
    file_path: str,
    writer_id: str,
    *,
    workspace_id: str | None = None,
) -> None:
    """Broadcast a ``file_written`` event on the cowork group's blackboard.

    Called after a successful remote-workspace write so collaborators on the
    same thread are notified that the file changed. The event lands on the
    thread's shared ``SqliteBlackboard`` under the ``file_written`` key as an
    append-only list of ``{file_path, writer_id, ts}`` entries — readers poll
    the key (or subscribe to future pub/sub) to refresh their view.

    No-op when ``thread_id`` is empty or ``group_store`` is None — callers
    that aren't bound to a cowork group simply skip the broadcast.
    """
    if not thread_id or group_store is None:
        return
    board = group_store.blackboard(thread_id)
    # Read existing entries (if any) and append. Use a list payload so
    # successive writes accumulate instead of overwriting each other.
    try:
        existing = board.read("file_written", default=[])
    except Exception:  # noqa: BLE001 — blackboard read must not crash the write
        existing = []
    if not isinstance(existing, list):
        existing = []
    entry = {
        "file_path": file_path,
        "writer_id": writer_id,
        "ts": time.time(),
        "workspace_id": workspace_id or "",
    }
    existing.append(entry)
    board.write("file_written", existing, writer=writer_id or "fs_router")
