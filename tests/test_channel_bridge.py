"""Tests for the Channel ↔ Cowork Group bridge (频道即群聊).

Covers:
  1. Role → grant / role mapping for all four channel roles.
  2. ``link_channel_to_group`` sends a ``room_link`` event and mirrors the
     channel's ACL into the group roster (reconstructable via ``fold_state``).
  3. ``sync_channel_members_to_group`` is idempotent.
  4. A departed channel member is removed on re-sync (``leave`` event).
  5. ``send_channel_message`` / ``channel_history`` round-trip with correct
     participant / display_name / text.
  6. Message persistence survives a store restart (same tmp_path base_dir).
  7. ``link_channel_to_group`` raises for a non-existent channel.

Each test uses a fresh tmp_path so the SQLite stores are isolated.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.cowork.group import fold_state
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.room_messages import RoomMessageStore
from runtime.workspace.channel_bridge import (
    channel_history,
    grant_for_channel_role,
    link_channel_to_group,
    map_channel_role,
    send_channel_message,
    sync_channel_members_to_group,
)
from runtime.workspace.org_store import OrgStore

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def org_store(tmp_path: Path) -> OrgStore:
    return OrgStore(db_path=tmp_path / "org.db")


@pytest.fixture
def group_store(tmp_path: Path) -> GroupStore:
    return GroupStore(base_dir=tmp_path / "cowork")


@pytest.fixture
def room_store(tmp_path: Path) -> RoomMessageStore:
    return RoomMessageStore(base_dir=tmp_path / "teamroom")


def _make_org(store: OrgStore, *, name: str = "Acme", owner_id: str = "owner-1"):
    return store.create_organization(name=name, owner_id=owner_id, organization_id="org-test")


def _make_channel(
    store: OrgStore,
    *,
    org_id: str = "org-test",
    name: str = "Launch Hall",
    channel_id: str | None = None,
):
    return store.create_channel(
        org_id=org_id,
        name=name,
        channel_id=channel_id or "ch-test",
    )


def _add_org_member(
    store: OrgStore,
    member_id: str,
    *,
    org_id: str = "org-test",
    kind: str = "human",
    role: str = "member",
):
    store.add_org_member(org_id, member_id, kind=kind, role=role)


# ─── 1. Role mapping ───────────────────────────────────────────────────────


def test_grant_for_channel_role_covers_all_roles() -> None:
    """The grant table is the contract every consumer depends on — pin it."""
    assert grant_for_channel_role("owner").scope == "all"
    assert grant_for_channel_role("admin").scope == "all"
    assert grant_for_channel_role("member").scope == "from_join"
    assert grant_for_channel_role("viewer").scope == "summary"
    # Unknown roles fail-safe to summary (never leak full history).
    assert grant_for_channel_role("intern").scope == "summary"
    assert grant_for_channel_role("").scope == "summary"


def test_map_channel_role_covers_all_roles() -> None:
    """The role translation table is the contract every consumer depends on."""
    assert map_channel_role("owner") == "participant"
    assert map_channel_role("admin") == "participant"
    assert map_channel_role("member") == "participant"
    assert map_channel_role("viewer") == "observer"
    # Unknown roles default to observer (fail-safe, never grant write access).
    assert map_channel_role("intern") == "observer"
    assert map_channel_role("") == "observer"


# ─── 2. link_channel_to_group ──────────────────────────────────────────────


def test_link_channel_to_group_mirrors_members(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """link_channel_to_group appends a room_link event AND mirrors the channel's
    ACL into the group roster in one call."""
    _make_org(org_store)
    _add_org_member(org_store, "alice", kind="human", role="member")
    _add_org_member(org_store, "bob", kind="human", role="viewer")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="owner")
    org_store.add_channel_member(ch.id, "bob", role="viewer")

    link_channel_to_group(org_store, group_store, ch.id, "t1")

    state = group_store.state("t1")
    # The room_link event is folded into state.
    assert state.room_id == ch.id
    # ACL members are mirrored into the group.
    assert {m.id for m in state.roster} == {"alice", "bob"}


def test_link_channel_to_group_records_room_link_event_first(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """The first event emitted by link_channel_to_group is the room_link event
    (the membership sync comes after)."""
    _make_org(org_store)
    _add_org_member(org_store, "alice")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="member")

    link_channel_to_group(org_store, group_store, ch.id, "t1")

    events = group_store.events("t1")
    assert events[0].action == "room_link"
    assert events[0].target_id == ch.id


def test_link_channel_to_group_roster_rebuilds_via_fold_state(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """The group roster can be reconstructed from the raw event log alone."""
    _make_org(org_store)
    _add_org_member(org_store, "alice", kind="human")
    _add_org_member(org_store, "bob", kind="agent")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="owner")
    org_store.add_channel_member(ch.id, "bob", role="member")

    link_channel_to_group(org_store, group_store, ch.id, "t1")

    rebuilt = fold_state(group_store.events("t1"))
    assert {m.id for m in rebuilt.roster} == {"alice", "bob"}
    # Member kind is resolved from the org (human for alice, agent for bob).
    alice = rebuilt.member("alice")
    assert alice is not None and alice.kind == "human"
    assert alice.role == "participant"
    assert alice.grant.scope == "all"
    bob = rebuilt.member("bob")
    assert bob is not None and bob.kind == "agent"
    assert bob.grant.scope == "from_join"


def test_link_channel_to_group_unknown_channel_raises(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """Linking a non-existent channel is a clear programming error, not a
    silent no-op."""
    with pytest.raises(ValueError, match="channel"):
        link_channel_to_group(org_store, group_store, "nope", "t1")


# ─── 3. sync idempotency ───────────────────────────────────────────────────


def test_sync_is_idempotent_when_roster_unchanged(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """Re-syncing without any ACL change emits no new events — important so
    periodic syncs don't bloat the event log."""
    _make_org(org_store)
    _add_org_member(org_store, "alice")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="member")
    link_channel_to_group(org_store, group_store, ch.id, "t1")
    events_before = len(group_store.events("t1"))

    sync_channel_members_to_group(org_store, group_store, ch.id, "t1")
    sync_channel_members_to_group(org_store, group_store, ch.id, "t1")

    assert len(group_store.events("t1")) == events_before


def test_sync_adds_new_channel_member_to_group(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """Adding a member to the channel and re-syncing invites them in."""
    _make_org(org_store)
    _add_org_member(org_store, "alice")
    _add_org_member(org_store, "carol")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="member")
    link_channel_to_group(org_store, group_store, ch.id, "t1")
    assert {m.id for m in group_store.state("t1").roster} == {"alice"}

    org_store.add_channel_member(ch.id, "carol", role="viewer")
    sync_channel_members_to_group(org_store, group_store, ch.id, "t1")

    state = group_store.state("t1")
    assert {m.id for m in state.roster} == {"alice", "carol"}
    carol = state.member("carol")
    assert carol is not None and carol.role == "observer"


def test_sync_removes_departed_channel_member_from_group(
    org_store: OrgStore, group_store: GroupStore
) -> None:
    """Removing a member from the channel and re-syncing emits a leave event."""
    _make_org(org_store)
    _add_org_member(org_store, "alice")
    _add_org_member(org_store, "bob")
    ch = _make_channel(org_store)
    org_store.add_channel_member(ch.id, "alice", role="member")
    org_store.add_channel_member(ch.id, "bob", role="viewer")
    link_channel_to_group(org_store, group_store, ch.id, "t1")
    assert {m.id for m in group_store.state("t1").roster} == {"alice", "bob"}

    org_store.remove_channel_member(ch.id, "bob")
    sync_channel_members_to_group(org_store, group_store, ch.id, "t1")

    state = group_store.state("t1")
    assert {m.id for m in state.roster} == {"alice"}
    assert state.member("bob") is None


# ─── 4. send / read channel messages ───────────────────────────────────────


def test_send_and_read_channel_message(org_store: OrgStore, room_store: RoomMessageStore) -> None:
    """send_channel_message appends a message that channel_history reads back
    with the correct participant / display_name / text."""
    _make_org(org_store)
    ch = _make_channel(org_store)

    seq = send_channel_message(
        room_store,
        ch.id,
        text="hello channel",
        participant_id="alice",
        display_name="Alice",
    )
    assert seq == 1

    history = channel_history(room_store, ch.id)
    assert len(history) == 1
    msg = history[0]
    assert msg["seq"] == 1
    assert msg["participant_id"] == "alice"
    assert msg["display_name"] == "Alice"
    assert msg["text"] == "hello channel"


def test_channel_messages_persist_across_restart(org_store: OrgStore, tmp_path: Path) -> None:
    """Reconstructing the RoomMessageStore on the same base_dir recovers the
    channel's messages (simulates a restart)."""
    _make_org(org_store)
    ch = _make_channel(org_store)

    store = RoomMessageStore(base_dir=tmp_path / "teamroom")
    send_channel_message(store, ch.id, text="first", participant_id="alice")
    send_channel_message(store, ch.id, text="second", participant_id="bob")

    # New instance over the same base_dir → data survives.
    reopened = RoomMessageStore(base_dir=tmp_path / "teamroom")
    history = channel_history(reopened, ch.id)
    assert [m["text"] for m in history] == ["first", "second"]
    assert [m["participant_id"] for m in history] == ["alice", "bob"]


def test_channel_history_after_seq_catch_up(
    org_store: OrgStore, room_store: RoomMessageStore
) -> None:
    """after_seq returns only messages newer than that seq (reconnect catch-up)."""
    _make_org(org_store)
    ch = _make_channel(org_store)
    send_channel_message(room_store, ch.id, text="first")
    send_channel_message(room_store, ch.id, text="second")

    history = channel_history(room_store, ch.id, after_seq=1)
    assert [m["seq"] for m in history] == [2]

