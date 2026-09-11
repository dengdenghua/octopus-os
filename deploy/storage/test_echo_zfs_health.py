#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEALTH = HERE / "echo-zfs-health"
BASH = shutil.which("bash")


@unittest.skipUnless(BASH, "requires a POSIX bash runtime")
class ZfsHealthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        scripts = {
            "usr/bin/uname": """
                #!/bin/bash
                [[ "$1" == -r ]] || exit 9
                printf '%s\n' "${ECHO_TEST_KERNEL:-6.12.1+deb13-amd64}"
            """,
            "usr/sbin/modinfo": """
                #!/bin/bash
                [[ "$1" == -k && "$3" == -F && "$5" == zfs ]] || exit 9
                case "$4" in
                  filename) printf '/lib/modules/%s/updates/dkms/zfs.ko\n' "$2" ;;
                  sig_id) printf '%s\n' "${ECHO_TEST_SIG_ID:-PKCS#7}" ;;
                  signer) printf '%s\n' "${ECHO_TEST_SIGNER:-Echo OS CI}" ;;
                  version) printf '%s\n' "${ECHO_TEST_VERSION:-2.3.9}" ;;
                  *) exit 9 ;;
                esac
            """,
            "usr/sbin/zfs": """
                #!/bin/bash
                [[ "$*" == "list -H -o name" ]]
            """,
            "usr/sbin/zpool": """
                #!/bin/bash
                [[ "$*" == "list -H -o name" ]]
            """,
            "usr/bin/stat": """
                #!/bin/bash
                [[ "$1" == -Lc && "$2" == '%u:%g:%a' && -n "$3" ]] || exit 9
                printf '%s\n' "${ECHO_TEST_METADATA:-0:0:755}"
            """,
            "usr/bin/systemctl": """
                #!/bin/bash
                [[ "$1" == is-active && "$2" == --quiet ]] || exit 9
                case "$3" in
                  zfs-load-module.service) [[ "${ECHO_TEST_LOADER_ACTIVE:-yes}" == yes ]] ;;
                  zfs-zed.service) [[ "${ECHO_TEST_ZED_ACTIVE:-yes}" == yes ]] ;;
                  *) exit 9 ;;
                esac
            """,
        }
        for relative, source in scripts.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
            path.chmod(0o755)
        (self.root / "sys/module/zfs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_health(
        self, overrides: dict[str, str] | None = None, *, sentinel: bool = True
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "ECHO_ZFS_RUNTIME_ROOT": str(self.root),
        }
        if sentinel:
            environment["ECHO_ZFS_SOURCE_TEST"] = "USE-SOURCE-RUNTIME"
        if overrides:
            environment.update(overrides)
        return subprocess.run(
            [str(BASH), str(HEALTH)],
            check=False,
            capture_output=True,
            text=True,
            env=environment,
            timeout=10,
        )

    def test_complete_runtime_emits_bounded_marker(self) -> None:
        result = self.run_health()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "ECHO_ZFS_READY kernel=6.12.1+deb13-amd64 version=2.3.9 signature=pkcs7\n",
        )

    def test_wrong_or_missing_signature_fails_closed(self) -> None:
        for overrides in (
            {"ECHO_TEST_SIG_ID": ""},
            {"ECHO_TEST_SIG_ID": "X.509"},
            {"ECHO_TEST_SIGNER": ""},
        ):
            with self.subTest(overrides=overrides):
                result = self.run_health(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PKCS#7", result.stderr)

    def test_unloaded_kernel_module_fails_closed(self) -> None:
        (self.root / "sys/module/zfs").rmdir()

        result = self.run_health()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not loaded", result.stderr)

    def test_invalid_kernel_version_or_mutable_runtime_fails_closed(self) -> None:
        cases = (
            ({"ECHO_TEST_KERNEL": "../../host"}, "kernel release"),
            ({"ECHO_TEST_VERSION": ""}, "module version"),
            ({"ECHO_TEST_METADATA": "0:0:775"}, "root-owned"),
            ({"ECHO_TEST_METADATA": "1000:1000:755"}, "root-owned"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                result = self.run_health(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_loader_or_event_daemon_failure_fails_closed(self) -> None:
        for overrides, message in (
            ({"ECHO_TEST_LOADER_ACTIVE": "no"}, "module loader"),
            ({"ECHO_TEST_ZED_ACTIVE": "no"}, "event daemon"),
        ):
            with self.subTest(overrides=overrides):
                result = self.run_health(overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_override_requires_explicit_source_test_sentinel(self) -> None:
        result = self.run_health(sentinel=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("source-test sentinel", result.stderr)


if __name__ == "__main__":
    unittest.main()
