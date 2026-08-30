"""Security regression tests for parallel_agents_router ownership enforcement.

These pin the ownership model added to ParallelAgentOrchestrator. Before
this fix, any authenticated user could read, cancel, or stream any other
user's batches/tasks. The orchestrator now stamps ``owner_id`` on each
batch, and endpoints enforce caller-owns-batch checks.

See parallel_agents_router.py ``_require_batch_owner`` + orchestrator.py
``_BatchEntry.owner_id``.
"""

from __future__ import annotations

import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.execution.parallel_agents.orchestrator import (  # noqa: E402
    ParallelAgentOrchestrator,
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


# ── legacy unowned batches are visible to everyone ─────────────────


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
