from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.ui.app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app())


def test_cron_list_defaults_empty(client: TestClient) -> None:
    response = client.get("/api/cron")

    assert response.status_code == 200
    assert response.json() == []


def test_cron_create_persists_to_app_paths(
    client: TestClient,
    tmp_path: Path,
) -> None:
    response = client.post(
        "/api/cron",
        json={
            "name": "hourly-summary",
            "command": "python scripts/summary.py",
            "cron_expression": "0 * * * *",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "hourly-summary"
    assert data["last_status"] == "created"

    stored = json.loads(
        (tmp_path / "data" / "cron_jobs.json").read_text(encoding="utf-8"),
    )
    assert stored == [data]
    assert client.get("/api/cron/").json() == [data]


def test_cron_create_replaces_job_with_same_name(client: TestClient) -> None:
    client.post(
        "/api/cron",
        json={"name": "nightly", "command": "old", "cron_expression": "0 0 * * *"},
    )

    response = client.post(
        "/api/cron",
        json={"name": "nightly", "command": "new", "cron_expression": "0 1 * * *"},
    )

    assert response.status_code == 200
    jobs = client.get("/api/cron").json()
    assert jobs == [
        {
            "name": "nightly",
            "command": "new",
            "cron_expression": "0 1 * * *",
            "last_run": None,
            "last_status": "created",
            "last_output": None,
            # ``creator_actor`` is ``"*"`` when no identity store is wired
            # (test default). Anonymous bucket — admins and anons both
            # see/edit these entries.
            "creator_actor": "*",
        }
    ]


def test_cron_create_rejects_invalid_expression(client: TestClient) -> None:
    response = client.post(
        "/api/cron",
        json={"name": "bad", "command": "echo bad", "cron_expression": "* * *"},
    )

    assert response.status_code == 400
    assert "invalid cron expression" in response.json()["detail"]


def test_cron_delete(client: TestClient) -> None:
    client.post(
        "/api/cron",
        json={"name": "cleanup", "command": "echo clean", "cron_expression": "0 * * * *"},
    )

    response = client.delete("/api/cron/cleanup")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "deleted": "cleanup"}
    assert client.get("/api/cron").json() == []


def test_cron_delete_missing_returns_404(client: TestClient) -> None:
    response = client.delete("/api/cron/missing")

    assert response.status_code == 404
