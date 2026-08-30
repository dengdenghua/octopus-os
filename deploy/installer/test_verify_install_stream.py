#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify_install_stream.py")
MODULE_SPEC = importlib.util.spec_from_file_location("verify_install_stream", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
stream_module = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(stream_module)


class InstallStreamVerifierTests(unittest.TestCase):
    def test_copies_exact_declared_bytes(self) -> None:
        payload = bytes(range(256)) * 4
        output = io.BytesIO()
        copied = stream_module.copy_exact(io.BytesIO(payload), output, len(payload))
        self.assertEqual(copied, len(payload))
        self.assertEqual(output.getvalue(), payload)

    def test_rejects_truncated_stream(self) -> None:
        with self.assertRaisesRegex(stream_module.InstallStreamError, "truncated"):
            stream_module.copy_exact(io.BytesIO(b"x" * 511), io.BytesIO(), 512)

    def test_rejects_trailing_stream_data(self) -> None:
        with self.assertRaisesRegex(stream_module.InstallStreamError, "after the declared"):
            stream_module.copy_exact(io.BytesIO(b"x" * 513), io.BytesIO(), 512)

    def test_rejects_non_sector_aligned_size(self) -> None:
        with self.assertRaisesRegex(stream_module.InstallStreamError, "512-byte"):
            stream_module.copy_exact(io.BytesIO(b"x"), io.BytesIO(), 1)

    def test_rejects_unbounded_size(self) -> None:
        with self.assertRaisesRegex(stream_module.InstallStreamError, "64 TiB"):
            stream_module.copy_exact(
                io.BytesIO(), io.BytesIO(), stream_module.MAX_INSTALL_BYTES + 512
            )


if __name__ == "__main__":
    unittest.main()
