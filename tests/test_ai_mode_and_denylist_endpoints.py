"""Tests for AI mode + path denylist HTTP endpoints."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.config_router import create_config_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_config_router(custom_models_path=None).router)
    return app


# ── /api/ai-mode ──────────────────────────────────────────────


@pytest.fixture
def ai_mode_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "ai_mode.json"
    monkeypatch.setenv("ECHO_AI_MODE_PATH", str(state))
    monkeypatch.delenv("ECHO_AI_MODE", raising=False)
    return state


def test_ai_mode_get_default_efficiency(ai_mode_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.get("/api/ai-mode")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "efficiency"
    assert body["recommended"] in {"efficiency", "privacy"}
    # Two visible cards
    assert len(body["modes"]) == 2
    assert {m["id"] for m in body["modes"]} == {"efficiency", "privacy"}


def test_ai_mode_post_switches_to_privacy(ai_mode_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.post("/api/ai-mode", json={"mode": "privacy"})
    assert r.status_code == 200
    assert r.json()["mode"] == "privacy"
    # Verify persisted
    r2 = client.get("/api/ai-mode")
    assert r2.json()["mode"] == "privacy"


def test_ai_mode_rejects_unknown(ai_mode_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.post("/api/ai-mode", json={"mode": "turbo"})
    assert r.status_code == 400


def test_ai_mode_device_summary_present(ai_mode_state: Path) -> None:
    client = TestClient(_make_app())
    body = client.get("/api/ai-mode").json()
    assert "device" in body
    for key in ("has_local_model", "has_gpu", "ram_gb", "cpu_count", "cloud_reachable"):
        assert key in body["device"]


# ── /api/path-denylist ────────────────────────────────────────


@pytest.fixture
def denylist_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    state = tmp_path / "denylist.json"
    monkeypatch.setenv("ECHO_PATH_DENYLIST_PATH", str(state))
    return state


def test_denylist_get_empty_initially(denylist_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.get("/api/path-denylist")
    assert r.status_code == 200
    assert r.json()["paths"] == []


def test_denylist_post_adds(denylist_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.post("/api/path-denylist", json={"path": "C:/Secret"})
    assert r.status_code == 200
    body = r.json()
    assert "C:/Secret" in body["paths"]
    assert body["ok"] is True
    # Persists
    data = json.loads(denylist_state.read_text(encoding="utf-8"))
    assert "C:/Secret" in data["paths"]


def test_denylist_post_rejects_empty(denylist_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.post("/api/path-denylist", json={"path": "  "})
    assert r.status_code == 400


def test_denylist_delete_removes(denylist_state: Path) -> None:
    client = TestClient(_make_app())
    client.post("/api/path-denylist", json={"path": "C:/x"})
    client.post("/api/path-denylist", json={"path": "C:/y"})
    r = client.request(
        "DELETE",
        "/api/path-denylist",
        json={"path": "C:/x"},
    )
    assert r.status_code == 200
    assert r.json()["paths"] == ["C:/y"]


def test_denylist_delete_rejects_empty(denylist_state: Path) -> None:
    client = TestClient(_make_app())
    r = client.request("DELETE", "/api/path-denylist", json={"path": ""})
    assert r.status_code == 400
