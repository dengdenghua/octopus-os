from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

MACOS_NATIVE_AVAILABLE = platform.system() == "Darwin" and bool(
    shutil.which("screencapture") and shutil.which("osascript")
)

_OSASCRIPT = shutil.which("osascript") or "/usr/bin/osascript"
_SCREENCAPTURE = shutil.which("screencapture") or "/usr/sbin/screencapture"
_MAX_OUTPUT = 256_000


def _run(
    command: list[str],
    *,
    timeout: float = 10.0,
) -> tuple[str, str | None]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return "", f"{type(exc).__name__}: {exc}"
    stdout = (completed.stdout or "")[:_MAX_OUTPUT].strip()
    stderr = (completed.stderr or "")[:4_000].strip()
    if completed.returncode != 0:
        return stdout, stderr or f"exit status {completed.returncode}"
    return stdout, None


def _run_jxa(source: str, *args: str, timeout: float = 10.0) -> tuple[str, str | None]:
    return _run(
        [_OSASCRIPT, "-l", "JavaScript", "-e", source, "--", *args],
        timeout=timeout,
    )


def screen_info() -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    source = r"""
ObjC.import('AppKit');
const screens = $.NSScreen.screens.js;
let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
for (const screen of screens) {
  const frame = screen.frame;
  minX = Math.min(minX, Number(frame.origin.x));
  minY = Math.min(minY, Number(frame.origin.y));
  maxX = Math.max(maxX, Number(frame.origin.x + frame.size.width));
  maxY = Math.max(maxY, Number(frame.origin.y + frame.size.height));
}
if (!Number.isFinite(minX)) {
  const frame = $.NSScreen.mainScreen.frame;
  minX = Number(frame.origin.x); minY = Number(frame.origin.y);
  maxX = Number(frame.origin.x + frame.size.width);
  maxY = Number(frame.origin.y + frame.size.height);
}
const event = $.NSEvent.mouseLocation;
const rawCursorX = Number(event.x - minX);
const rawCursorY = Number(maxY - event.y);
const width = Number(maxX - minX);
const height = Number(maxY - minY);
JSON.stringify({
  width,
  height,
  cursor_x: Math.max(0, Math.min(width - 1, rawCursorX)),
  cursor_y: Math.max(0, Math.min(height - 1, rawCursorY)),
  raw_cursor_x: rawCursorX,
  raw_cursor_y: rawCursorY,
  scale_factor: Number($.NSScreen.mainScreen.backingScaleFactor),
  display_count: screens.length,
  origin_x: minX,
  origin_y: minY,
  backend: 'macos-native'
});
"""
    output, error = _run_jxa(source)
    if error:
        return {"error": f"screen_info_failed: {error}"}
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        return {"error": f"screen_info_failed: {type(exc).__name__}: {exc}"}
    return payload if isinstance(payload, dict) else {"error": "invalid screen info"}


def capture_screen(path: str, region: list[int] | None = None) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    command = [_SCREENCAPTURE, "-x", "-t", "png"]
    if region is not None:
        x, y, width, height = region
        command.extend(["-R", f"{x},{y},{width},{height}"])
    command.append(path)
    _output, error = _run(command, timeout=20.0)
    if error:
        return {"error": f"capture_failed: {error}"}
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError as exc:
        return {"error": f"capture_failed: {type(exc).__name__}: {exc}"}
    return {
        "path": str(target),
        "size_bytes": size,
        "region": region,
        "backend": "macos-native",
    }


def _mouse_event_source(kind: str) -> str:
    event_types = {
        "move": "kCGEventMouseMoved",
        "left_down": "kCGEventLeftMouseDown",
        "left_up": "kCGEventLeftMouseUp",
        "right_down": "kCGEventRightMouseDown",
        "right_up": "kCGEventRightMouseUp",
        "middle_down": "kCGEventOtherMouseDown",
        "middle_up": "kCGEventOtherMouseUp",
    }
    event_type = event_types[kind]
    button = {
        "left_down": "kCGMouseButtonLeft",
        "left_up": "kCGMouseButtonLeft",
        "right_down": "kCGMouseButtonRight",
        "right_up": "kCGMouseButtonRight",
        "middle_down": "kCGMouseButtonCenter",
        "middle_up": "kCGMouseButtonCenter",
        "move": "kCGMouseButtonLeft",
    }[kind]
    return f"""
ObjC.import('CoreGraphics');
ObjC.import('Foundation');
const argv = $.NSProcessInfo.processInfo.arguments.js.slice(-2);
const point = {{x: Number(argv[0]), y: Number(argv[1])}};
const event = $.CGEventCreateMouseEvent(null, $.{event_type}, point, $.{button});
$.CGEventPost($.kCGHIDEventTap, event);
'ok';
"""


def move_mouse(x: int, y: int) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    _output, error = _run_jxa(_mouse_event_source("move"), str(x), str(y))
    if error:
        return {"error": f"move_failed: {error}"}
    return {"moved_to": {"x": x, "y": y}, "backend": "macos-native"}


def click_mouse(
    x: int,
    y: int,
    *,
    button: str = "left",
    clicks: int = 1,
) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    down = f"{button}_down"
    up = f"{button}_up"
    for _ in range(clicks):
        for kind in (down, up):
            _output, error = _run_jxa(_mouse_event_source(kind), str(x), str(y))
            if error:
                return {"error": f"click_failed: {error}"}
    return {
        "clicked_at": {"x": x, "y": y},
        "button": button,
        "clicks": clicks,
        "backend": "macos-native",
    }


def type_text(text: str) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    source = r"""
ObjC.import('Foundation');
const se = Application('System Events');
const argv = $.NSProcessInfo.processInfo.arguments.js;
se.keystroke(String(argv[argv.length - 1]));
'ok';
"""
    _output, error = _run_jxa(source, text, timeout=30.0)
    if error:
        return {"error": f"type_failed: {error}"}
    return {"text_len": len(text), "backend": "macos-native"}


_KEY_CODES = {
    "enter": 36,
    "return": 36,
    "tab": 48,
    "space": 49,
    "backspace": 51,
    "delete": 51,
    "escape": 53,
    "esc": 53,
    "left": 123,
    "right": 124,
    "down": 125,
    "up": 126,
}


def press_keys(keys: list[str]) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    normalized = [str(key).strip().lower() for key in keys]
    modifiers = {
        "command": "command down",
        "cmd": "command down",
        "meta": "command down",
        "control": "control down",
        "ctrl": "control down",
        "alt": "option down",
        "option": "option down",
        "shift": "shift down",
    }
    modifier_values = [modifiers[key] for key in normalized if key in modifiers]
    regular = [key for key in normalized if key not in modifiers]
    if len(regular) != 1:
        return {"error": "macOS key chord must contain exactly one non-modifier key"}
    key = regular[0]
    using = f" using {{{', '.join(modifier_values)}}}" if modifier_values else ""
    if key in _KEY_CODES:
        statement = f"key code {_KEY_CODES[key]}{using}"
    elif len(key) == 1:
        escaped = key.replace("\\", "\\\\").replace('"', '\\"')
        statement = f'keystroke "{escaped}"{using}'
    else:
        return {"error": f"unsupported macOS key: {key}"}
    source = f'tell application "System Events" to {statement}'
    _output, error = _run([_OSASCRIPT, "-e", source])
    if error:
        return {"error": f"press_failed: {error}"}
    return {"pressed": list(keys), "backend": "macos-native"}


def list_apps() -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    source = r"""
ObjC.import('Foundation');
const se = Application('System Events');
const rows = se.applicationProcesses.whose({backgroundOnly: false})().map((process) => {
  let windows = [];
  try {
    windows = process.windows().map((window, index) => ({
      id: `${process.unixId()}-${index}`,
      title: String(window.name() || ''),
      position: window.position(),
      size: window.size()
    }));
  } catch (_) {}
  return {
    id: String(process.bundleIdentifier() || process.name()),
    displayName: String(process.name()),
    isRunning: true,
    frontmost: Boolean(process.frontmost()),
    windows
  };
});
JSON.stringify(rows);
"""
    output, error = _run_jxa(source, timeout=20.0)
    if error:
        return {"error": f"list_apps_failed: {error}"}
    try:
        apps = json.loads(output)
    except (TypeError, ValueError) as exc:
        return {"error": f"list_apps_failed: {type(exc).__name__}: {exc}"}
    return {"apps": apps if isinstance(apps, list) else [], "backend": "macos-native"}


def activate_window_target(
    *,
    app_id: str = "",
    app_name: str = "",
    window_id: str = "",
    window_title: str = "",
) -> dict[str, Any]:
    """Bring one operator-selected macOS window to the foreground."""

    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    source = r"""
ObjC.import('Foundation');
const se = Application('System Events');
const argv = $.NSProcessInfo.processInfo.arguments.js;
const appId = String(argv[argv.length - 4] || '');
const appName = String(argv[argv.length - 3] || '');
const windowId = String(argv[argv.length - 2] || '');
const windowTitle = String(argv[argv.length - 1] || '');
let matches = appId ? se.applicationProcesses.whose({bundleIdentifier: appId})() : [];
if (!matches.length && appName) matches = se.applicationProcesses.whose({name: appName})();
if (!matches.length) throw new Error('selected application is no longer running');
const process = matches[0];
process.frontmost = true;
const windows = process.windows();
let chosen = null;
if (windowTitle) {
  chosen = windows.find((window) => {
    try { return String(window.name() || '') === windowTitle; } catch (_) { return false; }
  }) || null;
}
if (!chosen && windowId.includes('-')) {
  const index = Number(windowId.slice(windowId.lastIndexOf('-') + 1));
  if (Number.isInteger(index) && index >= 0 && index < windows.length) chosen = windows[index];
}
if (chosen) {
  try { chosen.actions.byName('AXRaise').perform(); } catch (_) {}
}
JSON.stringify({
  app_id: String(process.bundleIdentifier() || process.name()),
  app_name: String(process.name()),
  window_id: windowId,
  window_title: chosen ? String(chosen.name() || windowTitle) : windowTitle,
  backend: 'macos-native'
});
"""
    output, error = _run_jxa(source, app_id, app_name, window_id, window_title)
    if error:
        return {"error": f"activate_target_failed: {error}"}
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        return {"error": f"activate_target_failed: {type(exc).__name__}: {exc}"}
    return payload if isinstance(payload, dict) else {"error": "invalid target activation result"}


def accessibility_snapshot(max_nodes: int = 120) -> dict[str, Any]:
    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    bounded = max(1, min(int(max_nodes), 500))
    source = r"""
ObjC.import('Foundation');
const se = Application('System Events');
const limit = Math.max(1, Math.min(500, Number($.NSProcessInfo.processInfo.arguments.js.slice(-1)[0]) || 120));
const front = se.applicationProcesses.whose({frontmost: true})();
if (!front.length) throw new Error('no frontmost application');
const process = front[0];
const windows = process.windows();
const window = windows.length ? windows[0] : null;
const safe = (fn, fallback = '') => { try { const value = fn(); return value == null ? fallback : value; } catch (_) { return fallback; } };
const elements = [];
if (window) {
  const contents = safe(() => window.entireContents(), []);
  for (let index = 0; index < contents.length && elements.length < limit; index += 1) {
    const element = contents[index];
    const role = String(safe(() => element.role(), ''));
    const title = String(safe(() => element.title(), ''));
    const description = String(safe(() => element.description(), ''));
    const value = String(safe(() => element.value(), '')).slice(0, 500);
    const position = safe(() => element.position(), null);
    const size = safe(() => element.size(), null);
    if (!(role || title || description || value)) continue;
    elements.push({
      index: elements.length,
      role,
      title,
      description,
      value,
      position,
      size,
      enabled: Boolean(safe(() => element.enabled(), true))
    });
  }
}
JSON.stringify({
  app: {
    id: String(safe(() => process.bundleIdentifier(), process.name())),
    displayName: String(safe(() => process.name(), '')),
    pid: Number(safe(() => process.unixId(), 0))
  },
  window: window ? {
    id: `${Number(safe(() => process.unixId(), 0))}-0`,
    title: String(safe(() => window.name(), '')),
    position: safe(() => window.position(), null),
    size: safe(() => window.size(), null)
  } : null,
  focused: String(safe(() => process.focusedUIElement().description(), '')),
  elements,
  truncated: elements.length >= limit
});
"""
    output, error = _run_jxa(source, str(bounded), timeout=20.0)
    if error:
        return {
            "error": f"accessibility_snapshot_failed: {error}",
            "backend": "macos-accessibility",
        }
    try:
        snapshot = json.loads(output)
    except (TypeError, ValueError) as exc:
        return {
            "error": f"accessibility_snapshot_failed: {type(exc).__name__}: {exc}",
            "backend": "macos-accessibility",
        }
    if not isinstance(snapshot, dict):
        return {"error": "invalid accessibility snapshot", "backend": "macos-accessibility"}
    snapshot["backend"] = "macos-accessibility"
    snapshot["available"] = True
    return snapshot


def perform_accessibility_action(
    target: dict[str, Any],
    *,
    action: str = "press",
) -> dict[str, Any]:
    """Re-ground a snapshotted AX element and invoke its native action.

    Snapshot indexes are intentionally not reused: the UI may have changed
    between preview and confirmation.  The target's semantic attributes are
    matched against a fresh frontmost-window tree immediately before acting.
    Callers may fall back to the previewed bounds when native AX is unavailable.
    """

    if not MACOS_NATIVE_AVAILABLE:
        return {"error": "macOS native automation is unavailable"}
    normalized_action = str(action or "press").strip().lower()
    action_names = {
        "press": "AXPress",
        "show_menu": "AXShowMenu",
        "increment": "AXIncrement",
        "decrement": "AXDecrement",
        "expand": "AXExpand",
        "collapse": "AXCollapse",
    }
    native_action = action_names.get(normalized_action)
    if native_action is None:
        return {"error": f"unsupported accessibility action: {normalized_action}"}
    compact_target = {
        key: target.get(key)
        for key in (
            "role",
            "control_type",
            "title",
            "name",
            "description",
            "value",
            "position",
        )
        if target.get(key) not in (None, "")
    }
    source = r"""
ObjC.import('Foundation');
const se = Application('System Events');
const argv = $.NSProcessInfo.processInfo.arguments.js;
const target = JSON.parse(String(argv[argv.length - 2] || '{}'));
const nativeAction = String(argv[argv.length - 1] || 'AXPress');
const front = se.applicationProcesses.whose({frontmost: true})();
if (!front.length) throw new Error('no frontmost application');
const windows = front[0].windows();
if (!windows.length) throw new Error('frontmost application has no window');
const safe = (fn, fallback = '') => { try { const value = fn(); return value == null ? fallback : value; } catch (_) { return fallback; } };
const wantedRole = String(target.role || target.control_type || '');
const wantedTitle = String(target.title || target.name || '');
const wantedDescription = String(target.description || '');
const wantedValue = String(target.value || '').slice(0, 500);
const wantedPosition = Array.isArray(target.position) ? target.position : null;
let best = null;
let bestScore = -1e9;
for (const element of safe(() => windows[0].entireContents(), [])) {
  const role = String(safe(() => element.role(), ''));
  const title = String(safe(() => element.title(), ''));
  const description = String(safe(() => element.description(), ''));
  const value = String(safe(() => element.value(), '')).slice(0, 500);
  let score = 0;
  if (wantedRole) score += role === wantedRole ? 8 : -8;
  if (wantedTitle) score += title === wantedTitle ? 12 : -4;
  if (wantedDescription) score += description === wantedDescription ? 6 : -2;
  if (wantedValue) score += value === wantedValue ? 3 : 0;
  if (wantedPosition) {
    const position = safe(() => element.position(), null);
    if (Array.isArray(position) && position.length >= 2) {
      const distance = Math.abs(Number(position[0]) - Number(wantedPosition[0])) + Math.abs(Number(position[1]) - Number(wantedPosition[1]));
      score += Math.max(-4, 4 - distance / 50);
    }
  }
  if (score > bestScore) { best = element; bestScore = score; }
}
if (!best || bestScore < 4) throw new Error(`accessibility target is stale or ambiguous (score=${bestScore})`);
const native = best.actions.byName(nativeAction);
native.perform();
JSON.stringify({
  action: nativeAction,
  score: bestScore,
  role: String(safe(() => best.role(), '')),
  title: String(safe(() => best.title(), '')),
  backend: 'macos-accessibility'
});
"""
    output, error = _run_jxa(
        source,
        json.dumps(compact_target, ensure_ascii=False),
        native_action,
        timeout=20.0,
    )
    if error:
        return {"error": f"accessibility_action_failed: {error}"}
    try:
        result = json.loads(output)
    except (TypeError, ValueError) as exc:
        return {"error": f"accessibility_action_failed: {type(exc).__name__}: {exc}"}
    return result if isinstance(result, dict) else {"error": "invalid accessibility action result"}
