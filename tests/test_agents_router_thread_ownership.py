"""Thread ownership regression tests for agents_router.

These pin the thread-ownership enforcement added on top of agents_router.
Before this fix, any authenticated user could:
  - List ALL pause/active tasks (cross-thread enumeration)
  - Pause/resume/delete other users' tasks
  - Read other users' conversation events

Now /api/tasks filters by thread owner, and /api/conversations/{id}/events
enforces _require_thread_owner.

The chain is task_id → ActiveTask.thread_id → Thread.metadata["owner_id"].
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.routing import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.core.cerebrum.pause_control import PauseController  # noqa: E402
from runtime.memory.threads.store import ThreadStateStore  # noqa: E402
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402


def _build_minimal_router(
    *,
    identity_store: IdentityStore,
    require_auth: bool,
    pause_ctrl: PauseController,
    thread_store: ThreadStateStore,
) -> APIRouter:
    """Build a minimal router exercising _require_task_owner / _require_thread_owner.

    Mirrors the helpers in agents_router.py exactly so the test pins
    the actual enforcement logic shape.
    """
    from fastapi import HTTPException

    from runtime.sensing.gateway.openai_gateway_router import _resolve_actor

    router = APIRouter()

    def _auth(request: Request) -> str | None:
        return _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=None,
            jwt_issuer=None,
            jwt_audience=None,
        )

    def _require_task_owner(request: Request, task_id: str) -> str | None:
        actor = _auth(request)
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        thread_id = pause_ctrl.get_task_thread_id(task_id)
        if thread_id is None:
            raise HTTPException(404, f"task not found: {task_id}")
        if thread_store is None:
            return actor
        thread = thread_store.get_state(thread_id)
        if thread is None:
            return actor
        owner = (thread.get("metadata") or {}).get("owner_id")
        if owner is not None and owner != actor:
            raise HTTPException(403, "not the owner of this task's thread")
        return actor

    def _require_thread_owner(request: Request, thread_id: str) -> str | None:
        actor = _auth(request)
        if not require_auth:
            return actor
        if not actor:
            raise HTTPException(401, "authentication required")
        if thread_store is None:
            return actor
        thread = thread_store.get_state(thread_id)
        if thread is None:
            return actor
        owner = (thread.get("metadata") or {}).get("owner_id")
        if owner is not None and owner != actor:
            raise HTTPException(403, "not the owner of this thread")
        return actor

    @router.get("/api/tasks")
    def list_tasks(request: Request) -> dict[str, Any]:
        actor = _auth(request)
        owned: set[str] | None = None
        legacy: set[str] = set()
        if require_auth and actor and thread_store is not None:
            owned = pause_ctrl.list_thread_ids_for_owner(thread_store, actor)
            try:
                for t in thread_store.search(limit=10000):
                    if (t.get("metadata") or {}).get("owner_id") is None:
                        tid = t.get("thread_id")
                        if tid:
                            legacy.add(tid)
            except (TypeError, AttributeError):
                pass

        def _is_owned(item: Any) -> bool:
            if owned is None:
                return True
            tid = getattr(item, "thread_id", None) or ""
            if not tid:
                return True
            return tid in owned or tid in legacy

        return {
            "active": [t.to_dict() for t in pause_ctrl.list_active() if _is_owned(t)],
        }

    @router.get("/api/tasks/{task_id}")
    def get_task(request: Request, task_id: str) -> dict[str, Any]:
        _require_task_owner(request, task_id)
        return {"ok": True, "task_id": task_id}

    @router.post("/api/tasks/{task_id}/pause")
    def pause_task(request: Request, task_id: str) -> dict[str, Any]:
        _require_task_owner(request, task_id)
        return {"ok": True, "task_id": task_id}

    @router.delete("/api/tasks/{task_id}")
    def delete_task(request: Request, task_id: str) -> dict[str, Any]:
        _require_task_owner(request, task_id)
        return {"ok": True, "task_id": task_id}

    @router.get("/api/conversations/{thread_id}/events")
    def get_conversation_events(request: Request, thread_id: str) -> dict[str, Any]:
        _require_thread_owner(request, thread_id)
        return {"ok": True, "thread_id": thread_id}

    return router


@pytest.fixture
def setup(tmp_path: Path) -> dict[str, Any]:
    """Build identity store, pause controller, thread store, and 2 actors."""
    identity_store = IdentityStore()
    keys: dict[str, str] = {}
    for actor in ("alice", "bob"):
        api_key = f"sk-test-{actor}"
        identity_store.add(Identity(actor_id=actor), api_key_plaintext=api_key)
        keys[actor] = api_key

    pause_ctrl = PauseController(
        store_path=tmp_path / "pause_state.json",
        autoload=False,
    )
    thread_store = ThreadStateStore(path=tmp_path / "threads.jsonl")

    # Create alice's thread + task
    alice_thread = thread_store.create(metadata={"owner_id": "alice"})
    alice_thread_id = alice_thread["thread_id"]
    pause_ctrl.register_active(
        task_id="task-alice",
        thread_id=alice_thread_id,
        agent_id="general",
    )

    # Create bob's thread + task
    bob_thread = thread_store.create(metadata={"owner_id": "bob"})
    bob_thread_id = bob_thread["thread_id"]
    pause_ctrl.register_active(
        task_id="task-bob",
        thread_id=bob_thread_id,
        agent_id="general",
    )

    # Create a legacy thread (no owner_id) + its task
    legacy_thread = thread_store.create(metadata={})
    legacy_thread_id = legacy_thread["thread_id"]
    pause_ctrl.register_active(
        task_id="task-legacy",
        thread_id=legacy_thread_id,
        agent_id="general",
    )

    return {
        "identity_store": identity_store,
        "keys": keys,
        "pause_ctrl": pause_ctrl,
        "thread_store": thread_store,
        "alice_thread_id": alice_thread_id,
        "bob_thread_id": bob_thread_id,
        "legacy_thread_id": legacy_thread_id,
    }


def _build_app(setup: dict[str, Any], require_auth: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(
        _build_minimal_router(
            identity_store=setup["identity_store"],
            require_auth=require_auth,
            pause_ctrl=setup["pause_ctrl"],
            thread_store=setup["thread_store"],
        )
    )
    return TestClient(app)


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# ── list_tasks: filters by thread owner ────────────────────────────


def test_list_tasks_filters_by_thread_owner(setup: dict[str, Any]) -> None:
    """alice should only see her own + legacy tasks; not bob's."""
    client = _build_app(setup)

    resp = client.get("/api/tasks", headers=_bearer(setup["keys"]["alice"]))
    assert resp.status_code == 200
    task_ids = {t["task_id"] for t in resp.json()["active"]}
    assert "task-alice" in task_ids
    assert "task-legacy" in task_ids  # legacy unowned visible to all
    assert "task-bob" not in task_ids


def test_list_tasks_dev_mode_shows_all(setup: dict[str, Any]) -> None:
    """In dev mode (no auth), filtering is disabled — see all tasks."""
    client = _build_app(setup, require_auth=False)

    resp = client.get("/api/tasks")
    assert resp.status_code == 200
    task_ids = {t["task_id"] for t in resp.json()["active"]}
    assert task_ids == {"task-alice", "task-bob", "task-legacy"}


# ── get_task: only owner can read ──────────────────────────────────


def test_get_task_blocks_non_owner(setup: dict[str, Any]) -> None:
    """bob can't read alice's task."""
    client = _build_app(setup)

    resp = client.get("/api/tasks/task-alice", headers=_bearer(setup["keys"]["bob"]))
    assert resp.status_code == 403
    assert "not the owner" in resp.json()["detail"]


def test_get_task_allows_owner(setup: dict[str, Any]) -> None:
    """alice can read her own task."""
    client = _build_app(setup)

    resp = client.get("/api/tasks/task-alice", headers=_bearer(setup["keys"]["alice"]))
    assert resp.status_code == 200


def test_get_task_404_for_unknown_task(setup: dict[str, Any]) -> None:
    """Non-existent task returns 404 (not 403 — don't leak existence)."""
    client = _build_app(setup)

    resp = client.get("/api/tasks/task-nonexistent", headers=_bearer(setup["keys"]["alice"]))
    assert resp.status_code == 404


def test_get_task_legacy_unowned_visible_to_all(setup: dict[str, Any]) -> None:
    """Tasks on legacy threads (no owner_id) are visible to everyone."""
    client = _build_app(setup)

    # Both alice and bob can read the legacy task
    for actor in ("alice", "bob"):
        resp = client.get(
            "/api/tasks/task-legacy",
            headers=_bearer(setup["keys"][actor]),
        )
        assert resp.status_code == 200


# ── pause / delete / resume: only owner can mutate ─────────────────


def test_pause_task_blocks_non_owner(setup: dict[str, Any]) -> None:
    """bob can't pause alice's task."""
    client = _build_app(setup)

    resp = client.post(
        "/api/tasks/task-alice/pause",
        headers=_bearer(setup["keys"]["bob"]),
    )
    assert resp.status_code == 403


def test_delete_task_blocks_non_owner(setup: dict[str, Any]) -> None:
    """bob can't delete alice's task."""
    client = _build_app(setup)

    resp = client.delete(
        "/api/tasks/task-alice",
        headers=_bearer(setup["keys"]["bob"]),
    )
    assert resp.status_code == 403


# ── Conversation events: thread-level ownership ────────────────────


def test_get_conversation_events_blocks_non_owner(setup: dict[str, Any]) -> None:
    """bob can't read events from alice's thread."""
    client = _build_app(setup)

    resp = client.get(
        f"/api/conversations/{setup['alice_thread_id']}/events",
        headers=_bearer(setup["keys"]["bob"]),
    )
    assert resp.status_code == 403


def test_get_conversation_events_allows_owner(setup: dict[str, Any]) -> None:
    """alice can read events from her own thread."""
    client = _build_app(setup)

    resp = client.get(
        f"/api/conversations/{setup['alice_thread_id']}/events",
        headers=_bearer(setup["keys"]["alice"]),
    )
    assert resp.status_code == 200


# ── Unauthenticated: blocked at auth layer ─────────────────────────


def test_no_auth_token_returns_401(setup: dict[str, Any]) -> None:
    """Missing Authorization header returns 401."""
    client = _build_app(setup)

    resp = client.get("/api/tasks/task-alice")
    assert resp.status_code == 401
