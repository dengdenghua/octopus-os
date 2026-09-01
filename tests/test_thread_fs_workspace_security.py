"""Cross-router security tests for authenticated thread filesystem scope."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.threads import ThreadStateStore
from runtime.memory.threads.event_log import EventLog, list_threads
from runtime.platform.models import ParsedIntent
from runtime.platform.ui.app import create_app
from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway._realtime_cerebrum_thread import (
    _snapshot_to_thread_store,
)
from runtime.sensing.gateway.fs_router import create_fs_router
from runtime.sensing.gateway.thread_state_router import create_thread_state_router
from runtime.sensing.gateway.thread_workspace import (
    MANAGED_WORKSPACE_DELETION_KEY,
    MANAGED_WORKSPACE_DELETION_MARKER,
    MANAGED_WORKSPACE_MARKER,
    MANAGED_WORKSPACE_METADATA_KEY,
    ensure_managed_thread_workspace,
    managed_workspace_metadata,
    managed_workspace_path,
)


@pytest.mark.parametrize("thread_id", ["../escape", "/absolute", "nested/thread"])
def test_managed_workspace_rejects_unsafe_thread_path_segments(
    tmp_path: Path,
    thread_id: str,
) -> None:
    with pytest.raises(ValueError, match="thread id"):
        managed_workspace_path(
            tmp_path,
            tenant_id="tenant-a",
            actor_id="alice",
            thread_id=thread_id,
        )


def _authenticated_client(
    tmp_path: Path,
    *,
    logs_root: Path | None = None,
) -> tuple[TestClient, ThreadStateStore, Path, dict[str, str]]:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    store = ThreadStateStore()
    workspace_root = tmp_path / "managed"
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=store,
            logs_root=logs_root,
            identity_store=identities,
            require_auth=True,
            workspace_root=workspace_root,
        )
    )
    app.include_router(
        create_fs_router(
            thread_store=store,
            identity_store=identities,
            require_auth=True,
            workspace_root=workspace_root,
        )
    )
    return (
        TestClient(app),
        store,
        workspace_root,
        {"Authorization": "Bearer sk-alice"},
    )


class _FailOnceUpdateStore(ThreadStateStore):
    def __init__(self, *, commit_before_raise: bool = False) -> None:
        super().__init__()
        self.fail_updates = 0
        self.commit_before_raise = commit_before_raise
        self.created_ids: list[str] = []

    def create(self, **kwargs):
        created = super().create(**kwargs)
        self.created_ids.append(created["thread_id"])
        return created

    def fork_thread(self, thread_id: str, **kwargs):
        created = super().fork_thread(thread_id, **kwargs)
        self.created_ids.append(created["thread_id"])
        return created

    def update_state(self, thread_id: str, **kwargs):
        if self.fail_updates > 0:
            self.fail_updates -= 1
            if self.commit_before_raise:
                super().update_state(thread_id, **kwargs)
            raise OSError("injected workspace persistence failure")
        return super().update_state(thread_id, **kwargs)


def _auth_thread_client(
    tmp_path: Path,
    store: ThreadStateStore,
) -> tuple[TestClient, Path, dict[str, str]]:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    workspace_root = tmp_path / "managed"
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=store,
            identity_store=identities,
            require_auth=True,
            workspace_root=workspace_root,
        )
    )
    return TestClient(app), workspace_root, {"Authorization": "Bearer sk-alice"}


def test_http_thread_allocation_failure_rolls_back_row_and_directory(
    tmp_path: Path,
) -> None:
    store = _FailOnceUpdateStore()
    store.fail_updates = 1
    client, workspace_root, headers = _auth_thread_client(tmp_path, store)

    failed = client.post("/api/threads", headers=headers)
    assert failed.status_code == 503
    failed_id = store.created_ids[-1]
    failed_workspace = managed_workspace_path(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id=failed_id,
    )
    assert store.get(failed_id) is None
    assert not failed_workspace.exists()

    retried = client.post("/api/threads", headers=headers)
    assert retried.status_code == 200, retried.json()
    retried_workspace = Path(retried.json()["metadata"]["workspace_path"])
    assert retried_workspace.is_dir()


def test_http_fork_allocation_failure_rolls_back_child_and_directory(
    tmp_path: Path,
) -> None:
    store = _FailOnceUpdateStore()
    client, workspace_root, headers = _auth_thread_client(tmp_path, store)
    parent = client.post("/api/threads", headers=headers)
    assert parent.status_code == 200, parent.json()
    parent_id = parent.json()["thread_id"]
    store.fail_updates = 1

    failed = client.post(f"/api/threads/{parent_id}/fork", headers=headers)
    assert failed.status_code == 503
    child_id = store.created_ids[-1]
    child_workspace = managed_workspace_path(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id=child_id,
    )
    assert store.get(parent_id) is not None
    assert store.get(child_id) is None
    assert not child_workspace.exists()


def test_realtime_allocation_failure_is_retryable_for_existing_thread(
    tmp_path: Path,
) -> None:
    store = _FailOnceUpdateStore()
    store.ensure_thread(
        "retry-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    store.fail_updates = 1
    workspace_root = tmp_path / "managed"
    expected = managed_workspace_path(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="retry-thread",
    )

    with pytest.raises(RuntimeError, match="persistence failed"):
        ensure_managed_thread_workspace(
            workspace_root,
            thread_id="retry-thread",
            actor_id="alice",
            tenant_id="tenant-a",
            store=store,
        )
    assert store.get("retry-thread") is not None
    assert not expected.exists()

    allocated = ensure_managed_thread_workspace(
        workspace_root,
        thread_id="retry-thread",
        actor_id="alice",
        tenant_id="tenant-a",
        store=store,
    )
    assert allocated == expected
    assert allocated.is_dir()


def test_realtime_allocation_accepts_commit_before_adapter_error(tmp_path: Path) -> None:
    store = _FailOnceUpdateStore(commit_before_raise=True)
    store.ensure_thread(
        "committed-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    store.fail_updates = 1
    workspace_root = tmp_path / "managed"

    allocated = ensure_managed_thread_workspace(
        workspace_root,
        thread_id="committed-thread",
        actor_id="alice",
        tenant_id="tenant-a",
        store=store,
    )

    assert allocated.is_dir()
    assert store.get("committed-thread")["metadata"][MANAGED_WORKSPACE_METADATA_KEY] == (
        MANAGED_WORKSPACE_MARKER
    )


def test_realtime_compensation_never_recursively_deletes_taken_over_directory(
    tmp_path: Path,
) -> None:
    class _TakenOverStore(ThreadStateStore):
        def update_state(self, thread_id: str, **kwargs):
            workspace = Path(kwargs["metadata"]["workspace_path"])
            (workspace / "claimed.txt").write_text("keep", encoding="utf-8")
            raise OSError("injected failure after directory takeover")

    store = _TakenOverStore()
    store.ensure_thread(
        "taken-over-thread",
        metadata={"owner_actor_id": "alice", "tenant_id": "tenant-a"},
    )
    workspace_root = tmp_path / "managed"
    with pytest.raises(RuntimeError, match="persistence failed"):
        ensure_managed_thread_workspace(
            workspace_root,
            thread_id="taken-over-thread",
            actor_id="alice",
            tenant_id="tenant-a",
            store=store,
        )
    expected = managed_workspace_path(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="taken-over-thread",
    )
    assert (expected / "claimed.txt").read_text(encoding="utf-8") == "keep"


def test_authenticated_thread_ignores_client_workspace_and_fs_uses_server_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    # Deliberately make tmp_path a process-wide allowed root. Authenticated FS
    # must still use only the narrower server allocation.
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(tmp_path))
    client, store, workspace_root, headers = _authenticated_client(tmp_path)
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")

    created = client.post(
        "/api/threads",
        headers=headers,
        json={
            "metadata": {
                "workspace_path": str(tmp_path),
                "extra_workspaces": [str(tmp_path)],
                MANAGED_WORKSPACE_METADATA_KEY: MANAGED_WORKSPACE_MARKER,
                "label": "kept",
            }
        },
    )
    assert created.status_code == 200, created.json()
    thread = created.json()
    thread_id = thread["thread_id"]
    metadata = thread["metadata"]
    managed = Path(metadata["workspace_path"])
    assert managed.is_dir()
    assert managed.is_relative_to(workspace_root)
    assert metadata[MANAGED_WORKSPACE_METADATA_KEY] == MANAGED_WORKSPACE_MARKER
    assert metadata["label"] == "kept"
    assert "extra_workspaces" not in metadata

    target = managed / "notes" / "ok.txt"
    written = client.post(
        "/api/fs/write",
        headers=headers,
        json={"thread_id": thread_id, "path": str(target), "content": "ok"},
    )
    assert written.status_code == 200, written.json()
    assert target.read_text(encoding="utf-8") == "ok"

    blocked = client.get(
        "/api/fs/read",
        headers=headers,
        params={"thread_id": thread_id, "path": str(outside)},
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["error"] == "path_outside_workspace"

    spoof = client.post(
        f"/api/threads/{thread_id}/state",
        headers=headers,
        json={
            "metadata": {
                "workspace_path": str(tmp_path),
                "extra_workspaces": [str(tmp_path)],
                MANAGED_WORKSPACE_METADATA_KEY: MANAGED_WORKSPACE_MARKER,
            }
        },
    )
    assert spoof.status_code == 200
    persisted = store.get(thread_id)["metadata"]
    assert persisted["workspace_path"] == str(managed)
    assert "extra_workspaces" not in persisted

    # The realtime snapshot bridge also consumes per-turn client context. It
    # must preserve the HTTP router's allocation and server-owned identity.
    _snapshot_to_thread_store(
        SimpleNamespace(_thread_store=store),
        thread_id,
        SimpleNamespace(replay=lambda: []),
        ParsedIntent(
            raw="spoof workspace",
            intent_type="task",
            normalized_goal="spoof workspace",
            user_context={
                "workspace_path": str(tmp_path),
                "owner_actor_id": "mallory",
            },
        ),
    )
    persisted = store.get(thread_id)["metadata"]
    assert persisted["workspace_path"] == str(managed)
    assert persisted["owner_actor_id"] == "alice"

    forked = client.post(f"/api/threads/{thread_id}/fork", headers=headers)
    assert forked.status_code == 200, forked.json()
    child_id = forked.json()["thread_id"]
    child_workspace = Path(store.get(child_id)["metadata"]["workspace_path"])
    assert child_workspace.is_dir()
    assert child_workspace != managed
    parent_file_from_child = client.get(
        "/api/fs/read",
        headers=headers,
        params={"thread_id": child_id, "path": str(target)},
    )
    assert parent_file_from_child.status_code == 403


def test_authenticated_thread_delete_removes_verified_managed_workspace(
    tmp_path: Path,
) -> None:
    logs_root = tmp_path / "thread-logs"
    client, store, _workspace_root, headers = _authenticated_client(
        tmp_path,
        logs_root=logs_root,
    )
    created = client.post("/api/threads", headers=headers)
    assert created.status_code == 200, created.json()
    thread_id = created.json()["thread_id"]
    workspace = Path(created.json()["metadata"]["workspace_path"])
    (workspace / "keep-until-delete.txt").write_text("managed", encoding="utf-8")
    EventLog(logs_root / f"{thread_id}.jsonl").thread_started(thread_id)

    deleted = client.delete(f"/api/threads/{thread_id}", headers=headers)

    assert deleted.status_code == 204, deleted.text
    from runtime.memory.threads import ThreadPermanentlyDeletedError

    with pytest.raises(ThreadPermanentlyDeletedError):
        store.get(thread_id)
    assert not workspace.exists()
    assert list_threads(logs_root)[0].archived is True


def test_authenticated_thread_delete_never_removes_forged_or_symlinked_path(
    tmp_path: Path,
) -> None:
    client, store, workspace_root, headers = _authenticated_client(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.txt"
    sentinel.write_text("do not delete", encoding="utf-8")

    store.ensure_thread(
        "forged-delete",
        metadata={
            "owner_actor_id": "alice",
            "tenant_id": "tenant-a",
            "workspace_path": str(outside),
            MANAGED_WORKSPACE_METADATA_KEY: MANAGED_WORKSPACE_MARKER,
        },
    )
    forged = client.delete("/api/threads/forged-delete", headers=headers)
    assert forged.status_code == 409
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert store.get("forged-delete") is not None

    allocation = managed_workspace_metadata(
        workspace_root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="symlink-delete",
    )
    symlink_workspace = Path(allocation["workspace_path"])
    symlink_workspace.parent.mkdir(parents=True)
    symlink_workspace.symlink_to(outside, target_is_directory=True)
    store.ensure_thread(
        "symlink-delete",
        metadata={
            **allocation,
            "owner_actor_id": "alice",
            "tenant_id": "tenant-a",
        },
    )

    symlinked = client.delete("/api/threads/symlink-delete", headers=headers)
    assert symlinked.status_code == 409
    assert symlink_workspace.is_symlink()
    assert sentinel.read_text(encoding="utf-8") == "do not delete"
    assert store.get("symlink-delete") is not None


def test_authenticated_thread_workspace_cleanup_failure_is_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from runtime.sensing.gateway import thread_workspace as workspace_module

    client, store, workspace_root, headers = _authenticated_client(tmp_path)
    created = client.post("/api/threads", headers=headers)
    assert created.status_code == 200, created.json()
    thread_id = created.json()["thread_id"]
    workspace = Path(created.json()["metadata"]["workspace_path"])
    (workspace / "retry.txt").write_text("retry", encoding="utf-8")

    real_rmtree = workspace_module.shutil.rmtree
    failures = 1

    def _fail_once(path, *args, **kwargs):
        nonlocal failures
        if failures:
            failures -= 1
            raise OSError("injected cleanup failure")
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(workspace_module.shutil, "rmtree", _fail_once)
    failed = client.delete(f"/api/threads/{thread_id}", headers=headers)

    assert failed.status_code == 503
    lease = store.thread_delete_lease(thread_id)
    assert lease is not None
    persisted = store.thread_for_permanent_delete(thread_id, lease.token)
    assert persisted is not None
    assert persisted["metadata"][MANAGED_WORKSPACE_DELETION_KEY] == (
        MANAGED_WORKSPACE_DELETION_MARKER
    )
    assert not workspace.exists()
    staged_payloads = list((workspace_root / ".trash").glob("*/*/*/workspace"))
    assert len(staged_payloads) == 1
    assert (staged_payloads[0] / "retry.txt").read_text(encoding="utf-8") == "retry"
    blocked_recreate = client.post(
        "/api/fs/write",
        headers=headers,
        json={
            "thread_id": thread_id,
            "path": str(workspace / "recreated.txt"),
            "content": "must not return",
        },
    )
    assert blocked_recreate.status_code == 403
    assert not workspace.exists()

    retried = client.delete(f"/api/threads/{thread_id}", headers=headers)
    assert retried.status_code == 204, retried.text
    from runtime.memory.threads import ThreadPermanentlyDeletedError

    with pytest.raises(ThreadPermanentlyDeletedError):
        store.get(thread_id)
    assert not workspace.exists()
    assert not list((workspace_root / ".trash").glob("*/*/*/workspace"))


def test_authenticated_thread_state_delete_failure_is_retryable(tmp_path: Path) -> None:
    class _FailOnceDeleteStore(ThreadStateStore):
        failures = 1

        def finalize_permanent_delete(self, thread_id: str, token: str) -> bool:
            if self.failures:
                self.failures -= 1
                raise OSError("injected durable delete failure")
            return super().finalize_permanent_delete(thread_id, token)

    store = _FailOnceDeleteStore()
    client, _workspace_root, headers = _auth_thread_client(tmp_path, store)
    created = client.post("/api/threads", headers=headers)
    assert created.status_code == 200, created.json()
    thread_id = created.json()["thread_id"]
    workspace = Path(created.json()["metadata"]["workspace_path"])
    (workspace / "artifact.txt").write_text("remove", encoding="utf-8")

    failed = client.delete(f"/api/threads/{thread_id}", headers=headers)
    assert failed.status_code == 503
    lease = store.thread_delete_lease(thread_id)
    assert lease is not None
    persisted = store.thread_for_permanent_delete(thread_id, lease.token)
    assert persisted is not None
    assert persisted["metadata"][MANAGED_WORKSPACE_DELETION_KEY] == (
        MANAGED_WORKSPACE_DELETION_MARKER
    )
    assert not workspace.exists()

    retried = client.delete(f"/api/threads/{thread_id}", headers=headers)
    assert retried.status_code == 204, retried.text
    from runtime.memory.threads import ThreadPermanentlyDeletedError

    with pytest.raises(ThreadPermanentlyDeletedError):
        store.get(thread_id)


def test_authenticated_fs_rejects_forged_managed_marker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(tmp_path))
    client, store, _workspace_root, headers = _authenticated_client(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    store.ensure_thread(
        "forged-thread",
        metadata={
            "owner_actor_id": "alice",
            "tenant_id": "tenant-a",
            "workspace_path": str(tmp_path),
            MANAGED_WORKSPACE_METADATA_KEY: MANAGED_WORKSPACE_MARKER,
        },
    )

    response = client.get(
        "/api/fs/read",
        headers=headers,
        params={"thread_id": "forged-thread", "path": str(secret)},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "managed_workspace_required"


def test_anonymous_local_thread_keeps_user_selected_workspace(tmp_path: Path) -> None:
    store = ThreadStateStore()
    app = FastAPI()
    app.include_router(create_thread_state_router(store=store))
    app.include_router(create_fs_router(thread_store=store))
    client = TestClient(app)
    local_file = tmp_path / "local.txt"
    local_file.write_text("local", encoding="utf-8")

    created = client.post(
        "/api/threads",
        json={"metadata": {"workspace_path": str(tmp_path)}},
    )
    assert created.status_code == 200
    thread_id = created.json()["thread_id"]
    read = client.get(
        "/api/fs/read",
        params={"thread_id": thread_id, "path": str(local_file)},
    )
    assert read.status_code == 200
    assert read.json()["content"] == "local"


def test_authenticated_loopback_thread_keeps_owned_user_workspace(tmp_path: Path) -> None:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-bob",
    )
    store = ThreadStateStore()
    app = FastAPI()
    app.include_router(
        create_thread_state_router(
            store=store,
            identity_store=identities,
            require_auth=True,
            allow_local_workspace_access=True,
        )
    )
    app.include_router(
        create_fs_router(
            thread_store=store,
            identity_store=identities,
            require_auth=True,
            allow_local_workspace_access=True,
        )
    )
    client = TestClient(app)
    local_file = tmp_path / "local-auth.txt"
    local_file.write_text("owned local workspace", encoding="utf-8")

    created = client.post(
        "/api/threads",
        headers={"Authorization": "Bearer sk-alice"},
        json={"metadata": {"workspace_path": str(tmp_path)}},
    )

    assert created.status_code == 200
    thread_id = created.json()["thread_id"]
    metadata = created.json()["metadata"]
    assert metadata["workspace_path"] == str(tmp_path)
    assert metadata["owner_actor_id"] == "alice"
    assert metadata["tenant_id"] == "tenant-a"
    assert MANAGED_WORKSPACE_METADATA_KEY not in metadata

    owned_read = client.get(
        "/api/fs/read",
        headers={"Authorization": "Bearer sk-alice"},
        params={"thread_id": thread_id, "path": str(local_file)},
    )
    foreign_read = client.get(
        "/api/fs/read",
        headers={"Authorization": "Bearer sk-bob"},
        params={"thread_id": thread_id, "path": str(local_file)},
    )

    assert owned_read.status_code == 200
    assert owned_read.json()["content"] == "owned local workspace"
    assert foreign_read.status_code == 404


def test_authenticated_loopback_realtime_preserves_selected_workspace(tmp_path: Path) -> None:
    from runtime.platform.runtime_policy.workspaces import WorkspaceManager
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.gateway.realtime_turn_input import _build_intent

    selected = tmp_path / "selected"
    selected.mkdir()
    manager = WorkspaceManager(tmp_path / "managed")
    store = ThreadStateStore()
    gateway = RealtimeGateway(
        runtime=SimpleNamespace(),
        allow_local_workspace_access=True,
    )
    raw = gateway._sanitize_turn_params(
        {
            "threadId": "local-realtime-thread",
            "cwd": str(selected),
            "input": [
                {
                    "type": "text",
                    "text": "inspect the project",
                    "metadata": {
                        "context": {
                            "mode": "code",
                            "workspace_path": str(selected),
                            "allowed_write_paths": ["/etc"],
                            "attachment_read_roots": ["/etc"],
                        }
                    },
                }
            ],
        },
        SimpleNamespace(actor_id="alice", tenant_id="tenant-a"),
    )
    params = TurnParams.model_validate(raw)

    intent = _build_intent(
        "inspect the project",
        params,
        workspaces=manager,
        thread_store=store,
        allow_local_workspace_access=True,
    )

    context = intent.user_context
    assert context["cwd"] == str(selected)
    assert context["workspace_path"] == str(selected)
    assert context["workspace_scope"] == "project"
    assert context["owner_actor_id"] == "alice"
    assert context["tenant_id"] == "tenant-a"
    assert "allowed_write_paths" not in context
    assert "attachment_read_roots" not in context
    assert store.get("local-realtime-thread") is None


def test_authenticated_loopback_realtime_recovers_context_only_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Current/legacy browsers may carry the picker result only in context.

    An authenticated loopback desktop selection is still authoritative for
    this local process, even when the selected repository is outside the
    backend checkout and process-wide anonymous filesystem roots.
    """

    from runtime.platform.runtime_policy.workspaces import WorkspaceManager
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.gateway.realtime_turn_input import _build_intent

    selected = tmp_path / "sibling-project"
    selected.mkdir()
    unrelated_allowed_root = tmp_path / "anonymous-root"
    unrelated_allowed_root.mkdir()
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(unrelated_allowed_root))

    manager = WorkspaceManager(tmp_path / "managed")
    store = ThreadStateStore()
    gateway = RealtimeGateway(
        runtime=SimpleNamespace(),
        allow_local_workspace_access=True,
    )
    raw = gateway._sanitize_turn_params(
        {
            "threadId": "context-workspace-thread",
            "input": [
                {
                    "type": "text",
                    "text": "inspect the selected project",
                    "metadata": {
                        "context": {
                            "mode": "code",
                            "workspace_path": str(selected),
                            "workspace_scope": "project",
                        }
                    },
                }
            ],
        },
        SimpleNamespace(actor_id="alice", tenant_id="tenant-a"),
    )

    intent = _build_intent(
        "inspect the selected project",
        TurnParams.model_validate(raw),
        workspaces=manager,
        thread_store=store,
        allow_local_workspace_access=True,
    )

    assert intent.user_context["cwd"] == str(selected.resolve())
    assert intent.user_context["workspace_path"] == str(selected.resolve())
    assert intent.user_context["workspace_scope"] == "project"


def test_authenticated_realtime_new_thread_ignores_all_client_path_authority(
    tmp_path: Path,
) -> None:
    from runtime.platform.runtime_policy.workspaces import WorkspaceManager
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_gateway import RealtimeGateway
    from runtime.sensing.gateway.realtime_turn_input import _build_intent

    store = ThreadStateStore()
    manager = WorkspaceManager(tmp_path / "managed")
    gateway = RealtimeGateway(runtime=SimpleNamespace())
    outside = tmp_path / "outside"
    raw = gateway._sanitize_turn_params(
        {
            "threadId": "realtime-new-thread",
            "cwd": str(outside),
            "input": [
                {
                    "type": "text",
                    "text": "write the artifact",
                    "attachments": [{"filename": "input.txt", "path": "/etc/passwd"}],
                    "metadata": {
                        "actor_id": "mallory",
                        "context": {
                            "mode": "code",
                            "workspace_path": str(outside),
                            "extra_workspaces": [str(tmp_path)],
                            "personal_workspace_path": str(tmp_path / "personal"),
                            "allowed_write_paths": [str(tmp_path)],
                            "attachment_read_roots": ["/etc"],
                            "_artifact_output_root": str(tmp_path / "public"),
                        },
                    },
                }
            ],
        },
        SimpleNamespace(actor_id="alice", tenant_id="tenant-a"),
    )
    params = TurnParams.model_validate(raw)

    intent = _build_intent(
        "write the artifact",
        params,
        workspaces=manager,
        thread_store=store,
    )

    expected = managed_workspace_path(
        manager.root,
        tenant_id="tenant-a",
        actor_id="alice",
        thread_id="realtime-new-thread",
    )
    context = intent.user_context
    assert context["cwd"] == str(expected)
    assert context["workspace_path"] == str(expected)
    assert context["owner_actor_id"] == "alice"
    assert context["tenant_id"] == "tenant-a"
    assert context["_artifact_output_root"] == str(expected / "output" / "final")
    assert context["attachment_read_roots"] == [str((expected / "upload").resolve())]
    assert "extra_workspaces" not in context
    assert "personal_workspace_path" not in context
    assert "allowed_write_paths" not in context
    assert manager.layout("realtime-new-thread").root == expected
    assert (expected / "upload").is_dir()
    assert (expected / "output" / "final").is_dir()

    persisted = store.get("realtime-new-thread")["metadata"]
    assert persisted["workspace_path"] == str(expected)
    assert persisted["owner_actor_id"] == "alice"
    assert persisted["tenant_id"] == "tenant-a"
    assert persisted[MANAGED_WORKSPACE_METADATA_KEY] == MANAGED_WORKSPACE_MARKER
    assert persisted["extra_workspaces"] == []


def test_authenticated_realtime_rejects_other_actors_thread_workspace(tmp_path: Path) -> None:
    from runtime.platform.runtime_policy.workspaces import WorkspaceManager
    from runtime.protocol import TurnParams
    from runtime.sensing.gateway.realtime_turn_input import _build_intent

    manager = WorkspaceManager(tmp_path / "managed")
    store = ThreadStateStore()
    allocation = managed_workspace_metadata(
        manager.root,
        tenant_id="tenant-a",
        actor_id="bob",
        thread_id="owned-thread",
    )
    Path(allocation["workspace_path"]).mkdir(parents=True)
    store.ensure_thread(
        "owned-thread",
        metadata={
            **allocation,
            "owner_actor_id": "bob",
            "tenant_id": "tenant-a",
        },
    )
    params = TurnParams.model_validate(
        {
            "threadId": "owned-thread",
            "input": [{"type": "text", "text": "steal"}],
            "owner_actor_id": "alice",
            "tenant_id": "tenant-a",
        }
    )

    with pytest.raises(PermissionError, match="ownership"):
        _build_intent(
            "steal",
            params,
            workspaces=manager,
            thread_store=store,
        )


def test_app_factory_wires_same_managed_root_to_thread_and_fs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHO_HOME", str(tmp_path / "runtime-home"))
    identities = IdentityStore()
    identities.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    stack = SimpleNamespace(
        journal=None,
        executor=SimpleNamespace(journal=None, registry=None),
        runtime=SimpleNamespace(journal=None),
        planner=SimpleNamespace(router=None, planner_model=None),
        config=SimpleNamespace(mcp_servers=None),
        is_llm_planner=False,
    )
    client = TestClient(
        create_app(
            stack=stack,
            cocoloop_identity_store=identities,
            cocoloop_require_auth=True,
        )
    )
    headers = {"Authorization": "Bearer sk-alice"}
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")

    created = client.post(
        "/api/threads",
        headers=headers,
        json={"metadata": {"workspace_path": str(tmp_path)}},
    )
    assert created.status_code == 200, created.json()
    thread = created.json()
    managed = Path(thread["metadata"]["workspace_path"])
    assert managed.is_dir()
    assert managed != tmp_path

    blocked = client.get(
        "/api/fs/read",
        headers=headers,
        params={"thread_id": thread["thread_id"], "path": str(outside)},
    )
    assert blocked.status_code == 403

    # The app factory must hand the same ThreadStateStore to the subagent bus
    # router; otherwise its new ownership check would fail closed for everyone.
    from runtime.execution.subagents.event_bus import (
        EVT_SUB_CONCLUDED,
        publish_subagent_event,
        reset_for_tests,
    )

    reset_for_tests()
    try:
        publish_subagent_event(
            EVT_SUB_CONCLUDED,
            {"role": "researcher", "ok": True, "output": "owned"},
            thread_id="child",
            root_thread_id=thread["thread_id"],
        )
        stream = client.get(
            f"/api/subagents/stream/{thread['thread_id']}",
            params={"limit": 1},
            headers=headers,
        )
        assert stream.status_code == 200
        assert "owned" in stream.text
    finally:
        reset_for_tests()

