from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime.platform.reach as reach_module
from runtime.platform.ui.reach_router import create_reach_router
from runtime.safety.auth import Identity, IdentityStore


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_reach_router())
    return TestClient(app)


def _authenticated_client() -> TestClient:
    app = FastAPI()
    app.include_router(create_reach_router(require_auth=True, jwt_secret="x" * 32))
    return TestClient(app)


def test_reach_status(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    monkeypatch.setattr(
        reach_module,
        "diagnose_reach",
        lambda: {"ok": True, "healthy": 11, "total": 11, "channels": []},
    )

    response = _client().get("/api/reach/status")

    assert response.status_code == 200
    assert response.json()["collection_count"] == 0


def test_list_and_read_collection(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    root = tmp_path / "data" / "reach" / "collections"
    root.mkdir(parents=True)
    path = root / "collection-20260101T000000Z.json"
    path.write_text(json.dumps({"platform": "reddit"}), encoding="utf-8")

    listing = _client().get("/api/reach/collections")
    detail = _client().get(f"/api/reach/collections/{path.name}")

    assert listing.json()["collections"][0]["name"] == path.name
    assert detail.json()["data"]["platform"] == "reddit"


def test_collection_path_traversal_is_rejected(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))

    response = _client().get("/api/reach/collections/not-a-collection.json")

    assert response.status_code == 400


def test_collect_endpoint(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    monkeypatch.setattr(
        reach_module,
        "platform_collect",
        lambda **kwargs: {"ok": True, "platform": kwargs["platform"], "search_count": 1},
    )

    response = _client().post(
        "/api/reach/collect",
        json={"platform": "x", "queries": ["agents"]},
    )

    assert response.status_code == 200
    assert response.json()["platform"] == "x"


def test_reach_api_requires_auth_when_enabled() -> None:
    assert _authenticated_client().get("/api/reach/status").status_code == 401
    assert _authenticated_client().delete("/api/reach/cache").status_code == 401


def _role_client(*roles: str) -> TestClient:
    store = IdentityStore()
    store.add(Identity(actor_id="alice", roles=roles), api_key_plaintext="sk-alice")
    app = FastAPI()
    app.include_router(create_reach_router(identity_store=store, require_auth=True))
    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer sk-alice"})
    return client


def test_collection_reads_require_operator(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    root = tmp_path / "data" / "reach" / "collections"
    root.mkdir(parents=True)
    name = "collection-20260101T000000Z.json"
    (root / name).write_text(json.dumps({"platform": "reddit"}), encoding="utf-8")

    member = _role_client("member")
    assert member.get("/api/reach/collections").status_code == 403
    assert member.get(f"/api/reach/collections/{name}").status_code == 403

    operator = _role_client("operator")
    assert operator.get("/api/reach/collections").status_code == 200
    assert operator.get(f"/api/reach/collections/{name}").json()["data"]["platform"] == "reddit"

