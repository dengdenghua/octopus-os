#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import echo_scanning_policy as policy

HERE = Path(__file__).resolve().parent
POLICY = HERE / "echo_scanning_policy.py"
PACKAGED_CONFIG = HERE / "airscan.conf"


class ScanningPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / "airscan.conf"
        self.config.write_text(PACKAGED_CONFIG.read_text(encoding="utf-8"), encoding="utf-8")
        self.config.chmod(0o644)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self) -> None:
        policy.verify_file(
            self.config,
            expected_uid=os.getuid(),
            expected_gid=os.getgid(),
        )

    def replace(self, old: str, new: str) -> None:
        text = self.config.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.config.write_text(text.replace(old, new), encoding="utf-8")

    def test_packaged_policy_is_valid(self) -> None:
        self.verify()

    def test_discovery_protocol_and_remote_identity_are_fixed(self) -> None:
        cases = (
            ("discovery = enable", "discovery = disable"),
            ("protocol = auto", "protocol = manual"),
            ("ws-discovery = fast", "ws-discovery = full"),
            ("pretend-local = false", "pretend-local = true"),
        )
        original = self.config.read_text(encoding="utf-8")
        for old, new in cases:
            with self.subTest(new=new):
                self.config.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_debug_trace_or_payload_hexdump_is_rejected(self) -> None:
        cases = (
            ("enable = false", "enable = true"),
            ("hexdump = false", "hexdump = true"),
            ("[debug]", "[debug]\ntrace = /tmp/scan-payloads"),
        )
        original = self.config.read_text(encoding="utf-8")
        for old, new in cases:
            with self.subTest(new=new):
                self.config.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_manual_endpoints_and_unexpected_sections_are_rejected(self) -> None:
        cases = (
            ("[devices]", "[devices]\nOffice = http://203.0.113.7/eSCL"),
            ("[blacklist]", "[blacklist]\n\n[extra]\nvalue = 1"),
        )
        original = self.config.read_text(encoding="utf-8")
        for old, new in cases:
            with self.subTest(new=new):
                self.config.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaises(policy.PolicyError):
                    self.verify()

    def test_duplicate_options_are_rejected(self) -> None:
        self.replace("discovery = enable", "discovery = enable\ndiscovery = disable")
        with self.assertRaises(policy.PolicyError):
            self.verify()

    def test_symlink_mutable_or_wrong_owner_policy_is_rejected(self) -> None:
        self.config.chmod(0o666)
        with self.assertRaises(policy.PolicyError):
            self.verify()
        self.config.chmod(0o644)

        link = self.root / "linked.conf"
        link.symlink_to(self.config)
        with self.assertRaises(policy.PolicyError):
            policy.verify_file(link, expected_uid=os.getuid(), expected_gid=os.getgid())

        with self.assertRaises(policy.PolicyError):
            policy.verify_file(
                self.config,
                expected_uid=os.getuid() + 1,
                expected_gid=os.getgid(),
            )

    def test_cli_override_requires_explicit_source_sentinel(self) -> None:
        arguments = [
            str(POLICY),
            "--airscan-config",
            str(self.config),
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
            env={**os.environ, "ECHO_SCANNING_SOURCE_TEST": "USE-SOURCE-RUNTIME"},
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("ECHO_SCANNING_POLICY_READY", allowed.stdout)


if __name__ == "__main__":
    unittest.main()
