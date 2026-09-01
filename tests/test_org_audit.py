"""Tests for ``runtime.workspace.org_audit`` (阶段二 · 审计日志).

Covers:
  1. 基础 HMAC 审计链:追加/校验/查询/导出
  2. 篡改检测:改一条记录 → 校验失败
  3. Router 集成:组织/频道/成员/ACL 写操作均记录审计事件(含 actor)
  4. 角色变更:before/after 捕获(org_member_role_change / channel_member_role_change)
  5. 未知事件类型拒绝
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.org_router import create_org_router
from runtime.workspace import (
    OrgStore,
    append_org_audit_event,
    export_org_audit_bundle,
    list_org_audit_events,
    verify_org_audit_chain,
)

SECRET = "00" * 32  # fixed 32-byte hex secret for deterministic tests


def _identity_store() -> IdentityStore:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="key-alice")
    store.add(Identity(actor_id="bob"), api_key_plaintext="key-bob")
    return store


def _client(tmp_path: Path, *, chain_path: Path) -> TestClient:
    store = OrgStore(db_path=tmp_path / "org.db")
    app = FastAPI()
    app.include_router(
        create_org_router(
            org_store=store,
            identity_store=_identity_store(),
            audit_chain_path=str(chain_path),
            audit_chain_secret=SECRET,
        )
    )
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


# ─── 1. 基础审计链 ─────────────────────────────────────────────────────────


def test_append_verify_list_export(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    rec = append_org_audit_event(
        event_type="org_member_add",
        actor="alice",
        org_id="o1",
        target="bob",
        detail={"role": "member"},
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )
    assert rec["event_type"] == "org_member_add"
    assert rec["actor"] == "alice"
    assert rec["org_id"] == "o1"
    assert rec["target"] == "bob"
    assert rec["audit_chain"]["path"] == str(chain)
    assert rec["audit_chain"]["mac"]

    append_org_audit_event(
        event_type="channel_member_remove",
        actor="alice",
        org_id="o1",
        target="carol",
        channel_id="c1",
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )

    verified = verify_org_audit_chain(audit_chain_path=chain, audit_chain_secret=SECRET)
    assert verified["ok"] is True
    assert verified["entries_checked"] == 2

    events = list_org_audit_events(audit_chain_path=chain, audit_chain_secret=SECRET)
    assert [e["event_type"] for e in events] == [
        "channel_member_remove",
        "org_member_add",
    ]
    # Filter by actor / event_type / channel.
    assert (
        list_org_audit_events(
            audit_chain_path=chain,
            audit_chain_secret=SECRET,
            event_type="org_member_add",
        )[0]["target"]
        == "bob"
    )
    assert (
        list_org_audit_events(
            audit_chain_path=chain,
            audit_chain_secret=SECRET,
            actor="alice",
        )[0]["actor"]
        == "alice"
    )

    bundle = export_org_audit_bundle(audit_chain_path=chain, audit_chain_secret=SECRET)
    assert bundle["schema"] == "echo.org_audit_export.v1"
    assert bundle["chain"]["line_count"] == 2
    assert bundle["integrity"]["ok"] is True


def test_unknown_event_type_rejected(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    try:
        append_org_audit_event(
            event_type="bogus",
            actor="alice",
            org_id="o1",
            target="bob",
            audit_chain_path=chain,
            audit_chain_secret=SECRET,
        )
    except ValueError as exc:
        assert "unknown org audit event_type" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError for unknown event_type")


# ─── 2. 篡改检测 ───────────────────────────────────────────────────────────


def test_tamper_detection(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    append_org_audit_event(
        event_type="org_member_add",
        actor="alice",
        org_id="o1",
        target="bob",
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )
    append_org_audit_event(
        event_type="org_member_remove",
        actor="alice",
        org_id="o1",
        target="bob",
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )

    # Mutate the first record's payload (e.g. flip the actor).
    lines = chain.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["actor"] = "mallory"
    lines[0] = json.dumps(first, ensure_ascii=False)
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verified = verify_org_audit_chain(audit_chain_path=chain, audit_chain_secret=SECRET)
    assert verified["ok"] is False
    assert verified["broken_at"] == 0


def test_delete_record_breaks_chain(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    append_org_audit_event(
        event_type="org_member_add",
        actor="alice",
        org_id="o1",
        target="bob",
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )
    append_org_audit_event(
        event_type="org_member_remove",
        actor="alice",
        org_id="o1",
        target="bob",
        audit_chain_path=chain,
        audit_chain_secret=SECRET,
    )

    # Drop the genesis record → seq gap + prev_mac mismatch.
    lines = chain.read_text(encoding="utf-8").splitlines()[1:]
    chain.write_text("\n".join(lines) + "\n", encoding="utf-8")

    verified = verify_org_audit_chain(audit_chain_path=chain, audit_chain_secret=SECRET)
    assert verified["ok"] is False


# ─── 3. Router 集成:写操作记录审计 ─────────────────────────────────────────


def test_router_records_org_and_member_audit(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    client = _client(tmp_path, chain_path=chain)
    org = _create_org(client)
    client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "kind": "human", "role": "member"},
        headers=_auth("alice"),
    )

    events = list_org_audit_events(audit_chain_path=chain, audit_chain_secret=SECRET)
    types = [e["event_type"] for e in events]
    # org creation + owner auto-member + bob add.
    assert "org_create" in types
    assert "org_member_add" in types
    # Every event carries the actor (who did it).
    assert all(e["actor"] for e in events)
    assert verify_org_audit_chain(audit_chain_path=chain, audit_chain_secret=SECRET)["ok"]


def test_router_records_channel_acl_audit(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    client = _client(tmp_path, chain_path=chain)
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
    client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    client.delete(f"/api/channels/{ch['id']}/members/bob", headers=_auth("alice"))

    events = list_org_audit_events(audit_chain_path=chain, audit_chain_secret=SECRET)
    types = [e["event_type"] for e in events]
    # channel create + owner auto membership + bob grant + bob remove.
    assert "org_channel_create" in types
    assert "channel_member_add" in types
    assert "channel_member_remove" in types
    # The remove event is scoped to the channel.
    remove = next(e for e in events if e["event_type"] == "channel_member_remove")
    assert remove["channel_id"] == ch["id"]
    assert remove["target"] == "bob"
    assert verify_org_audit_chain(audit_chain_path=chain, audit_chain_secret=SECRET)["ok"]


def test_router_audit_does_not_break_when_chain_missing(tmp_path: Path) -> None:
    # No audit_chain_path/secret passed → append falls back to defaults and
    # must not break the mutation (audit failures are swallowed).
    store = OrgStore(db_path=tmp_path / "org.db")
    app = FastAPI()
    app.include_router(create_org_router(org_store=store))
    client = TestClient(app)
    org = client.post("/api/orgs", json={"name": "Acme", "owner_id": "alice"}).json()
    assert org["name"] == "Acme"


# ─── 4. 角色变更 before/after ──────────────────────────────────────────────


def test_org_member_role_change_captures_before_after(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    client = _client(tmp_path, chain_path=chain)
    org = _create_org(client)
    client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    # Promote bob to admin (upsert → role change).
    r = client.post(
        f"/api/orgs/{org['id']}/members",
        json={"member_id": "bob", "role": "admin"},
        headers=_auth("alice"),
    )
    assert r.status_code == 200

    change = next(
        e
        for e in list_org_audit_events(audit_chain_path=chain, audit_chain_secret=SECRET)
        if e["event_type"] == "org_member_role_change"
    )
    assert change["target"] == "bob"
    assert change["detail"]["before"] == "member"
    assert change["detail"]["after"] == "admin"


def test_channel_member_role_change_captures_before_after(tmp_path: Path) -> None:
    chain = tmp_path / "org_audit.jsonl"
    client = _client(tmp_path, chain_path=chain)
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
    client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "bob", "role": "member"},
        headers=_auth("alice"),
    )
    client.post(
        f"/api/channels/{ch['id']}/members",
        json={"member_id": "bob", "role": "admin"},
        headers=_auth("alice"),
    )

    change = next(
        e
        for e in list_org_audit_events(audit_chain_path=chain, audit_chain_secret=SECRET)
        if e["event_type"] == "channel_member_role_change"
    )
    assert change["channel_id"] == ch["id"]
    assert change["detail"]["before"] == "member"
    assert change["detail"]["after"] == "admin"

