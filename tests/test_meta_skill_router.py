from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.meta_skill_router import create_meta_skill_router


def test_meta_skill_router_lists_packs() -> None:
    app = FastAPI()
    app.include_router(create_meta_skill_router())
    client = TestClient(app)

    response = client.get("/api/meta-skills")
    assert response.status_code == 200
    body = response.json()
    assert "count" in body
    assert "packs" in body
    assert isinstance(body["packs"], list)


def test_meta_skill_router_requires_auth_when_enabled() -> None:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_meta_skill_router(
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/meta-skills").status_code == 401
    assert (
        client.get(
            "/api/meta-skills",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 200
    )

