from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.execution.suckers import computer_macos, computer_skills
from runtime.sensing.gateway.computer_router import create_computer_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_computer_router())
    return TestClient(app)


def test_execute_prefers_native_semantic_action_over_coordinate_click(monkeypatch):
    clicks: list[tuple[int, int]] = []
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)
    monkeypatch.setattr(
        computer_macos,
        "perform_accessibility_action",
        lambda target, **kwargs: {
            "backend": "macos-accessibility",
            "action": kwargs["action"],
            "title": target["title"],
        },
    )
    monkeypatch.setattr(
        computer_skills,
        "_mouse_click",
        lambda x, y, **_kwargs: clicks.append((x, y)) or {"clicked": True},
    )
    client = _client()
    preview = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "click",
            "x": 12,
            "y": 34,
            "semantic_action": "press",
            "semantic_target": {"role": "AXButton", "title": "Save"},
        },
    ).json()

    executed = client.post(
        "/api/computer/actions/execute",
        json={"token": preview["token"]},
    ).json()

    assert executed["result"]["semantic"] is True
    assert executed["result"]["coordinate_fallback"] is False
    assert clicks == []


def test_appshot_returns_stable_ids_and_incremental_delta(monkeypatch):
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)

    def capture_appshot(path: str, **_kwargs):
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"appshot")
        return {"path": str(target), "size_bytes": 7, "backend": "macos-native"}

    snapshots = iter(
        [
            [
                {
                    "index": 0,
                    "role": "AXButton",
                    "title": "Save",
                    "position": [0, 0],
                    "size": [40, 20],
                }
            ],
            [
                {
                    "index": 0,
                    "role": "AXButton",
                    "title": "Done",
                    "position": [0, 0],
                    "size": [40, 20],
                }
            ],
        ]
    )
    monkeypatch.setattr(computer_skills, "_screen_capture", capture_appshot)
    monkeypatch.setattr(
        computer_macos,
        "accessibility_snapshot",
        lambda **_kwargs: {
            "available": True,
            "backend": "macos-accessibility",
            "app": {"id": "com.example.App", "displayName": "Example"},
            "window": {"id": "42-0", "title": "Example document"},
            "elements": next(snapshots),
        },
    )
    client = _client()
    first = client.post("/api/computer/appshot", json={}).json()
    second = client.post(
        "/api/computer/appshot",
        json={"previous_snapshot_id": first["snapshot_id"]},
    ).json()

    assert first["accessibility"]["elements"][0]["semantic_id"].startswith("ax-")
    delta = second["accessibility_delta"]
    assert delta["from_snapshot_id"] == first["snapshot_id"]
    assert [item["title"] for item in delta["added"]] == ["Done"]
    assert [item["title"] for item in delta["removed"]] == ["Save"]
    assert delta["unchanged_count"] == 0


def test_plan_actions_prefers_macos_semantic_control(monkeypatch):
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)
    monkeypatch.setattr(
        computer_macos,
        "accessibility_snapshot",
        lambda **_kwargs: {
            "available": True,
            "elements": [
                {
                    "index": 0,
                    "role": "AXButton",
                    "title": "Save",
                    "position": [100, 200],
                    "size": [80, 40],
                    "enabled": True,
                }
            ],
        },
    )

    data = (
        _client()
        .post(
            "/api/computer/actions/plan",
            json={"goal": "click Save", "capture": False},
        )
        .json()
    )

    action = data["suggestions"][0]["action"]
    assert action["source"] == "macos-accessibility"
    assert action["semantic_action"] == "press"
    assert action["semantic_target"]["title"] == "Save"
    assert (action["x"], action["y"]) == (140, 220)

