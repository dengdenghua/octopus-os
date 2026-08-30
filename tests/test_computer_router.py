from __future__ import annotations

import platform
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.execution.suckers import computer_skills, computer_uia_skills
from runtime.sensing.gateway.computer_router import create_computer_router


class _Rect:
    left = 0
    top = 0
    right = 200
    bottom = 100


class _Control:
    Name = "Router Button"
    ControlTypeName = "ButtonControl"
    ClassName = "Button"
    AutomationId = "routerButton"
    BoundingRectangle = _Rect()
    IsEnabled = True
    IsOffscreen = False

    def GetChildren(self) -> list[Any]:  # noqa: N802 - mirrors uiautomation API
        return []


class _FakeUia:
    def GetRootControl(self) -> _Control:  # noqa: N802 - mirrors uiautomation API
        return _Control()

    def GetForegroundControl(self) -> _Control:  # noqa: N802 - mirrors uiautomation API
        return _Control()


class _DynamicRect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _DynamicControl:
    def __init__(
        self,
        name: str,
        control_type: str,
        rect: _DynamicRect,
        *,
        children: list[Any] | None = None,
    ) -> None:
        self.Name = name
        self.ControlTypeName = control_type
        self.ClassName = control_type.replace("Control", "")
        self.AutomationId = name.replace(" ", "")
        self.BoundingRectangle = rect
        self.IsEnabled = True
        self.IsOffscreen = False
        self._children = children or []

    def GetChildren(self) -> list[Any]:  # noqa: N802 - mirrors uiautomation API
        return self._children


class _FakeRankedUia:
    def __init__(self) -> None:
        self.button = _DynamicControl(
            "Router Button",
            "ButtonControl",
            _DynamicRect(300, 200, 420, 260),
        )
        self.root = _DynamicControl(
            "Router",
            "WindowControl",
            _DynamicRect(0, 0, 800, 600),
            children=[self.button],
        )

    def GetRootControl(self) -> _DynamicControl:  # noqa: N802 - mirrors uiautomation API
        return self.root

    def GetForegroundControl(self) -> _DynamicControl:  # noqa: N802 - mirrors uiautomation API
        return self.root


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(create_computer_router())
    return app


def test_uia_status_endpoint_reports_unavailable(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", False)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", None)
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)

    data = TestClient(_app()).get("/api/computer/uia/status").json()
    assert data["ok"] is False
    assert data["available"] is False
    assert "uiautomation not installed" in data["error"]


def test_uia_tree_and_find_endpoints(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", True)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", _FakeUia())
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)

    client = TestClient(_app())
    tree = client.get("/api/computer/uia/tree").json()
    assert tree["ok"] is True
    assert tree["nodes"][0]["name"] == "Router Button"

    found = client.get("/api/computer/uia/find", params={"query": "router"}).json()
    assert found["ok"] is True
    assert found["count"] == 1
    assert found["matches"][0]["automation_id"] == "routerButton"


def test_plan_actions_uses_uia_grounding(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", True)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", _FakeUia())
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)

    data = (
        TestClient(_app())
        .post(
            "/api/computer/actions/plan",
            json={"goal": "click Router", "capture": False},
        )
        .json()
    )
    assert data["ok"] is True
    action = data["suggestions"][0]["action"]
    assert action["action"] == "click"
    assert action["x"] == 100
    assert action["y"] == 50
    assert action["source"] == "uia"
    assert action["matched_control"]["name"] == "Router Button"
    assert data["suggestions"][0]["token"]


def test_plan_actions_prefers_interactive_uia_match(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", True)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", _FakeRankedUia())
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)

    data = (
        TestClient(_app())
        .post(
            "/api/computer/actions/plan",
            json={"goal": "click Router", "capture": False},
        )
        .json()
    )
    action = data["suggestions"][0]["action"]
    assert action["x"] == 360
    assert action["y"] == 230
    assert action["matched_control"]["name"] == "Router Button"
    assert action["matched_control"]["score"] > 100


def test_execute_claims_computer_lease(monkeypatch):
    monkeypatch.setattr(
        computer_skills,
        "_mouse_move",
        lambda **kwargs: {"moved": True, **kwargs},
    )
    client = TestClient(_app())

    preview = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "move",
            "x": 10,
            "y": 20,
            "lease_owner_id": "project-a",
            "lease_owner_label": "Project A",
        },
    ).json()
    result = client.post(
        "/api/computer/actions/execute",
        json={"token": preview["token"], "lease_owner_id": "project-a"},
    ).json()

    assert result["ok"] is True
    assert result["lease"]["held"] is True
    assert result["lease"]["owner_id"] == "project-a"
    status = client.get("/api/computer/status").json()
    assert status["lease"]["owner_label"] == "Project A"


def test_execute_rejects_when_another_project_holds_lease(monkeypatch):
    monkeypatch.setattr(
        computer_skills,
        "_mouse_move",
        lambda **kwargs: {"moved": True, **kwargs},
    )
    client = TestClient(_app())

    first = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "move",
            "x": 10,
            "y": 20,
            "lease_owner_id": "project-a",
            "lease_owner_label": "Project A",
        },
    ).json()
    client.post(
        "/api/computer/actions/execute",
        json={"token": first["token"], "lease_owner_id": "project-a"},
    )

    second = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "move",
            "x": 30,
            "y": 40,
            "lease_owner_id": "project-b",
            "lease_owner_label": "Project B",
        },
    ).json()
    blocked = client.post(
        "/api/computer/actions/execute",
        json={"token": second["token"], "lease_owner_id": "project-b"},
    )

    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["lease"]["owner_id"] == "project-a"


def test_release_lease_allows_next_project_to_execute(monkeypatch):
    monkeypatch.setattr(
        computer_skills,
        "_mouse_move",
        lambda **kwargs: {"moved": True, **kwargs},
    )
    client = TestClient(_app())

    first = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "move",
            "x": 10,
            "y": 20,
            "lease_owner_id": "project-a",
        },
    ).json()
    client.post(
        "/api/computer/actions/execute",
        json={"token": first["token"], "lease_owner_id": "project-a"},
    )
    released = client.post(
        "/api/computer/lease/release",
        json={"lease_owner_id": "project-a"},
    ).json()
    assert released["lease"]["held"] is False

    second = client.post(
        "/api/computer/actions/preview",
        json={
            "action": "move",
            "x": 30,
            "y": 40,
            "lease_owner_id": "project-b",
        },
    ).json()
    result = client.post(
        "/api/computer/actions/execute",
        json={"token": second["token"], "lease_owner_id": "project-b"},
    ).json()
    assert result["ok"] is True
    assert result["lease"]["owner_id"] == "project-b"
