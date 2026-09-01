#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-scanning-health"

EXECUTABLES = (
    "usr/bin/python3",
    "usr/bin/systemctl",
    "usr/bin/scanimage",
    "usr/bin/getent",
    "usr/bin/stat",
    "usr/lib/echo-os/echo-scanning-policy.py",
    "usr/bin/sane-find-scanner",
    "usr/bin/airscan-discover",
    "usr/bin/skanpage",
    "usr/sbin/ipp-usb",
    "usr/sbin/avahi-daemon",
)
RUNTIME_FILES = (
    "etc/sane.d/airscan.conf",
    "etc/sane.d/dll.conf",
    "etc/sane.d/dll.d/airscan",
    "usr/lib/x86_64-linux-gnu/libsane.so.1",
    "usr/lib/x86_64-linux-gnu/sane/libsane-airscan.so.1",
    "usr/lib/udev/rules.d/60-libsane1.rules",
    "usr/lib/udev/rules.d/99-libsane1.rules",
    "usr/share/applications/org.kde.skanpage.desktop",
    "usr/lib/systemd/system/saned.service",
    "usr/lib/systemd/system/saned.socket",
    "usr/lib/systemd/system/saned@.service",
    "etc/ipp-usb/ipp-usb.conf",
    "usr/lib/systemd/system/ipp-usb.service",
    "usr/lib/udev/rules.d/71-ipp-usb.rules",
    "usr/lib/systemd/system/avahi-daemon.service",
    "usr/share/dbus-1/system-services/org.freedesktop.Avahi.service",
)


class ScanningHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for relative in EXECUTABLES:
            source = "#!/bin/bash\nexit 0\n"
            if relative == "usr/bin/systemctl":
                source = """
                    #!/bin/bash
                    case "$1" in
                      is-enabled)
                        [[ "$2" == saned.socket ]] || exit 9
                        printf '%s\n' "${ECHO_TEST_SANED_ENABLEMENT:-disabled}"
                        [[ "${ECHO_TEST_SANED_ENABLEMENT:-disabled}" != enabled ]]
                        ;;
                      is-active)
                        [[ "$2" == --quiet && "$3" == saned.socket ]] || exit 9
                        [[ "${ECHO_TEST_SANED_ACTIVE:-no}" == yes ]]
                        ;;
                      *) exit 9 ;;
                    esac
                """
            elif relative == "usr/bin/scanimage":
                source = """
                    #!/bin/bash
                    [[ "$*" == --version ]] || exit 9
                    [[ "${ECHO_TEST_SANE_LOADER:-yes}" == yes ]]
                """
            elif relative in {
                "usr/bin/sane-find-scanner",
                "usr/bin/airscan-discover",
                "usr/bin/skanpage",
                "usr/sbin/ipp-usb",
                "usr/sbin/avahi-daemon",
            }:
                source = """
                    #!/bin/bash
                    echo "boot health invoked a device enumerator or daemon" >&2
                    exit 99
                """
            elif relative == "usr/bin/getent":
                source = """
                    #!/bin/bash
                    [[ "$1" == group && "$2" == scanner ]] || exit 9
                    [[ "${ECHO_TEST_SCANNER_GROUP:-yes}" == yes ]] || exit 1
                    printf 'scanner:x:108:echo\n'
                """
            elif relative == "usr/bin/stat":
                source = """
                    #!/bin/bash
                    [[ "$1" == -Lc && "$2" == '%u:%g:%a' && -n "$3" ]] || exit 9
                    printf '%s\n' "${ECHO_TEST_RUNTIME_METADATA:-0:0:755}"
                """
            elif relative == "usr/bin/python3":
                source = """
                    #!/bin/bash
                    [[ "$#" == 1 && "$1" == */usr/lib/echo-os/echo-scanning-policy.py ]] || exit 9
                    [[ "${ECHO_TEST_SCANNING_POLICY:-yes}" == yes ]]
                """
            self.write(relative, source, executable=True)
        for relative in RUNTIME_FILES:
            self.write(relative, "fixture\n", executable=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, source: str, *, executable: bool) -> Path:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        target.chmod(0o755 if executable else 0o644)
        return target

    def run_health(
        self,
        overrides: dict[str, str] | None = None,
        *,
        sentinel: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "ECHO_SCANNING_RUNTIME_ROOT": str(self.root),
        }
        if sentinel:
            environment["ECHO_SCANNING_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
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

    def test_complete_runtime_emits_one_bounded_marker(self) -> None:
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ECHO_SCANNING_READY provider=sane frontend=skanpage usb=udev,ipp-usb "
            "network=airscan-on-demand sharing=off retention=user-owned\n",
        )

    def test_policy_group_and_backend_loader_fail_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_SCANNING_POLICY": "no"}, "policy"),
            ({"ECHO_TEST_SCANNER_GROUP": "no"}, "group"),
            ({"ECHO_TEST_SANE_LOADER": "no"}, "loader"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_scanner_sharing_must_be_disabled_and_inactive(self) -> None:
        cases = (
            ({"ECHO_TEST_SANED_ENABLEMENT": "enabled"}, "not disabled"),
            ({"ECHO_TEST_SANED_ACTIVE": "yes"}, "is active"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_missing_usb_airscan_or_kde_runtime_fails_closed(self) -> None:
        for relative in (
            "usr/sbin/ipp-usb",
            "usr/bin/airscan-discover",
            "usr/bin/skanpage",
            "usr/lib/x86_64-linux-gnu/sane/libsane-airscan.so.1",
            "usr/lib/udev/rules.d/60-libsane1.rules",
        ):
            with self.subTest(relative=relative):
                target = self.root / relative
                saved = target.read_bytes()
                target.unlink()
                result = self.run_health()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing", result.stderr)
                target.write_bytes(saved)
                target.chmod(0o755 if relative in EXECUTABLES else 0o644)

    def test_mutable_or_non_root_runtime_is_rejected(self) -> None:
        for metadata in ("501:20:755", "0:0:775", "0:0:777"):
            with self.subTest(metadata=metadata):
                result = self.run_health({"ECHO_TEST_RUNTIME_METADATA": metadata})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("immutable root-owned", result.stderr)

    def test_override_requires_explicit_source_test_sentinel(self) -> None:
        result = self.run_health(sentinel=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("source-test sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
