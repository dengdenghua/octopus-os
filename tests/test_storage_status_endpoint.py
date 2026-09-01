"""/api/storage/status surfaces the storage heartbeat's view to the UI."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime.sensing.gateway.storage_supervisor as ss
from runtime.platform.ui.health_router import create_health_router


def _client() -> TestClient:
    app = FastAPI()
    # The storage endpoint doesn't touch ``state``; a placeholder is enough.
    app.include_router(create_health_router(state=object()))
    return TestClient(app)


def test_storage_status_reports_up(monkeypatch) -> None:
    monkeypatch.setattr(
        ss,
        "storage_status",
        lambda: {"up": True, "heartbeat": True, "restart_count": 0},
    )
    resp = _client().get("/api/storage/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["up"] is True
    assert body["heartbeat"] is True


def test_storage_status_reports_down(monkeypatch) -> None:
    monkeypatch.setattr(
        ss,
        "storage_status",
        lambda: {"up": False, "heartbeat": True, "restart_count": 2},
    )
    body = _client().get("/api/storage/status").json()
    assert body["up"] is False
    assert body["restart_count"] == 2


def test_storage_status_never_500s_on_error(monkeypatch) -> None:
    def _boom() -> dict:
        raise RuntimeError("supervisor exploded")

    monkeypatch.setattr(ss, "storage_status", _boom)
    resp = _client().get("/api/storage/status")
    assert resp.status_code == 200
    assert resp.json()["up"] is False

