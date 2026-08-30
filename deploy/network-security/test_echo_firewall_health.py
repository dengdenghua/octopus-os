#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-firewall-health"


class EchoFirewallHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.policy = self.executable(
            "policy",
            """
            #!/usr/bin/env bash
            [[ "$*" == "verify-runtime --machine" ]] || exit 9
            printf '%s\n' "${ECHO_TEST_POLICY_ZONE:-echo-public}"
            """,
        )
        self.systemctl = self.executable(
            "systemctl",
            """
            #!/usr/bin/env bash
            [[ "$*" == "is-active --quiet firewalld.service" ]] || exit 9
            [[ "${ECHO_TEST_FIREWALLD_ACTIVE:-yes}" == yes ]]
            """,
        )
        self.busctl = self.executable(
            "busctl",
            """
            #!/usr/bin/env bash
            [[ "$*" == "--system status org.fedoraproject.FirewallD1" ]] || exit 9
            [[ "${ECHO_TEST_DBUS_READY:-yes}" == yes ]]
            """,
        )
        self.firewall_cmd = self.executable(
            "firewall-cmd",
            """
            #!/usr/bin/env bash
            case "$*" in
              --state) printf '%s\n' "${ECHO_TEST_STATE:-running}" ;;
              --get-default-zone) printf '%s\n' "${ECHO_TEST_RUNTIME_ZONE:-echo-public}" ;;
              --get-zones) printf '%s\n' "block drop echo-public home public trusted work" ;;
              '--zone=echo-public --get-target') printf '%s\n' "${ECHO_TEST_TARGET:-default}" ;;
              '--zone=echo-public --get-services') printf '%s\n' "${ECHO_TEST_SERVICES:-dhcpv6-client}" ;;
              '--zone=echo-public --get-ports') printf '%s' "${ECHO_TEST_PORTS:-}" ;;
              '--zone=echo-public --get-protocols') printf '%s' "${ECHO_TEST_PROTOCOLS:-}" ;;
              '--zone=echo-public --get-source-ports') printf '%s' "${ECHO_TEST_SOURCE_PORTS:-}" ;;
              '--zone=echo-public --list-rich-rules') printf '%s' "${ECHO_TEST_RICH_RULES:-}" ;;
              '--zone=echo-public --query-forward') exit "${ECHO_TEST_FORWARD_STATUS:-1}" ;;
              '--zone=echo-public --query-masquerade') exit "${ECHO_TEST_MASQUERADE_STATUS:-1}" ;;
              *) exit 9 ;;
            esac
            """,
        )
        self.nft = self.executable(
            "nft",
            """
            #!/usr/bin/env bash
            [[ "$*" == "list table inet firewalld" ]] || exit 9
            [[ "${ECHO_TEST_NFT_READY:-yes}" == yes ]]
            """,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def executable(self, name: str, source: str) -> Path:
        path = self.bin / name
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        os.chmod(path, 0o755)
        return path

    def run_health(
        self,
        overrides: dict[str, str] | None = None,
        *,
        sentinel: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "ECHO_FIREWALL_POLICY_TOOL": str(self.policy),
            "ECHO_FIREWALL_SYSTEMCTL": str(self.systemctl),
            "ECHO_FIREWALL_BUSCTL": str(self.busctl),
            "ECHO_FIREWALL_CMD": str(self.firewall_cmd),
            "ECHO_FIREWALL_NFT": str(self.nft),
        }
        if sentinel:
            environment["ECHO_FIREWALL_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
        if overrides:
            environment.update(overrides)
        return subprocess.run(
            [str(HEALTH)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )

    def test_vendor_default_requires_closed_runtime_surface(self) -> None:
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ECHO_FIREWALL_READY backend=nftables default-zone=echo-public "
            "inbound=deny forward=explicit\n",
        )

    def test_authorized_non_vendor_default_is_reported_without_forging_deny(self) -> None:
        result = self.run_health(
            {
                "ECHO_TEST_POLICY_ZONE": "work",
                "ECHO_TEST_RUNTIME_ZONE": "work",
            }
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("default-zone=work inbound=admin-defined", result.stdout)

    def test_service_dbus_runtime_zone_and_nft_fail_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_FIREWALLD_ACTIVE": "no"}, "not active"),
            ({"ECHO_TEST_DBUS_READY": "no"}, "D-Bus"),
            ({"ECHO_TEST_STATE": "not running"}, "not running"),
            ({"ECHO_TEST_RUNTIME_ZONE": "public"}, "disagree"),
            ({"ECHO_TEST_NFT_READY": "no"}, "not loaded"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_vendor_zone_rejects_open_service_port_protocol_or_rich_rule(self) -> None:
        cases = (
            {"ECHO_TEST_SERVICES": "dhcpv6-client ssh"},
            {"ECHO_TEST_PORTS": "8000/tcp"},
            {"ECHO_TEST_PROTOCOLS": "gre"},
            {"ECHO_TEST_SOURCE_PORTS": "5353/udp"},
            {"ECHO_TEST_RICH_RULES": 'rule family="ipv4" accept'},
        )
        for environment in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unexpected", result.stderr)

    def test_vendor_zone_rejects_forwarding_masquerade_or_accept_target(self) -> None:
        cases = (
            {"ECHO_TEST_FORWARD_STATUS": "0"},
            {"ECHO_TEST_MASQUERADE_STATUS": "0"},
            {"ECHO_TEST_TARGET": "ACCEPT"},
        )
        for environment in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)

    def test_runtime_overrides_require_explicit_source_test_sentinel(self) -> None:
        result = self.run_health(sentinel=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("source-test sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
