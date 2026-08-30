"""Bridge an enterprise Channel (频道) into a cowork group (频道即群聊).

A Channel (org + department + ACL, persisted by ``OrgStore``) is bound to a
cowork thread via an event-sourced ``room_link`` MemberEvent — the same pattern
``cowork_bridge.link_workspace_to_group`` uses for Workspaces. Once linked, the
channel's member ACL is mirrored into the group as invite/leave events so the
thread sees a live view of "who can talk in this channel", and the channel's
messages are persisted to a ``RoomMessageStore`` keyed by the channel id.

Mapping rules (``sync_channel_members_to_group``):
  - channel members not yet in the group  → ``invite`` event
  - group members no longer in the channel → ``leave`` event
  - role: ``owner``/``admin``/``member`` → ``participant`` (can act)
          ``viewer``/unknown            → ``observer`` (read-only)

ContextGrant per role (the privacy seam — what slice of thread history a
newly-invited member may see):
  - ``owner``/``admin`` → ``scope=all``       (full history)
  - ``member``          → ``scope=from_join``  (from join onward)
  - ``viewer``/unknown  → ``scope=summary``    (summary only, fail-safe)

Both helpers are pure orchestrators over the existing stores; no new storage
engine is introduced.
"""

from __future__ import annotations

from runtime.memory.cowork.group import ContextGrant, MemberEvent, MemberRole
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.ids import require_cowork_id
from runtime.memory.cowork.room_messages import RoomMessageStore
from runtime.workspace.org_store import OrgStore

__all__ = [
    "channel_history",
    "grant_for_channel_role",
    "link_channel_to_group",
    "map_channel_role",
    "send_channel_message",
    "sync_channel_members_to_group",
]

# Channel roles that map to a group ``participant`` (can act on the thread).
# Everything else (``viewer``, unknown) maps to ``observer``.
_PARTICIPANT_ROLES = frozenset({"owner", "admin", "member"})


def map_channel_role(channel_role: str) -> MemberRole:
    """Translate a Channel member role into a cowork group role.

    ``owner``/``admin``/``member`` → ``participant`` (can act on the thread);
    ``viewer`` → ``observer`` (read-only). Any unknown role defaults to
    ``observer`` so an accidental misconfiguration never hands out write access.
    """
    return "participant" if channel_role in _PARTICIPANT_ROLES else "observer"


def grant_for_channel_role(channel_role: str) -> ContextGrant:
    """Translate a Channel member role into a ContextGrant.

    The grant decides what slice of the thread's history a newly-invited member
    is allowed to see — see ``runtime.memory.cowork.group.visible_message_range``.

      - ``owner`` / ``admin`` → ``scope=all``        (full history)
      - ``member``            → ``scope=from_join``   (from join onward)
      - ``viewer`` / unknown  → ``scope=summary``     (no raw history, fail-safe)
    """
    if channel_role in ("owner", "admin"):
        return ContextGrant(scope="all")
    if channel_role == "member":
        return ContextGrant(scope="from_join")
    return ContextGrant(scope="summary")


def sync_channel_members_to_group(
    org_store: OrgStore,
    group_store: GroupStore,
    channel_id: str,
    thread_id: str,
    *,
    actor: str = "",
) -> None:
    """Mirror the channel's member ACL into the cowork group.

    New members (in the channel, not in the group) get an ``invite`` event;
    members that left the channel get a ``leave`` event. Role changes for
    existing members are *not* replayed here — callers that need a role refresh
    should leave + re-invite explicitly. The actor id on every emitted event is
    the ``actor`` argument (defaults to ``"channel:<channel_id>"``).

    ``ChannelMember`` has no ``kind`` field, so each member's kind is resolved
    by looking it up in the channel's org (``OrgMember``); members not found in
    the org default to ``"agent"``.
    """
    channel = org_store.get_channel(channel_id)
    if channel is None:
        raise ValueError(f"channel {channel_id!r} does not exist")

    channel_members = org_store.list_channel_members(channel_id)
    state = group_store.state(thread_id)

    # Resolve each channel member's kind (human/agent) from the org roster.
    org_kinds = {m.member_id: m.kind for m in org_store.list_org_members(channel.org_id)}

    channel_member_ids = {cm.member_id for cm in channel_members}
    group_member_ids = {m.id for m in state.roster}

    attribution = actor or f"channel:{channel_id}"

    # Invite channel members that aren't yet in the group.
    for cm in channel_members:
        if cm.member_id in group_member_ids:
            continue
        group_store.append(
            thread_id,
            MemberEvent(
                action="invite",
                actor=attribution,
                target_id=cm.member_id,
                target_kind=org_kinds.get(cm.member_id, "agent"),
                role=map_channel_role(cm.role),
                grant=grant_for_channel_role(cm.role),
            ),
        )

    # Remove group members that are no longer in the channel.
    for member_id in group_member_ids - channel_member_ids:
        group_store.append(
            thread_id,
            MemberEvent(
                action="leave",
                actor=attribution,
                target_id=member_id,
            ),
        )


def link_channel_to_group(
    org_store: OrgStore,
    group_store: GroupStore,
    channel_id: str,
    thread_id: str,
    *,
    actor: str = "system",
) -> None:
    """Bind ``channel_id`` to the cowork thread ``thread_id``.

    Sends a ``room_link`` MemberEvent carrying the channel's id, then syncs the
    channel's member ACL into the group (see ``sync_channel_members_to_group``).
    Re-calling on an already-linked thread re-stamps the link (the latest
    ``room_link`` wins in ``fold_state``) and re-runs the membership diff — both
    are idempotent. Raises ``ValueError`` if the channel does not exist.
    """
    require_cowork_id(thread_id, label="thread_id")
    channel = org_store.get_channel(channel_id)
    if channel is None:
        raise ValueError(f"channel {channel_id!r} does not exist")

    group_store.append(
        thread_id,
        MemberEvent(
            action="room_link",
            actor=actor,
            target_id=channel_id,
        ),
    )

    sync_channel_members_to_group(
        org_store,
        group_store,
        channel_id,
        thread_id,
        actor=f"channel:{channel_id}",
    )


def send_channel_message(
    room_message_store: RoomMessageStore,
    channel_id: str,
    *,
    text: str,
    participant_id: str = "",
    display_name: str = "",
) -> int:
    """Append a message to the channel's durable log, returning its seq.

    ``channel_id`` is used directly as the room id (it is a uuid4, which is a
    valid cowork slug). Validation of ``channel_id``/``text`` is delegated to the
    store's ``require_cowork_id`` / ``require_message_text``.
    """
    require_cowork_id(channel_id, label="channel_id")
    return room_message_store.append(
        channel_id,
        text=text,
        participant_id=participant_id,
        display_name=display_name,
    )


def channel_history(
    room_message_store: RoomMessageStore,
    channel_id: str,
    *,
    limit: int = 200,
    after_seq: int = 0,
) -> list[dict]:
    """The channel's message log (newest-``limit`` by default), in order.

    Only messages with ``seq > after_seq`` are returned, which supports reconnect
    catch-up. A thin wrapper around ``RoomMessageStore.history``.
    """
    return room_message_store.history(channel_id, limit=limit, after_seq=after_seq)
