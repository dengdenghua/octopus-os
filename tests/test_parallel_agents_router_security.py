"""Security regression tests for parallel_agents_router ownership enforcement.

These pin the ownership model added to ParallelAgentOrchestrator. Before
this fix, any authenticated user could read, cancel, or stream any other
user's batches/tasks. The orchestrator now stamps ``owner_id`` on each
batch, and endpoints enforce caller-owns-batch checks.

See parallel_agents_router.py ``_require_batch_owner`` + orchestrator.py
``_BatchEntry.owner_id``.
"""

from __future__ import annotations

import threading
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.execution.parallel_agents.orchestrator import (  # noqa: E402
    ParallelAgentOrchestrator,
)
from runtime.platform.process.session import (  # noqa: E402
    Session,
    current_session,  # noqa: E402
    session_scope,
)
from runtime.platform.process.task_supervisor import (  # noqa: E402
    TaskRunStatus,
    TaskSupervisor,
)
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.parallel_agents_router import (  # noqa: E402
    create_parallel_agents_router,
)


def _build_app() -> tuple[TestClient, dict[str, str]]:
    """Build app with require_auth=True + 3 identities (alice, bob, carol)."""
    store = IdentityStore()
    keys: dict[str, str] = {}
    for actor in ("alice", "bob", "carol"):
        api_key = f"sk-test-{actor}"
        store.add(Identity(actor_id=actor), api_key_plaintext=api_key)
        keys[actor] = api_key

    orchestrator = ParallelAgentOrchestrator()
    app = FastAPI()
    app.include_router(
        create_parallel_agents_router(
            orchestrator=orchestrator,
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    # Expose orchestrator so tests can directly check internal state
    client.orchestrator = orchestrator  # type: ignore[attr-defined]
    return client, keys


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _wait_for(predicate, *, timeout: float = 4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.01)
    return predicate()


def _dispatch_batch(
    client: TestClient,
    keys: dict[str, str],
    owner: str,
) -> str:
    """Dispatch a trivial batch and return batch_id."""
    resp = client.post(
        "/api/agents/parallel/dispatch",
        json={
            "tasks": [{"description": f"task by {owner}", "subagent_name": "general-purpose"}],
        },
        headers=_bearer(keys[owner]),
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()["batch_id"]


# ── dispatch stamps owner_id ────────────────────────────────────────


def test_dispatch_stamps_owner() -> None:
    """The orchestrator batch entry should have owner_id == actor."""
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")
    orch: ParallelAgentOrchestrator = client.orchestrator  # type: ignore[attr-defined]
    owner = orch.get_batch_owner(batch_id)
    assert owner == "alice"
    assert orch.get_batch_tenant(batch_id) == "legacy:alice"


def test_dispatch_worker_receives_host_execution_lease(tmp_path) -> None:
    store = IdentityStore()
    store.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-test-alice",
    )
    supervisor = TaskSupervisor.from_path(tmp_path / "task-runs.json", holder_id="parallel-worker")
    observed: list[dict[str, str | None]] = []
    ready = threading.Event()

    def _runner(description, *, subagent_name, context=None, cancel_event=None):
        session = current_session()
        assert session is not None
        request = session.execution_request
        assert request is not None
        observed.append(
            {
                "task_id": request.task.task_id,
                "thread_id": request.task.thread_id,
                "actor_id": request.task.actor_id,
                "tenant_id": request.task.tenant_id,
                "lease_task_id": (
                    session.execution_lease.assert_allowed().task_id
                    if session.execution_lease is not None
                    else None
                ),
            }
        )
        ready.set()
        return f"completed: {description}"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=_runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    app = FastAPI()
    app.include_router(
        create_parallel_agents_router(
            orchestrator=orchestrator,
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    try:
        response = client.post(
            "/api/agents/parallel/dispatch",
            headers=_bearer("sk-test-alice"),
            json={
                "thread_id": "parallel-thread",
                "tasks": [{"description": "host-bound parallel work"}],
            },
        )
        assert response.status_code == 200
        batch_id = response.json()["batch_id"]
        aggregate_task_id = response.json()["host_task_id"]
        assert aggregate_task_id == f"parallel-batch:{batch_id}"

        assert ready.wait(timeout=3.0)
        for _ in range(150):
            batch = client.get(
                f"/api/agents/parallel/batch/{batch_id}",
                headers=_bearer("sk-test-alice"),
            ).json()
            if batch["status"] == "completed":
                break
            time.sleep(0.02)
        assert batch["status"] == "completed"
        assert len(observed) == 1
        host_task_id = observed[0]["task_id"]
        assert host_task_id is not None
        assert observed[0] == {
            "task_id": host_task_id,
            "thread_id": "parallel-thread",
            "actor_id": "alice",
            "tenant_id": "tenant-a",
            "lease_task_id": host_task_id,
        }
        record = supervisor.store.get(host_task_id)
        assert record is not None
        assert record.status == TaskRunStatus.COMPLETED
        assert record.lease is None
        assert record.owner_id == "alice"
        assert record.parent_task_id == aggregate_task_id
        assert record.metadata["tenant_id"] == "tenant-a"
        assert record.kind == "parallel_agent"
        aggregate = supervisor.store.get(aggregate_task_id)
        assert aggregate is not None
        assert aggregate.status == TaskRunStatus.COMPLETED
        assert aggregate.lease is None
        assert aggregate.kind == "parallel_batch"
        assert aggregate.metadata["tenant_id"] == "tenant-a"
    finally:
        orchestrator.shutdown(wait=False)


def test_recovery_snapshot_route_reads_durable_host_after_restart(tmp_path) -> None:
    """The authenticated recovery endpoint survives an orchestrator restart."""
    store = IdentityStore()
    store.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-test-alice",
    )
    store.add(
        Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-test-bob",
    )
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-recovery-reader",
    )
    started = threading.Event()
    release = threading.Event()

    def runner(description, **_kwargs):
        started.set()
        release.wait(timeout=5.0)
        return f"done: {description}"

    running = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="auto",
    )
    restarted_supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-restarted-reader",
    )
    restarted = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=restarted_supervisor,
        worker_isolation="auto",
    )
    app = FastAPI()
    app.include_router(
        create_parallel_agents_router(
            orchestrator=restarted,
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    try:
        batch = running.dispatch(
            [{"task_id": "restart-route-task", "description": "restart route recovery"}],
            thread_id="restart-route-thread",
            owner_id="alice",
            tenant_id="tenant-a",
        )
        assert started.wait(timeout=3.0)

        recovered = client.get(
            f"/api/agents/parallel/batch/{batch.batch_id}/recovery-snapshot",
            headers=_bearer("sk-test-alice"),
        )
        assert recovered.status_code == 200, recovered.json()
        body = recovered.json()
        assert body["batch_id"] == batch.batch_id
        assert body["host_task_id"] == f"parallel-batch:{batch.batch_id}"
        assert body["safety"]["durable_only"] is True
        assert body["tasks"][0]["task_id"] == "restart-route-task"
        assert body["tasks"][0]["status"] == "running"

        listed = client.get(
            "/api/agents/parallel/recovery-snapshots",
            headers=_bearer("sk-test-alice"),
        )
        assert listed.status_code == 200, listed.json()
        assert listed.json()["schema"] == "echo.parallel_batch_recovery_snapshots.v1"
        assert [item["batch_id"] for item in listed.json()["snapshots"]] == [batch.batch_id]
        hidden = client.get(
            "/api/agents/parallel/recovery-snapshots",
            headers=_bearer("sk-test-bob"),
        )
        assert hidden.status_code == 200, hidden.json()
        assert hidden.json()["snapshots"] == []

        forbidden = client.get(
            f"/api/agents/parallel/batch/{batch.batch_id}/recovery-snapshot",
            headers=_bearer("sk-test-bob"),
        )
        assert forbidden.status_code == 403
    finally:
        release.set()
        running.shutdown(wait=True)
        restarted.shutdown(wait=True)


def test_explicit_recovery_route_creates_a_new_batch_for_named_lane(tmp_path) -> None:
    store = IdentityStore()
    store.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-test-alice",
    )
    supervisor = TaskSupervisor.from_path(
        tmp_path / "task-runs.json",
        holder_id="parallel-recovery-route",
    )
    attempts = 0

    def runner(description, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first run failed")
        return f"done: {description}"

    orchestrator = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="thread",
    )
    app = FastAPI()
    app.include_router(
        create_parallel_agents_router(
            orchestrator=orchestrator,
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)
    try:
        created = client.post(
            "/api/agents/parallel/dispatch",
            headers=_bearer("sk-test-alice"),
            json={"tasks": [{"task_id": "retry-lane", "description": "retry this"}]},
        )
        assert created.status_code == 200, created.json()
        source_id = created.json()["batch_id"]
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := orchestrator.get_batch(source_id)) is not None
                    and current.status == "failed"
                    else None
                )
            )
            is not None
        )

        resumed = client.post(
            f"/api/agents/parallel/batch/{source_id}/resume",
            headers=_bearer("sk-test-alice"),
            json={"task_ids": ["retry-lane"]},
        )
        assert resumed.status_code == 200, resumed.json()
        body = resumed.json()
        assert body["schema"] == "echo.parallel_batch_recovery_resume.v1"
        assert body["source_batch_id"] == source_id
        new_id = body["batch"]["batch_id"]
        assert new_id != source_id
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := orchestrator.get_batch(new_id)) is not None
                    and current.status == "completed"
                    else None
                )
            )
            is not None
        )

        forbidden = client.post(
            f"/api/agents/parallel/batch/{source_id}/resume",
            headers=_bearer("sk-test-alice"),
            json={"task_ids": ["missing"]},
        )
        assert forbidden.status_code == 400
    finally:
        orchestrator.shutdown(wait=True)


def test_explicit_recovery_route_survives_replaced_orchestrator(tmp_path) -> None:
    """The HTTP recovery action can use durable specs after a restart."""
    store = IdentityStore()
    store.add(
        Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-test-alice",
    )
    path = tmp_path / "task-runs.json"
    supervisor = TaskSupervisor.from_path(
        path,
        holder_id="parallel-recovery-route-source",
    )
    attempts = 0

    def runner(description, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first run failed")
        return f"done: {description}"

    source = ParallelAgentOrchestrator(
        task_runner=runner,
        task_supervisor=supervisor,
        worker_isolation="thread",
    )
    replacement = None
    source_stopped = False
    try:
        source_app = FastAPI()
        source_app.include_router(
            create_parallel_agents_router(
                orchestrator=source,
                identity_store=store,
                require_auth=True,
            )
        )
        source_client = TestClient(source_app)
        created = source_client.post(
            "/api/agents/parallel/dispatch",
            headers=_bearer("sk-test-alice"),
            json={"tasks": [{"task_id": "restart-lane", "description": "retry after restart"}]},
        )
        assert created.status_code == 200, created.json()
        source_id = created.json()["batch_id"]
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := source.get_batch(source_id)) is not None
                    and current.status == "failed"
                    else None
                )
            )
            is not None
        )
        source.shutdown(wait=True)
        source_stopped = True

        replacement_supervisor = TaskSupervisor.from_path(
            path,
            holder_id="parallel-recovery-route-replacement",
        )
        replacement = ParallelAgentOrchestrator(
            task_runner=runner,
            task_supervisor=replacement_supervisor,
            worker_isolation="thread",
        )
        replacement_app = FastAPI()
        replacement_app.include_router(
            create_parallel_agents_router(
                orchestrator=replacement,
                identity_store=store,
                require_auth=True,
            )
        )
        replacement_client = TestClient(replacement_app)
        resumed = replacement_client.post(
            f"/api/agents/parallel/batch/{source_id}/resume",
            headers=_bearer("sk-test-alice"),
            json={"task_ids": ["restart-lane"]},
        )
        assert resumed.status_code == 200, resumed.json()
        body = resumed.json()
        assert body["source_batch_id"] == source_id
        replacement_id = body["batch"]["batch_id"]
        assert replacement_id != source_id
        assert (
            _wait_for(
                lambda: (
                    current
                    if (current := replacement.get_batch(replacement_id)) is not None
                    and current.status == "completed"
                    else None
                )
            )
            is not None
        )
        assert attempts == 2
        duplicate = replacement_client.post(
            f"/api/agents/parallel/batch/{source_id}/resume",
            headers=_bearer("sk-test-alice"),
            json={"task_ids": ["restart-lane"]},
        )
        assert duplicate.status_code == 409
    finally:
        if not source_stopped:
            source.shutdown(wait=True)
        if replacement is not None:
            replacement.shutdown(wait=True)


def test_dispatch_drops_forged_caller_session_without_host_parent() -> None:
    observed: list[object] = []

    def _runner(description, *, subagent_name, context=None, cancel_event=None):
        observed.append((context or {}).get("caller_session"))
        return "ok"

    orchestrator = ParallelAgentOrchestrator(task_runner=_runner, worker_isolation="thread")
    forged = Session(actor="attacker", thread_id="forged")
    try:
        with session_scope(Session(actor="caller", thread_id="caller")):
            batch = orchestrator.dispatch(
                [{"description": "ignore forged authority"}],
                context={"caller_session": forged},
            )
        for _ in range(100):
            snapshot = orchestrator.get_batch(batch.batch_id)
            assert snapshot is not None
            if snapshot.status == "completed":
                break
            time.sleep(0.01)
        assert observed == [None]
    finally:
        orchestrator.shutdown(wait=False)


# ── get_batch: only owner can read ─────────────────────────────────


def test_get_batch_blocks_non_owner() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")

    # bob can't read alice's batch
    resp = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403
    assert "not the owner" in resp.json()["detail"]


def test_get_batch_allows_owner() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")

    resp = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200
    assert resp.json()["batch_id"] == batch_id


# ── cancel_task: only owner can cancel ─────────────────────────────


def test_cancel_task_blocks_non_owner() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")
    batch = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["alice"]),
    ).json()
    task_id = batch["results"][0]["task_id"]

    # bob tries to cancel alice's task
    resp = client.post(
        f"/api/agents/parallel/cancel/{task_id}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_cancel_task_allows_owner() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")
    batch = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["alice"]),
    ).json()
    task_id = batch["results"][0]["task_id"]

    resp = client.post(
        f"/api/agents/parallel/cancel/{task_id}",
        headers=_bearer(keys["alice"]),
    )
    # Might be 200 if still pending, or 404 if already running/done —
    # both are fine, we just need to confirm alice wasn't 403'd.
    assert resp.status_code in (200, 404)


# ── cancel_all: only cancels caller's own batches ──────────────────


def test_cancel_all_scoped_to_caller() -> None:
    """cancel_all should only cancel the caller's own batches."""
    client, keys = _build_app()
    alice_batch = _dispatch_batch(client, keys, "alice")
    bob_batch = _dispatch_batch(client, keys, "bob")

    # bob calls cancel_all
    resp = client.post(
        "/api/agents/parallel/cancel-all",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200

    # alice's batch should still be readable (not cancelled by bob)
    resp = client.get(
        f"/api/agents/parallel/batch/{alice_batch}",
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200

    # bob's batch should be cancelled
    for _ in range(100):
        resp = client.get(
            f"/api/agents/parallel/batch/{bob_batch}",
            headers=_bearer(keys["bob"]),
        )
        assert resp.status_code == 200
        if all(
            task["status"] in ("cancelled", "completed", "failed")
            for task in resp.json()["results"]
        ):
            break
        time.sleep(0.01)
    # All tasks in bob's batch should be cancelled or terminal
    for task in resp.json()["results"]:
        assert task["status"] in ("cancelled", "completed", "failed")


# ── stream: only owner can subscribe ───────────────────────────────


def test_stream_blocks_non_owner() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")

    # bob tries to stream alice's batch
    resp = client.get(
        f"/api/agents/parallel/stream/{batch_id}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── unauthenticated callers are blocked ────────────────────────────


def test_no_auth_token_returns_401() -> None:
    client, keys = _build_app()
    batch_id = _dispatch_batch(client, keys, "alice")

    resp = client.get(f"/api/agents/parallel/batch/{batch_id}")
    assert resp.status_code == 401


# ── status is intentionally actor-agnostic ─────────────────────────


def test_status_is_actor_agnostic() -> None:
    """``GET /api/agents/parallel/status`` returns aggregate counts
    across all users. Intentionally actor-agnostic — pinned here so
    a future audit doesn't accidentally lock it down."""
    client, keys = _build_app()
    _dispatch_batch(client, keys, "alice")
    _dispatch_batch(client, keys, "bob")

    # carol (who owns no batches) can still see aggregate status
    resp = client.get(
        "/api/agents/parallel/status",
        headers=_bearer(keys["carol"]),
    )
    assert resp.status_code == 200
    assert resp.json()["active_count"] >= 0  # aggregate, not per-user


# ── single-user dev mode: require_auth=False bypasses checks ───────


def test_dev_mode_bypasses_ownership_checks() -> None:
    """When require_auth=False, ownership checks are skipped."""
    orchestrator = ParallelAgentOrchestrator()
    app = FastAPI()
    app.include_router(
        create_parallel_agents_router(
            orchestrator=orchestrator,
            require_auth=False,
        )
    )
    client = TestClient(app)

    # No auth header — should still work in dev mode
    resp = client.post(
        "/api/agents/parallel/dispatch",
        json={
            "tasks": [{"description": "dev task", "subagent_name": "general-purpose"}],
        },
    )
    assert resp.status_code == 200
    batch_id = resp.json()["batch_id"]

    # Anyone can read it in dev mode
    resp = client.get(f"/api/agents/parallel/batch/{batch_id}")
    assert resp.status_code == 200


# ── legacy unowned batches fail closed for authenticated tenants ────


def test_legacy_unowned_batches_hidden_from_tenants() -> None:
    """Unowned legacy batches must not become cross-tenant shared state."""
    client, keys = _build_app()
    orch: ParallelAgentOrchestrator = client.orchestrator  # type: ignore[attr-defined]

    # Directly dispatch a batch with owner_id=None (legacy mode)
    result = orch.dispatch(
        [{"description": "legacy task", "subagent_name": "general-purpose"}],
        owner_id=None,
    )
    batch_id = result.batch_id

    # Ordinary tenants cannot claim or discover an unowned legacy batch.
    resp = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 404

    resp = client.get(
        f"/api/agents/parallel/batch/{batch_id}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 404
