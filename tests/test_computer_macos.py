from __future__ import annotations

import json
from pathlib import Path

from runtime.execution.suckers import computer_macos


def test_screen_info_decodes_native_payload(monkeypatch) -> None:
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)
    monkeypatch.setattr(
        computer_macos,
        "_run_jxa",
        lambda *_args, **_kwargs: (
            json.dumps(
                {
                    "width": 1440,
                    "height": 900,
                    "cursor_x": 300,
                    "cursor_y": 200,
                    "backend": "macos-native",
                }
            ),
            None,
        ),
    )

    assert computer_macos.screen_info() == {
        "width": 1440,
        "height": 900,
        "cursor_x": 300,
        "cursor_y": 200,
        "backend": "macos-native",
    }


def test_capture_screen_uses_native_region(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "shot.png"
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)

    def fake_run(command: list[str], **_kwargs):
        assert command[-3:] == ["-R", "1,2,30,40", str(target)]
        target.write_bytes(b"png")
        return "", None

    monkeypatch.setattr(computer_macos, "_run", fake_run)

    result = computer_macos.capture_screen(str(target), [1, 2, 30, 40])

    assert result["backend"] == "macos-native"
    assert result["size_bytes"] == 3


def test_press_keys_rejects_ambiguous_chord(monkeypatch) -> None:
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)

    result = computer_macos.press_keys(["a", "b"])

    assert "exactly one" in result["error"]


def test_native_unavailable_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", False)

    assert "error" in computer_macos.list_apps()


def test_activate_window_target_uses_selected_app_and_window(monkeypatch) -> None:
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)
    seen = {}

    def fake_run_jxa(source, *args, timeout=10.0):
        seen["source"] = source
        seen["args"] = args
        return (
            '{"app_id":"com.example.App","app_name":"Example","window_id":"42-1",'
            '"window_title":"Inbox","backend":"macos-native"}',
            None,
        )

    monkeypatch.setattr(computer_macos, "_run_jxa", fake_run_jxa)

    result = computer_macos.activate_window_target(
        app_id="com.example.App",
        app_name="Example",
        window_id="42-1",
        window_title="Inbox",
    )

    assert seen["args"] == ("com.example.App", "Example", "42-1", "Inbox")
    assert "process.frontmost = true" in seen["source"]
    assert result["window_title"] == "Inbox"


def test_perform_accessibility_action_regrounds_before_press(monkeypatch) -> None:
    monkeypatch.setattr(computer_macos, "MACOS_NATIVE_AVAILABLE", True)
    seen = {}

    def fake_run_jxa(source, *args, timeout=10.0):
        seen["source"] = source
        seen["args"] = args
        return (
            '{"action":"AXPress","score":24,"role":"AXButton",'
            '"title":"Save","backend":"macos-accessibility"}',
            None,
        )

    monkeypatch.setattr(computer_macos, "_run_jxa", fake_run_jxa)

    result = computer_macos.perform_accessibility_action(
        {
            "role": "AXButton",
            "title": "Save",
            "position": [100, 200],
        },
        action="press",
    )

    assert seen["args"][1] == "AXPress"
    assert '"title": "Save"' in seen["args"][0]
    assert "bestScore < 4" in seen["source"]
    assert result["backend"] == "macos-accessibility"

