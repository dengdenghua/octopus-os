"""Implementation note."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.execution.suckers import computer_skills
from runtime.execution.suckers.computer_skills import (
    COMPUTER_SKILL_NAMES,
    _keyboard_press,
    _keyboard_type,
    _mouse_click,
    _mouse_move,
    _screen_capture,
    _screen_info,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakePoint:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class _FakeImage:
    def __init__(self, saved_paths: list[str]) -> None:
        self._saved = saved_paths

    def save(self, path: str) -> None:
        self._saved.append(path)
        # Implementation note.
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)


class _FakePyAutoGUI:
    FAILSAFE = True

    def __init__(self, screen=(1920, 1080), cursor=(100, 200)) -> None:
        self._screen = screen
        self._cursor = cursor
        self.clicks: list[dict[str, Any]] = []
        self.moves: list[dict[str, Any]] = []
        self.writes: list[tuple[str, float]] = []
        self.presses: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.screenshots: list[Any] = []
        self.saved_paths: list[str] = []
        self.raise_on: set[str] = set()

    def size(self):
        if "size" in self.raise_on:
            raise RuntimeError("no display")
        return self._screen

    def position(self):
        return _FakePoint(*self._cursor)

    def click(self, x=0, y=0, clicks=1, button="left", duration=0.0):
        if "click" in self.raise_on:
            raise RuntimeError("failsafe")
        self.clicks.append({"x": x, "y": y, "clicks": clicks, "button": button})

    def moveTo(self, x=0, y=0, duration=0.0):  # noqa: N802 — pyautogui shim
        self.moves.append({"x": x, "y": y, "duration": duration})

    def write(self, text, interval=0.0):
        self.writes.append((text, interval))

    def press(self, key):
        self.presses.append(key)

    def hotkey(self, *keys):
        self.hotkeys.append(tuple(keys))

    def screenshot(self, region=None):
        self.screenshots.append(region)
        return _FakeImage(self.saved_paths)


@pytest.fixture
def fake_pyautogui(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setattr(computer_skills, "pyautogui", fake)
    monkeypatch.setattr(computer_skills, "PYAUTOGUI_AVAILABLE", True)
    return fake


# ═══════════════════════════════════════════════════════════
# screen_info
# ═══════════════════════════════════════════════════════════


class TestScreenInfo:
    def test_returns_size_and_cursor(self, fake_pyautogui):
        r = _screen_info()
        assert r["width"] == 1920
        assert r["height"] == 1080
        assert r["cursor_x"] == 100
        assert r["cursor_y"] == 200

    def test_not_installed(self, monkeypatch):
        monkeypatch.setattr(computer_skills, "pyautogui", None)
        monkeypatch.setattr(computer_skills, "PYAUTOGUI_AVAILABLE", False)
        monkeypatch.setattr(computer_skills.computer_macos, "MACOS_NATIVE_AVAILABLE", False)
        r = _screen_info()
        assert "error" in r
        assert "pyautogui" in r["error"]

    def test_display_failure(self, fake_pyautogui):
        fake_pyautogui.raise_on.add("size")
        r = _screen_info()
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# screen_capture · path_guard + region
# ═══════════════════════════════════════════════════════════


class TestScreenCapture:
    def test_full_screen(self, fake_pyautogui, tmp_path: Path):
        out = tmp_path / "shot.png"
        r = _screen_capture(
            path=str(out),
            sandbox_dir=str(tmp_path),
        )
        assert "error" not in r
        assert r["size_bytes"] > 0
        assert out.exists()
        assert fake_pyautogui.screenshots == [None]

    def test_region_forwarded(self, fake_pyautogui, tmp_path: Path):
        out = tmp_path / "crop.png"
        r = _screen_capture(
            path=str(out),
            sandbox_dir=str(tmp_path),
            region=[100, 100, 200, 200],
        )
        assert "error" not in r
        assert fake_pyautogui.screenshots == [(100, 100, 200, 200)]

    def test_missing_path(self, fake_pyautogui):
        r = _screen_capture(path="")
        assert "error" in r

    def test_path_outside_sandbox_rejected(self, fake_pyautogui, tmp_path: Path):
        outside = tmp_path / "other"
        outside.mkdir()
        sandbox = tmp_path / "sandbox"
        sandbox.mkdir()
        r = _screen_capture(
            path=str(outside / "x.png"),
            sandbox_dir=str(sandbox),
        )
        assert "error" in r
        assert "path_blocked" in r["error"]

    def test_region_bad_shape(self, fake_pyautogui, tmp_path: Path):
        out = tmp_path / "x.png"
        r = _screen_capture(
            path=str(out),
            sandbox_dir=str(tmp_path),
            region=[1, 2, 3],  # Implementation note.
        )
        assert "error" in r

    def test_region_negative_rejected(self, fake_pyautogui, tmp_path: Path):
        out = tmp_path / "x.png"
        r = _screen_capture(
            path=str(out),
            sandbox_dir=str(tmp_path),
            region=[-1, 0, 100, 100],
        )
        assert "error" in r

    def test_region_zero_size_rejected(self, fake_pyautogui, tmp_path: Path):
        out = tmp_path / "x.png"
        r = _screen_capture(
            path=str(out),
            sandbox_dir=str(tmp_path),
            region=[0, 0, 0, 100],
        )
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMouseClick:
    def test_basic_click(self, fake_pyautogui):
        r = _mouse_click(x=500, y=400)
        assert "error" not in r
        assert r["clicked_at"] == {"x": 500, "y": 400}
        assert fake_pyautogui.clicks == [
            {"x": 500, "y": 400, "clicks": 1, "button": "left"},
        ]

    def test_right_double_click(self, fake_pyautogui):
        r = _mouse_click(x=10, y=10, button="right", clicks=2)
        assert "error" not in r
        assert fake_pyautogui.clicks[0]["button"] == "right"
        assert fake_pyautogui.clicks[0]["clicks"] == 2

    def test_out_of_bounds_rejected(self, fake_pyautogui):
        r = _mouse_click(x=5000, y=5000)
        assert "error" in r
        assert "out of bounds" in r["error"]

    def test_negative_coord_rejected(self, fake_pyautogui):
        r = _mouse_click(x=-1, y=0)
        assert "error" in r

    def test_invalid_button(self, fake_pyautogui):
        r = _mouse_click(x=0, y=0, button="middle_right")
        assert "error" in r

    def test_clicks_out_of_range(self, fake_pyautogui):
        assert "error" in _mouse_click(x=0, y=0, clicks=0)
        assert "error" in _mouse_click(x=0, y=0, clicks=10)

    def test_click_raises_wraps_error(self, fake_pyautogui):
        fake_pyautogui.raise_on.add("click")
        r = _mouse_click(x=0, y=0)
        assert "error" in r
        assert "click_failed" in r["error"]

    def test_negative_duration_rejected(self, fake_pyautogui):
        r = _mouse_click(x=0, y=0, duration=-1)
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# mouse_move
# ═══════════════════════════════════════════════════════════


class TestMouseMove:
    def test_move_to_coord(self, fake_pyautogui):
        r = _mouse_move(x=800, y=600, duration=0.5)
        assert "error" not in r
        assert fake_pyautogui.moves == [
            {"x": 800, "y": 600, "duration": 0.5},
        ]

    def test_out_of_bounds(self, fake_pyautogui):
        r = _mouse_move(x=99999, y=0)
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# keyboard_type
# ═══════════════════════════════════════════════════════════


class TestKeyboardType:
    def test_types_text(self, fake_pyautogui):
        r = _keyboard_type(text="hello world")
        assert "error" not in r
        assert r["text_len"] == 11
        assert fake_pyautogui.writes == [("hello world", 0.01)]

    def test_missing_text(self, fake_pyautogui):
        r = _keyboard_type(text="")
        assert "error" in r

    def test_non_str_rejected(self, fake_pyautogui):
        r = _keyboard_type(text=123)  # type: ignore[arg-type]
        assert "error" in r

    def test_too_long_rejected(self, fake_pyautogui):
        r = _keyboard_type(text="x" * 10_001)
        assert "error" in r
        assert "too long" in r["error"]

    def test_negative_interval(self, fake_pyautogui):
        r = _keyboard_type(text="hi", interval=-0.1)
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# keyboard_press
# ═══════════════════════════════════════════════════════════


class TestKeyboardPress:
    def test_single_key(self, fake_pyautogui):
        r = _keyboard_press(keys=["enter"])
        assert "error" not in r
        assert fake_pyautogui.presses == ["enter"]

    def test_combo(self, fake_pyautogui):
        r = _keyboard_press(keys=["ctrl", "c"])
        assert "error" not in r
        assert fake_pyautogui.hotkeys == [("ctrl", "c")]

    def test_triple_combo(self, fake_pyautogui):
        _keyboard_press(keys=["ctrl", "shift", "t"])
        assert fake_pyautogui.hotkeys == [("ctrl", "shift", "t")]

    def test_empty_keys_rejected(self, fake_pyautogui):
        assert "error" in _keyboard_press(keys=[])
        assert "error" in _keyboard_press(keys=None)

    def test_non_list_rejected(self, fake_pyautogui):
        r = _keyboard_press(keys="enter")  # type: ignore[arg-type]
        assert "error" in r

    def test_non_str_element_rejected(self, fake_pyautogui):
        r = _keyboard_press(keys=["ctrl", 123])  # type: ignore[list-item]
        assert "error" in r

    def test_empty_str_key_rejected(self, fake_pyautogui):
        r = _keyboard_press(keys=["ctrl", ""])
        assert "error" in r


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRegistration:
    def test_register_all_does_not_include_computer(self):
        """Implementation note."""
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.builtins import register_all

        reg = SkillRegistry()
        register_all(reg)
        names = set(reg.all_names())
        for n in COMPUTER_SKILL_NAMES:
            assert n not in names, f"{n} should not be auto-registered"

    def test_register_computer_skills_returns_all_when_available(
        self,
        fake_pyautogui,
    ):
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.computer_skills import (
            register_computer_skills,
        )

        reg = SkillRegistry()
        n = register_computer_skills(reg)
        assert n == len(COMPUTER_SKILL_NAMES)
        for name in COMPUTER_SKILL_NAMES:
            assert reg.has(name)

    def test_register_returns_0_when_not_installed(self, monkeypatch):
        monkeypatch.setattr(computer_skills, "PYAUTOGUI_AVAILABLE", False)
        monkeypatch.setattr(computer_skills.computer_macos, "MACOS_NATIVE_AVAILABLE", False)
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.computer_skills import (
            register_computer_skills,
        )

        reg = SkillRegistry()
        assert register_computer_skills(reg) == 0
