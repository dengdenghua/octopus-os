"""Tests for the Workspace ↔ Cowork Group binding.

Covers:
  1. ``workspace_link`` MemberEvent can be appended and folded into GroupState.
  2. ``link_workspace_to_group`` sends the link event and syncs members.
  3. Re-syncing after workspace member changes adds/removes group members.
  4. Role mapping: owner/editor → participant, reviewer/viewer → observer.
  5. ``resolve_session`` surfaces the linked workspace on the unified session.

Each test uses a fresh tmp_path so the SQLite stores are isolated. The crypto
cache reset mirrors ``test_workspace_store.py`` so per-test machine-id/key
state never leaks across tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.cowork.group import MemberEvent, fold_state
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.session import resolve_session
from runtime.workspace import WorkspaceStore
from runtime.workspace import crypto as crypto_mod
from runtime.workspace.cowork_bridge import (
    link_workspace_to_group,
    map_workspace_role,
    sync_workspace_members_to_group,
)

# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_crypto_cache() -> None:
    """Reset the workspace crypto module cache before/after each test so
    per-test env-var changes take effect cleanly and don't leak.
    """
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None
    yield
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(db_path=tmp_path / "workspaces.db")


@pytest.fixture
def group_store(tmp_path: Path) -> GroupStore:
    return GroupStore(base_dir=tmp_path / "cowork")


def _make_workspace(
    store: WorkspaceStore,
    *,
    name: str = "Team Files",
    mount_type: str = "local",
    owner_id: str = "owner-1",
    workspace_id: str | None = None,
):
    """Thin helper that creates a workspace with a known id (so tests can
    reference it without waiting on the uuid)."""
    return store.create_workspace(
        name=name,
        mount_type=mount_type,
        mount_target="/tmp/ws",
        mount_options={},
        owner_id=owner_id,
        workspace_id=workspace_id or "ws-test",
    )


# ─── 1. workspace_link event fold ──────────────────────────────────────────


def test_workspace_link_event_round_trips_through_to_dict() -> None:
    """MemberEvent.to_dict / from_dict must preserve the workspace payload."""
    ev = MemberEvent(
        action="workspace_link",
        actor="system",
        target_id="ws-1",
        workspace={"id": "ws-1", "name": "Team Files", "mount_type": "smb"},
    )
    rt = MemberEvent.from_dict(ev.to_dict())
    assert rt.action == "workspace_link"
    assert rt.target_id == "ws-1"
    assert rt.workspace == {"id": "ws-1", "name": "Team Files", "mount_type": "smb"}


def test_workspace_link_event_folds_into_group_state(group_store: GroupStore) -> None:
    """A workspace_link event attaches the workspace info to GroupState."""
    group_store.append(
        "t1",
        MemberEvent(
            action="workspace_link",
            actor="system",
            target_id="ws-1",
            workspace={"id": "ws-1", "name": "Team Files", "mount_type": "smb"},
        ),
    )
    state = group_store.state("t1")
    assert state.workspace == {"id": "ws-1", "name": "Team Files", "mount_type": "smb"}


def test_workspace_link_latest_event_wins(group_store: GroupStore) -> None:
    """A later workspace_link event overrides an earlier one — same rule as
    room_link, so re-linking to a different workspace stays consistent."""
    group_store.append(
        "t1",
        MemberEvent(
            action="workspace_link",
            actor="system",
            target_id="ws-old",
            workspace={"id": "ws-old", "name": "Old", "mount_type": "local"},
        ),
    )
    group_store.append(
        "t1",
        MemberEvent(
            action="workspace_link",
            actor="system",
            target_id="ws-new",
            workspace={"id": "ws-new", "name": "New", "mount_type": "smb"},
        ),
    )
    state = group_store.state("t1")
    assert state.workspace == {"id": "ws-new", "name": "New", "mount_type": "smb"}


def test_workspace_link_event_survives_replay_until_seq(group_store: GroupStore) -> None:
    """``until_seq`` replay excludes workspace links past that point — the
    event-sourced nature means "what was linked at message N" is queryable."""
    group_store.append(
        "t1",
        MemberEvent(
            action="workspace_link",
            actor="system",
            target_id="ws-1",
            workspace={"id": "ws-1", "name": "Team", "mount_type": "local"},
        ),
    )
    # Second event we can pin a seq on.
    group_store.append(
        "t1",
        MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent"),
    )
    first_seq = group_store.events("t1")[0].seq
    # Replay before the workspace_link → no workspace.
    assert group_store.state("t1", until_seq=first_seq - 1).workspace is None
    # Replay through the workspace_link → workspace present.
    assert group_store.state("t1", until_seq=first_seq).workspace is not None


def test_fold_state_workspace_is_none_without_link_event() -> None:
    """No workspace_link events → GroupState.workspace stays None."""
    events = [
        MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent"),
        MemberEvent(action="mode", actor="u", mode="swarm"),
    ]
    assert fold_state(events).workspace is None


# ─── 2. link_workspace_to_group ────────────────────────────────────────────


def test_link_workspace_to_group_sends_event_and_syncs_members(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """link_workspace_to_group appends a workspace_link event AND mirrors the
    workspace's members into the group roster in one call."""
    ws = _make_workspace(workspace_store, name="Team Files", mount_type="smb")
    workspace_store.add_member(ws.id, "alice", role="editor")
    workspace_store.add_member(ws.id, "bob", role="viewer")

    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    # The link event is folded into state.
    assert state.workspace == {"id": ws.id, "name": "Team Files", "mount_type": "smb"}
    # The workspace members are mirrored into the group (owner-1 is auto-added
    # by create_workspace).
    assert {m.id for m in state.roster} == {"owner-1", "alice", "bob"}


def test_link_workspace_to_group_unknown_workspace_raises(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Linking a non-existent workspace is a clear programming error, not a
    silent no-op."""
    with pytest.raises(ValueError, match="workspace"):
        link_workspace_to_group(workspace_store, group_store, "nope", "t1")


def test_link_workspace_to_group_records_workspace_link_event(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """The first event emitted by link_workspace_to_group is the
    workspace_link event (the membership sync comes after)."""
    ws = _make_workspace(workspace_store)
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")
    events = group_store.events("t1")
    assert events[0].action == "workspace_link"
    assert events[0].target_id == ws.id
    assert events[0].workspace == {
        "id": ws.id,
        "name": ws.name,
        "mount_type": ws.mount_type,
    }


# ─── 3. Re-sync reflects workspace member changes ──────────────────────────


def test_sync_adds_new_workspace_members_to_group(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Adding a member to the workspace and re-syncing invites them into the
    group without disturbing the existing roster."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "alice", role="editor")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")
    assert {m.id for m in group_store.state("t1").roster} == {"owner-1", "alice"}

    # Carol joins the workspace → re-sync should invite her.
    workspace_store.add_member(ws.id, "carol", role="editor")
    sync_workspace_members_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    assert {m.id for m in state.roster} == {"owner-1", "alice", "carol"}


def test_sync_removes_departed_workspace_members_from_group(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Removing a member from the workspace and re-syncing emits a leave event
    so the group roster tracks the workspace."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "alice", role="editor")
    workspace_store.add_member(ws.id, "bob", role="viewer")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")
    assert {m.id for m in group_store.state("t1").roster} == {
        "owner-1",
        "alice",
        "bob",
    }

    # Bob leaves the workspace → re-sync should drop him from the group.
    workspace_store.remove_member(ws.id, "bob")
    sync_workspace_members_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    assert {m.id for m in state.roster} == {"owner-1", "alice"}


def test_sync_is_idempotent_when_roster_unchanged(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Re-syncing without any roster change emits no new events — important
    so periodic syncs don't bloat the event log."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "alice", role="editor")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")
    events_before = len(group_store.events("t1"))

    sync_workspace_members_to_group(workspace_store, group_store, ws.id, "t1")
    sync_workspace_members_to_group(workspace_store, group_store, ws.id, "t1")

    assert len(group_store.events("t1")) == events_before


# ─── 4. Role mapping ───────────────────────────────────────────────────────


def test_map_workspace_role_covers_all_four_roles() -> None:
    """The role translation table is the contract every consumer depends on
    — pin it down explicitly."""
    assert map_workspace_role("owner") == "participant"
    assert map_workspace_role("editor") == "participant"
    assert map_workspace_role("reviewer") == "observer"
    assert map_workspace_role("viewer") == "observer"
    # Unknown roles default to observer (fail-safe, never grant write access).
    assert map_workspace_role("intern") == "observer"
    assert map_workspace_role("") == "observer"


def test_role_mapping_owner_becomes_participant(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Workspace owner is mirrored as a participant (can act in the group)."""
    ws = _make_workspace(workspace_store, owner_id="owner-1")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    owner = state.member("owner-1")
    assert owner is not None
    assert owner.role == "participant"


def test_role_mapping_viewer_becomes_observer(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Workspace viewer is mirrored as an observer (read-only in the group)."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "watcher", role="viewer")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    watcher = state.member("watcher")
    assert watcher is not None
    assert watcher.role == "observer"


def test_role_mapping_reviewer_becomes_observer(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Workspace reviewer is also an observer in the group."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "rev", role="reviewer")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    rev = state.member("rev")
    assert rev is not None
    assert rev.role == "observer"


def test_role_mapping_editor_becomes_participant(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """Workspace editor is a participant (write access preserved)."""
    ws = _make_workspace(workspace_store)
    workspace_store.add_member(ws.id, "ed", role="editor")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    ed = state.member("ed")
    assert ed is not None
    assert ed.role == "participant"


# ─── 5. resolve_session surfaces the linked workspace ──────────────────────


def test_resolve_session_includes_linked_workspace(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """The unified CollaborationSession carries the linked workspace so API
    consumers read one object instead of stitching two stores."""
    ws = _make_workspace(workspace_store, name="Team Files", mount_type="smb")
    workspace_store.add_member(ws.id, "alice", role="editor")
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")

    session = resolve_session(group_store, "t1")
    assert session.workspace == {"id": ws.id, "name": "Team Files", "mount_type": "smb"}
    # The roster is mirrored into the session too.
    assert {m["id"] for m in session.roster} == {"owner-1", "alice"}


def test_resolve_session_workspace_is_none_when_unlinked(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """A thread with no workspace_link event has workspace=None on the
    session — never an empty dict, so callers can ``if session.workspace``."""
    group_store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    session = resolve_session(group_store, "t1")
    assert session.workspace is None


def test_resolve_session_to_dict_serializes_workspace(
    workspace_store: WorkspaceStore, group_store: GroupStore
) -> None:
    """to_dict() on the session preserves the workspace field for HTTP/JSON."""
    ws = _make_workspace(workspace_store)
    link_workspace_to_group(workspace_store, group_store, ws.id, "t1")
    payload = resolve_session(group_store, "t1").to_dict()
    assert payload["workspace"] == {
        "id": ws.id,
        "name": ws.name,
        "mount_type": ws.mount_type,
    }

