#!/usr/bin/env python3
from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr

import verify_wayland_native_app_ipc as MODULE


UUID_A = "11111111-1111-1111-1111-111111111111"
UUID_B = "22222222-2222-2222-2222-222222222222"
UUID_C = "33333333-3333-3333-3333-333333333333"


def state(*windows: dict[str, object]) -> str:
    return json.dumps({"ok": True, "windows": list(windows)})


def window(window_id: str, pid: int, wm_class: str) -> dict[str, object]:
    return {"id": window_id, "pid": pid, "wmClass": wm_class}


class WaylandNativeAppIpcTests(unittest.TestCase):
    def test_baseline_preserves_canonical_compositor_ids(self) -> None:
        windows = MODULE.parse_state(
            state(
                window(UUID_A, 10, "org.kde.dolphin"),
                window(UUID_B, 20, "kate"),
            )
        )
        self.assertEqual(MODULE.baseline_ids(windows), f"{UUID_A}\n{UUID_B}")

    def test_exact_kcalc_is_selected_after_the_baseline(self) -> None:
        windows = MODULE.parse_state(
            state(
                window(UUID_A, 10, "org.kde.kcalc"),
                window(UUID_B, 20, "org.kde.kcalc.desktop"),
            )
        )
        self.assertEqual(
            MODULE.find_new_kcalc(windows, frozenset((UUID_A,))), UUID_B
        )

    def test_xwayland_kcalc_identity_is_accepted(self) -> None:
        windows = MODULE.parse_state(state(window(UUID_A, 10, "kcalc.KCalc")))
        self.assertEqual(MODULE.find_new_kcalc(windows, frozenset()), UUID_A)

    def test_old_zero_pid_and_unrelated_windows_cannot_satisfy_the_gate(self) -> None:
        windows = MODULE.parse_state(
            state(
                window(UUID_A, 10, "org.kde.kcalc"),
                window(UUID_B, 0, "org.kde.kcalc"),
                window(UUID_C, 30, "org.kde.kate"),
            )
        )
        self.assertIsNone(MODULE.find_new_kcalc(windows, frozenset((UUID_A,))))

    def test_multiple_new_kcalc_windows_are_rejected(self) -> None:
        windows = MODULE.parse_state(
            state(
                window(UUID_A, 10, "org.kde.kcalc"),
                window(UUID_B, 20, "kcalc"),
            )
        )
        with self.assertRaises(MODULE.WaylandIpcEvidenceError):
            MODULE.find_new_kcalc(windows, frozenset())

    def test_malformed_duplicate_and_oversized_state_is_rejected(self) -> None:
        invalid_values = (
            "not-json",
            json.dumps({"ok": False, "windows": []}),
            state(window("not-a-uuid", 1, "kcalc")),
            state(window(UUID_A, True, "kcalc")),
            state(window(UUID_A, 1, "kcalc"), window(UUID_A, 2, "kate")),
            json.dumps(
                {
                    "ok": True,
                    "windows": [
                        window(
                            f"{index:08x}-1111-1111-1111-111111111111",
                            1,
                            "kate",
                        )
                        for index in range(MODULE.MAX_WINDOWS + 1)
                    ],
                }
            ),
        )
        for value in invalid_values:
            with self.subTest(value=value[:80]):
                with self.assertRaises(MODULE.WaylandIpcEvidenceError):
                    MODULE.parse_state(value)

    def test_baseline_argument_is_bounded_unique_and_canonical(self) -> None:
        self.assertEqual(
            MODULE.parse_baseline_ids(f"{UUID_A}\n{UUID_B}\n"),
            frozenset((UUID_A, UUID_B)),
        )
        for value in ("not-a-uuid", f"{UUID_A}\n{UUID_A}"):
            with self.subTest(value=value):
                with self.assertRaises(MODULE.WaylandIpcEvidenceError):
                    MODULE.parse_baseline_ids(value)

    def test_absent_command_distinguishes_present_closed_and_invalid_ids(self) -> None:
        payload = state(window(UUID_A, 10, "kcalc"))
        original = MODULE.read_standard_input
        try:
            MODULE.read_standard_input = lambda: payload
            self.assertEqual(MODULE.main(["absent", "--window-id", UUID_A]), 1)
            self.assertEqual(MODULE.main(["absent", "--window-id", UUID_B]), 0)
            with redirect_stderr(io.StringIO()):
                self.assertEqual(MODULE.main(["absent", "--window-id", "bad"]), 2)
        finally:
            MODULE.read_standard_input = original


if __name__ == "__main__":
    unittest.main()
