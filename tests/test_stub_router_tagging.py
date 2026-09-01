"""Implementation note."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.stub_router import create_stub_router


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()
    app.include_router(create_stub_router())
    return TestClient(app)


# Implementation note.


def test_intelligence_post_has_stub_tag(client: TestClient) -> None:
    """The canonical 2026-04-24 regression · must be green."""
    r = client.post(
        "/api/intelligence/subscriptions",
        json={"topic": "x", "max_results": 3, "frequency_seconds": 3600},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("_stub") is True, f"missing _stub tag · body={body}"
    assert body.get("_stub_reason") == "compatibility_fallback"
    assert r.headers["X-Echo-Stub"] == "true"
    assert r.headers["X-Echo-Stub-Reason"] == "compatibility_fallback"


def test_intelligence_patch_has_stub_tag(client: TestClient) -> None:
    r = client.patch(
        "/api/intelligence/subscriptions/some-id",
        json={"enabled": True},
    )
    assert r.status_code == 200
    assert r.json().get("_stub") is True


def test_memory_post_has_stub_tag(client: TestClient) -> None:
    r = client.post("/api/memory/facts", json={"fact": "hello"})
    assert r.status_code == 200
    assert r.json().get("_stub") is True


def test_swarm_task_post_has_stub_tag(client: TestClient) -> None:
    r = client.post("/api/swarm/tasks", json={"goal": "demo"})
    assert r.status_code == 200
    assert r.json().get("_stub") is True


def test_arena_vote_post_has_stub_tag(client: TestClient) -> None:
    r = client.post("/api/arena/vote", json={"battle_id": "x", "winner": "A"})
    assert r.status_code == 200
    assert r.json().get("_stub") is True


# Implementation note.


def test_intelligence_get_has_stub_tag(client: TestClient) -> None:
    r = client.get("/api/intelligence/subscriptions")
    assert r.status_code == 200
    body = r.json()
    # Implementation note.
    assert isinstance(body, dict)
    assert body.get("_stub") is True
    # Implementation note.
    assert "subscriptions" in body


# Implementation note.


def test_list_only_response_is_not_mutated(client: TestClient) -> None:
    """Implementation note."""
    # /api/alerts returns a list at top level
    r = client.get("/api/alerts")
    assert r.status_code == 200
    assert r.headers["X-Echo-Stub"] == "true"
    body = r.json()
    # Implementation note.
    assert isinstance(body, list)


# Implementation note.


def test_account_envelope_has_stub_tag(client: TestClient) -> None:
    """Implementation note."""
    r = client.get("/api/account/profile")
    assert r.status_code == 200
    body = r.json()
    assert body.get("success") is True
    assert body.get("_stub") is True  # Implementation note.
    assert body.get("_stub_reason") == "compatibility_fallback"


# Implementation note.


def test_content_length_matches_patched_body(client: TestClient) -> None:
    """Implementation note."""
    r = client.post(
        "/api/intelligence/subscriptions",
        json={"topic": "len-check"},
    )
    assert r.status_code == 200
    # Implementation note.
    # Implementation note.
    cl = r.headers.get("content-length")
    if cl is not None:  # Implementation note.
        assert int(cl) == len(r.content)


def test_stub_router_can_be_disabled() -> None:
    app = FastAPI()
    app.include_router(create_stub_router(enabled=False))
    disabled_client = TestClient(app)

    r = disabled_client.get("/api/account/profile")

    assert r.status_code == 404


def test_stub_router_can_be_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ECHO_DISABLE_STUB_API", "1")
    app = FastAPI()
    app.include_router(create_stub_router())
    disabled_client = TestClient(app)

    r = disabled_client.get("/api/account/profile")

    assert r.status_code == 404
