#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("echo_update_status.py")
SPEC = importlib.util.spec_from_file_location("echo_update_status", MODULE_PATH)
assert SPEC and SPEC.loader
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


class UpdateStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "state"
        self.owner = os.geteuid()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, state_name: str = "ready") -> dict[str, object]:
        result: dict[str, object] = {
            "schema": 1,
            "state": state_name,
            "phase": "fetch",
            "updatedAt": 1_800_000_000,
        }
        if state_name in {"ready", "installing", "reboot-required"}:
            result.update(version="0.2.1", manifestSha256="d" * 64)
        if state_name == "failed":
            result["errorCode"] = 1
        return result

    def test_atomic_round_trip_exposes_only_bounded_public_state(self) -> None:
        status.write_status(self.root, self.record(), owner=self.owner)
        self.assertEqual(status.read_status(self.root, owner=self.owner), self.record())
        metadata = (self.root / status.STATUS_NAME).stat()
        self.assertEqual(metadata.st_mode & 0o777, 0o644)
        self.assertFalse(any(path.name.startswith(".status-") for path in self.root.iterdir()))

    def test_rejects_incomplete_authenticated_state(self) -> None:
        incomplete = self.record()
        incomplete.pop("manifestSha256")
        with self.assertRaisesRegex(status.StatusError, "incomplete"):
            status.write_status(self.root, incomplete, owner=self.owner)

    def test_rejects_unknown_fields_and_raw_error_text(self) -> None:
        unsafe = self.record("failed")
        unsafe["error"] = "download URL and internal path"
        with self.assertRaisesRegex(status.StatusError, "unknown"):
            status.validate_record(unsafe)

    def test_rejects_group_writable_state_root(self) -> None:
        self.root.mkdir(mode=0o755)
        self.root.chmod(0o775)
        with self.assertRaisesRegex(status.StatusError, "unsafe"):
            status.write_status(self.root, self.record(), owner=self.owner)

    def test_rejects_symlink_status_file(self) -> None:
        self.root.mkdir(mode=0o755)
        outside = Path(self.temp.name) / "outside.json"
        outside.write_text(json.dumps(self.record()))
        (self.root / status.STATUS_NAME).symlink_to(outside)
        with self.assertRaises(status.StatusError):
            status.read_status(self.root, owner=self.owner)

    def test_failed_state_requires_only_a_bounded_code(self) -> None:
        failed = self.record("failed")
        status.write_status(self.root, failed, owner=self.owner)
        self.assertEqual(status.read_status(self.root, owner=self.owner), failed)


if __name__ == "__main__":
    unittest.main()
