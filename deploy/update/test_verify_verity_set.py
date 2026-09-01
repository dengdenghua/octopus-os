#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).with_name("verify-verity-set.py")
SPEC = importlib.util.spec_from_file_location("verify_verity_set", MODULE_PATH)
assert SPEC and SPEC.loader
verity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verity)


class VerifyVeritySetTests(unittest.TestCase):
    ROOT_HASH = "11" * 16 + "22" * 16

    def signature(self) -> dict[str, str]:
        return {
            "rootHash": self.ROOT_HASH,
            "certificateFingerprint": "33" * 32,
            "signature": base64.b64encode(b"pkcs7-test").decode("ascii"),
        }

    def test_signature_partition_requires_exact_bounded_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signature.raw"
            path.write_bytes(json.dumps(self.signature()).encode() + b"\x00" * 128)
            self.assertEqual(verity.read_signature_partition(path), self.signature())

            invalid = self.signature()
            invalid["extra"] = "unsigned-ambiguity"
            path.write_bytes(json.dumps(invalid).encode())
            with self.assertRaisesRegex(verity.VerityError, "unexpected schema"):
                verity.read_signature_partition(path)

            path.write_bytes(json.dumps(self.signature()).encode() + b"\x00hidden")
            with self.assertRaisesRegex(verity.VerityError, "non-zero trailing"):
                verity.read_signature_partition(path)

            path.write_bytes(b"x" * (verity.MAX_SIGNATURE_BYTES + 1))
            with self.assertRaisesRegex(verity.VerityError, "exceeds 4 MiB"):
                verity.read_signature_partition(path)

    def test_regular_split_artifact_uuid_comes_from_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            expected = uuid.UUID("11111111-1111-1111-1111-111111111111")
            path = Path(directory) / f"echo-os_1.root.{expected}.raw"
            path.write_bytes(b"root")
            self.assertEqual(verity.partition_uuid(path), expected)

    def test_uki_has_one_roothash_and_no_root_selector(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            uki = Path(directory) / "echo.efi"
            uki.write_bytes(b"uki")
            with mock.patch.object(
                verity,
                "run",
                return_value=f".cmdline:\n roothash={self.ROOT_HASH} ro quiet\n".encode(),
            ):
                self.assertEqual(verity.inspect_uki_roothash(uki), self.ROOT_HASH)

            with mock.patch.object(
                verity,
                "run",
                return_value=(
                    f"roothash={self.ROOT_HASH} roothash={self.ROOT_HASH}\n"
                ).encode(),
            ):
                with self.assertRaisesRegex(verity.VerityError, "exactly one"):
                    verity.inspect_uki_roothash(uki)

            with mock.patch.object(
                verity,
                "run",
                return_value=(
                    f"roothash={self.ROOT_HASH} root=PARTLABEL=mutable\n"
                ).encode(),
            ):
                with self.assertRaisesRegex(verity.VerityError, "must not select"):
                    verity.inspect_uki_roothash(uki)

    def test_verify_set_binds_uuid_signature_tree_and_uki(self) -> None:
        root = Path("root.raw")
        hash_tree = Path("root-verity.raw")
        signature_path = Path("root-verity-sig.raw")
        certificate = Path("release.pem")
        uki = Path("echo.efi")
        expected_uuids = [uuid.UUID(self.ROOT_HASH[:32]), uuid.UUID(self.ROOT_HASH[-32:])]
        with mock.patch.object(
            verity, "read_signature_partition", return_value=self.signature()
        ):
            with mock.patch.object(
                verity, "partition_uuid", side_effect=expected_uuids
            ):
                with mock.patch.object(verity, "verify_signature") as verify_signature:
                    with mock.patch.object(
                        verity, "inspect_uki_roothash", return_value=self.ROOT_HASH
                    ):
                        with mock.patch.object(
                            verity, "run", return_value=b""
                        ) as run:
                            self.assertEqual(
                                verity.verify_set(
                                    root,
                                    hash_tree,
                                    signature_path,
                                    certificate,
                                    uki,
                                ),
                                self.ROOT_HASH,
                            )
        verify_signature.assert_called_once()
        run.assert_called_once_with(
            ["veritysetup", "verify", str(root), str(hash_tree), self.ROOT_HASH]
        )


if __name__ == "__main__":
    unittest.main()
