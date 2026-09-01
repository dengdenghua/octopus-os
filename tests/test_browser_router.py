from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.ui.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ECHO_BROWSER_EXTENSION_DIR", raising=False)
    return TestClient(create_app())


def test_browser_session_launch_and_list(client: TestClient) -> None:
    response = client.post(
        "/api/browser/launch",
        json={"session_id": "s1", "headless": True},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "launched"
    assert data["session"]["session_id"] == "s1"
    assert data["session"]["project_id"] == "s1"
    assert data["session"]["profile_id"] == "s1"
    assert data["session"]["automation_mode"] == "browser_context"
    assert data["session"]["uses_system_mouse"] is False
    assert data["session"]["desktop_lease_required"] is False

    sessions = client.get("/api/browser/sessions")
    assert sessions.status_code == 200
    assert sessions.json()["count"] == 1

    page_info = client.get("/api/browser/page-info", params={"session_id": "s1"})
    assert page_info.status_code == 200
    assert page_info.json() == {"url": "", "title": ""}


def test_browser_config_update(client: TestClient) -> None:
    response = client.put(
        "/api/browser/config",
        json={
            "connection_mode": "extension",
            "viewport_width": 1024,
            "viewport_height": 768,
            "headless": False,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["connection_mode"] == "extension"
    assert data["viewport_width"] == 1024
    assert data["viewport_height"] == 768
    assert data["headless"] is False


def test_browser_relay_heartbeat_and_status(client: TestClient) -> None:
    heartbeat = client.post(
        "/api/browser/relay/heartbeat",
        json={
            "extension_version": "test",
            "active_tab": {"id": 1, "url": "https://example.test", "title": "Example"},
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["ok"] is True

    status = client.get("/api/browser/relay/status")
    assert status.status_code == 200
    data = status.json()
    assert data["extension_version"] == "test"
    assert data["active_tab"]["title"] == "Example"


def test_browser_bookmarklet_callback_validation(client: TestClient) -> None:
    bad = client.get(
        "/api/browser/relay/bookmarklet-poll",
        params={"callback": "alert(1)"},
    )
    good = client.get(
        "/api/browser/relay/bookmarklet-poll",
        params={"callback": "echo.cb", "url": "https://example.test"},
    )

    assert bad.status_code == 400
    assert good.status_code == 200
    assert good.headers["content-type"].startswith("application/javascript")
    assert good.text.startswith("echo.cb(")


def test_browser_extension_path_uses_workspace_fallback(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.get("/api/browser/extension-path")

    assert response.status_code == 200
    data = response.json()
    # Fresh workspace with no env override and no existing dir falls
    # back to the committed product location ``extensions/echo-browser-relay``.
    assert data["path"] == str(tmp_path / "extensions" / "echo-browser-relay")
    assert data["exists"] is True
