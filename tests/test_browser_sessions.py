from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.platform.runtime_policy.browser_sessions import BrowserSessionCenter
from runtime.platform.ui.browser_router import create_browser_router


def test_browser_session_center_snapshots_without_runtime_objects() -> None:
    config = {"headless": True}
    ticks = iter([10, 11])
    center = BrowserSessionCenter(config, now=lambda: next(ticks))

    session = center.ensure("workspace")
    center.record_action(session, "navigate", "https://example.com")

    snapshot = center.snapshot(session)
    assert snapshot == {
        "session_id": "workspace",
        "project_id": "workspace",
        "profile_id": "workspace",
        "profile_dir": "",
        "automation_mode": "browser_context",
        "uses_system_mouse": False,
        "desktop_lease_required": False,
        "is_launched": True,
        "created_at": 10,
        "last_activity": 11,
        "action_count": 1,
        "headless": True,
        "mode": "mock",
        "runtime": "mock",
        "has_page": False,
        "healthy": True,
        "current_url": "",
        "current_title": "",
        "viewport_width": 1440,
        "viewport_height": 900,
        "recovered_from_crash": False,
        "recovery_revalidated_at": 0,
        "browser_regression_enabled": False,
        "browser_regression_mode": "off",
        "browser_regression_preview_url": "",
        "browser_regression_requires_visible_cursor": False,
    }


def test_browser_session_status_does_not_launch_missing_session() -> None:
    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    response = client.get("/api/browser/session/status", params={"session_id": "workspace"})

    assert response.status_code == 200
    body = response.json()
    assert body["exists"] is False
    assert body["session"]["session_id"] == "workspace"
    assert body["session"]["uses_system_mouse"] is False
    assert body["session"]["desktop_lease_required"] is False
    assert body["session"]["is_launched"] is False

    sessions = client.get("/api/browser/sessions").json()
    assert sessions == {"sessions": [], "count": 0}


def test_browser_session_ensure_and_reset_are_reflected_in_legacy_sessions() -> None:
    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    ensured = client.post(
        "/api/browser/session/ensure",
        json={"session_id": "workspace", "headless": False},
    )
    assert ensured.status_code == 200
    assert ensured.json()["session"]["headless"] is False

    sessions = client.get("/api/browser/sessions").json()
    assert sessions["count"] == 1
    assert sessions["sessions"][0]["session_id"] == "workspace"

    reset = client.post("/api/browser/session/reset", json={"session_id": "workspace"})
    assert reset.status_code == 200
    assert reset.json()["status"] == "closed"

    status = client.get(
        "/api/browser/session/status",
        params={"session_id": "workspace"},
    ).json()
    assert status["exists"] is False


def test_browser_session_ensure_persists_regression_settings() -> None:
    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    response = client.post(
        "/api/browser/session/ensure",
        json={
            "session_id": "workspace",
            "browser_regression_enabled": True,
            "browser_regression_mode": "human_cursor",
            "browser_regression_preview_url": "http://localhost:3000/preview",
            "browser_regression_requires_visible_cursor": True,
        },
    )

    assert response.status_code == 200
    session = response.json()["session"]
    assert session["browser_regression_enabled"] is True
    assert session["browser_regression_mode"] == "human_cursor"
    assert session["browser_regression_preview_url"] == "http://localhost:3000/preview"
    assert session["browser_regression_requires_visible_cursor"] is True

    status = client.get(
        "/api/browser/session/status",
        params={"session_id": "workspace"},
    ).json()
    assert status["session"]["browser_regression_enabled"] is True


def test_browser_sessions_are_project_scoped_profiles() -> None:
    app = FastAPI()
    app.include_router(create_browser_router())
    client = TestClient(app)

    alpha = client.post(
        "/api/browser/session/ensure",
        json={
            "session_id": "browser-alpha",
            "project_id": "Project Alpha",
            "profile_id": "Project Alpha",
        },
    ).json()["session"]
    beta = client.post(
        "/api/browser/session/ensure",
        json={
            "session_id": "browser-beta",
            "project_id": "Project Beta",
            "profile_id": "Project Beta",
        },
    ).json()["session"]

    assert alpha["project_id"] == "Project Alpha"
    assert beta["project_id"] == "Project Beta"
    assert alpha["profile_id"] == "project-alpha"
    assert beta["profile_id"] == "project-beta"
    assert alpha["profile_id"] != beta["profile_id"]
    assert alpha["automation_mode"] == "browser_context"
    assert alpha["uses_system_mouse"] is False
    assert alpha["desktop_lease_required"] is False
