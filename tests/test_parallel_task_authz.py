"""Authenticated boundary regression tests for the legacy parallel task API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.misc.parallel_runner import (
    ParallelTask,
    ParallelTaskRunner,
    create_parallel_task_router,
)
from runtime.memory.threads import ThreadStateStore
from runtime.platform.runtime_policy.workspaces import managed_workspace_metadata
from runtime.safety.auth import Identity, IdentityStore


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_thread(
    store: ThreadStateStore,
    workspace_root: Path,
    *,
    thread_id: str,
    actor_id: str,
    tenant_id: str,
) -> Path:
    allocation = managed_workspace_metadata(
        workspace_root,
        thread_id=thread_id,
        actor_id=actor_id,
        tenant_id=tenant_id,
    )
    workspace = Path(allocation["workspace_path"])
    workspace.mkdir(parents=True)
    store.ensure_thread(
        thread_id,
        metadata={
            "owner_actor_id": actor_id,
            "tenant_id": tenant_id,
            **allocation,
        },
    )
    return workspace


def _secured_client(
    tmp_path: Path,
    monkeypatch: Any,
) -> tuple[
    TestClient,
    dict[str, str],
    dict[str, Path],
    list[ParallelTask],
    list[ParallelTaskRunner],
]:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-alice",
    )
    identities.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-bob",
    )
    store = ThreadStateStore()
    workspace_root = tmp_path / "managed"
    workspaces = {
        "alice": _add_thread(
            store,
            workspace_root,
            thread_id="thread-alice",
            actor_id="alice",
            tenant_id="tenant-a",
        ),
        "bob": _add_thread(
            store,
            workspace_root,
            thread_id="thread-bob",
            actor_id="bob",
            tenant_id="tenant-b",
        ),
    }
    captured: list[ParallelTask] = []
    runners: list[ParallelTaskRunner] = []

    def _capture_submit(self: ParallelTaskRunner, task: ParallelTask) -> ParallelTask:
        from runtime.safety.approval.cancellation import CancellationSource

        if self not in runners:
            runners.append(self)
        with self._lock:
            self._tasks[task.id] = task
            self._sources[task.id] = CancellationSource()
        captured.append(task)
        return task

    monkeypatch.setattr(ParallelTaskRunner, "submit", _capture_submit)
    app = FastAPI()
    app.include_router(
        create_parallel_task_router(
            stack=object(),
            thread_store=store,
            workspace_root=workspace_root,
            identity_store=identities,
            require_auth=True,
        )
    )
    return (
        TestClient(app),
        {"alice": "sk-alice", "bob": "sk-bob"},
        workspaces,
        captured,
        runners,
    )


def test_parallel_submit_requires_owned_managed_thread(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys, _workspaces, captured, _runners = _secured_client(tmp_path, monkeypatch)
    body = {"prompt": "inspect", "thread_id": "thread-alice"}

    assert client.post("/api/tasks/submit", json=body).status_code == 401
    assert (
        client.post(
            "/api/tasks/submit",
            json={"prompt": "inspect"},
            headers=_headers(keys["alice"]),
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/tasks/submit",
            json=body,
            headers=_headers(keys["bob"]),
        ).status_code
        == 404
    )
    assert captured == []


def test_parallel_submit_overwrites_path_identity_tool_and_approval_injection(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys, workspaces, captured, _runners = _secured_client(tmp_path, monkeypatch)
    escaped = tmp_path / "escaped"

    response = client.post(
        "/api/tasks/submit",
        headers=_headers(keys["alice"]),
        json={
            "prompt": "inspect",
            "thread_id": "thread-alice",
            "workspace_path": str(escaped),
            "context": {
                "workspace_path": str(escaped),
                "extra_workspaces": ["/"],
                "owner_actor_id": "bob",
                "tenant_id": "tenant-b",
                "tool_allowlist_mode": "all",
                "extra_tools": ["exec_shell"],
                "auto_approve": True,
                "permission_mode": "bypassPermissions",
                "approvalPolicy": "never",
                "execution_environment": "local",
                "metadata": {
                    "workspace_path": "/",
                    "extra_workspaces": ["/"],
                    "actor_id": "bob",
                    "tenant_id": "tenant-b",
                    "auto_approve": True,
                    "approval_policy": "never",
                },
            },
        },
    )

    assert response.status_code == 200
    task = captured[-1]
    assert task.workspace_path == str(workspaces["alice"])
    assert task.owner_actor_id == "alice"
    assert task.tenant_id == "tenant-a"
    assert task.context == {"metadata": {}}
    assert response.json()["workspace_path"] == str(workspaces["alice"])
    assert not escaped.exists()


def test_parallel_worker_reapplies_server_authority_inside_session(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys, workspaces, captured, runners = _secured_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/tasks/submit",
        headers=_headers(keys["alice"]),
        json={"prompt": "inspect", "thread_id": "thread-alice"},
    )
    assert response.status_code == 200

    seen: dict[str, Any] = {}

    def _fake_react_loop(*, intent: Any, **_kwargs: Any) -> str:
        from runtime.platform.process.session import current_session

        seen["context"] = intent.user_context
        seen["session"] = current_session()
        return "done"

    monkeypatch.setattr(
        "runtime.core.cerebrum.react_loop.run_react_loop",
        _fake_react_loop,
    )
    runner = runners[-1]
    task = captured[-1]
    runner._run_task(task.id)

    context = seen["context"]
    session = seen["session"]
    assert context["workspace_path"] == str(workspaces["alice"])
    assert context["extra_workspaces"] == []
    assert context["auto_approve"] is False
    assert context["permission_mode"] == "default"
    assert context["approval_policy"] == "on-request"
    assert context["execution_environment"] == "sandbox"
    assert session.actor == "alice"
    assert session.thread_id == "thread-alice"
    assert session.metadata["tenant_id"] == "tenant-a"


def test_parallel_task_status_and_cancel_are_principal_scoped(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    client, keys, _workspaces, captured, _runners = _secured_client(tmp_path, monkeypatch)
    for actor in ("alice", "bob"):
        response = client.post(
            "/api/tasks/submit",
            headers=_headers(keys[actor]),
            json={"prompt": actor, "thread_id": f"thread-{actor}"},
        )
        assert response.status_code == 200

    alice_task, bob_task = captured
    alice_list = client.get("/api/tasks", headers=_headers(keys["alice"]))
    bob_list = client.get("/api/tasks", headers=_headers(keys["bob"]))
    assert [item["id"] for item in alice_list.json()["tasks"]] == [alice_task.id]
    assert [item["id"] for item in bob_list.json()["tasks"]] == [bob_task.id]
    assert (
        client.get(
            f"/api/tasks/{alice_task.id}",
            headers=_headers(keys["bob"]),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/tasks/{alice_task.id}/cancel",
            headers=_headers(keys["bob"]),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/tasks/{alice_task.id}/cancel",
            headers=_headers(keys["alice"]),
        ).json()["cancelled"]
        is True
    )


def test_parallel_tasks_keep_anonymous_local_compatibility(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    captured: list[ParallelTask] = []

    def _capture_submit(self: ParallelTaskRunner, task: ParallelTask) -> ParallelTask:
        from runtime.safety.approval.cancellation import CancellationSource

        with self._lock:
            self._tasks[task.id] = task
            self._sources[task.id] = CancellationSource()
        captured.append(task)
        return task

    monkeypatch.setattr(ParallelTaskRunner, "submit", _capture_submit)
    app = FastAPI()
    app.include_router(create_parallel_task_router(stack=object()))
    client = TestClient(app)
    workspace = tmp_path / "local-workspace"

    response = client.post(
        "/api/tasks/submit",
        json={
            "prompt": "local",
            "workspace_path": str(workspace),
            "context": {"auto_approve": True, "extra_workspaces": [str(tmp_path)]},
        },
    )

    assert response.status_code == 200
    assert captured[-1].workspace_path == str(workspace)
    assert captured[-1].context["extra_workspaces"] == [str(tmp_path)]
    assert client.get("/api/tasks").status_code == 200

