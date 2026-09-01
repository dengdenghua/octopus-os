#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-removable-storage-health"

EXECUTABLES = (
    "usr/bin/systemctl",
    "usr/bin/busctl",
    "usr/bin/udisksctl",
    "usr/bin/stat",
    "usr/libexec/udisks2/udisksd",
    "usr/sbin/mkfs.vfat",
    "usr/sbin/fsck.vfat",
    "usr/sbin/mkfs.exfat",
    "usr/sbin/fsck.exfat",
    "usr/bin/ntfs-3g",
    "usr/sbin/mkfs.ntfs",
    "usr/sbin/mkfs.ext4",
    "usr/sbin/fsck.ext4",
    "usr/sbin/mkfs.btrfs",
    "usr/bin/btrfs",
    "usr/sbin/mkfs.xfs",
    "usr/sbin/xfs_repair",
    "usr/bin/dolphin",
)
POLICY_FILES = (
    "usr/lib/systemd/system/udisks2.service",
    "usr/lib/udev/rules.d/80-udisks2.rules",
    "usr/share/dbus-1/system-services/org.freedesktop.UDisks2.service",
    "usr/share/dbus-1/system.d/org.freedesktop.UDisks2.conf",
    "usr/share/polkit-1/actions/org.freedesktop.UDisks2.policy",
    "usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kio/mtp.so",
    "usr/share/applications/org.kde.dolphin.desktop",
    "usr/share/solid/actions/solid_mtp.desktop",
)


class RemovableStorageHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        for relative in EXECUTABLES:
            source = "#!/bin/bash\nexit 0\n"
            if relative == "usr/bin/systemctl":
                source = """
                    #!/bin/bash
                    [[ "$*" == "is-active --quiet udisks2.service" ]] || exit 9
                    [[ "${ECHO_TEST_UDISKS_ACTIVE:-yes}" == yes ]]
                """
            elif relative == "usr/bin/busctl":
                source = """
                    #!/bin/bash
                    [[ "$*" == "--system status org.freedesktop.UDisks2" ]] || exit 9
                    [[ "${ECHO_TEST_UDISKS_DBUS:-yes}" == yes ]]
                """
            elif relative == "usr/bin/udisksctl":
                source = """
                    #!/bin/bash
                    [[ "$*" == status ]] || exit 9
                    [[ "${ECHO_TEST_UDISKS_STATUS:-yes}" == yes ]]
                """
            elif relative == "usr/bin/stat":
                source = """
                    #!/bin/bash
                    [[ "$1" == -Lc && "$2" == '%u:%g:%a' && -n "$3" ]] || exit 9
                    printf '%s\n' "${ECHO_TEST_RUNTIME_METADATA:-0:0:755}"
                """
            self.write(relative, source, executable=True)
        for relative in POLICY_FILES:
            self.write(relative, "fixture\n", executable=False)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, relative: str, source: str, *, executable: bool) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    def run_health(
        self,
        overrides: dict[str, str] | None = None,
        *,
        sentinel: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "ECHO_REMOVABLE_STORAGE_RUNTIME_ROOT": str(self.root),
        }
        if sentinel:
            environment["ECHO_REMOVABLE_STORAGE_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
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

    def test_complete_root_emits_one_bounded_readiness_marker(self) -> None:
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ECHO_REMOVABLE_STORAGE_READY provider=udisks2 policy=polkit "
            "mount=on-demand filesystems=vfat,exfat,ntfs,ext4,btrfs,xfs portable=mtp\n",
        )

    def test_service_dbus_and_status_api_fail_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_UDISKS_ACTIVE": "no"}, "not active"),
            ({"ECHO_TEST_UDISKS_DBUS": "no"}, "D-Bus"),
            ({"ECHO_TEST_UDISKS_STATUS": "no"}, "status API"),
        )
        for environment, message in cases:
            with self.subTest(environment=environment):
                result = self.run_health(environment)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_missing_filesystem_or_desktop_integration_fails_closed(self) -> None:
        for relative in (
            "usr/sbin/mkfs.exfat",
            "usr/bin/ntfs-3g",
            "usr/sbin/mkfs.btrfs",
            "usr/lib/x86_64-linux-gnu/qt6/plugins/kf6/kio/mtp.so",
            "usr/share/applications/org.kde.dolphin.desktop",
        ):
            with self.subTest(relative=relative):
                target = self.root / relative
                saved = target.read_bytes()
                target.unlink()
                result = self.run_health()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("missing", result.stderr)
                target.parent.mkdir(parents=True, exist_ok=True)
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

    def test_runtime_root_must_be_absolute_directory_not_symlink(self) -> None:
        relative = self.run_health(
            {"ECHO_REMOVABLE_STORAGE_RUNTIME_ROOT": "relative"},
        )
        self.assertNotEqual(relative.returncode, 0)
        link = self.root.parent / f"{self.root.name}-link"
        link.symlink_to(self.root)
        try:
            linked = self.run_health(
                {"ECHO_REMOVABLE_STORAGE_RUNTIME_ROOT": str(link)},
            )
            self.assertNotEqual(linked.returncode, 0)
        finally:
            link.unlink()


if __name__ == "__main__":
    unittest.main()
