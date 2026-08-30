from __future__ import annotations

import sqlite3

from runtime.memory import user_store
from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.journal._journal_models import TokenUsageEvent
from runtime.memory.journal.journal import InMemoryJournal
from runtime.safety.auth.scope import TenantScope


def test_trace_store_migrates_legacy_tables_before_creating_scope_indexes(tmp_path):
    db_path = tmp_path / "legacy-trace.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            turn_id TEXT,
            agent_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        INSERT INTO messages(ts, thread_id, role, content)
        VALUES('2026-01-01T00:00:00Z', 'legacy-thread', 'user', 'preserve me');
        """
    )
    conn.close()

    store = AgentTraceStore(db_path)
    try:
        columns = {row[1] for row in store._conn.execute("PRAGMA table_info(messages)").fetchall()}
        indexes = {row[1] for row in store._conn.execute("PRAGMA index_list(messages)").fetchall()}
        assert {"tenant_id", "owner_actor_id"} <= columns
        assert "idx_messages_scope" in indexes
        assert store.messages()[0]["content"] == "preserve me"
    finally:
        store.close()


def test_trace_store_filters_rows_by_tenant_and_owner(tmp_path):
    store = AgentTraceStore(tmp_path / "trace.db")
    alice = TenantScope("tenant-a", "alice")
    bob = TenantScope("tenant-b", "bob")
    operator = TenantScope("tenant-a", "operator", allow_cross_tenant=True)

    store.record_event(
        event_type="TASK_RUN_STARTED",
        payload={"secret": "alice"},
        task_id="task-a",
        scope=alice,
    )
    store.record_event(
        event_type="TASK_RUN_STARTED",
        payload={"secret": "bob"},
        task_id="task-b",
        scope=bob,
    )
    # Rows created before the migration have no ownership and must not become
    # visible merely because a tenant principal is authenticated.
    store.record_event(event_type="LEGACY", payload={"secret": "old"}, task_id="legacy")

    assert [row["task_id"] for row in store.events(scope=alice)] == ["task-a"]
    assert [row["task_id"] for row in store.events(scope=bob)] == ["task-b"]
    assert {row["task_id"] for row in store.events(scope=operator)} == {
        "task-a",
        "task-b",
        "legacy",
    }
    assert store.stats(scope=alice)["events"] == 1

    store.close()


def test_journal_scope_filters_legacy_and_other_tenant_events():
    journal = InMemoryJournal()
    alice = TenantScope("tenant-a", "alice")
    bob = TenantScope("tenant-b", "bob")

    journal.write(
        TokenUsageEvent(
            tenant_id="tenant-a",
            owner_actor_id="alice",
            input_tokens=1,
            output_tokens=2,
        )
    )
    journal.write(
        TokenUsageEvent(
            tenant_id="tenant-b",
            owner_actor_id="bob",
            input_tokens=3,
            output_tokens=4,
        )
    )
    journal.write(TokenUsageEvent(input_tokens=99, output_tokens=99))

    assert len(journal.read_by_type("token_usage", scope=alice)) == 1
    assert len(journal.read_by_type("token_usage", scope=bob)) == 1
    assert len(journal.read_by_type("token_usage")) == 3


def test_memory_store_partitions_tenant_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    alice = TenantScope("tenant-a", "alice")
    bob = TenantScope("tenant-b", "bob")

    user_store.add_fact("alice secret", tenant_scope=alice)
    user_store.add_fact("bob secret", tenant_scope=bob)

    assert [row["content"] for row in user_store.read_memory(alice)["facts"]] == ["alice secret"]
    assert [row["content"] for row in user_store.read_memory(bob)["facts"]] == ["bob secret"]
    assert user_store.read_memory(alice)["facts"][0]["tenant_id"] == "tenant-a"


def test_video_index_path_and_media_root_are_tenant_scoped(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.memory.hemolymph import video_semantic_index
    from runtime.memory.hemolymph.video_semantic_index import tenant_video_db_path
    from runtime.safety.auth.identity import Identity, IdentityStore
    from runtime.sensing.gateway.media_router import create_media_router

    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="alice-media-key",
    )
    app = FastAPI()
    monkeypatch.setattr(
        video_semantic_index,
        "build_video_index",
        lambda *args, **kwargs: {"ok": True, "videos_indexed": 0},
    )
    app.include_router(
        create_media_router(identity_store=identities, require_auth=True),
        prefix="/media",
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer alice-media-key"}

    # The tenant's default media root is valid; an arbitrary host directory
    # is not accepted even when the caller is authenticated.
    assert client.post("/media/video/index", headers=headers, json={}).status_code == 200
    assert (
        client.post(
            "/media/video/index",
            headers=headers,
            json={"directory": str(tmp_path)},
        ).status_code
        == 403
    )
    assert tenant_video_db_path(TenantScope("tenant-a", "alice")) != tenant_video_db_path(
        TenantScope("tenant-b", "bob")
    )


def test_media_allowlist_does_not_grant_shared_root_to_normal_user(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from runtime.memory.hemolymph import video_semantic_index
    from runtime.safety.auth.identity import Identity, IdentityStore
    from runtime.sensing.gateway.media_router import create_media_router

    shared_root = tmp_path / "shared-media"
    shared_root.mkdir()
    monkeypatch.setenv("ECHO_MEDIA_ALLOWED_ROOTS", str(shared_root))
    monkeypatch.setattr(
        video_semantic_index,
        "build_video_index",
        lambda *args, **kwargs: {"ok": True},
    )
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="alice-media-key",
    )
    app = FastAPI()
    app.include_router(
        create_media_router(identity_store=identities, require_auth=True),
        prefix="/media",
    )

    response = TestClient(app).post(
        "/media/video/index",
        headers={"Authorization": "Bearer alice-media-key"},
        json={"directory": str(shared_root)},
    )
    assert response.status_code == 403


def test_video_watchers_are_partitioned_by_database(tmp_path, monkeypatch):
    from runtime.memory.hemolymph import video_watchdog

    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "0")
    video_watchdog.stop_all()
    root = tmp_path / "media"
    root.mkdir()
    first = video_watchdog.start_watching(root, db_path=tmp_path / "a.db")
    second = video_watchdog.start_watching(root, db_path=tmp_path / "b.db")
    try:
        assert first is not second
        assert first.db_path != second.db_path
    finally:
        video_watchdog.stop_all()

