"""Dense coverage for browser_router endpoints (audit Q-05)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.browser_router import create_browser_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_browser_router())
    return TestClient(app)


def test_system_info_and_sessions() -> None:
    client = _client()
    info = client.get("/api/browser/system-info")
    assert info.status_code == 200
    assert "system" in info.json()
    sessions = client.get("/api/browser/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["sessions"] == []


def test_session_status_health_missing_session() -> None:
    client = _client()
    st = client.get("/api/browser/session/status")
    assert st.status_code in (200, 404)
    h = client.get("/api/browser/session/health")
    assert h.status_code in (200, 404)


def test_launch_requires_session_id() -> None:
    client = _client()
    r = client.post("/api/browser/launch", json={})
    assert r.status_code == 400

