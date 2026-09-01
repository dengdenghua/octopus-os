#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

MODULE_PATH = Path(__file__).with_name("interrupt-sysupdate-after-write.py")
SPEC = importlib.util.spec_from_file_location("interrupt_sysupdate_after_write", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SysupdateInterruptionTests(unittest.TestCase):
    sector_size = 512
    start_sector = 8
    sector_count = 4

    def make_image(self, root: Path) -> Path:
        image = root / "disk.raw"
        image.write_bytes(b"\0" * (self.sector_size * 32))
        return image

    def make_fake(self, root: Path, body: str) -> Path:
        fake = root / "real-systemd-sysupdate"
        fake.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        fake.chmod(0o755)
        return fake

    def environment(self, image: Path, fake: Path) -> dict[str, str]:
        offset = self.sector_size * self.start_sector
        length = self.sector_size * self.sector_count
        before = hashlib.sha256(image.read_bytes()[offset : offset + length]).hexdigest()
        return {
            "ECHO_REAL_SYSUPDATE_BIN": str(fake),
            "ECHO_UPDATE_INTERRUPT_IMAGE": str(image),
            "ECHO_UPDATE_INTERRUPT_SECTOR_SIZE": str(self.sector_size),
            "ECHO_UPDATE_INTERRUPT_START_SECTOR": str(self.start_sector),
            "ECHO_UPDATE_INTERRUPT_SECTOR_COUNT": str(self.sector_count),
            "ECHO_UPDATE_INTERRUPT_BEFORE_SHA256": before,
        }

    def test_kills_real_process_group_after_first_observed_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = self.make_image(root)
            pid_file = root / "child.pid"
            fake = self.make_fake(
                root,
                "import os, time\n"
                "from pathlib import Path\n"
                "image = os.environ['ECHO_UPDATE_INTERRUPT_IMAGE']\n"
                "offset = int(os.environ['ECHO_UPDATE_INTERRUPT_SECTOR_SIZE']) * int(os.environ['ECHO_UPDATE_INTERRUPT_START_SECTOR'])\n"
                "Path(os.environ['ECHO_TEST_CHILD_PID']).write_text(str(os.getpid()))\n"
                "with open(image, 'r+b', buffering=0) as stream:\n"
                "    stream.seek(offset)\n"
                "    stream.write(b'changed')\n"
                "    os.fsync(stream.fileno())\n"
                "time.sleep(30)\n",
            )
            environment = self.environment(image, fake)
            environment["ECHO_TEST_CHILD_PID"] = str(pid_file)
            stdout = io.StringIO()
            with patch.dict(os.environ, environment, clear=False), redirect_stdout(stdout):
                result = MODULE.main(("--image=/tmp/disposable.raw", "update"))
            self.assertEqual(result, 128 + signal.SIGKILL)
            self.assertIn("ECHO_UPDATE_INTERRUPTION_TRIGGERED", stdout.getvalue())
            self.assertIn("ECHO_UPDATE_INTERRUPTION_OBSERVED result=signal-9", stdout.getvalue())
            pid = int(pid_file.read_text(encoding="utf-8"))
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_rejects_process_that_exits_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = self.make_image(root)
            fake = self.make_fake(root, "raise SystemExit(0)\n")
            stderr = io.StringIO()
            with (
                patch.dict(os.environ, self.environment(image, fake), clear=False),
                redirect_stderr(stderr),
            ):
                result = MODULE.main(("update",))
            self.assertEqual(result, 1)
            self.assertIn("before a root write was observed", stderr.getvalue())

    def test_passes_check_new_through_without_waiting_for_a_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = self.make_image(root)
            fake = self.make_fake(root, "print('0.2.1')\n")
            environment = os.environ.copy()
            environment.update(self.environment(image, fake))
            completed = subprocess.run(
                (
                    sys.executable,
                    str(MODULE_PATH),
                    "--image=/tmp/disposable.raw",
                    "check-new",
                ),
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stdout, "0.2.1\n")

    def test_rejects_stale_sample_without_launching(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            image = self.make_image(root)
            fake = self.make_fake(root, "raise SystemExit(99)\n")
            environment = self.environment(image, fake)
            environment["ECHO_UPDATE_INTERRUPT_BEFORE_SHA256"] = "f" * 64
            stderr = io.StringIO()
            with patch.dict(os.environ, environment, clear=False), redirect_stderr(stderr):
                result = MODULE.main(("update",))
            self.assertEqual(result, 1)
            self.assertIn("changed before systemd-sysupdate started", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
