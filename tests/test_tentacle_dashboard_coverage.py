"""Dense coverage for tentacle dashboard endpoints (audit Q-05)."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.tentacle.dashboard import create_tentacle_router


def _coordinator(**kw) -> SimpleNamespace:
    return SimpleNamespace(
        pool=SimpleNamespace(all_online=lambda: None, _tentacles={}),
        stats=lambda: {"devices": 0},
        pc_screen_capture=kw.get("pc_screen_capture"),
    )


def _client(coordinator) -> TestClient:
    app = FastAPI()
    app.include_router(create_tentacle_router(coordinator, require_auth=False))
    return TestClient(app)


def test_tasks_and_stats() -> None:
    client = _client(_coordinator())
    tasks = client.get("/api/tentacle/tasks")
    assert tasks.status_code == 200 and tasks.json() == []
    stats = client.get("/api/tentacle/stats")
    assert stats.status_code == 200 and stats.json() == {"devices": 0}


def test_pc_screen_stats_none_and_present() -> None:
    client = _client(_coordinator(pc_screen_capture=None))
    assert client.get("/api/tentacle/pc-screen/stats").json() == {"running": False}

    fake_capture = SimpleNamespace(stats={"running": True, "frame_count": 5})
    client2 = _client(_coordinator(pc_screen_capture=fake_capture))
    stats = client2.get("/api/tentacle/pc-screen/stats").json()
    assert stats["running"] is True and stats["frame_count"] == 5


def test_devices_empty() -> None:
    client = _client(_coordinator())
    assert client.get("/api/tentacle/devices").json() == []

