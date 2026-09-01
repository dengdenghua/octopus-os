from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.safety.auth import Identity, IdentityStore
from runtime.sensing.gateway.skill_market_router import create_skill_market_router


class _FakeSkillMarket:
    def list_installed(self):
        return []


def test_skill_market_router_requires_auth_when_enabled() -> None:
    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_skill_market_router(
            skill_market=_FakeSkillMarket(),
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/skills/market/installed").status_code == 401
    assert (
        client.get(
            "/api/skills/market/installed",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 200
    )

