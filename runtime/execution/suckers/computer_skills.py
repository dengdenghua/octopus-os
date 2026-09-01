from __future__ import annotations

import contextlib
import importlib.util
from pathlib import Path
from typing import Any

from . import computer_macos
from .computer_api_skills import register_computer_api_skills
from .computer_uia_skills import UIA_SKILL_NAMES, register_computer_uia_skills
from .registry import Skill, SkillRegistry
from .testing import SkillExpect, SkillTestCase

pyautogui: Any | None = None
PYAUTOGUI_AVAILABLE = importlib.util.find_spec("pyautogui") is not None
_PYAUTOGUI_LOAD_ERROR: str | None = None


_MAX_TYPE_CHARS = 10_000
_MAX_SCREENSHOT_BYTES = 20 * 1024 * 1024  # 20MB
_VALID_BUTTONS = ("left", "right", "middle")


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _check_pyautogui() -> str | None:
    global _PYAUTOGUI_LOAD_ERROR, pyautogui
    if not PYAUTOGUI_AVAILABLE:
        return "pyautogui not installed · `pip install pyautogui`"
    if pyautogui is None and _PYAUTOGUI_LOAD_ERROR is None:
        try:
            import pyautogui as _pyautogui  # type: ignore[import-untyped]

            _pyautogui.FAILSAFE = True
            pyautogui = _pyautogui
        except Exception as exc:  # noqa: BLE001
            _PYAUTOGUI_LOAD_ERROR = f"{type(exc).__name__}: {exc}"
    if pyautogui is None:
        return f"pyautogui unavailable: {_PYAUTOGUI_LOAD_ERROR or 'load failed'}"
    return None


def _native_available() -> bool:
    return PYAUTOGUI_AVAILABLE or computer_macos.MACOS_NATIVE_AVAILABLE


def _check_coord(x: int, y: int) -> str | None:
    if not isinstance(x, int) or not isinstance(y, int):
        return f"x/y must be int, got ({type(x).__name__}, {type(y).__name__})"
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        info = computer_macos.screen_info()
        if "error" in info:
            return str(info["error"])
        w, h = int(info.get("width") or 0), int(info.get("height") or 0)
        if not (0 <= x < w and 0 <= y < h):
            return f"coord ({x},{y}) out of bounds (screen is {w}x{h})"
        return None
    err = _check_pyautogui()
    if err:
        return err
    try:
        w, h = pyautogui.size()
    except Exception as e:  # noqa: BLE001
        return f"screen_size_failed: {type(e).__name__}: {e}"
    if not (0 <= x < w and 0 <= y < h):
        return f"coord ({x},{y}) out of bounds (screen is {w}x{h})"
    return None


# ═══════════════════════════════════════════════════════════
# screen_capture / screen_info
# ═══════════════════════════════════════════════════════════


def _screen_capture(
    path: str = "",
    *,
    sandbox_dir: str | None = None,
    region: list[int] | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not path:
        return {"error": "missing path"}

    from runtime.safety.auth.path_guard import check_path

    verdict = check_path(path, sandbox_dir=sandbox_dir)
    if not verdict.allow:
        return {"error": f"path_blocked: {verdict.reason}"}
    resolved = verdict.resolved or path

    if region is not None:
        if not isinstance(region, list) or len(region) != 4:
            return {"error": "region must be [x, y, w, h]"}
        if not all(isinstance(i, int) for i in region):
            return {"error": "region values must be int"}
        x, y, w, h = region
        if x < 0 or y < 0 or w <= 0 or h <= 0:
            return {"error": f"region invalid: {region}"}

    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        result = computer_macos.capture_screen(str(resolved), region)
        if "error" in result:
            return result
    else:
        err = _check_pyautogui()
        if err:
            return {"error": err}
        try:
            if region is not None:
                img = pyautogui.screenshot(region=tuple(region))
            else:
                img = pyautogui.screenshot()
            img.save(str(resolved))
        except Exception as e:  # noqa: BLE001
            return {"error": f"capture_failed: {type(e).__name__}: {e}"}

    size = 0
    with contextlib.suppress(OSError):
        size = Path(str(resolved)).stat().st_size
    if size > _MAX_SCREENSHOT_BYTES:
        with contextlib.suppress(OSError):
            Path(str(resolved)).unlink()
        return {
            "error": f"screenshot too large: {size} > {_MAX_SCREENSHOT_BYTES}",
            "path": str(resolved),
        }
    return {
        "path": str(resolved),
        "size_bytes": size,
        "region": region,
    }


def _screen_info(**_kw: Any) -> dict[str, Any]:
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        return computer_macos.screen_info()
    err = _check_pyautogui()
    if err:
        return {"error": err}
    try:
        w, h = pyautogui.size()
        pos = pyautogui.position()
    except Exception as e:  # noqa: BLE001
        return {"error": f"screen_info_failed: {type(e).__name__}: {e}"}
    return {
        "width": w,
        "height": h,
        "cursor_x": pos.x if hasattr(pos, "x") else pos[0],
        "cursor_y": pos.y if hasattr(pos, "y") else pos[1],
    }


# ═══════════════════════════════════════════════════════════
# mouse_click / mouse_move
# ═══════════════════════════════════════════════════════════


def _mouse_click(
    x: int = -1,
    y: int = -1,
    *,
    button: str = "left",
    clicks: int = 1,
    duration: float = 0.0,
    **_kw: Any,
) -> dict[str, Any]:
    err = _check_coord(x, y)
    if err:
        return {"error": err}
    if button not in _VALID_BUTTONS:
        return {"error": f"invalid button: {button!r} (allowed: {_VALID_BUTTONS})"}
    if not isinstance(clicks, int) or not (1 <= clicks <= 5):
        return {"error": f"clicks must be int in [1,5], got {clicks!r}"}
    if not isinstance(duration, (int, float)) or duration < 0:
        return {"error": f"duration must be >= 0, got {duration!r}"}
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        return computer_macos.click_mouse(x, y, button=button, clicks=clicks)
    try:
        pyautogui.click(
            x=x,
            y=y,
            clicks=clicks,
            button=button,
            duration=duration,
        )
    except Exception as e:  # noqa: BLE001
        return {"error": f"click_failed: {type(e).__name__}: {e}"}
    return {
        "clicked_at": {"x": x, "y": y},
        "button": button,
        "clicks": clicks,
    }


def _mouse_move(
    x: int = -1,
    y: int = -1,
    *,
    duration: float = 0.2,
    **_kw: Any,
) -> dict[str, Any]:
    err = _check_coord(x, y)
    if err:
        return {"error": err}
    if not isinstance(duration, (int, float)) or duration < 0:
        return {"error": f"duration must be >= 0, got {duration!r}"}
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        result = computer_macos.move_mouse(x, y)
        if "error" not in result:
            result["duration"] = duration
        return result
    try:
        pyautogui.moveTo(x=x, y=y, duration=duration)
    except Exception as e:  # noqa: BLE001
        return {"error": f"move_failed: {type(e).__name__}: {e}"}
    return {"moved_to": {"x": x, "y": y}, "duration": duration}


# ═══════════════════════════════════════════════════════════
# keyboard_type / keyboard_press
# ═══════════════════════════════════════════════════════════


def _keyboard_type(
    text: str = "",
    *,
    interval: float = 0.01,
    **_kw: Any,
) -> dict[str, Any]:
    if not text:
        return {"error": "missing text"}
    if not isinstance(text, str):
        return {"error": f"text must be str, got {type(text).__name__}"}
    if len(text) > _MAX_TYPE_CHARS:
        return {"error": f"text too long: {len(text)} > {_MAX_TYPE_CHARS}"}
    if not isinstance(interval, (int, float)) or interval < 0:
        return {"error": f"interval must be >= 0, got {interval!r}"}
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        result = computer_macos.type_text(text)
        if "error" not in result:
            result["interval"] = interval
        return result
    err = _check_pyautogui()
    if err:
        return {"error": err}
    try:
        pyautogui.write(text, interval=interval)
    except Exception as e:  # noqa: BLE001
        return {"error": f"type_failed: {type(e).__name__}: {e}"}
    return {"text_len": len(text), "interval": interval}


def _keyboard_press(
    keys: list[str] | None = None,
    **_kw: Any,
) -> dict[str, Any]:
    if not keys:
        return {"error": "keys must be a non-empty list"}
    if not isinstance(keys, list):
        return {"error": f"keys must be list, got {type(keys).__name__}"}
    for k in keys:
        if not isinstance(k, str) or not k:
            return {"error": f"invalid key: {k!r}"}
    if not PYAUTOGUI_AVAILABLE and computer_macos.MACOS_NATIVE_AVAILABLE:
        return computer_macos.press_keys(keys)
    err = _check_pyautogui()
    if err:
        return {"error": err}
    try:
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
    except Exception as e:  # noqa: BLE001
        return {"error": f"press_failed: {type(e).__name__}: {e}"}
    return {"pressed": list(keys)}


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


COMPUTER_SKILL_NAMES = [
    "screen_capture",
    "screen_info",
    "mouse_click",
    "mouse_move",
    "keyboard_type",
    "keyboard_press",
    "computer_observe",
    "computer_plan_next",
    "computer_preview_action",
    "computer_execute_token",
    *UIA_SKILL_NAMES,
]


def register_computer_skills(
    registry: SkillRegistry,
    *,
    verify_tests: bool = True,
) -> int:
    if not _native_available():
        return 0

    registry.register(
        Skill(
            name="screen_capture",
            description="Capture screen (or region [x,y,w,h]) to PNG · path_guard enforced.",
            affinity=["desktop", "capture"],
            cost_profile="low",
            trusted_source="skill://public/screen_capture",
            handler=_screen_capture,
            tests=[
                SkillTestCase(
                    name="missing_path",
                    tier="golden",
                    args={"path": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        ),
        verify_tests=verify_tests,
    )
    registry.register(
        Skill(
            name="screen_info",
            description="Return screen width/height and current cursor position.",
            affinity=["desktop", "info"],
            cost_profile="low",
            trusted_source="skill://public/screen_info",
            handler=_screen_info,
            tests=[],
        ),
        verify_tests=verify_tests,
    )
    registry.register(
        Skill(
            name="mouse_click",
            description="Click at (x,y) · button=left|right|middle · clicks ≤ 5 · bounds-checked.",
            affinity=["desktop", "mouse"],
            cost_profile="low",
            trusted_source="skill://public/mouse_click",
            handler=_mouse_click,
            exclusive_resource="device:desktop",  # ADR-010 · single physical screen
            tests=[
                SkillTestCase(
                    name="invalid_button",
                    tier="golden",
                    args={"x": 0, "y": 0, "button": "floating"},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        ),
        verify_tests=verify_tests,
    )
    registry.register(
        Skill(
            name="mouse_move",
            description="Smoothly move cursor to (x,y) over `duration` seconds.",
            affinity=["desktop", "mouse"],
            cost_profile="low",
            trusted_source="skill://public/mouse_move",
            handler=_mouse_move,
            exclusive_resource="device:desktop",  # ADR-010 · single physical screen
            tests=[],
        ),
        verify_tests=verify_tests,
    )
    registry.register(
        Skill(
            name="keyboard_type",
            description="Type text via keyboard (len ≤ 10k, with per-char interval).",
            affinity=["desktop", "keyboard"],
            cost_profile="low",
            trusted_source="skill://public/keyboard_type",
            handler=_keyboard_type,
            exclusive_resource="device:desktop",  # ADR-010 · single physical keyboard
            tests=[
                SkillTestCase(
                    name="missing_text",
                    tier="golden",
                    args={"text": ""},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        ),
        verify_tests=verify_tests,
    )
    registry.register(
        Skill(
            name="keyboard_press",
            description="Press a key or key combo · keys=['enter'] or ['ctrl','c'].",
            affinity=["desktop", "keyboard"],
            cost_profile="low",
            trusted_source="skill://public/keyboard_press",
            handler=_keyboard_press,
            exclusive_resource="device:desktop",  # ADR-010 · single physical keyboard
            tests=[
                SkillTestCase(
                    name="empty_keys",
                    tier="golden",
                    args={"keys": []},
                    expect=SkillExpect(schema_keys=["error"]),
                ),
            ],
        ),
        verify_tests=verify_tests,
    )
    return 6 + register_computer_api_skills(registry) + register_computer_uia_skills(registry)
