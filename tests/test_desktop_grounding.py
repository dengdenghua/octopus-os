"""Desktop semantic grounding for the vision loop — incl. Windows UIA."""

from __future__ import annotations

import sys

from runtime.execution.suckers.desktop_grounding import (
    _format_uia_grounding,
    combined_grounding,
    uia_control_grounding,
)


def test_format_uia_grounding_emits_actionable_controls() -> None:
    tree = {
        "ok": True,
        "tree": {"name": "Notepad"},
        "nodes": [
            {
                "control_type": "Button",
                "name": "Save",
                "center": {"x": 100, "y": 50},
                "enabled": True,
                "offscreen": False,
            },
            {
                "control_type": "Edit",
                "name": "",
                "center": {"x": 200, "y": 150},
                "enabled": True,
                "offscreen": False,
            },
            {
                "control_type": "Text",
                "name": "a label",  # not actionable
                "center": {"x": 10, "y": 10},
                "enabled": True,
            },
            {
                "control_type": "Button",
                "name": "Hidden",  # offscreen
                "center": {"x": 1, "y": 1},
                "enabled": True,
                "offscreen": True,
            },
            {
                "control_type": "Button",
                "name": "Disabled",  # disabled
                "center": {"x": 2, "y": 2},
                "enabled": False,
            },
            {
                "control_type": "Button",
                "name": "NoCenter",  # no center
                "center": None,
                "enabled": True,
            },
        ],
    }
    out = _format_uia_grounding(tree, max_elements=25)
    assert "Notepad" in out
    assert "Button 'Save' @ (100,50)" in out
    assert "Edit '' @ (200,150)" in out
    assert "Text" not in out  # filtered: not actionable
    assert "Hidden" not in out  # filtered: offscreen
    assert "Disabled" not in out  # filtered: disabled
    assert "NoCenter" not in out  # filtered: no resolvable center


def test_format_uia_grounding_caps_elements() -> None:
    nodes = [
        {"control_type": "Button", "name": f"b{i}", "center": {"x": i, "y": i}, "enabled": True}
        for i in range(50)
    ]
    out = _format_uia_grounding({"ok": True, "tree": {"name": "X"}, "nodes": nodes}, 5)
    assert out.count("Button") == 5  # capped at max_elements
    assert "b4" in out and "b5" not in out


def test_format_uia_grounding_handles_empty_and_failed() -> None:
    assert _format_uia_grounding({"ok": False}, 25) == ""
    assert _format_uia_grounding({"ok": True, "nodes": []}, 25) == ""
    assert _format_uia_grounding("not a dict", 25) == ""


def test_uia_grounding_is_noop_off_windows() -> None:
    if sys.platform != "win32":
        assert uia_control_grounding() == ""
    # combined_grounding must never raise and always return a string
    assert isinstance(combined_grounding(), str)

