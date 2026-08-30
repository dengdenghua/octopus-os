from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.threads.store import ThreadStateStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.thread_share_relay import ThreadShareRelayClient
from runtime.sensing.gateway.thread_share_store import (
    ThreadShareStore,
    build_public_thread_snapshot,
)
from runtime.sensing.gateway.thread_state_router import create_thread_state_router


def _snapshot() -> dict[str, object]:
    return {
        "title": "Public result",
        "messages": [{"role": "user", "content": "hello"}],
        "artifacts": [],
        "stats": {"turns": 1, "messages": 1, "artifacts": 0},
    }


def test_share_store_persists_only_a_capability_token_hash(tmp_path: Path) -> None:
    store = ThreadShareStore(tmp_path)

    created = store.create(
        thread_id="thread-one",
        actor_id="alice",
        tenant_id="tenant-a",
        snapshot=_snapshot(),
    )

    token = created["token"]
    assert isinstance(token, str)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    files = list(tmp_path.glob("*.json"))
    assert [path.name for path in files] == [f"{token_hash}.json"]

    raw = files[0].read_text(encoding="utf-8")
    persisted = json.loads(raw)
    assert token not in raw
    assert "token" not in persisted
    assert persisted["token_hash"] == token_hash
    assert persisted["share_id"].startswith("shr_")
    assert persisted["expires_at"]
    assert files[0].stat().st_mode & 0o777 == 0o600

    loaded = store.get(token)
    assert loaded is not None
    assert "token" not in loaded
    assert "token" not in store.public_record(loaded)


def test_share_store_enforces_quota_size_and_physically_cleans_expired_records(
    tmp_path: Path,
) -> None:
    store = ThreadShareStore(tmp_path, max_active_per_owner=1, max_snapshot_bytes=1024)
    created = store.create(
        thread_id="one",
        actor_id="alice",
        tenant_id="tenant-a",
        snapshot=_snapshot(),
    )

    try:
        store.create(
            thread_id="two",
            actor_id="alice",
            tenant_id="tenant-a",
            snapshot=_snapshot(),
        )
    except RuntimeError as exc:
        assert "quota" in str(exc)
    else:  # pragma: no cover - guard against silently dropping the quota
        raise AssertionError("expected active share quota")

    oversized = {**_snapshot(), "title": "x" * 2_000}
    try:
        ThreadShareStore(tmp_path / "large", max_snapshot_bytes=1024).create(
            thread_id="large",
            actor_id="alice",
            tenant_id="tenant-a",
            snapshot=oversized,
        )
    except ValueError as exc:
        assert "too large" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected snapshot size limit")

    path = next(tmp_path.glob("*.json"))
    persisted = json.loads(path.read_text(encoding="utf-8"))
    persisted["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    path.write_text(json.dumps(persisted), encoding="utf-8")
    assert store.get(str(created["token"])) is None
    assert store.cleanup() == 1
    assert not path.exists()


def test_public_snapshot_redacts_secrets_and_strips_both_path_styles() -> None:
    values = {
        "title": "Review /Users/alice/private/launch.md",
        "messages": [
            {"type": "system", "content": "never publish this system prompt"},
            {
                "type": "human",
                "content": (
                    r"Open C:\Users\Alice\private\plan.md and "
                    "api_key=top-secret-value"
                ),
            },
            {
                "type": "ai",
                "content": [
                    {
                        "type": "reasoning",
                        "text": "hidden structured reasoning",
                    },
                    {
                        "type": "text",
                        "text": "Done with sk-abcdefghijklmnopqrstuvwxyz123456",
                    },
                    {
                        "type": "tool_result",
                        "text": "hidden structured tool result",
                    },
                ],
                "reasoning": "private chain of thought",
            },
            {"type": "tool", "content": "raw command output"},
        ],
        "artifacts": [
            r"C:\Users\Alice\private\report-final.pdf",
            "/Users/alice/private/result.csv",
            r"\\server\share\preview.png",
            {"path": r"C:\Users\Alice\private\structured.docx", "secret": "ignore"},
            {"secret": "not-an-artifact"},
        ],
    }

    snapshot = build_public_thread_snapshot(
        {"values": values},
        {"values": values},
    )

    assert snapshot["artifacts"] == [
        "report-final.pdf",
        "result.csv",
        "preview.png",
        "structured.docx",
    ]
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "never publish this system prompt" not in serialized
    assert "raw command output" not in serialized
    assert "private chain of thought" not in serialized
    assert "hidden structured reasoning" not in serialized
    assert "hidden structured tool result" not in serialized
    assert "not-an-artifact" not in serialized
    assert "top-secret-value" not in serialized
    assert "sk-abcdefghijklmnopqrstuvwxyz123456" not in serialized
    assert "/Users/alice/private" not in serialized
    assert r"C:\Users\Alice\private" not in serialized
    assert "[已隐藏]" in serialized
    assert "[本地路径已隐藏]" in serialized


def test_share_router_creates_once_reads_anonymously_and_revokes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ECHO_PUBLIC_SHARE_BASE_URL",
        "https://share.echo-age.com/ui",
    )
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice-test",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-bob-test",
    )
    store = ThreadStateStore()
    store.ensure_thread(
        "thread-public-share",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
        values={
            "title": "Launch review",
            "messages": [
                {"type": "human", "content": "Review api_key=private-key"},
                {"type": "ai", "content": "The public answer"},
            ],
            "artifacts": [r"C:\private\release-notes.md"],
        },
    )

    create_calls = 0
    real_create = ThreadShareStore.create

    def counted_create(self: ThreadShareStore, **kwargs):
        nonlocal create_calls
        create_calls += 1
        return real_create(self, **kwargs)

    monkeypatch.setattr(ThreadShareStore, "create", counted_create)
    logs_root = tmp_path / "threads"
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=store,
            logs_root=logs_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    alice = {"Authorization": "Bearer sk-alice-test"}
    bob = {"Authorization": "Bearer sk-bob-test"}

    assert client.post("/api/threads/thread-public-share/shares").status_code == 401
    created = client.post(
        "/api/threads/thread-public-share/shares",
        headers=alice,
    )
    assert created.status_code == 201, created.json()
    assert create_calls == 1
    assert "reused" not in created.json()
    token = created.json()["token"]
    share_id = created.json()["share_id"]
    assert created.json()["share_path"] == f"#/share/{token}"
    assert created.json()["share_url"] == f"https://share.echo-age.com/ui/#/share/{token}"
    assert created.json()["expires_at"]

    persisted_files = list((tmp_path / "thread-shares").glob("*.json"))
    assert len(persisted_files) == 1
    assert token not in persisted_files[0].name
    assert token not in persisted_files[0].read_text(encoding="utf-8")

    public = client.post("/api/public/thread-shares/resolve", json={"token": token})
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store"
    payload = public.json()
    assert payload["title"] == "Launch review"
    assert payload["artifacts"] == ["release-notes.md"]
    assert payload["messages"][-1] == {
        "role": "assistant",
        "content": "The public answer",
    }
    for private_key in (
        "token",
        "token_hash",
        "thread_id",
        "actor_id",
        "tenant_id",
        "snapshot_hash",
    ):
        assert private_key not in payload
    assert "private-key" not in json.dumps(payload, ensure_ascii=False)

    listed = client.get("/api/threads/thread-public-share/shares", headers=alice)
    assert listed.status_code == 200
    assert listed.json()["shares"] == [
        {
            "share_id": share_id,
            "created_at": created.json()["created_at"],
            "expires_at": created.json()["expires_at"],
            "title": "Launch review",
            "stats": {"turns": 1, "messages": 2, "artifacts": 1},
        }
    ]
    serialized_list = json.dumps(listed.json())
    assert token not in serialized_list
    assert "token_hash" not in serialized_list

    legacy = client.get(f"/api/public/thread-shares/{token}")
    assert legacy.status_code == 200
    assert legacy.headers["deprecation"] == "true"

    assert client.delete(f"/api/thread-shares/{token}").status_code == 401
    assert client.delete(f"/api/thread-shares/{token}", headers=bob).status_code == 404
    assert client.get(f"/api/public/thread-shares/{token}").status_code == 200
    assert client.delete(f"/api/thread-shares/by-id/{share_id}", headers=bob).status_code == 404
    assert client.delete(f"/api/thread-shares/by-id/{share_id}", headers=alice).status_code == 204
    revoked = client.get(f"/api/public/thread-shares/{token}")
    assert revoked.status_code == 404
    assert revoked.headers["cache-control"] == "no-store"
    assert revoked.headers["pragma"] == "no-cache"
    assert list((tmp_path / "thread-shares").glob("*.json")) == []


def test_share_router_uses_configured_cloud_relay_without_persisting_local_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class Relay:
        def create(
            self,
            *,
            source_thread_id: str,
            snapshot: dict[str, object],
            actor_id: str,
            tenant_id: str,
        ):
            calls.append(("create", (source_thread_id, actor_id, tenant_id, snapshot)))
            return {
                "token": "remote-capability",
                "share_id": "shr_remote",
                "share_path": "#/share/remote-capability",
                "share_url": "https://share.example.com/#/share/remote-capability",
                "created_at": "2026-08-25T00:00:00Z",
                "expires_at": "2026-09-24T00:00:00Z",
            }

        def list_for_thread(
            self,
            *,
            source_thread_id: str,
            actor_id: str,
            tenant_id: str,
        ):
            calls.append(("list", (source_thread_id, actor_id, tenant_id)))
            return [{"share_id": "shr_remote"}]

        def revoke(self, share_id: str, *, actor_id: str, tenant_id: str):
            calls.append(("revoke", (share_id, actor_id, tenant_id)))

    relay = Relay()
    monkeypatch.setattr(
        ThreadShareRelayClient,
        "from_env",
        classmethod(lambda _cls: relay),
    )
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice-relay",
    )
    state_store = ThreadStateStore()
    state_store.ensure_thread(
        "thread-relay",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
        values={
            "title": "Relay task",
            "messages": [{"type": "human", "content": "Publish this"}],
        },
    )
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=state_store,
            logs_root=tmp_path / "threads",
            identity_store=identities,
            require_auth=True,
        )
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-alice-relay"}

    created = client.post("/api/threads/thread-relay/shares", headers=headers)
    assert created.status_code == 201
    assert created.json()["share_url"].startswith("https://share.example.com/")
    assert list((tmp_path / "thread-shares").glob("*.json")) == []
    assert client.get("/api/threads/thread-relay/shares", headers=headers).json() == {
        "shares": [{"share_id": "shr_remote"}]
    }
    assert client.delete("/api/thread-shares/by-id/shr_remote", headers=headers).status_code == 204
    assert [name for name, _ in calls] == ["create", "list", "revoke"]
    assert calls[0][1][:3] == ("thread-relay", "alice", "tenant-a")
    assert calls[1][1] == ("thread-relay", "alice", "tenant-a")
    assert calls[2][1] == ("shr_remote", "alice", "tenant-a")

