#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-printing-health"

EXECUTABLES = (
    "usr/bin/python3",
    "usr/bin/systemctl",
    "usr/bin/lpstat",
    "usr/bin/findmnt",
    "usr/bin/stat",
    "usr/lib/echo-os/echo-printing-policy.py",
    "usr/sbin/cupsd",
    "usr/bin/lp",
    "usr/bin/cancel",
    "usr/sbin/lpadmin",
    "usr/libexec/cups-pk-helper-mechanism",
    "usr/sbin/ipp-usb",
    "usr/sbin/avahi-daemon",
    "usr/bin/kde-add-printer",
    "usr/bin/configure-printer",
    "usr/bin/kde-print-queue",
    "usr/lib/cups/backend/ipp",
    "usr/lib/cups/backend/ipps",
    "usr/lib/cups/filter/pdftopdf",
    "usr/lib/cups/filter/pdftoraster",
)
POLICY_FILES = (
    "etc/cups/cupsd.conf",
    "etc/ipp-usb/ipp-usb.conf",
    "usr/lib/systemd/system/cups.service",
    "usr/lib/systemd/system/cups.socket",
    "usr/lib/systemd/system/cups.path",
    "usr/lib/systemd/system/ipp-usb.service",
    "usr/lib/udev/rules.d/71-ipp-usb.rules",
    "usr/lib/systemd/system/avahi-daemon.service",
    "usr/lib/systemd/system/avahi-daemon.socket",
    "usr/share/dbus-1/system-services/org.freedesktop.Avahi.service",
    "usr/share/dbus-1/system.d/avahi-dbus.conf",
    "etc/dbus-1/system.d/org.opensuse.CupsPkHelper.Mechanism.conf",
    "usr/share/dbus-1/system-services/org.opensuse.CupsPkHelper.Mechanism.service",
    "usr/share/polkit-1/actions/org.opensuse.cupspkhelper.mechanism.policy",
    "usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kded/printmanager.so",
    "usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_printer_manager.so",
    "usr/share/applications/kcm_printer_manager.desktop",
    "usr/share/applications/org.kde.kde-add-printer.desktop",
)


class PrintingHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for relative in EXECUTABLES:
            source = "#!/bin/bash\nexit 0\n"
            if relative == "usr/bin/systemctl":
                source = """
                    #!/bin/bash
                    [[ "$1" == is-active && "$2" == --quiet ]] || exit 9
                    case "$3" in
                      cups.socket) [[ "${ECHO_TEST_CUPS_SOCKET:-yes}" == yes ]] ;;
                      cups.service) [[ "${ECHO_TEST_CUPS_SERVICE:-yes}" == yes ]] ;;
                      *) exit 9 ;;
                    esac
                """
            elif relative == "usr/bin/lpstat":
                source = """
                    #!/bin/bash
                    [[ "$*" == -r ]] || exit 9
                    [[ "${ECHO_TEST_CUPS_API:-yes}" == yes ]]
                """
            elif relative == "usr/bin/findmnt":
                source = """
                    #!/bin/bash
                    [[ "$1" == --noheadings && "$2" == --output && "$3" == SOURCE &&
                       "$4" == --target && -n "$5" ]] || exit 9
                    printf '%s\n' "${ECHO_TEST_SPOOL_SOURCE:-/dev/mapper/echo-var}"
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
                    [[ "$#" == 1 && "$1" == */usr/lib/echo-os/echo-printing-policy.py ]] || exit 9
                    [[ "${ECHO_TEST_PRINTING_POLICY:-yes}" == yes ]]
                """
            self.write(relative, source, executable=True)
        for relative in POLICY_FILES:
            self.write(relative, "fixture\n", executable=False)
        (self.root / "var/spool/cups").mkdir(parents=True)

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
            "ECHO_PRINTING_RUNTIME_ROOT": str(self.root),
        }
        if sentinel:
            environment["ECHO_PRINTING_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
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
            "ECHO_PRINTING_READY provider=cups transport=local-only auth=polkit "
            "driverless=ipp-usb retention=off storage=encrypted-var\n",
        )

    def test_scheduler_socket_api_and_policy_fail_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_CUPS_SOCKET": "no"}, "socket"),
            ({"ECHO_TEST_CUPS_SERVICE": "no"}, "scheduler"),
            ({"ECHO_TEST_CUPS_API": "no"}, "API"),
            ({"ECHO_TEST_PRINTING_POLICY": "no"}, "policy"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_missing_kde_policykit_filter_or_usb_runtime_fails_closed(self) -> None:
        for relative in (
            "usr/libexec/cups-pk-helper-mechanism",
            "usr/sbin/ipp-usb",
            "usr/lib/cups/filter/pdftopdf",
            "usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/kcms/systemsettings/kcm_printer_manager.so",
            "usr/share/polkit-1/actions/org.opensuse.cupspkhelper.mechanism.policy",
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

    def test_spool_must_reside_on_encrypted_var(self) -> None:
        result = self.run_health({"ECHO_TEST_SPOOL_SOURCE": "/dev/vda1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("encrypted persistent var", result.stderr)

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
