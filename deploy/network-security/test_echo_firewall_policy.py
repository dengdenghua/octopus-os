#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "echo_firewall_policy.py"
SPEC = importlib.util.spec_from_file_location("echo_firewall_policy", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EchoFirewallPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.config = self.root / "firewalld.conf"
        self.zone = self.root / "echo-public.xml"
        self.config.write_bytes((HERE / "firewalld.conf").read_bytes())
        self.zone.write_bytes((HERE / "echo-public.xml").read_bytes())
        os.chmod(self.config, 0o644)
        os.chmod(self.zone, 0o644)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, *, baseline: bool = True) -> str:
        return MODULE.verify_policy(
            self.config,
            self.zone,
            expected_uid=os.getuid(),
            require_vendor_default=baseline,
        )

    def test_fresh_baseline_and_authorized_runtime_zone_are_distinct(self) -> None:
        self.assertEqual(self.verify(), "echo-public")
        self.config.write_text(
            self.config.read_text(encoding="utf-8").replace(
                "DefaultZone=echo-public",
                "DefaultZone=work",
            ),
            encoding="utf-8",
        )
        self.assertEqual(self.verify(baseline=False), "work")
        with self.assertRaisesRegex(MODULE.FirewallPolicyError, "fresh image"):
            self.verify()

    def test_backend_forwarding_cleanup_and_reload_invariants_cannot_change(self) -> None:
        changes = {
            "FirewallBackend=nftables": "FirewallBackend=iptables",
            "StrictForwardPorts=yes": "StrictForwardPorts=no",
            "CleanupOnExit=no": "CleanupOnExit=yes",
            "ReloadPolicy=INPUT:DROP,FORWARD:DROP,OUTPUT:DROP": (
                "ReloadPolicy=INPUT:ACCEPT,FORWARD:ACCEPT,OUTPUT:ACCEPT"
            ),
            "NftablesTableOwner=yes": "NftablesTableOwner=no",
        }
        original = self.config.read_text(encoding="utf-8")
        for old, new in changes.items():
            with self.subTest(option=old):
                self.config.write_text(original.replace(old, new), encoding="utf-8")
                with self.assertRaisesRegex(MODULE.FirewallPolicyError, "invariant"):
                    self.verify(baseline=False)
        self.config.write_text(original, encoding="utf-8")

    def test_duplicate_unknown_missing_or_unsafe_config_is_rejected(self) -> None:
        original = self.config.read_text(encoding="utf-8")
        cases = (
            original + "DefaultZone=echo-public\n",
            original + "AllowZoneDrifting=yes\n",
            original.replace("LogDenied=off\n", ""),
            original.replace("DefaultZone=echo-public", "DefaultZone=../trusted"),
            original.replace("FirewallBackend=nftables", " FirewallBackend=nftables"),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.config.write_text(text, encoding="utf-8")
                with self.assertRaises(MODULE.FirewallPolicyError):
                    self.verify(baseline=False)
        self.config.write_text(original, encoding="utf-8")

    def test_vendor_zone_cannot_open_ports_ssh_forwarding_or_masquerade(self) -> None:
        original = self.zone.read_text(encoding="utf-8")
        additions = (
            '<port port="8000" protocol="tcp"/>',
            '<service name="ssh"/>',
            "<forward/>",
            "<masquerade/>",
            '<rule family="ipv4"><accept/></rule>',
        )
        for addition in additions:
            with self.subTest(addition=addition):
                self.zone.write_text(
                    original.replace("</zone>", f"  {addition}\n</zone>"),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(MODULE.FirewallPolicyError, "unexpected capability"):
                    self.verify()
        self.zone.write_text(original, encoding="utf-8")

    def test_vendor_zone_rejects_entities_wrong_target_and_extra_service(self) -> None:
        original = self.zone.read_text(encoding="utf-8")
        cases = (
            '<!DOCTYPE zone [<!ENTITY open "ssh">]>\n' + original,
            original.replace('target="default"', 'target="ACCEPT"'),
            original.replace('name="dhcpv6-client"', 'name="ssh"'),
        )
        for index, text in enumerate(cases):
            with self.subTest(case=index):
                self.zone.write_text(text, encoding="utf-8")
                with self.assertRaises(MODULE.FirewallPolicyError):
                    self.verify()
        self.zone.write_text(original, encoding="utf-8")

    def test_policy_files_must_be_private_from_unprivileged_writers(self) -> None:
        os.chmod(self.config, 0o664)
        with self.assertRaisesRegex(MODULE.FirewallPolicyError, "mode-0644"):
            self.verify()
        os.chmod(self.config, 0o644)
        self.zone.unlink()
        self.zone.symlink_to(HERE / "echo-public.xml")
        with self.assertRaises(OSError):
            self.verify()


if __name__ == "__main__":
    unittest.main()
