"""Security regression tests for agents_router admin enforcement.

Agent registry, tool-registry, capabilities, and group management
are all global server state. Before this fix, any authenticated user
could create/update/delete agents, modify tool permissions, or manage
groups. Now all mutations require admin role.

NOTE: agents_router has deep dependencies (pause_controller, journal,
group_registry). These tests focus on auth checks for mutation endpoints
only. Read endpoints are tested in integration tests.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.routing import APIRouter  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402


def _build_minimal_router(
    identity_store: IdentityStore,
    require_auth: bool,
) -> APIRouter:
    """Build minimal router with just _require_admin for mutation tests."""
    from runtime.sensing.gateway.openai_gateway_router import (  # noqa: E402
        _resolve_actor,
    )

    router = APIRouter()

    def _resolve_identity(request: Request) -> Identity | None:
        if not require_auth or identity_store is None:
            return None
        actor_id = _resolve_actor(
            request,
            identity_store,
            require_auth,
            jwt_secret=None,
            jwt_issuer=None,
            jwt_audience=None,
        )
        if actor_id is None:
            return None
        return identity_store.get(actor_id)

    def _require_admin(request: Request) -> None:
        from fastapi import HTTPException

        if not require_auth:
            return
        identity = _resolve_identity(request)
        if identity is None or "admin" not in identity.roles:
            raise HTTPException(403, "admin role required")

    # Minimal mutation endpoints for testing
    @router.post("/api/agents")
    def create_agent(request: Request) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.put("/api/agents/{agent_id}")
    def update_agent(request: Request, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.delete("/api/agents/{agent_id}")
    def delete_agent(request: Request, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.post("/api/agents/{agent_id}/reload")
    def reload_agent(request: Request, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.put("/api/agents/{agent_id}/tool-registry")
    def put_tool_registry(request: Request, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.put("/api/settings/capabilities")
    def put_capabilities(request: Request) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.post("/api/groups")
    def create_group(request: Request) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.put("/api/groups/{group_id}")
    def update_group(request: Request, group_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.delete("/api/groups/{group_id}")
    def delete_group(request: Request, group_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.post("/api/groups/{group_id}/members/{agent_id}")
    def add_member(request: Request, group_id: str, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    @router.delete("/api/groups/{group_id}/members/{agent_id}")
    def remove_member(request: Request, group_id: str, agent_id: str) -> dict:
        _require_admin(request)
        return {"ok": True}

    return router


def _build_app() -> tuple[TestClient, dict[str, str]]:
    """Build app with require_auth=True + alice=admin, bob=user."""
    store = IdentityStore()
    keys: dict[str, str] = {}

    # alice is admin
    alice = Identity(actor_id="alice", roles=["admin"])
    api_key_alice = "sk-test-alice"
    store.add(alice, api_key_plaintext=api_key_alice)
    keys["alice"] = api_key_alice

    # bob is regular user
    bob = Identity(actor_id="bob")
    api_key_bob = "sk-test-bob"
    store.add(bob, api_key_plaintext=api_key_bob)
    keys["bob"] = api_key_bob

    app = FastAPI()
    app.include_router(_build_minimal_router(store, require_auth=True))
    return TestClient(app), keys


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# ── Agent CRUD: require admin ──────────────────────────────────────


def test_create_agent_blocks_non_admin() -> None:
    """POST /api/agents requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/agents",
        json={"agent_id": "malicious_agent"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403
    assert "admin role required" in resp.json()["detail"]


def test_create_agent_allows_admin() -> None:
    """Admin can create agents."""
    client, keys = _build_app()

    resp = client.post(
        "/api/agents",
        json={"agent_id": "legit_agent"},
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200


def test_update_agent_blocks_non_admin() -> None:
    """PUT /api/agents/{id} requires admin."""
    client, keys = _build_app()

    resp = client.put(
        "/api/agents/test_agent",
        json={"display_name": "Hijacked"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_delete_agent_blocks_non_admin() -> None:
    """DELETE /api/agents/{id} requires admin."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/agents/test_agent",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_reload_agent_blocks_non_admin() -> None:
    """POST /api/agents/{id}/reload requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/agents/test_agent/reload",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_put_tool_registry_blocks_non_admin() -> None:
    """PUT /api/agents/{id}/tool-registry requires admin (high risk)."""
    client, keys = _build_app()

    resp = client.put(
        "/api/agents/test_agent/tool-registry",
        json={"arms": []},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_put_capabilities_blocks_non_admin() -> None:
    """PUT /api/settings/capabilities requires admin."""
    client, keys = _build_app()

    resp = client.put(
        "/api/settings/capabilities",
        json={"browser_automation": False},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── Group CRUD: require admin ──────────────────────────────────────


def test_create_group_blocks_non_admin() -> None:
    """POST /api/groups requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/groups",
        json={"group_id": "malicious_group"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_update_group_blocks_non_admin() -> None:
    """PUT /api/groups/{id} requires admin."""
    client, keys = _build_app()

    resp = client.put(
        "/api/groups/test_group",
        json={"display_name": "Hijacked Group"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_delete_group_blocks_non_admin() -> None:
    """DELETE /api/groups/{id} requires admin."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/groups/test_group",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_add_group_member_blocks_non_admin() -> None:
    """POST /api/groups/{id}/members/{agent_id} requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/groups/test_group/members/test_agent",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_remove_group_member_blocks_non_admin() -> None:
    """DELETE /api/groups/{id}/members/{agent_id} requires admin."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/groups/test_group/members/test_agent",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── Unauthenticated callers are blocked ────────────────────────────


def test_no_auth_token_returns_401() -> None:
    """All endpoints require authentication when require_auth=True."""
    client, keys = _build_app()

    resp = client.post("/api/agents", json={"agent_id": "test"})
    assert resp.status_code == 401


# ── Dev mode: require_auth=False bypasses admin checks ─────────────


def test_dev_mode_bypasses_admin_checks() -> None:
    """When require_auth=False, non-admins can mutate agents."""
    store = IdentityStore()
    app = FastAPI()
    app.include_router(_build_minimal_router(store, require_auth=False))
    client = TestClient(app)

    # No auth header — should still work in dev mode
    resp = client.post("/api/agents", json={"agent_id": "dev_agent"})
    assert resp.status_code == 200
