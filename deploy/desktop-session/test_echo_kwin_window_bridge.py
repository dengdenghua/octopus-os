#!/usr/bin/env python3
"""Portable state/protocol tests for echo-kwin-window-bridge."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import threading
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("echo-kwin-window-bridge")
loader = importlib.machinery.SourceFileLoader("echo_kwin_window_bridge", str(MODULE_PATH))
spec = importlib.util.spec_from_loader(loader.name, loader)
assert spec is not None
bridge = importlib.util.module_from_spec(spec)
loader.exec_module(bridge)

WINDOW_ID = "23d24387-4430-4c58-9d2f-83e89095d625"


def sample_window(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "id": WINDOW_ID,
        "desktop": 0,
        "pid": 4242,
        "host": "",
        "wmClass": "org.kde.dolphin",
        "title": "Home — Dolphin",
        "active": True,
        "minimized": False,
        "provider": "kwin-wayland",
    }
    value.update(overrides)
    return value


def sample_output(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "name": "Virtual-1",
        "x": 0,
        "y": 0,
        "width": 1280,
        "height": 800,
        "scale": 1.25,
    }
    value.update(overrides)
    return value


def sample_snapshot(
    windows: list[dict[str, object]] | None = None,
    outputs: list[dict[str, object]] | None = None,
) -> str:
    return json.dumps(
        {
            "windows": [sample_window()] if windows is None else windows,
            "outputs": [sample_output()] if outputs is None else outputs,
        }
    )


def main() -> None:
    state = bridge.BridgeState()
    assert state.capabilities()["ok"] is False
    assert state.publish(sample_snapshot(), "kwin-script-v2") is True
    assert state.capabilities()["provider"] == "kwin-wayland"
    listed = bridge.handle_request(state, {"method": "list"})
    assert listed["ok"] is True
    assert listed["windows"][0]["id"] == WINDOW_ID
    assert listed["windows"][0]["minimized"] is False
    assert listed["outputs"] == [sample_output()]

    assert state.publish("{}", "kwin-script-v1") is False
    assert state.publish(
        sample_snapshot([sample_window(provider="ewmh-x11")]), "kwin-script-v2"
    ) is False
    assert state.publish(
        sample_snapshot([sample_window(id="not-a-uuid")]), "kwin-script-v2"
    ) is False
    assert state.publish(
        sample_snapshot([sample_window(), sample_window()]), "kwin-script-v2"
    ) is False
    assert state.publish(sample_snapshot(outputs=[]), "kwin-script-v2") is False
    assert state.publish(
        sample_snapshot(outputs=[sample_output(), sample_output()]), "kwin-script-v2"
    ) is False
    assert state.publish(
        sample_snapshot(outputs=[sample_output(scale=float("inf"))]),
        "kwin-script-v2",
    ) is False
    assert state.list_windows()["windows"][0]["id"] == WINDOW_ID

    result: dict[str, object] = {}

    def request_focus() -> None:
        result.update(state.request_action("focus", WINDOW_ID, timeout_seconds=1.0))

    request_thread = threading.Thread(target=request_focus)
    request_thread.start()
    actions = []
    for _attempt in range(100):
        actions = json.loads(state.take_actions())
        if actions:
            break
        threading.Event().wait(0.005)
    assert len(actions) == 1
    assert actions[0]["action"] == "focus"
    assert actions[0]["windowId"] == WINDOW_ID
    assert state.complete_action(actions[0]["sequence"], True, "") is True
    request_thread.join(timeout=1.0)
    assert not request_thread.is_alive()
    assert result == {
        "ok": True,
        "action": "focus",
        "windowId": WINDOW_ID,
        "provider": "kwin-wayland",
    }

    unknown = state.request_action(
        "close", "b4d8b5a4-b9b9-48dc-8395-48f2d7ac2242", timeout_seconds=0.01
    )
    assert unknown["ok"] is False
    assert unknown["error"] == "unknown KWin window UUID"
    injected = state.request_action("focus;shutdown", WINDOW_ID, timeout_seconds=0.01)
    assert injected["ok"] is False
    assert injected["error"] == "unknown window action"
    assert bridge.handle_request(state, {"method": "erase"}) == {
        "ok": False,
        "error": "unknown bridge method",
    }
    print("Echo OS KWin window-bridge protocol tests OK")


if __name__ == "__main__":
    main()
