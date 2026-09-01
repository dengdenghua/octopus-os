"""Tests for ``runtime.sensing.gateway.org_router``.

Covers the 阶段一 org API surface:
  1. 创建组织 + owner 自动成为成员
  2. 创建/查询部门、频道
  3. 组织管理员可加成员/建频道,非管理员 403
  4. 频道 ACL:非成员访问频道 403、成员可访问
  5. ``GET /api/orgs/mine``、``GET /api/channels/mine`` 过滤
  6. 删组织/删频道级联
  7. 匿名(无身份)写操作 → 403

Uses a tmp-path SQLite DB + an ``IdentityStore`` (API-key bearer) for isolation.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.org_router import create_org_router
from runtime.workspace import OrgStore


def _identity_store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="key-alice")
    store.add(Identity(actor_id="bob"), api_key_plaintext="key-bob")
    return store


def _client(
    tmp_path: Path,
    *,
    identity_store: IdentityStore | None = None,
) -> TestClient:
    store = OrgStore(db_path=tmp_path / "org.db")
    app = FastAPI()
    app.include_router(create_org_router(org_store=store, identity_store=identity_store))
    return TestClient(app)


def _auth(name: str) -> dict[str, str]:
    return {"Authorization": f"Bearer key-{name}"}


def _create_org(client: TestClient, *, name: str = "Acme", owner: str = "alice") -> dict:
    r = client.post(
        "/api/orgs",
        json={"name": name, "owner_id": owner},
        headers=_auth(owner),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─── 1. 创建组织 + owner 自动成为成员 ───────────────────────────────────────


def test_create_org_and_owner_auto_member(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)

    assert org["name"] == "Acme"
    assert org["owner_id"] == "alice"
    assert org["id"]

    members = client.get(f"/api/orgs/{org['id']}/members", headers=_auth("alice")).json()["members"]
    assert len(members) == 1
    assert members[0]["member_id"] == "alice"
    assert members[0]["role"] == "owner"


def test_create_org_rejects_missing_name(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    r = client.post("/api/orgs", json={"name": "", "owner_id": "alice"}, headers=_auth("alice"))
    assert r.status_code == 400


# ─── 2. 创建/查询部门、频道 ─────────────────────────────────────────────────


def test_create_and_query_department_and_channel(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)

    dept = client.post(
        f"/api/orgs/{org['id']}/departments",
        json={"name": "Eng"},
        headers=_auth("alice"),
    ).json()
    assert dept["name"] == "Eng"
    assert dept["org_id"] == org["id"]

    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "general", "department_id": dept["id"]},
        headers=_auth("alice"),
    ).json()
    assert ch["name"] == "general"
    assert ch["department_id"] == dept["id"]

    depts = client.get(f"/api/orgs/{org['id']}/departments", headers=_auth("alice")).json()
    assert depts["count"] == 1
    assert depts["departments"][0]["id"] == dept["id"]

    channels = client.get(f"/api/orgs/{org['id']}/channels", headers=_auth("alice")).json()
    assert channels["count"] == 1
    assert channels["channels"][0]["id"] == ch["id"]

    assert client.get(f"/api/orgs/{org['id']}").json()["id"] == org["id"]


# ─── 3. 组织管理员可加成员/建频道,非管理员 403 ──────────────────────────────


def test_org_admin_can_add_member_and_build_channel(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)

    # alice (owner) adds bob as a member.
    r = client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "kind": "human", "role": "member"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    assert r.json()["member_id"] == "bob"

    # bob (member, not admin) is forbidden from adding a member.
    r2 = client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "carol", "role": "member"},
        headers=_auth("bob"),
    )
    assert r2.status_code == 403

    # bob (member, not admin) is forbidden from creating a channel.
    r3 = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "x"},
        headers=_auth("bob"),
    )
    assert r3.status_code == 403

    # bob cannot delete the org or create a department.
    assert client.delete(f"/api/orgs/{org['id']}", headers=_auth("bob")).status_code == 403
    assert (
        client.post(
            f"/api/orgs/{org['id']}/departments",
            json={"name": "X"},
            headers=_auth("bob"),
        ).status_code
        == 403
    )


# ─── 4. 频道 ACL:非成员访问频道 403、成员可访问 ─────────────────────────────


def test_channel_acl_non_member_forbidden_member_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)
    client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "private"},
        headers=_auth("alice"),
    ).json()

    # bob is not in the channel ACL → cannot read it.
    assert client.get(f"/api/channels/{ch['id']}", headers=_auth("bob")).status_code == 403
    # bob cannot list the channel's members either.
    assert client.get(f"/api/channels/{ch['id']}/members", headers=_auth("bob")).status_code == 403

    # alice (channel owner) can read it.
    assert client.get(f"/api/channels/{ch['id']}", headers=_auth("alice")).status_code == 200

    # alice grants bob channel membership.
    r = client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200
    assert r.json()["member_id"] == "bob"

    # bob can now read the channel.
    assert client.get(f"/api/channels/{ch['id']}", headers=_auth("bob")).status_code == 200


def test_channel_acl_member_cannot_manage_acl(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)
    client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "private"},
        headers=_auth("alice"),
    ).json()
    client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )

    # bob is a channel member but not admin → cannot add another member.
    r = client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "carol", "role": "member"},
        headers=_auth("bob"),
    )
    assert r.status_code == 403
    # bob cannot delete the channel.
    assert client.delete(f"/api/channels/{ch['id']}", headers=_auth("bob")).status_code == 403


# ─── 5. /mine 过滤 ─────────────────────────────────────────────────────────


def test_orgs_mine_and_channels_mine_filter(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)
    client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "general"},
        headers=_auth("alice"),
    ).json()

    # alice's orgs include Acme.
    mine_alice = client.get("/api/orgs/mine", headers=_auth("alice")).json()
    assert {o["id"] for o in mine_alice["organizations"]} == {org["id"]}

    # bob is a member → also sees Acme.
    mine_bob = client.get("/api/orgs/mine", headers=_auth("bob")).json()
    assert {o["id"] for o in mine_bob["organizations"]} == {org["id"]}

    # alice (channel owner) sees the channel.
    ch_alice = client.get("/api/channels/mine", headers=_auth("alice")).json()
    assert {c["id"] for c in ch_alice["channels"]} == {ch["id"]}

    # bob is not in the channel ACL → sees no channels.
    ch_bob = client.get("/api/channels/mine", headers=_auth("bob")).json()
    assert ch_bob["count"] == 0


# ─── 6. 删组织/删频道级联 ──────────────────────────────────────────────────


def test_delete_channel_cascades_acl(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)
    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "general"},
        headers=_auth("alice"),
    ).json()

    r = client.delete(f"/api/channels/{ch['id']}", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["deleted"] == ch["id"]
    assert client.get(f"/api/channels/{ch['id']}", headers=_auth("alice")).status_code == 404


def test_delete_org_cascades_departments_and_channels(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    org = _create_org(client)
    dept = client.post(
        f"/api/orgs/{org['id']}/departments",
        json={"name": "Eng"},
        headers=_auth("alice"),
    ).json()
    ch = client.post(
        f"/api/orgs/{org['id']}/channels",
        json={"name": "general", "department_id": dept["id"]},
        headers=_auth("alice"),
    ).json()

    r = client.delete(f"/api/orgs/{org['id']}", headers=_auth("alice"))
    assert r.status_code == 200
    assert r.json()["deleted"] == org["id"]

    assert client.get(f"/api/orgs/{org['id']}").status_code == 404
    assert client.get(f"/api/orgs/{org['id']}/departments").status_code == 404
    assert client.get(f"/api/orgs/{org['id']}/channels").status_code == 404
    # The channel is gone too.
    assert client.get(f"/api/channels/{ch['id']}", headers=_auth("alice")).status_code == 404
    # orgs/mine no longer lists it for alice.
    mine = client.get("/api/orgs/mine", headers=_auth("alice")).json()
    assert mine["count"] == 0


# ─── 7. 匿名(无身份)写操作 → 403 ───────────────────────────────────────────


def test_anonymous_write_forbidden(tmp_path: Path) -> None:
    # No identity_store → actor resolves to None.
    client = _client(tmp_path)
    org = client.post("/api/orgs", json={"name": "Acme", "owner_id": "alice"}).json()

    assert (
        client.post(
            f"/api/orgs/{org['id']}/members",
            json={"member_id": "bob", "role": "member"},
        ).status_code
        == 403
    )
    assert client.post(f"/api/orgs/{org['id']}/channels", json={"name": "x"}).status_code == 403
    assert client.delete(f"/api/orgs/{org['id']}").status_code == 403


def test_org_not_found_404(tmp_path: Path) -> None:
    client = _client(tmp_path, identity_store=_identity_store())
    assert client.get("/api/orgs/ghost").status_code == 404
    assert client.delete("/api/orgs/ghost", headers=_auth("alice")).status_code == 404

