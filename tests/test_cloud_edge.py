from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from runtime.cloud_edge.app import create_cloud_edge_app
from runtime.cloud_edge.router import create_cloud_edge_router
from runtime.cloud_edge.shares import normalise_public_snapshot
from runtime.platform.plugins.bundled.mx2025_viewer.cloud_sync import MXCloudSyncConnector
from runtime.safety.auth.identity import Identity, IdentityStore

SECRET = "cloud-edge-test-secret-that-is-longer-than-thirty-two-bytes"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _app(tmp_path: Path, *, enabled: bool = True, **router_options: Any) -> TestClient:
    identities = IdentityStore()
    identities.add(
        Identity("alice", roles=("operator",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="alice-key",
    )
    identities.add(
        Identity("bob", roles=("member",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="bob-key",
    )
    app = FastAPI()
    app.include_router(
        create_cloud_edge_router(
            db_path=tmp_path / "edge.sqlite3",
            token_secret=SECRET if enabled else None,
            identity_store=identities,
            require_auth=True,
            **router_options,
        )
    )
    return TestClient(app)


class _BorrowedClient:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def __enter__(self) -> TestClient:
        return self.client

    def __exit__(self, *_args: object) -> None:
        return None


def _enroll_and_token(client: TestClient) -> tuple[str, str]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    granted = client.put(
        "/api/cloud-edge/entitlements",
        headers={"Authorization": "Bearer alice-key"},
        json={"feature": "mx2025.sync", "active": True},
    )
    assert granted.status_code == 200
    pairing = client.post(
        "/api/cloud-edge/pairing-codes",
        headers={"Authorization": "Bearer alice-key"},
        json={"device_name": "Alice Mac"},
    )
    assert pairing.status_code == 200
    enrolled = client.post(
        "/edge/v1/enroll",
        json={
            "pairing_code": pairing.json()["pairing_code"],
            "public_key": _b64(public),
            "device_name": "Alice Mac",
        },
    )
    assert enrolled.status_code == 200
    device_id = enrolled.json()["device_id"]
    challenge = client.post(f"/edge/v1/challenge/{device_id}").json()["challenge"]
    signed = private.sign(f"echo-edge-token-v1:{device_id}:{challenge}".encode())
    token = client.post(
        "/edge/v1/token",
        json={"device_id": device_id, "challenge": challenge, "signature": _b64(signed)},
    )
    assert token.status_code == 200
    assert token.json()["expires_in"] == 900
    assert token.json()["features"] == ["mx2025.sync"]
    return device_id, token.json()["access_token"]


def test_device_pairing_short_token_ingest_and_owner_readback(tmp_path: Path) -> None:
    client = _app(tmp_path)
    device_id, access_token = _enroll_and_token(client)
    message = {
        "source": "mx2025",
        "source_room_id": "room-1",
        "source_message_id": "message-1",
        "title": "老师一",
        "content": "盘中消息",
        "published_at": "2026-08-23 10:00:00",
        "payload": {"kind": "conversation_message"},
    }
    headers = {"Authorization": f"Bearer {access_token}"}
    first = client.post("/edge/v1/messages/batch", headers=headers, json={"messages": [message]})
    assert first.status_code == 200
    assert first.json() == {"ok": True, "accepted": 1, "duplicate": 0}

    duplicate = client.post(
        "/edge/v1/messages/batch", headers=headers, json={"messages": [message]}
    )
    assert duplicate.json() == {"ok": True, "accepted": 0, "duplicate": 1}
    listed = client.get(
        "/api/cloud-edge/messages",
        headers={"Authorization": "Bearer alice-key"},
    )
    assert listed.status_code == 200
    assert [(item["device_id"], item["content"]) for item in listed.json()["messages"]] == [
        (device_id, "盘中消息")
    ]
    assert (
        client.put(
            "/api/cloud-edge/entitlements",
            headers={"Authorization": "Bearer alice-key"},
            json={"feature": "mx2025.sync", "active": False},
        ).status_code
        == 200
    )
    denied_after_revocation = client.post(
        "/edge/v1/messages/batch",
        headers=headers,
        json={"messages": [{**message, "source_message_id": "message-2"}]},
    )
    assert denied_after_revocation.status_code == 403


def test_pairing_and_challenge_are_single_use_and_revocation_is_immediate(tmp_path: Path) -> None:
    client = _app(tmp_path)
    device_id, access_token = _enroll_and_token(client)
    devices = client.get(
        "/api/cloud-edge/devices",
        headers={"Authorization": "Bearer alice-key"},
    ).json()["devices"]
    assert [item["device_id"] for item in devices] == [device_id]
    revoked = client.delete(
        f"/api/cloud-edge/devices/{device_id}",
        headers={"Authorization": "Bearer alice-key"},
    )
    assert revoked.json() == {"ok": True}
    denied = client.get(
        "/edge/v1/entitlements",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert denied.status_code == 401
    assert client.post(f"/edge/v1/challenge/{device_id}").status_code == 404


def test_management_is_authenticated_and_disabled_surface_fails_closed(tmp_path: Path) -> None:
    client = _app(tmp_path)
    assert "/api/cloud-edge/messages/stream" in client.get("/openapi.json").json()["paths"]
    assert (
        client.post("/api/cloud-edge/pairing-codes", json={"device_name": "x"}).status_code == 401
    )
    disabled = _app(tmp_path / "disabled", enabled=False)
    assert disabled.get("/api/cloud-edge/status").json() == {
        "enabled": False,
        "token_ttl_seconds": 900,
    }
    response = disabled.post(
        "/api/cloud-edge/pairing-codes",
        headers={"Authorization": "Bearer alice-key"},
        json={"device_name": "x"},
    )
    assert response.status_code == 503


def test_member_can_pair_own_device_but_cannot_grant_entitlements(tmp_path: Path) -> None:
    client = _app(tmp_path)
    pairing = client.post(
        "/api/cloud-edge/pairing-codes",
        headers={"Authorization": "Bearer bob-key"},
        json={"device_name": "Bob Mac"},
    )
    assert pairing.status_code == 200
    self_grant = client.put(
        "/api/cloud-edge/entitlements",
        headers={"Authorization": "Bearer bob-key"},
        json={"feature": "premium.agents", "active": True},
    )
    assert self_grant.status_code == 403
    assert (
        client.put(
            "/api/cloud-edge/entitlements",
            headers={"Authorization": "Bearer alice-key"},
            json={"owner_id": "bob", "feature": "mx2025.sync", "active": True},
        ).status_code
        == 200
    )
    bob_features = client.get(
        "/api/cloud-edge/entitlements",
        headers={"Authorization": "Bearer bob-key"},
    )
    assert bob_features.json() == {"features": ["mx2025.sync"]}


def test_local_connector_enrolls_signs_and_flushes_durable_outbox(tmp_path: Path) -> None:
    client = _app(tmp_path / "cloud")
    assert (
        client.put(
            "/api/cloud-edge/entitlements",
            headers={"Authorization": "Bearer alice-key"},
            json={"feature": "mx2025.sync", "active": True},
        ).status_code
        == 200
    )
    pairing = client.post(
        "/api/cloud-edge/pairing-codes",
        headers={"Authorization": "Bearer alice-key"},
        json={"device_name": "Local Mac"},
    ).json()["pairing_code"]
    connector = MXCloudSyncConnector(
        tmp_path / "local",
        http_client=_BorrowedClient(client),
    )
    configured = connector.configure(
        cloud_url="http://localhost",
        pairing_code=pairing,
        device_name="Local Mac",
    )
    assert configured["configured"] is True
    assert "private_key" not in configured
    queued = connector.enqueue(
        [
            {
                "source": "mx2025",
                "source_room_id": "room-9",
                "source_message_id": "message-9",
                "title": "九号老师",
                "content": "本地先落盘，云端后同步",
                "published_at": None,
                "payload": {"kind": "conversation_message"},
            }
        ]
    )
    assert queued == {"queued": 1, "duplicate": 0}
    assert connector.status()["pending"] == 1
    assert [item["title"] for item in connector.recent_messages(query="九号", limit=10)] == [
        "九号老师"
    ]
    assert connector.recent_messages(room_id="missing") == []
    assert connector.flush() == {"ok": True, "configured": True, "sent": 1}
    assert connector.status()["pending"] == 0
    assert connector.recent_messages(limit=1)[0]["cloud_synced"] is True
    cloud_messages = client.get(
        "/api/cloud-edge/messages",
        headers={"Authorization": "Bearer alice-key"},
    ).json()["messages"]
    assert [item["content"] for item in cloud_messages] == ["本地先落盘，云端后同步"]


def test_local_connector_official_ingest_mode_keeps_key_out_of_status(tmp_path: Path) -> None:
    received: list[dict] = []
    app = FastAPI()

    @app.post("/v1/data-sources/mx/messages/batch")
    def ingest(payload: dict, request: Request) -> dict[str, int | bool]:
        assert (
            request.headers["X-Official-Data-Key"]
            == "official-ingest-key-longer-than-thirty-two-bytes"
        )
        received.extend(payload["messages"])
        return {"ok": True, "accepted": len(payload["messages"]), "duplicate": 0}

    connector = MXCloudSyncConnector(
        tmp_path / "official-local",
        http_client=_BorrowedClient(TestClient(app)),
    )
    status = connector.configure_official_ingest(
        cloud_url="http://localhost",
        ingest_key="official-ingest-key-longer-than-thirty-two-bytes",
    )
    assert status["configured"] is True
    assert status["mode"] == "official_ingest"
    assert "ingest_key" not in status
    connector.enqueue(
        [
            {
                "source": "mx2025",
                "source_room_id": "r1",
                "source_message_id": "m1",
                "content": "云端官方库",
            }
        ]
    )
    assert connector.flush() == {"ok": True, "configured": True, "sent": 1}
    assert received[0]["content"] == "云端官方库"


def test_lightweight_standalone_app_is_fail_closed_and_healthy(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="at least 32"):
        create_cloud_edge_app(
            data_dir=tmp_path,
            token_secret="short",
            admin_key="also-short",
            registration_code="registration-code-for-tests",
        )
    with pytest.raises(RuntimeError, match="independent"):
        create_cloud_edge_app(
            data_dir=tmp_path,
            token_secret=SECRET,
            admin_key=SECRET,
            registration_code="registration-code-for-tests",
        )
    app = create_cloud_edge_app(
        data_dir=tmp_path,
        token_secret=SECRET,
        admin_key="standalone-admin-key-that-is-longer-than-thirty-two-bytes",
        registration_code="registration-code-for-tests",
    )
    client = TestClient(app)
    assert client.get("/livez").json() == {"ok": True}
    assert client.get("/readyz").json() == {"ready": True}
    assert (
        client.post("/api/cloud-edge/pairing-codes", json={"device_name": "Mac"}).status_code == 401
    )
    assert (
        client.post(
            "/api/cloud-edge/pairing-codes",
            headers={
                "Authorization": "Bearer standalone-admin-key-that-is-longer-than-thirty-two-bytes"
            },
            json={"device_name": "Mac"},
        ).status_code
        == 200
    )


def test_standalone_accounts_points_and_account_scoped_pairing(tmp_path: Path) -> None:
    admin_key = "standalone-admin-key-that-is-longer-than-thirty-two-bytes"
    app = create_cloud_edge_app(
        data_dir=tmp_path,
        token_secret=SECRET,
        admin_key=admin_key,
        registration_code="registration-code-for-tests",
    )
    client = TestClient(app)
    registered = client.post(
        "/v1/accounts/register",
        json={
            "username": "alice",
            "password": "correct-horse-battery",
            "registration_code": "registration-code-for-tests",
        },
    )
    assert registered.status_code == 201
    body = registered.json()
    account_id = body["account"]["account_id"]
    headers = {"Authorization": f"Bearer {body['access_token']}"}

    first = client.post("/v1/points/check-in", headers=headers).json()
    second = client.post("/v1/points/check-in", headers=headers).json()
    assert first["balance_after"] == 10
    assert second["duplicate"] is True
    assert client.get("/v1/points", headers=headers).json() == {"balance": 10}
    assert (
        client.post(
            "/v1/admin/points/adjust",
            headers={"Authorization": f"Bearer {admin_key}"},
            json={
                "account_id": account_id,
                "amount": 25,
                "reason": "测试赠送",
                "idempotency_key": "test-grant-001",
            },
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/v1/points/spend",
            headers=headers,
            json={"amount": 15, "purpose": "测试订阅", "idempotency_key": "test-spend-001"},
        ).json()["balance_after"]
        == 20
    )
    assert len(client.get("/v1/points/ledger", headers=headers).json()["entries"]) == 3

    product = {
        "sku": "mx.monthly",
        "name": "萌侠同步月卡",
        "feature": "mx2025.sync",
        "price_points": 20,
        "duration_days": 30,
        "active": True,
    }
    assert (
        client.put(
            "/v1/admin/subscription-products/mx.monthly",
            headers={"Authorization": f"Bearer {admin_key}"},
            json=product,
        ).status_code
        == 200
    )
    purchase = {"sku": "mx.monthly", "idempotency_key": "purchase-test-001"}
    activated = client.post("/v1/subscriptions/activate", headers=headers, json=purchase)
    assert activated.status_code == 200
    assert activated.json()["balance_after"] == 0
    assert (
        client.post("/v1/subscriptions/activate", headers=headers, json=purchase).json()[
            "duplicate"
        ]
        is True
    )
    assert client.get("/v1/points", headers=headers).json() == {"balance": 0}
    assert client.get("/api/cloud-edge/entitlements", headers=headers).json() == {
        "features": ["mx2025.sync"]
    }
    assert len(client.get("/v1/subscriptions", headers=headers).json()["subscriptions"]) == 1

    pairing = client.post(
        "/api/cloud-edge/pairing-codes", headers=headers, json={"device_name": "Alice Mac"}
    )
    assert pairing.status_code == 200
    assert (
        client.post(
            "/api/cloud-edge/pairing-codes",
            json={"device_name": "Anonymous Mac"},
        ).status_code
        == 401
    )

    refreshed = client.post("/v1/accounts/refresh", json={"refresh_token": body["refresh_token"]})
    assert refreshed.status_code == 200
    assert (
        client.post(
            "/v1/accounts/refresh", json={"refresh_token": body["refresh_token"]}
        ).status_code
        == 401
    )


def _public_snapshot(content: str = "Public answer") -> dict[str, Any]:
    return {
        "title": "Launch review /Users/alice/private/plan.md",
        "messages": [
            {"role": "user", "content": "Review api_key=super-private-value"},
            {"role": "assistant", "content": content},
        ],
        "artifacts": [r"C:\Users\Alice\private\release-notes.md"],
    }


def test_cloud_public_share_device_flow_hashes_token_and_revokes(tmp_path: Path) -> None:
    client = _app(tmp_path, public_share_base_url="https://share.example")
    _device_id, access_token = _enroll_and_token(client)
    headers = {"Authorization": f"Bearer {access_token}"}
    body = {"source_thread_id": "thread-launch", "snapshot": _public_snapshot()}

    assert client.post("/edge/v1/thread-shares", json=body).status_code == 401
    created = client.post("/edge/v1/thread-shares", headers=headers, json=body)
    assert created.status_code == 201, created.json()
    payload = created.json()
    token = payload["token"]
    share_id = payload["share_id"]
    assert payload["share_path"] == f"#/share/{token}"
    assert payload["share_url"] == f"https://share.example/#/share/{token}"

    with sqlite3.connect(tmp_path / "edge.sqlite3") as conn:
        row = conn.execute(
            "SELECT token_hash, snapshot_json FROM public_thread_shares WHERE share_id=?",
            (share_id,),
        ).fetchone()
        columns = {
            item[1] for item in conn.execute("PRAGMA table_info(public_thread_shares)").fetchall()
        }
    assert row is not None
    assert row[0] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row[1]
    assert "token" not in columns
    assert all(token.encode() not in path.read_bytes() for path in tmp_path.glob("edge.sqlite3*"))
    restarted = _app(tmp_path, public_share_base_url="https://share.example")
    assert (
        restarted.post("/api/public/thread-shares/resolve", json={"token": token}).status_code
        == 200
    )

    listed = client.get("/edge/v1/thread-shares?source_thread_id=thread-launch", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["shares"] == [
        {
            "share_id": share_id,
            "source_thread_id": "thread-launch",
            "created_at": payload["created_at"],
            "expires_at": payload["expires_at"],
            "title": "Launch review [local path redacted]",
            "stats": {"turns": 1, "messages": 2, "artifacts": 1},
        }
    ]
    assert token not in listed.text

    public = client.post("/api/public/thread-shares/resolve", json={"token": token})
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    assert public.headers["pragma"] == "no-cache"
    public_payload = public.json()
    assert public_payload["artifacts"] == ["release-notes.md"]
    serialized = json.dumps(public_payload, ensure_ascii=False)
    assert "super-private-value" not in serialized
    assert "/Users/alice/private" not in serialized
    for private_key in (
        "token",
        "token_hash",
        "share_id",
        "source_thread_id",
        "tenant_id",
        "owner_id",
        "creator_id",
        "creator_type",
    ):
        assert private_key not in public_payload
    assert (
        client.post("/api/v1/public/thread-shares/resolve", json={"token": token}).json()
        == public_payload
    )
    legacy = client.get(f"/api/v1/public/thread-shares/{token}")
    assert legacy.json() == public_payload
    assert legacy.headers["deprecation"] == "true"

    assert client.delete(f"/edge/v1/thread-shares/{share_id}").status_code == 401
    assert client.delete(f"/edge/v1/thread-shares/{share_id}", headers=headers).status_code == 204
    missing = client.post("/api/public/thread-shares/resolve", json={"token": token})
    assert missing.status_code == 404
    assert missing.headers["cache-control"] == "no-store"


def test_cloud_public_share_account_auth_is_owner_scoped_and_filterable(
    tmp_path: Path,
) -> None:
    client = _app(tmp_path)
    alice = {"Authorization": "Bearer alice-key"}
    bob = {"Authorization": "Bearer bob-key"}
    body = {"source_thread_id": "thread-account", "snapshot": _public_snapshot()}

    created = client.post("/api/cloud-edge/thread-shares", headers=alice, json=body)
    assert created.status_code == 201, created.json()
    share_id = created.json()["share_id"]
    assert client.get("/api/cloud-edge/thread-shares", headers=bob).json() == {"shares": []}
    assert (
        client.delete(f"/api/cloud-edge/thread-shares/{share_id}", headers=bob).status_code == 404
    )
    listed = client.get(
        "/api/cloud-edge/thread-shares?source_thread_id=thread-account", headers=alice
    ).json()["shares"]
    assert [item["share_id"] for item in listed] == [share_id]
    assert client.get(
        "/api/cloud-edge/thread-shares?source_thread_id=another-thread", headers=alice
    ).json() == {"shares": []}
    assert (
        client.delete(f"/api/cloud-edge/thread-shares/{share_id}", headers=alice).status_code == 204
    )


def test_standalone_cloud_share_accepts_account_api_key(tmp_path: Path) -> None:
    admin_key = "standalone-admin-key-that-is-longer-than-thirty-two-bytes"
    relay_key = "standalone-share-relay-key-that-is-longer-than-thirty-two-bytes"
    client = TestClient(
        create_cloud_edge_app(
            data_dir=tmp_path,
            token_secret=SECRET,
            admin_key=admin_key,
            registration_code="registration-code-for-tests",
            share_relay_key=relay_key,
        )
    )
    headers = {
        "X-API-Key": relay_key,
        "X-Echo-Share-Owner-Scope": f"relay_{'a' * 64}",
    }
    other_owner_headers = {
        "X-API-Key": relay_key,
        "X-Echo-Share-Owner-Scope": f"relay_{'b' * 64}",
    }
    assert (
        client.post(
            "/api/cloud-edge/pairing-codes",
            headers=headers,
            json={"device_name": "must-not-work"},
        ).status_code
        == 401
    )
    created = client.post(
        "/api/cloud-edge/thread-shares",
        headers=headers,
        json={"snapshot": _public_snapshot()},
    )
    assert created.status_code == 201
    share_id = created.json()["share_id"]
    assert client.get("/api/cloud-edge/thread-shares", headers=other_owner_headers).json() == {
        "shares": []
    }
    assert (
        client.delete(
            f"/api/cloud-edge/thread-shares/{share_id}", headers=other_owner_headers
        ).status_code
        == 404
    )
    assert [
        item["share_id"]
        for item in client.get("/api/cloud-edge/thread-shares", headers=headers).json()["shares"]
    ] == [share_id]
    assert (
        client.delete(f"/api/cloud-edge/thread-shares/{share_id}", headers=headers).status_code
        == 204
    )

    registered = client.post(
        "/v1/accounts/register",
        json={
            "username": "share-user",
            "password": "correct-horse-battery",
            "registration_code": "registration-code-for-tests",
        },
    )
    account_headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}
    account_created = client.post(
        "/api/cloud-edge/thread-shares",
        headers=account_headers,
        json={"source_thread_id": "account-thread", "snapshot": _public_snapshot()},
    )
    assert account_created.status_code == 201
    assert [
        item["share_id"]
        for item in client.get(
            "/api/cloud-edge/thread-shares?source_thread_id=account-thread",
            headers=account_headers,
        ).json()["shares"]
    ] == [account_created.json()["share_id"]]


def test_cloud_public_share_enforces_owner_quota_ttl_and_gc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _app(tmp_path, share_max_per_owner=1, share_ttl_seconds=1)
    _device_id, access_token = _enroll_and_token(client)
    headers = {"Authorization": f"Bearer {access_token}"}
    body = {"snapshot": _public_snapshot()}
    created = client.post("/edge/v1/thread-shares", headers=headers, json=body)
    assert created.status_code == 201
    assert client.post("/edge/v1/thread-shares", headers=headers, json=body).status_code == 409

    token = created.json()["token"]
    future = int(time.time()) + 2
    with monkeypatch.context() as patch:
        patch.setattr("runtime.cloud_edge.store.time.time", lambda: future)
        expired = client.post("/api/public/thread-shares/resolve", json={"token": token})
    assert expired.status_code == 404
    assert expired.headers["cache-control"] == "no-store"
    with sqlite3.connect(tmp_path / "edge.sqlite3") as conn:
        assert conn.execute("SELECT COUNT(*) FROM public_thread_shares").fetchone()[0] == 0


def test_cloud_public_share_enforces_snapshot_and_global_size_limits(tmp_path: Path) -> None:
    snapshot = _public_snapshot("bounded content")
    normalised = normalise_public_snapshot(snapshot)
    snapshot_bytes = len(
        json.dumps(
            normalised,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )

    too_small = _app(
        tmp_path / "item-limit",
        share_max_snapshot_bytes=snapshot_bytes - 1,
    )
    _device_id, token = _enroll_and_token(too_small)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        too_small.post(
            "/edge/v1/thread-shares", headers=headers, json={"snapshot": snapshot}
        ).status_code
        == 413
    )

    capacity = _app(
        tmp_path / "total-limit",
        share_max_snapshot_bytes=snapshot_bytes,
        share_max_total_bytes=snapshot_bytes * 2 - 1,
        share_max_per_owner=10,
    )
    _device_id, token = _enroll_and_token(capacity)
    headers = {"Authorization": f"Bearer {token}"}
    assert (
        capacity.post(
            "/edge/v1/thread-shares", headers=headers, json={"snapshot": snapshot}
        ).status_code
        == 201
    )
    assert (
        capacity.post(
            "/edge/v1/thread-shares", headers=headers, json={"snapshot": snapshot}
        ).status_code
        == 507
    )


def test_cloud_public_share_base_url_requires_https_off_loopback(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        _app(tmp_path, public_share_base_url="http://share.example")
    client = _app(tmp_path / "loopback", public_share_base_url="http://127.0.0.1:3000")
    _device_id, access_token = _enroll_and_token(client)
    created = client.post(
        "/edge/v1/thread-shares",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"snapshot": _public_snapshot()},
    )
    assert created.json()["share_url"].startswith("http://127.0.0.1:3000/#/share/")


