#!/usr/bin/env python3
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from echo_printing_policy import (
    PolicyError,
    verify_cups_policy,
    verify_files,
    verify_ipp_usb_policy,
)

HERE = Path(__file__).resolve().parent
CUPS_POLICY = (HERE / "cupsd.conf").read_text(encoding="utf-8")
IPP_USB_POLICY = (HERE / "ipp-usb.conf").read_text(encoding="utf-8")


class PrintingPolicyTests(unittest.TestCase):
    def test_packaged_policies_are_valid(self) -> None:
        verify_cups_policy(CUPS_POLICY)
        verify_ipp_usb_policy(IPP_USB_POLICY)

    def test_cups_rejects_wildcard_or_public_listener(self) -> None:
        cases = (
            CUPS_POLICY.replace("Listen localhost:631", "Port 631"),
            CUPS_POLICY.replace("Listen localhost:631", "Listen *:631"),
            CUPS_POLICY.replace("Listen localhost:631", "SSLListen *:631"),
        )
        for candidate in cases:
            with (
                self.subTest(candidate=candidate.splitlines()[9:13]),
                self.assertRaises(PolicyError),
            ):
                verify_cups_policy(candidate)

    def test_cups_rejects_sharing_browsing_or_web_admin(self) -> None:
        replacements = (
            ("Browsing No", "Browsing Yes"),
            ("BrowseLocalProtocols none", "BrowseLocalProtocols dnssd"),
            ("DefaultShared No", "DefaultShared Yes"),
            ("WebInterface No", "WebInterface Yes"),
        )
        for old, new in replacements:
            with self.subTest(new=new), self.assertRaises(PolicyError):
                verify_cups_policy(CUPS_POLICY.replace(old, new))

    def test_cups_rejects_print_payload_retention(self) -> None:
        for old, new in (
            ("PageLogFormat\n", 'PageLogFormat "%p %u %j"\n'),
            ("PreserveJobFiles No", "PreserveJobFiles Yes"),
            ("PreserveJobHistory No", "PreserveJobHistory Yes"),
            ("MaxJobs 50", "MaxJobs 500"),
        ):
            with self.subTest(new=new), self.assertRaises(PolicyError):
                verify_cups_policy(CUPS_POLICY.replace(old, new))

    def test_cups_rejects_duplicate_override_or_include(self) -> None:
        for suffix in ("\nBrowsing No\n", "\nInclude /tmp/override.conf\n"):
            with self.subTest(suffix=suffix), self.assertRaises(PolicyError):
                verify_cups_policy(CUPS_POLICY + suffix)

    def test_cups_requires_system_auth_for_every_admin_location(self) -> None:
        candidate = CUPS_POLICY.replace(
            "<Location /admin/conf>\n  AuthType Default\n  Require user @SYSTEM",
            "<Location /admin/conf>\n  AuthType None",
        )
        with self.assertRaises(PolicyError):
            verify_cups_policy(candidate)

    def test_cups_rejects_allow_directive_in_any_block(self) -> None:
        candidate = CUPS_POLICY.replace(
            "<Location />\n  Order allow,deny",
            "<Location />\n  Allow all\n  Order allow,deny",
        )
        with self.assertRaises(PolicyError):
            verify_cups_policy(candidate)

    def test_ipp_usb_must_remain_loopback_only(self) -> None:
        with self.assertRaises(PolicyError):
            verify_ipp_usb_policy(IPP_USB_POLICY.replace("interface = loopback", "interface = all"))

    def test_ipp_usb_rejects_verbose_payload_logging(self) -> None:
        for name in ("device-log", "main-log", "console-log"):
            with self.subTest(name=name), self.assertRaises(PolicyError):
                verify_ipp_usb_policy(IPP_USB_POLICY.replace(f"{name} = error", f"{name} = debug"))

    def test_policy_files_reject_symlink_or_mutable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cups = root / "cupsd.conf"
            usb = root / "ipp-usb.conf"
            cups.write_text(CUPS_POLICY, encoding="utf-8")
            usb.write_text(IPP_USB_POLICY, encoding="utf-8")
            cups.chmod(0o644)
            usb.chmod(0o644)
            verify_files(
                cups,
                usb,
                expected_uid=os.getuid(),
                expected_gid=os.getgid(),
            )

            cups.chmod(0o666)
            with self.assertRaises(PolicyError):
                verify_files(
                    cups,
                    usb,
                    expected_uid=os.getuid(),
                    expected_gid=os.getgid(),
                )
            cups.chmod(0o644)
            link = root / "cups-link.conf"
            link.symlink_to(cups)
            with self.assertRaises(PolicyError):
                verify_files(
                    link,
                    usb,
                    expected_uid=os.getuid(),
                    expected_gid=os.getgid(),
                )


if __name__ == "__main__":
    unittest.main()
