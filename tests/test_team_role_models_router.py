from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway.team_role_models_router import (
    create_team_role_models_router,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.include_router(create_team_role_models_router())
    return TestClient(app)


def test_get_returns_roles_and_tiers(client: TestClient) -> None:
    r = client.get("/api/team/role-models")
    assert r.status_code == 200
    body = r.json()
    assert body["tiers"] == ["default", "cheap", "primary"]
    assert any(row["role"] == "planner" for row in body["roles"])
    assert any(row["role"] == "researcher" for row in body["roles"])


def test_put_persists_and_get_reflects(
    client: TestClient,
    tmp_path: Path,
) -> None:
    r = client.put(
        "/api/team/role-models",
        json={"overrides": {"planner": "cheap", "researcher": "primary"}},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["overrides"] == {
        "planner": "cheap",
        "researcher": "primary",
    }
    assert (tmp_path / "data" / "team_role_models.json").exists()

    rows = {row["role"]: row["tier"] for row in client.get("/api/team/role-models").json()["roles"]}
    assert rows["planner"] == "cheap"
    assert rows["researcher"] == "primary"


def test_requires_auth_when_enabled() -> None:
    from runtime.safety.auth import Identity, IdentityStore

    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(
        create_team_role_models_router(
            identity_store=store,
            require_auth=True,
        )
    )
    client = TestClient(app)

    assert client.get("/api/team/role-models").status_code == 401
    assert (
        client.get(
            "/api/team/role-models",
            headers={"Authorization": "Bearer sk-alice"},
        ).status_code
        == 200
    )

