"""Actual ASGI routes, persisted task authority and real temporary rollback files."""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers import SkillRegistry
from runtime.memory.journal import FileOpEvent, InMemoryJournal, journal_context
from runtime.platform.models import TaskId
from runtime.platform.process._task_supervisor_models import TaskLease, TaskRunRecord, TaskRunStatus
from runtime.platform.process._task_supervisor_store import TaskSupervisorStore
from runtime.safety.auth.identity import Identity, IdentityStore
from runtime.sensing.gateway.observability_router import create_observability_router


@pytest.fixture
def bound(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    task_id = TaskId(uuid4())
    journal = InMemoryJournal()
    store = TaskSupervisorStore(tmp_path / "tasks.json")
    record = TaskRunRecord(
        task_id=str(task_id),
        owner_id="alice",
        thread_id="thread-a",
        status="completed",
        workspace_path=str(root),
    )
    store.upsert(record)
    threads = {
        "thread-a": {
            "metadata": {
                "owner_actor_id": "alice",
                "tenant_id": "tenant-a",
                "workspace_path": str(root),
            }
        }
    }
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", roles=("operator",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="alice-test-token",
    )
    identities.add(
        Identity(actor_id="member", roles=("member",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="member-test-token",
    )
    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=journal,
            registry=SkillRegistry(),
            identity_store=identities,
            require_auth=True,
            thread_store=threads,
            workspace_root=root,
            allow_local_workspace_access=True,
            task_supervisor=SimpleNamespace(store=store),
        )
    )
    client = TestClient(app, headers={"Authorization": "Bearer alice-test-token"})

    def write(name="state.txt", *, actual="after", event_path=None, ts=None):
        target = root / name
        target.write_text(actual, encoding="utf-8")
        with journal_context(tenant_id="tenant-a", owner_actor_id="alice"):
            journal.write(
                FileOpEvent(
                    task_id=task_id,
                    path=str(target),
                    action="write",
                    rollback={
                        "reversible": True,
                        "action": "write",
                        "path": event_path or str(target),
                        "content": "before",
                        "expected_current_sha256": hashlib.sha256(b"after").hexdigest(),
                    },
                    **({"ts": ts} if ts is not None else {}),
                )
            )
        return target

    return SimpleNamespace(
        root=root,
        task_id=str(task_id),
        journal=journal,
        store=store,
        record=record,
        threads=threads,
        client=client,
        write=write,
        app=app,
        identities=identities,
    )


def test_preview_and_apply_derive_server_root(bound):
    target = bound.write()
    preview = bound.client.get("/api/files/rollback/preview", params={"task_id": bound.task_id})
    assert preview.status_code == 200, preview.text
    assert preview.json()["state"] == "preview_ready"
    assert preview.json()["execution_complete"] is False
    assert "content" not in preview.json()["entries"][0]
    assert target.read_text() == "after"
    applied = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert applied.status_code == 200, applied.text
    assert applied.json()["execution_complete"] is True
    assert target.read_text() == "before"


def test_client_root_cannot_redirect_identical_file(bound, tmp_path):
    target = bound.write(event_path="state.txt")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    other = outside / "state.txt"
    other.write_text("after")
    response = bound.client.post(
        "/api/files/rollback/apply",
        json={
            "task_id": bound.task_id,
            "project_root": str(outside),
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "rollback_project_root_mismatch"
    assert other.read_text() == target.read_text() == "after"


@pytest.mark.parametrize("root", [True, 42, [], "relative/path", ""])
def test_rejects_invalid_root_values(bound, root):
    target = bound.write()
    response = bound.client.post(
        "/api/files/rollback/apply",
        json={
            "task_id": bound.task_id,
            "project_root": root,
        },
    )
    assert response.status_code == 400
    assert target.read_text() == "after"


@pytest.mark.parametrize(
    "field,value", [("owner_actor_id", "bob"), ("tenant_id", "tenant-b"), ("workspace_path", None)]
)
def test_current_thread_authority_can_revoke_old_event(bound, field, value):
    target = bound.write()
    bound.threads["thread-a"]["metadata"][field] = value
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 403
    assert target.read_text() == "after"


@pytest.mark.parametrize(
    "status", ["pending", "running", "waiting_approval", "paused", "verifying", "repairing"]
)
def test_active_task_cannot_be_rewound_or_rolled_back(bound, status):
    target = bound.write()
    bound.store.upsert(bound.record.model_copy(update={"status": TaskRunStatus(status)}))
    for url, body in [
        ("/api/files/rollback/apply", {"task_id": bound.task_id}),
        (f"/api/tasks/{bound.task_id}/rewind/apply", {"iteration": 0}),
    ]:
        assert bound.client.post(url, json=body).status_code == 409
    assert target.read_text() == "after"


def test_other_owner_active_task_and_live_lease_block_workspace(bound):
    target = bound.write()
    other = bound.record.model_copy(
        update={"task_id": "other", "owner_id": "bob", "status": TaskRunStatus.RUNNING}
    )
    bound.store.upsert(other)
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 409
    # Allocate a fenced token through the authority store, so this fixture
    # exercises rollback denial rather than failing payload validation first.
    bound.store.upsert_mutate(
        other.task_id,
        lambda _existing, next_lease_token: other.model_copy(
            update={
                "status": TaskRunStatus.COMPLETED,
                "lease": TaskLease(holder_id="worker", token=next_lease_token()),
            }
        )
    )
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "rollback_lease_conflict"
    assert target.read_text() == "after"


@pytest.mark.parametrize("thread_id", ["thread-a", None])
def test_active_task_without_explicit_root_cannot_bypass_busy_check(bound, thread_id):
    target = bound.write()
    bound.store.upsert(
        bound.record.model_copy(
            update={
                "task_id": "other",
                "owner_id": "bob",
                "status": TaskRunStatus.RUNNING,
                "workspace_path": None,
                "thread_id": thread_id,
            }
        )
    )
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 409
    assert target.read_text() == "after"


def test_active_task_in_independent_workspace_does_not_block(bound, tmp_path):
    target = bound.write()
    other_root = tmp_path / "independent"
    other_root.mkdir()
    bound.store.upsert(
        bound.record.model_copy(
            update={
                "task_id": "other",
                "owner_id": "bob",
                "status": TaskRunStatus.RUNNING,
                "workspace_path": str(other_root),
            }
        )
    )
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 200
    assert target.read_text() == "before"


def test_outside_event_prevents_all_writes(bound, tmp_path):
    first = bound.write()
    outside = tmp_path / "outside.txt"
    outside.write_text("after")
    bound.write("other.txt", event_path=str(outside))
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 403
    assert first.read_text() == outside.read_text() == "after"


def test_unbound_task_denied_and_invisible_selector_empty(bound):
    target = bound.write()
    bound.store.upsert(bound.record.model_copy(update={"thread_id": None}))
    assert (
        bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id}).status_code
        == 403
    )
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": str(uuid4())})
    assert response.status_code == 200
    assert response.json()["state"] == "empty"
    assert response.json()["project_root"] is None
    assert target.read_text() == "after"


def test_auth_is_required_and_member_cannot_apply(bound):
    target = bound.write()
    client = TestClient(bound.app)
    assert (
        client.post("/api/files/rollback/apply", json={"task_id": bound.task_id}).status_code == 401
    )
    assert (
        client.post(
            "/api/files/rollback/apply",
            json={"task_id": bound.task_id},
            headers={"Authorization": "Bearer member-test-token"},
        ).status_code
        == 403
    )
    assert target.read_text() == "after"


def test_conflict_does_not_hide_independent_success(bound):
    restored = bound.write()
    conflict = bound.write("edited.txt", actual="user edit")
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["state"] == "partial"
    assert result["execution_complete"] is False
    assert result["applied"] == result["skipped"] == 1
    assert restored.read_text() == "before"
    assert conflict.read_text() == "user edit"


@pytest.mark.parametrize(
    "field,value", [("iteration", True), ("iteration", "1"), ("dry_run", "false"), ("dry_run", 0)]
)
def test_rewind_never_coerces_execute_flags(bound, field, value):
    target = bound.write()
    response = bound.client.post(
        f"/api/tasks/{bound.task_id}/rewind/apply", json={"iteration": 0, field: value}
    )
    assert response.status_code == 400
    assert target.read_text() == "after"


def test_rewind_dry_run_and_apply_real_same_timestamp_checkpoint(bound):
    with journal_context(tenant_id="tenant-a", owner_actor_id="alice"):
        bound.journal.write_react_checkpoint(
            TaskId(bound.task_id),
            iteration_completed=0,
            max_iterations=2,
            messages_snapshot=[],
            steps_snapshot=[],
        )
    # Windows can assign equal times to sequential writes. Order remains exact.
    target = bound.write(ts=bound.journal.read_by_type("react_checkpoint")[0].ts)
    url = f"/api/tasks/{bound.task_id}/rewind/apply"
    preview = bound.client.post(url, json={"iteration": 0, "dry_run": True})
    assert preview.status_code == 200, preview.text
    assert preview.json()["file_rollback"]["state"] == "preview_ready"
    assert preview.json()["history_complete"] is True
    assert target.read_text() == "after"
    applied = bound.client.post(url, json={"iteration": 0})
    assert applied.status_code == 200, applied.text
    assert applied.json()["execution_complete"] is True
    assert target.read_text() == "before"


def test_concurrent_rollback_and_rewind_rejected_before_mutation(bound, monkeypatch):
    from runtime.memory.runtime_state import file_transactions

    entered, release = threading.Event(), threading.Event()
    original = file_transactions.apply_file_rollback_ledger
    target = bound.write()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10), "test failed to release rollback"
        return original(*args, **kwargs)

    monkeypatch.setattr(file_transactions, "apply_file_rollback_ledger", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(
            bound.client.post, "/api/files/rollback/apply", json={"task_id": bound.task_id}
        )
        try:
            assert entered.wait(10)
            response = bound.client.post(
                f"/api/tasks/{bound.task_id}/rewind/apply", json={"iteration": 0}
            )
            assert response.status_code == 409
            assert response.json()["detail"]["error"] == "rollback_in_progress"
            assert target.read_text() == "after"
        finally:
            release.set()
        assert pending.result(timeout=10).status_code == 200
    assert target.read_text() == "before"


def test_audit_failure_preserves_known_commit_result(bound, monkeypatch):
    target = bound.write()

    def broken_audit(event):
        raise OSError("synthetic audit storage unavailable")

    monkeypatch.setattr(bound.journal, "write", broken_audit)
    response = bound.client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 200
    assert response.json()["applied"] == 1
    assert response.json()["audit_recorded"] is False
    assert response.json()["state"] == "partial"
    assert response.json()["execution_complete"] is False
    assert response.json()["errors"] == ["rollback_audit_unavailable"]
    assert target.read_text() == "before"


def test_server_managed_workspace_required_in_shared_mode(bound, tmp_path):
    from pathlib import Path

    from runtime.platform.runtime_policy.workspaces import managed_workspace_metadata

    metadata = bound.threads["thread-a"]["metadata"]
    shared_root = tmp_path / "managed"
    managed = managed_workspace_metadata(
        shared_root, tenant_id="tenant-a", actor_id="alice", thread_id="thread-a"
    )
    root = Path(managed["workspace_path"])
    root.mkdir(parents=True)
    target = root / "state.txt"
    target.write_text("after")
    metadata.update(managed)
    bound.store.upsert(bound.record.model_copy(update={"workspace_path": str(root)}))
    bound.write(event_path=str(target))
    app = FastAPI()
    # Use the same persisted authority with the actual shared-mode root verifier.
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", roles=("operator",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="shared-token",
    )
    app.include_router(
        create_observability_router(
            journal=bound.journal,
            registry=SkillRegistry(),
            identity_store=identities,
            require_auth=True,
            thread_store=bound.threads,
            workspace_root=shared_root,
            task_supervisor=SimpleNamespace(store=bound.store),
        )
    )
    client = TestClient(app, headers={"Authorization": "Bearer shared-token"})
    assert (
        client.get("/api/files/rollback/preview", params={"task_id": bound.task_id}).status_code
        == 200
    )
    metadata["workspace_path"] = str(
        bound.root
    )  # A marker alone cannot authorize another host directory.
    assert (
        client.post("/api/files/rollback/apply", json={"task_id": bound.task_id}).status_code == 403
    )
    assert target.read_text() == "after"
    metadata.update(managed)
    response = client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 200, response.text
    assert target.read_text() == "before"


def test_standalone_root_must_be_server_configured(bound, tmp_path):
    target = bound.write()
    app = FastAPI()
    allowed = tmp_path / "configured"
    allowed.mkdir()
    app.include_router(
        create_observability_router(
            journal=bound.journal, registry=SkillRegistry(), workspace_root=allowed
        )
    )
    response = TestClient(app).post(
        "/api/files/rollback/apply",
        json={"task_id": bound.task_id, "project_root": str(bound.root)},
    )
    assert response.status_code == 403
    assert target.read_text() == "after"


@pytest.mark.parametrize("uncertain", [False, True])
def test_durable_history_preserves_commit_and_recovery_evidence(
    bound, tmp_path, monkeypatch, uncertain
):
    from runtime.memory.journal import JSONLJournal
    from runtime.memory.runtime_state import _file_rollback_io as rollback_io

    target = bound.write()
    journal_path = tmp_path / "journal.jsonl"
    journal = JSONLJournal(journal_path)
    for event in bound.journal.read_all():
        journal.write(event)
    backup = bound.root / ".rollback-recovery.txt"
    original = rollback_io.atomic_restore_text

    def commit_then_fail(*args, **kwargs):
        backup.write_bytes(target.read_bytes())
        original(*args, **kwargs)
        error = OSError("synthetic finalization failure")
        error.rollback_committed = not uncertain
        error.rollback_commit_uncertain = uncertain
        error.rollback_backup_path = backup
        error.rollback_evidence_private = False
        raise error

    monkeypatch.setattr(rollback_io, "atomic_restore_text", commit_then_fail)

    def client_for(current_journal):
        app = FastAPI()
        app.include_router(
            create_observability_router(
                journal=current_journal,
                registry=SkillRegistry(),
                identity_store=bound.identities,
                require_auth=True,
                thread_store=bound.threads,
                workspace_root=bound.root,
                allow_local_workspace_access=True,
                task_supervisor=SimpleNamespace(store=bound.store),
            )
        )
        return TestClient(app, headers={"Authorization": "Bearer alice-test-token"})

    response = client_for(journal).post(
        "/api/files/rollback/apply", json={"task_id": bound.task_id}
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["state"] == ("uncertain" if uncertain else "partial")
    assert payload["execution_complete"] is False
    assert payload["audit_recorded"] is True
    assert target.read_text() == "before"
    assert backup.read_text() == "after"
    history = client_for(JSONLJournal(journal_path)).get("/api/files/rollback/history")
    assert history.status_code == 200
    outcome = history.json()["events"][0]["outcomes"][0]
    assert outcome == payload["outcomes"][0]
    assert outcome["committed"] is (None if uncertain else True)
    assert outcome["recovery_paths"] == [str(backup)]
    assert outcome["evidence_private"] is False
    assert '"content":' not in history.text


def test_durable_thread_authority_rechecks_other_worker_changes(bound, tmp_path):
    from runtime.memory.threads import ThreadStateStore

    target = bound.write()
    path = tmp_path / "threads.jsonl"
    threads = ThreadStateStore(path)
    threads.ensure_thread("thread-a", metadata=bound.threads["thread-a"]["metadata"])
    app = FastAPI()
    app.include_router(
        create_observability_router(
            journal=bound.journal,
            registry=SkillRegistry(),
            identity_store=bound.identities,
            require_auth=True,
            thread_store=threads,
            workspace_root=bound.root,
            allow_local_workspace_access=True,
            task_supervisor=SimpleNamespace(store=bound.store),
        )
    )
    client = TestClient(app, headers={"Authorization": "Bearer alice-test-token"})
    assert (
        client.get("/api/files/rollback/preview", params={"task_id": bound.task_id}).status_code
        == 200
    )
    other_worker = ThreadStateStore(path)
    other_worker.update_state("thread-a", metadata={"owner_actor_id": "bob"})
    assert (
        threads.get("thread-a")["metadata"]["owner_actor_id"] == "alice"
    )  # stale in-memory snapshot
    response = client.post("/api/files/rollback/apply", json={"task_id": bound.task_id})
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "rollback_workspace_access_revoked"
    assert target.read_text() == "after"
