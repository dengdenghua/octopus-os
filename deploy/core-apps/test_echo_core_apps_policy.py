#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import echo_core_apps_policy as policy

HERE = Path(__file__).resolve().parent
POLICY = HERE / "echo_core_apps_policy.py"
PACKAGED_MIMEAPPS = HERE / "mimeapps.list"


class CoreAppsPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.mimeapps = self.root / "mimeapps.list"
        self.mimeapps.write_text(PACKAGED_MIMEAPPS.read_text(encoding="utf-8"), encoding="utf-8")
        self.mimeapps.chmod(0o644)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self) -> None:
        policy.verify_file(
            self.mimeapps,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )

    def replace(self, old: str, new: str) -> None:
        text = self.mimeapps.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.mimeapps.write_text(text.replace(old, new), encoding="utf-8")

    def test_packaged_defaults_are_valid(self) -> None:
        self.verify()

    def test_critical_default_handlers_are_fixed(self) -> None:
        cases = (
            ("inode/directory=org.kde.dolphin.desktop;", "inode/directory=evil.desktop;"),
            ("x-scheme-handler/https=firefox-esr.desktop;", "x-scheme-handler/https=evil.desktop;"),
            ("application/pdf=org.kde.okular.desktop;", "application/pdf=evil.desktop;"),
            ("image/png=org.kde.gwenview.desktop;", "image/png=evil.desktop;"),
            ("application/zip=org.kde.ark.desktop;", "application/zip=evil.desktop;"),
            ("video/mp4=org.kde.haruna.desktop;", "video/mp4=evil.desktop;"),
        )
        original = self.mimeapps.read_text(encoding="utf-8")
        for old, new in cases:
            with self.subTest(new=new):
                self.mimeapps.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_multiple_command_like_or_missing_handlers_are_rejected(self) -> None:
        cases = (
            ("text/plain=org.kde.kate.desktop;", "text/plain=org.kde.kate.desktop;evil.desktop;"),
            ("application/json=org.kde.kate.desktop;", "application/json=/bin/sh -c evil;"),
            ("audio/flac=org.kde.haruna.desktop;\n", ""),
        )
        original = self.mimeapps.read_text(encoding="utf-8")
        for old, new in cases:
            with self.subTest(new=new):
                self.mimeapps.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_extra_section_or_handler_is_rejected(self) -> None:
        original = self.mimeapps.read_text(encoding="utf-8")
        cases = (
            f"{original}\n[Added Associations]\ntext/plain=evil.desktop;\n",
            f"{original}application/x-unknown=evil.desktop;\n",
        )
        for content in cases:
            with self.subTest(content=content[-80:]):
                self.mimeapps.write_text(content, encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_duplicate_handler_is_rejected(self) -> None:
        self.replace(
            "text/plain=org.kde.kate.desktop;",
            "text/plain=org.kde.kate.desktop;\ntext/plain=evil.desktop;",
        )
        with self.assertRaises(policy.PolicyError):
            self.verify()

    def test_symlink_mutable_or_wrong_owner_policy_is_rejected(self) -> None:
        self.mimeapps.chmod(0o666)
        with self.assertRaises(policy.PolicyError):
            self.verify()
        self.mimeapps.chmod(0o644)

        link = self.root / "linked.list"
        link.symlink_to(self.mimeapps)
        with self.assertRaises(policy.PolicyError):
            policy.verify_file(link, expected_uid=os.getuid(), expected_gid=os.getgid())

        with self.assertRaises(policy.PolicyError):
            policy.verify_file(
                self.mimeapps,
                expected_uid=os.getuid() + 1,
                expected_gid=os.getgid(),
            )

    def test_cli_override_requires_explicit_source_sentinel(self) -> None:
        arguments = [
            str(POLICY),
            "--mimeapps",
            str(self.mimeapps),
            "--expected-uid",
            str(os.getuid()),
            "--expected-gid",
            str(os.getgid()),
        ]
        denied = subprocess.run(arguments, check=False, capture_output=True, text=True)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("source-test sentinel", denied.stderr)

        allowed = subprocess.run(
            arguments,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "ECHO_CORE_APPS_SOURCE_TEST": "USE-SOURCE-RUNTIME"},
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("ECHO_CORE_APPS_POLICY_READY", allowed.stdout)


if __name__ == "__main__":
    unittest.main()
