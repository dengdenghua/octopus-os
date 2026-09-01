#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "echo_core_apps_session_smoke.py"
SPEC = importlib.util.spec_from_file_location("echo_core_apps_session_smoke", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cannot import core-app session smoke")
smoke = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = smoke
SPEC.loader.exec_module(smoke)


class CoreAppsSessionSmokeTests(unittest.TestCase):
    def test_session_dependencies_do_not_cross_x11_and_wayland_boundaries(self) -> None:
        common = {
            smoke.XDG_MIME,
            smoke.XDG_OPEN,
            smoke.GIO,
            smoke.DESKTOP_FILE_VALIDATE,
            smoke.ZIP,
        }
        x11 = set(smoke.required_session_executables("x11"))
        wayland = set(smoke.required_session_executables("wayland"))

        self.assertEqual(x11, common | {smoke.WMCTRL})
        self.assertEqual(wayland, common | {smoke.KWIN_BRIDGE})
        self.assertNotIn(smoke.WMCTRL, wayland)
        self.assertNotIn(smoke.KWIN_BRIDGE, x11)
        with self.assertRaisesRegex(smoke.SessionSmokeError, "unsupported"):
            smoke.required_session_executables("mir")

    def test_case_matrix_uses_fixed_desktop_identities(self) -> None:
        self.assertEqual(
            [(case.name, case.desktop_id) for case in smoke.CASES],
            [
                ("directory", "org.kde.dolphin.desktop"),
                ("http", "firefox-esr.desktop"),
                ("text", "org.kde.kate.desktop"),
                ("pdf", "org.kde.okular.desktop"),
                ("image", "org.kde.gwenview.desktop"),
                ("archive", "org.kde.ark.desktop"),
                ("audio", "org.kde.haruna.desktop"),
                ("terminal", "org.kde.konsole.desktop"),
                ("calculator", "org.kde.kcalc.desktop"),
            ],
        )

    def test_browser_fixture_is_loopback_only_bounded_and_quiet(self) -> None:
        server, thread, target = smoke.start_loopback_http_server()
        try:
            self.assertTrue(target.startswith("http://127.0.0.1:"))
            with urllib.request.urlopen(target, timeout=2) as response:  # noqa: S310
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), smoke.HTTP_FIXTURE_BODY)
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(  # noqa: S310
                    target.rsplit("/", 1)[0] + "/not-served", timeout=2
                )
            self.assertEqual(error.exception.code, 404)
        finally:
            smoke.stop_loopback_http_server(server, thread)
        self.assertFalse(thread.is_alive())
        self.assertLess(len(smoke.HTTP_FIXTURE_BODY), 1024)
        self.assertIn(b"<title>echo-core-browser.html</title>", smoke.HTTP_FIXTURE_BODY)

    def test_generated_pdf_has_consistent_xref_and_eof(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "fixture.pdf"
            smoke._write_pdf(target)
            content = target.read_bytes()
        self.assertTrue(content.startswith(b"%PDF-1.4\n"))
        self.assertTrue(content.endswith(b"%%EOF\n"))
        start_xref = int(content.rsplit(b"startxref\n", 1)[1].splitlines()[0])
        self.assertEqual(content[start_xref : start_xref + 5], b"xref\n")

    def test_generated_wav_is_bounded_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "fixture.wav"
            smoke._write_wav(target)
            content = target.read_bytes()
        riff, size, wave = struct.unpack_from("<4sI4s", content)
        self.assertEqual((riff, wave), (b"RIFF", b"WAVE"))
        self.assertEqual(size + 8, len(content))
        self.assertLess(len(content), 4096)

    def test_x11_parser_and_identity_match_require_real_pid(self) -> None:
        windows = smoke.parse_x11_windows(
            "0x03a00007  0 4242 host kate.kate echo-core-text.txt — Kate\n"
        )
        self.assertEqual(len(windows), 1)
        case = next(case for case in smoke.CASES if case.name == "text")
        self.assertTrue(smoke.identity_matches(windows[0], case))
        self.assertEqual(smoke.case_window(windows, set(), case), windows[0])
        without_pid = [{**windows[0], "pid": 0}]
        self.assertIsNone(smoke.case_window(without_pid, set(), case))
        repeated = "0x03a00007  0 4242 host kate.kate echo-core-text.txt — Kate\n"
        with self.assertRaises(smoke.SessionSmokeError):
            smoke.parse_x11_windows(repeated * 4097)

        browser_case = next(case for case in smoke.CASES if case.name == "http")
        browser_window = {
            "id": "0x03a00008",
            "pid": 4343,
            "wmClass": "Navigator.firefox-esr",
            "title": "echo-core-browser.html — Mozilla Firefox",
        }
        self.assertTrue(smoke.identity_matches(browser_window, browser_case))
        self.assertEqual(smoke.case_window([browser_window], set(), browser_case), browser_window)

        terminal_case = next(case for case in smoke.CASES if case.name == "terminal")
        terminal_window = {
            "id": "0x03a00009",
            "pid": 4444,
            "wmClass": "konsole.konsole",
            "title": "localized shell title without a fixture name",
        }
        self.assertEqual(
            smoke.case_window([terminal_window], set(), terminal_case), terminal_window
        )
        self.assertIsNone(
            smoke.case_window(
                [{**terminal_window, "wmClass": "unrelated.application"}],
                set(),
                terminal_case,
            )
        )

    def test_case_window_rejects_baseline_and_wrong_title(self) -> None:
        case = next(case for case in smoke.CASES if case.name == "pdf")
        window = {
            "id": "0x4",
            "pid": 100,
            "wmClass": "okular.okular",
            "title": case.filename,
        }
        self.assertIsNone(smoke.case_window([window], {"0x4"}, case))
        self.assertIsNone(smoke.case_window([{**window, "title": "unrelated.pdf"}], set(), case))

    def test_cli_requires_explicit_ci_sentinel_before_runtime_access(self) -> None:
        environment = {**os.environ, "XDG_SESSION_TYPE": "x11"}
        environment.pop("ECHO_CORE_APPS_SESSION_TEST", None)
        result = subprocess.run(
            [str(SCRIPT), "--session", "x11"],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("explicit CI sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
