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

