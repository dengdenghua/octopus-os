"""Tests for runtime.workspace.org + org_store (阶段一 企业组织树).

Covers:
  1. Organization CRUD + auto-add owner as member
  2. Org members (unified Human + Agent)
  3. Department tree (nested, org-scoped parent validation)
  4. Channel CRUD + org-scoped department membership
  5. Channel ACL (add/remove/upsert, org-member requirement, access check)
  6. Access-filtered channel listing (非成员不可见频道内容)
  7. Cascade deletes (org → departments/channels/members; channel → ACL)
  8. Model dataclass round-trips

Uses a tmp-path SQLite DB for isolation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.workspace import (
    Channel,
    ChannelMember,
    Department,
    Organization,
    OrgMember,
    OrgStore,
    role_has_channel_admin,
    role_has_org_admin,
)


@pytest.fixture
def store(tmp_path: Path) -> OrgStore:
    return OrgStore(db_path=tmp_path / "org.db")


# ─── 1. Organization CRUD ──────────────────────────────────────────────────


def test_create_and_get_organization(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="user-1")
    assert org.id
    assert org.name == "Acme"
    assert org.owner_id == "user-1"
    assert org.created_at > 0

    fetched = store.get_organization(org.id)
    assert fetched is not None
    assert fetched.id == org.id
    assert fetched.name == "Acme"
    assert fetched.owner_id == "user-1"


def test_get_organization_returns_none_for_unknown(store: OrgStore) -> None:
    assert store.get_organization("nope") is None


def test_create_organization_auto_adds_owner_as_member(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="user-1")
    members = store.list_org_members(org.id)
    assert len(members) == 1
    assert members[0].member_id == "user-1"
    assert members[0].role == "owner"
    assert store.list_organizations_for_user("user-1") == [org]


def test_list_organizations_orders_by_created_at(store: OrgStore) -> None:
    a = store.create_organization(name="A", owner_id="u1", created_at=1.0)
    b = store.create_organization(name="B", owner_id="u1", created_at=2.0)
    assert [o.id for o in store.list_organizations()] == [a.id, b.id]


def test_list_organizations_for_user_returns_only_member_orgs(
    store: OrgStore,
) -> None:
    o1 = store.create_organization(name="o1", owner_id="u1")
    o2 = store.create_organization(name="o2", owner_id="u2")
    store.add_org_member(o2.id, "u1", role="viewer")
    assert {o.id for o in store.list_organizations_for_user("u1")} == {o1.id, o2.id}
    assert store.list_organizations_for_user("u3") == []
    assert store.list_organizations_for_user("") == []


def test_delete_organization_cascades(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    dept = store.create_department(org_id=org.id, name="Eng")
    ch = store.create_channel(org_id=org.id, name="general", department_id=dept.id)
    store.add_channel_member(ch.id, "u1", role="owner")
    store.add_org_member(org.id, "agent-1", kind="agent", role="member")

    assert store.delete_organization(org.id) is True
    assert store.delete_organization(org.id) is False  # idempotent
    assert store.get_organization(org.id) is None
    assert store.get_department(dept.id) is None
    assert store.get_channel(ch.id) is None
    assert store.list_org_members(org.id) == []
    assert store.list_channels_for_user("u1") == []


def test_create_organization_rejects_empty_name(store: OrgStore) -> None:
    with pytest.raises(ValueError, match="name"):
        store.create_organization(name=" ", owner_id="u1")


def test_create_organization_rejects_empty_owner(store: OrgStore) -> None:
    with pytest.raises(ValueError, match="owner_id"):
        store.create_organization(name="Acme", owner_id="")


# ─── 2. Org members (unified Human + Agent) ────────────────────────────────


def test_add_org_member_human_and_agent(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(
        org.id, "agent-1", kind="agent", role="member", display_name="Code Reviewer"
    )
    store.add_org_member(org.id, "u2", kind="human", role="admin")

    members = store.list_org_members(org.id)
    by_id = {m.member_id: m for m in members}
    assert by_id["agent-1"].kind == "agent"
    assert by_id["agent-1"].display_name == "Code Reviewer"
    assert by_id["u2"].kind == "human"
    assert by_id["u2"].role == "admin"


def test_add_org_member_upserts_role(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="viewer")
    assert store.get_org_member_role(org.id, "u2") == "viewer"
    store.add_org_member(org.id, "u2", kind="human", role="member")
    assert store.get_org_member_role(org.id, "u2") == "member"
    rows = [m for m in store.list_org_members(org.id) if m.member_id == "u2"]
    assert len(rows) == 1


def test_remove_org_member(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="viewer")
    assert store.remove_org_member(org.id, "u2") is True
    assert store.remove_org_member(org.id, "u2") is False
    assert store.get_org_member_role(org.id, "u2") is None


def test_add_org_member_rejects_invalid_kind(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    with pytest.raises(ValueError, match="kind"):
        store.add_org_member(org.id, "x", kind="robot", role="member")


def test_add_org_member_rejects_invalid_role(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    with pytest.raises(ValueError, match="role"):
        store.add_org_member(org.id, "x", kind="human", role="superuser")


def test_add_org_member_rejects_unknown_org(store: OrgStore) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        store.add_org_member("ghost", "u1", kind="human", role="viewer")


# ─── 3. Department tree ────────────────────────────────────────────────────


def test_create_department_nested(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    eng = store.create_department(org_id=org.id, name="Eng")
    backend = store.create_department(org_id=org.id, name="Backend", parent_id=eng.id)

    assert eng.parent_id is None
    assert backend.parent_id == eng.id
    assert {d.id for d in store.list_departments(org.id)} == {eng.id, backend.id}


def test_create_department_rejects_parent_from_other_org(store: OrgStore) -> None:
    o1 = store.create_organization(name="o1", owner_id="u1")
    o2 = store.create_organization(name="o2", owner_id="u2")
    parent = store.create_department(org_id=o1.id, name="Eng")
    with pytest.raises(ValueError, match="not in org"):
        store.create_department(org_id=o2.id, name="X", parent_id=parent.id)


def test_delete_department_removes_attached_channels(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    dept = store.create_department(org_id=org.id, name="Eng")
    ch = store.create_channel(org_id=org.id, name="dev", department_id=dept.id)
    store.add_channel_member(ch.id, "u1", role="owner")

    assert store.delete_department(dept.id) is True
    assert store.get_department(dept.id) is None
    assert store.get_channel(ch.id) is None


def test_delete_department_returns_false_when_missing(store: OrgStore) -> None:
    assert store.delete_department("ghost") is False


# ─── 4. Channel CRUD ───────────────────────────────────────────────────────


def test_create_and_get_channel(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    ch = store.create_channel(org_id=org.id, name="general")
    assert ch.org_id == org.id
    assert ch.kind == "channel"
    assert ch.department_id is None

    fetched = store.get_channel(ch.id)
    assert fetched is not None
    assert fetched.id == ch.id
    assert fetched.name == "general"


def test_create_channel_group_kind(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    ch = store.create_channel(org_id=org.id, name="squad", kind="group")
    assert ch.kind == "group"


def test_create_channel_rejects_invalid_kind(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    with pytest.raises(ValueError, match="kind"):
        store.create_channel(org_id=org.id, name="x", kind="room")


def test_create_channel_rejects_department_from_other_org(store: OrgStore) -> None:
    o1 = store.create_organization(name="o1", owner_id="u1")
    o2 = store.create_organization(name="o2", owner_id="u2")
    dept = store.create_department(org_id=o1.id, name="Eng")
    with pytest.raises(ValueError, match="not in org"):
        store.create_channel(org_id=o2.id, name="x", department_id=dept.id)


def test_delete_channel_removes_acl(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    ch = store.create_channel(org_id=org.id, name="general")
    store.add_channel_member(ch.id, "u1", role="owner")
    assert store.delete_channel(ch.id) is True
    assert store.get_channel(ch.id) is None
    assert store.list_channel_members(ch.id) == []


# ─── 5. Channel ACL ────────────────────────────────────────────────────────


def test_add_channel_member_requires_org_membership(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    ch = store.create_channel(org_id=org.id, name="general")
    # u2 is not an org member → rejected.
    with pytest.raises(ValueError, match="not a member of org"):
        store.add_channel_member(ch.id, "u2", role="member")


def test_add_channel_member_for_org_member(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    ch = store.create_channel(org_id=org.id, name="general")
    m = store.add_channel_member(ch.id, "u2", role="member")
    assert isinstance(m, ChannelMember)
    assert m.channel_id == ch.id
    assert m.member_id == "u2"
    assert store.get_channel_member_role(ch.id, "u2") == "member"


def test_add_channel_member_upserts_role(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    ch = store.create_channel(org_id=org.id, name="general")
    store.add_channel_member(ch.id, "u2", role="viewer")
    store.add_channel_member(ch.id, "u2", role="admin")
    assert store.get_channel_member_role(ch.id, "u2") == "admin"
    rows = store.list_channel_members(ch.id)
    assert len([r for r in rows if r.member_id == "u2"]) == 1


def test_remove_channel_member(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    ch = store.create_channel(org_id=org.id, name="general")
    store.add_channel_member(ch.id, "u2", role="member")
    assert store.remove_channel_member(ch.id, "u2") is True
    assert store.remove_channel_member(ch.id, "u2") is False
    assert store.get_channel_member_role(ch.id, "u2") is None


def test_add_channel_member_rejects_invalid_role(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    ch = store.create_channel(org_id=org.id, name="general")
    with pytest.raises(ValueError, match="role"):
        store.add_channel_member(ch.id, "u1", role="superuser")


def test_add_channel_member_rejects_unknown_channel(store: OrgStore) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        store.add_channel_member("ghost", "u1", role="member")


# ─── 6. Access check + filtered listing ────────────────────────────────────


def test_can_access_channel_requires_acl(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    ch = store.create_channel(org_id=org.id, name="general")
    # Owner u1 is auto org-admin → can access (admin override).
    assert store.can_access_channel(ch.id, "u1") is True
    # u2 is an org member but NOT in the channel ACL → cannot access.
    assert store.can_access_channel(ch.id, "u2") is False
    # A stranger can't access.
    assert store.can_access_channel(ch.id, "stranger") is False


def test_can_access_channel_after_grant(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    ch = store.create_channel(org_id=org.id, name="general")
    assert store.can_access_channel(ch.id, "u2") is False
    store.add_channel_member(ch.id, "u2", role="member")
    assert store.can_access_channel(ch.id, "u2") is True


def test_can_access_channel_returns_false_for_unknown(store: OrgStore) -> None:
    assert store.can_access_channel("ghost", "u1") is False
    assert store.can_access_channel("", "u1") is False
    assert store.can_access_channel("ch", "") is False


def test_list_channels_for_user_filters_by_acl(store: OrgStore) -> None:
    org = store.create_organization(name="Acme", owner_id="u1")
    store.add_org_member(org.id, "u2", kind="human", role="member")
    public = store.create_channel(org_id=org.id, name="public")
    private = store.create_channel(org_id=org.id, name="private")
    store.add_channel_member(public.id, "u2", role="member")

    # u2 sees only the channel they're in.
    assert {c.id for c in store.list_channels_for_user("u2")} == {public.id}
    # u1 (org admin) sees all channels in the org.
    assert {c.id for c in store.list_channels_for_user("u1")} == {public.id, private.id}
    # A stranger sees nothing.
    assert store.list_channels_for_user("stranger") == []


def test_list_channels_for_user_empty_for_empty_id(store: OrgStore) -> None:
    assert store.list_channels_for_user("") == []


# ─── 7. Role helpers ───────────────────────────────────────────────────────


def test_role_helpers() -> None:
    assert role_has_org_admin("owner") is True
    assert role_has_org_admin("admin") is True
    assert role_has_org_admin("member") is False
    assert role_has_org_admin("viewer") is False
    assert role_has_channel_admin("owner") is True
    assert role_has_channel_admin("admin") is True
    assert role_has_channel_admin("member") is False


# ─── 8. Model round-trips ──────────────────────────────────────────────────


def test_organization_model_round_trip() -> None:
    o = Organization(id="org1", name="Acme", owner_id="u1", created_at=1.0)
    assert Organization.from_dict(o.to_dict()) == o


def test_department_model_round_trip() -> None:
    d = Department(id="d1", org_id="org1", name="Eng", parent_id="root", created_at=1.0)
    assert Department.from_dict(d.to_dict()) == d
    root = Department(id="d2", org_id="org1", name="Root")
    assert Department.from_dict(root.to_dict()).parent_id is None


def test_org_member_model_round_trip() -> None:
    m = OrgMember(
        org_id="org1",
        member_id="agent-1",
        kind="agent",
        role="member",
        display_name="Bot",
        added_at=1.0,
    )
    assert OrgMember.from_dict(m.to_dict()) == m
    # Unknown role coerces to "member".
    assert OrgMember.from_dict({"org_id": "o", "member_id": "x", "role": "boss"}).role == "member"


def test_channel_model_round_trip() -> None:
    c = Channel(
        id="c1", org_id="org1", name="general", kind="group", department_id="d1", created_at=1.0
    )
    assert Channel.from_dict(c.to_dict()) == c
    # Unknown kind coerces to "channel".
    assert (
        Channel.from_dict({"id": "c", "org_id": "o", "name": "n", "kind": "room"}).kind == "channel"
    )


def test_channel_member_model_round_trip() -> None:
    m = ChannelMember(channel_id="c1", member_id="u1", role="admin", added_at=1.0)
    assert ChannelMember.from_dict(m.to_dict()) == m

