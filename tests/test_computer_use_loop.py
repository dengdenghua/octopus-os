"""Implementation note."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.execution.suckers import computer_skills
from runtime.execution.suckers.computer_use_loop import (
    MockVisionPlanner,
    ModelRouterVisionPlanner,
    _parse_action_text,
    _run_computer_use_loop,
    make_computer_use_loop_skill,
    register_computer_use_loop,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakePoint:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y


class _FakeImage:
    def __init__(self, paths: list[str]) -> None:
        self._paths = paths

    def save(self, path: str) -> None:
        self._paths.append(path)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)


class _FakePyAutoGUI:
    FAILSAFE = True

    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []
        self.moves: list[dict[str, Any]] = []
        self.writes: list[tuple[str, float]] = []
        self.presses: list[str] = []
        self.hotkeys: list[tuple[str, ...]] = []
        self.saved: list[str] = []

    def size(self):
        return (1920, 1080)

    def position(self):
        return _FakePoint(100, 200)

    def click(self, x=0, y=0, clicks=1, button="left", duration=0.0):
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
        return _FakeImage(self.saved)


@pytest.fixture
def fake_pyautogui(monkeypatch):
    fake = _FakePyAutoGUI()
    monkeypatch.setattr(computer_skills, "pyautogui", fake)
    monkeypatch.setattr(computer_skills, "PYAUTOGUI_AVAILABLE", True)
    # Implementation note.
    from runtime.execution.suckers import computer_use_loop

    monkeypatch.setattr(computer_use_loop, "PYAUTOGUI_AVAILABLE", True)
    return fake


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMockPlanner:
    def test_returns_scripted_actions_in_order(self):
        p = MockVisionPlanner(
            actions=[
                {"action": "click", "x": 10, "y": 20},
                {"action": "done", "summary": "ok"},
            ]
        )
        a1 = p.next_action(goal="g", screenshot_path="/tmp/x.png", history=[])
        a2 = p.next_action(goal="g", screenshot_path="/tmp/x.png", history=[{}])
        assert a1["action"] == "click"
        assert a2["action"] == "done"
        assert len(p.calls) == 2

    def test_fail_when_script_exhausted(self):
        p = MockVisionPlanner(actions=[{"action": "done", "summary": "x"}])
        p.next_action(goal="g", screenshot_path="/tmp/x", history=[])
        a = p.next_action(goal="g", screenshot_path="/tmp/x", history=[])
        assert a["action"] == "fail"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoop:
    def test_done_exits_success(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "done", "summary": "goal satisfied"},
            ]
        )
        r = _run_computer_use_loop(
            goal="find the button",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "success"
        assert r["iterations"] == 1
        assert r["summary"] == "goal satisfied"
        assert len(r["screenshots"]) == 1

    def test_click_then_done(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "click", "x": 500, "y": 400},
                {"action": "done", "summary": "done"},
            ]
        )
        r = _run_computer_use_loop(
            goal="click it",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "success"
        assert r["iterations"] == 2
        assert len(fake_pyautogui.clicks) == 1
        assert fake_pyautogui.clicks[0]["x"] == 500

    def test_type_action(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "type", "text": "hello"},
                {"action": "done", "summary": "typed"},
            ]
        )
        _run_computer_use_loop(
            goal="type stuff",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert fake_pyautogui.writes == [("hello", 0.01)]

    def test_key_combo(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "key", "keys": ["ctrl", "a"]},
                {"action": "done", "summary": "selected"},
            ]
        )
        _run_computer_use_loop(
            goal="select all",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert fake_pyautogui.hotkeys == [("ctrl", "a")]

    def test_wait_action_sleeps(self, fake_pyautogui, tmp_path: Path):
        import time as _t

        planner = MockVisionPlanner(
            actions=[
                {"action": "wait", "ms": 50},
                {"action": "done", "summary": "x"},
            ]
        )
        t0 = _t.monotonic()
        r = _run_computer_use_loop(
            goal="sleep",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        dt = _t.monotonic() - t0
        assert r["status"] == "success"
        # Implementation note.
        assert dt >= 0.04

    def test_fail_action_exits(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "fail", "reason": "cannot find button"},
            ]
        )
        r = _run_computer_use_loop(
            goal="x",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "planner_gave_up"
        assert "cannot find" in r["reason"]

    def test_max_iterations_exit(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "click", "x": 10, "y": 10},
            ]
            * 10
        )  # Implementation note.
        r = _run_computer_use_loop(
            goal="infinite",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "max_iterations"
        assert r["iterations"] == 3
        assert len(fake_pyautogui.clicks) == 3

    def test_stop_on_error_exits(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "click", "x": -1, "y": -1},  # Implementation note.
                {"action": "done", "summary": "never"},
            ]
        )
        r = _run_computer_use_loop(
            goal="bad click",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=True,
        )
        assert r["status"] == "error"
        assert "action failed" in r["reason"]
        assert r["iterations"] == 1

    def test_continue_on_error(self, fake_pyautogui, tmp_path: Path):
        """Implementation note."""
        planner = MockVisionPlanner(
            actions=[
                {"action": "click", "x": -1, "y": -1},  # error
                {"action": "click", "x": 500, "y": 400},  # recover
                {"action": "done", "summary": "recovered"},
            ]
        )
        r = _run_computer_use_loop(
            goal="recover",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "success"
        assert r["iterations"] == 3

    def test_unknown_action_rejected(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "explode"},
            ]
        )
        r = _run_computer_use_loop(
            goal="x",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "error"
        assert "unknown action" in r["reason"]

    def test_planner_raises_captured(self, fake_pyautogui, tmp_path: Path):
        class _Bad:
            def next_action(self, **_kw):
                raise RuntimeError("planner crash")

        r = _run_computer_use_loop(
            goal="x",
            planner=_Bad(),
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=3,
            wait_between_ms=0,
            stop_on_error=False,
        )
        assert r["status"] == "error"
        assert "planner raised" in r["reason"]

    def test_history_passed_to_planner(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(
            actions=[
                {"action": "click", "x": 10, "y": 10},
                {"action": "type", "text": "hi"},
                {"action": "done", "summary": "x"},
            ]
        )
        _run_computer_use_loop(
            goal="x",
            planner=planner,
            screenshot_dir=str(tmp_path / "shots"),
            sandbox_dir=str(tmp_path),
            max_iterations=5,
            wait_between_ms=0,
            stop_on_error=False,
        )
        # Implementation note.
        assert [c["history_len"] for c in planner.calls] == [0, 1, 2]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSkillFactory:
    def test_skill_rejects_missing_goal(self, fake_pyautogui, tmp_path: Path):
        planner = MockVisionPlanner(actions=[{"action": "done", "summary": ""}])
        skill = make_computer_use_loop_skill(
            planner,
            default_screenshot_dir=str(tmp_path / "shots"),
            default_sandbox_dir=str(tmp_path),
        )
        r = skill.handler(goal="")
        assert "error" in r

    def test_skill_reject_bad_max_iterations(
        self,
        fake_pyautogui,
        tmp_path: Path,
    ):
        planner = MockVisionPlanner(actions=[{"action": "done", "summary": ""}])
        skill = make_computer_use_loop_skill(
            planner,
            default_screenshot_dir=str(tmp_path / "shots"),
            default_sandbox_dir=str(tmp_path),
        )
        assert "error" in skill.handler(goal="x", max_iterations=0)
        assert "error" in skill.handler(goal="x", max_iterations=500)

    def test_registers_into_registry(self, fake_pyautogui, tmp_path: Path):
        from runtime.execution.suckers import SkillRegistry

        planner = MockVisionPlanner(actions=[{"action": "done", "summary": ""}])
        reg = SkillRegistry()
        n = register_computer_use_loop(
            reg,
            planner,
            default_screenshot_dir=str(tmp_path / "shots"),
            default_sandbox_dir=str(tmp_path),
        )
        assert n == 1
        assert reg.has("computer_use_loop")

    def test_register_all_does_not_include_loop(self):
        """Implementation note."""
        from runtime.execution.suckers import SkillRegistry
        from runtime.execution.suckers.builtins import register_all

        reg = SkillRegistry()
        register_all(reg)
        assert not reg.has("computer_use_loop")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAnthropicPlannerParsing:
    def test_plain_json(self):
        a = _parse_action_text('{"action":"click","x":1,"y":2}')
        assert a["action"] == "click"
        assert a["x"] == 1

    def test_json_in_code_fence(self):
        a = _parse_action_text('here you go:\n```json\n{"action":"done","summary":"x"}\n```')
        assert a["action"] == "done"

    def test_json_with_surrounding_prose(self):
        a = _parse_action_text('Thinking... I should click. {"action":"click","x":5,"y":6}')
        assert a["action"] == "click"

    def test_malformed_json_becomes_fail(self):
        a = _parse_action_text("not json at all")
        assert a["action"] == "fail"

    def test_missing_action_key_becomes_fail(self):
        a = _parse_action_text('{"x":1,"y":2}')
        assert a["action"] == "fail"


class TestModelRouterVisionPlanner:
    def test_calls_router_with_images(self, tmp_path: Path):
        """Implementation note."""
        from runtime.sensing.model_router import MockModelRouter

        shot = tmp_path / "x.png"
        shot.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

        router = MockModelRouter(
            response='{"action":"click","x":10,"y":20}',
        )
        p = ModelRouterVisionPlanner(router=router, model="x")
        r = p.next_action(
            goal="g",
            screenshot_path=str(shot),
            history=[],
        )
        assert r["action"] == "click"

        # Implementation note.
        assert len(router.call_log) == 1
        req = router.call_log[0]
        assert len(req.images_b64) == 1
        assert req.images_b64[0]  # non-empty b64

    def test_router_failure_returns_fail_action(self, tmp_path: Path):
        from runtime.sensing.model_router import ModelRequest as _MR  # noqa: N814
        from runtime.sensing.model_router import ModelResponse, ModelRouter

        class _Bad(ModelRouter):
            def call(self, req: _MR) -> ModelResponse:
                raise RuntimeError("api down")

        shot = tmp_path / "x.png"
        shot.write_bytes(b"\x89PNG" + b"0" * 50)
        p = ModelRouterVisionPlanner(router=_Bad(), model="x")
        r = p.next_action(goal="g", screenshot_path=str(shot), history=[])
        assert r["action"] == "fail"
        assert "router call failed" in r["reason"]

    def test_screenshot_read_failure(self):
        from runtime.sensing.model_router import MockModelRouter

        p = ModelRouterVisionPlanner(
            router=MockModelRouter(response="{}"),
            model="x",
        )
        r = p.next_action(
            goal="g",
            screenshot_path="/nonexistent/missing.png",
            history=[],
        )
        assert r["action"] == "fail"
        assert "screenshot read" in r["reason"]
