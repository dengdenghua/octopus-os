from __future__ import annotations

import platform
from typing import Any

import pytest

from runtime.execution.suckers import computer_uia_skills
from runtime.execution.suckers.computer_uia_skills import (
    UIA_SKILL_NAMES,
    _computer_uia_find,
    _computer_uia_status,
    _computer_uia_tree,
    register_computer_uia_skills,
)


class _Rect:
    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Control:
    def __init__(
        self,
        *,
        name: str,
        control_type: str,
        rect: _Rect | None = None,
        automation_id: str = "",
        class_name: str = "",
        enabled: bool = True,
        offscreen: bool = False,
        children: list[Any] | None = None,
    ) -> None:
        self.Name = name
        self.ControlTypeName = control_type
        self.BoundingRectangle = rect
        self.AutomationId = automation_id
        self.ClassName = class_name
        self.IsEnabled = enabled
        self.IsOffscreen = offscreen
        self._children = children or []

    def GetChildren(self) -> list[Any]:  # noqa: N802 - mirrors uiautomation API
        return self._children


class _FakeUia:
    def __init__(self, root: _Control, foreground: _Control | None = None) -> None:
        self.root = root
        self.foreground = foreground or root

    def GetRootControl(self) -> _Control:  # noqa: N802 - mirrors uiautomation API
        return self.root

    def GetForegroundControl(self) -> _Control:  # noqa: N802 - mirrors uiautomation API
        return self.foreground


@pytest.fixture
def fake_uia(monkeypatch):
    ok = _Control(
        name="OK",
        control_type="ButtonControl",
        rect=_Rect(10, 20, 110, 60),
        automation_id="okButton",
        class_name="Button",
    )
    hidden = _Control(
        name="Hidden",
        control_type="TextControl",
        rect=_Rect(0, 0, 10, 10),
        offscreen=True,
    )
    edit = _Control(
        name="Search",
        control_type="EditControl",
        rect=_Rect(120, 20, 420, 60),
        automation_id="searchBox",
        class_name="Edit",
    )
    window = _Control(
        name="Demo Window",
        control_type="WindowControl",
        rect=_Rect(0, 0, 800, 600),
        automation_id="demoWindow",
        class_name="DemoApp",
        children=[ok, hidden, edit],
    )
    fake = _FakeUia(root=window, foreground=window)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", True)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", fake)
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)
    return fake


def test_status_reports_missing_dependency(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(computer_uia_skills, "UIA_AVAILABLE", False)
    monkeypatch.setattr(computer_uia_skills, "uiautomation", None)
    monkeypatch.setattr(computer_uia_skills, "_UIA_LOAD_ERROR", None)
    result = _computer_uia_status()
    assert result["ok"] is False
    assert "uiautomation not installed" in result["error"]


def test_status_reports_non_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    result = _computer_uia_status()
    assert result["ok"] is False
    assert "Windows" in result["error"]


def test_tree_returns_bounded_semantic_nodes(fake_uia):
    result = _computer_uia_tree(max_depth=2, max_nodes=10)
    assert result["ok"] is True
    assert result["count"] == 3
    names = [node["name"] for node in result["nodes"]]
    assert names == ["Demo Window", "OK", "Search"]
    ok_node = next(node for node in result["nodes"] if node["name"] == "OK")
    assert ok_node["interactive"] is True
    assert ok_node["center"] == {"x": 60, "y": 40}


def test_tree_can_include_offscreen_nodes(fake_uia):
    result = _computer_uia_tree(max_depth=2, max_nodes=10, include_offscreen=True)
    names = [node["name"] for node in result["nodes"]]
    assert "Hidden" in names


def test_tree_truncates_by_max_nodes(fake_uia):
    result = _computer_uia_tree(max_depth=2, max_nodes=2)
    assert result["ok"] is True
    assert result["count"] == 2
    assert result["truncated"] is True


def test_find_matches_name_and_automation_id(fake_uia):
    by_name = _computer_uia_find(query="ok")
    assert by_name["ok"] is True
    assert by_name["count"] == 1
    assert by_name["matches"][0]["automation_id"] == "okButton"

    by_id = _computer_uia_find(query="searchbox")
    assert by_id["count"] == 1
    assert by_id["matches"][0]["name"] == "Search"


def test_find_requires_query(fake_uia):
    result = _computer_uia_find(query="")
    assert result["ok"] is False
    assert "missing query" in result["error"]


def test_registers_uia_skills():
    from runtime.execution.suckers import SkillRegistry

    reg = SkillRegistry()
    count = register_computer_uia_skills(reg)
    assert count == len(UIA_SKILL_NAMES)
    for name in UIA_SKILL_NAMES:
        assert reg.has(name)
