"""HTTP layer for the local-model cookbook: public snapshot + auth-gated pull."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.cookbook_router import create_cookbook_router
from runtime.sensing.model_router import hwfit


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_cookbook_router())  # require_auth defaults to False
    return TestClient(app)


def test_snapshot_returns_hardware_and_recommendations(monkeypatch) -> None:
    monkeypatch.setattr(
        hwfit,
        "cookbook_snapshot",
        lambda: {
            "hardware": {"backend": "cuda"},
            "ollama_available": True,
            "recommendations": [],
            "pulls": {},
        },
    )
    resp = _client().get("/api/cookbook/snapshot")
    assert resp.status_code == 200
    assert resp.json()["hardware"]["backend"] == "cuda"


def test_snapshot_never_500s_on_error(monkeypatch) -> None:
    def _boom() -> dict:
        raise RuntimeError("detect exploded")

    monkeypatch.setattr(hwfit, "cookbook_snapshot", _boom)
    resp = _client().get("/api/cookbook/snapshot")
    assert resp.status_code == 200
    assert resp.json()["ollama_available"] is False


def test_pull_invokes_start_pull(monkeypatch) -> None:
    seen = {}

    def _fake_start(tag: str) -> dict:
        seen["tag"] = tag
        return {"status": "started", "tag": tag}

    monkeypatch.setattr(hwfit, "start_pull", _fake_start)
    resp = _client().post("/api/cookbook/pull", json={"tag": "qwen2.5:7b"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"
    assert seen["tag"] == "qwen2.5:7b"


def test_pull_requires_tag_field() -> None:
    # Missing body field → 422 from pydantic validation.
    assert _client().post("/api/cookbook/pull", json={}).status_code == 422


def test_verify_starts_existing_model_job(monkeypatch):
    monkeypatch.setattr(hwfit, "start_verify", lambda tag: {"status": "started", "tag": tag})
    response = _client().post("/api/cookbook/verify", json={"tag": "fixture:1b"})
    assert response.status_code == 200
    assert response.json()["tag"] == "fixture:1b"


def test_errors_are_not_success_responses(monkeypatch):
    monkeypatch.setattr(hwfit, "start_verify", lambda tag: {"status": "error", "error": "busy"})
    assert _client().post("/api/cookbook/verify", json={"tag": "fixture:1b"}).status_code == 400


def test_verify_and_download_require_authentication():
    app = FastAPI()
    app.include_router(create_cookbook_router(require_auth=True))
    with TestClient(app) as client:
        for path in ("verify", "pull"):
            assert client.post(f"/api/cookbook/{path}", json={"tag": "fixture:1b"}).status_code in (
                401,
                403,
            )


def test_context_estimate_is_bounded_and_forwarded(monkeypatch):
    monkeypatch.setattr(hwfit, "cookbook_snapshot", lambda tokens: {"context_tokens": tokens})
    assert (
        _client().get("/api/cookbook/snapshot?context_tokens=8192").json()["context_tokens"] == 8192
    )
    assert _client().get("/api/cookbook/snapshot?context_tokens=9999999").status_code == 422
