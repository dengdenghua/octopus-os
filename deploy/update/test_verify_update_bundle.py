#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-update-bundle.py")
SPEC = importlib.util.spec_from_file_location("verify_update_bundle", MODULE_PATH)
assert SPEC and SPEC.loader
update = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update)


class UpdateBundleVerifierTests(unittest.TestCase):
    ROOT_UUID = "11111111-2222-3333-4444-555555555555"
    VERITY_UUID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    VERITY_SIG_UUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"

    def make_bundle(self, root: Path, version: str = "0.2.1") -> Path:
        bundle = root / "bundle"
        bundle.mkdir()
        payloads = {
            f"echo-os_{version}.root.{self.ROOT_UUID}.raw.zst": b"compressed-root-test",
            f"echo-os_{version}.root-verity.{self.VERITY_UUID}.raw.zst": b"verity-tree-test",
            f"echo-os_{version}.root-verity-sig.{self.VERITY_SIG_UUID}.raw.zst": b"verity-signature-test",
            f"echo-os_{version}.efi": b"uki-test",
            update.SOURCE_IDENTITY_NAME: (
                json.dumps(
                    {
                        "schema": 1,
                        "kind": "echo-os-source-identity",
                        "repository": "https://github.com/example/echo-os.git",
                        "commit": "a" * 40,
                        "tree": "b" * 40,
                        "commit_time": "2024-01-01T00:00:00+00:00",
                        "source_date_epoch": 1704067200,
                        "dirty": False,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8"),
        }
        lines = []
        for name, content in payloads.items():
            (bundle / name).write_bytes(content)
            lines.append(f"{hashlib.sha256(content).hexdigest()}  {name}")
        (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (bundle / "SHA256SUMS.gpg").write_bytes(b"detached-signature-test")
        return bundle

    def test_valid_exact_bundle_passes_preflight_and_full_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            self.assertEqual(update.verify_bundle(bundle, hash_payloads=False), "0.2.1")
            self.assertEqual(update.verify_bundle(bundle), "0.2.1")

    def test_preflight_bounds_work_before_payload_hashing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            payload = bundle / f"echo-os_0.2.1.root.{self.ROOT_UUID}.raw.zst"
            payload.write_bytes(b"tampered-but-bounded")
            self.assertEqual(update.verify_bundle(bundle, hash_payloads=False), "0.2.1")
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle)

    def test_unsigned_extra_or_missing_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            (bundle / "unsigned.txt").write_text("extra", encoding="utf-8")
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)
            (bundle / "unsigned.txt").unlink()
            (bundle / "echo-os_0.2.1.efi").unlink()
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

    def test_symlinked_bundle_or_payload_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = self.make_bundle(root)
            linked_bundle = root / "linked-bundle"
            linked_bundle.symlink_to(bundle, target_is_directory=True)
            with self.assertRaises(update.BundleError):
                update.verify_bundle(linked_bundle, hash_payloads=False)

            payload = bundle / "echo-os_0.2.1.efi"
            target = root / "external.efi"
            target.write_bytes(payload.read_bytes())
            payload.unlink()
            payload.symlink_to(target)
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

    def test_duplicate_kind_and_mixed_versions_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            manifest = bundle / "SHA256SUMS"
            valid_manifest = manifest.read_text(encoding="utf-8")
            first_line = valid_manifest.splitlines()[0]
            manifest.write_text(valid_manifest + first_line + "\n", encoding="utf-8")
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

            manifest.write_text(
                valid_manifest.replace("echo-os_0.2.1.efi", "echo-os_0.2.2.efi"),
                encoding="utf-8",
            )
            (bundle / "echo-os_0.2.1.efi").rename(bundle / "echo-os_0.2.2.efi")
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

    def test_manifest_signature_and_payload_size_caps_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            manifest = bundle / "SHA256SUMS"
            valid_manifest = manifest.read_bytes()
            signature = bundle / "SHA256SUMS.gpg"
            signature.write_bytes(b"x" * (update.MAX_SIGNATURE_SIZE + 1))
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

            signature.write_bytes(b"signature")
            manifest.write_bytes(b"x" * (update.MAX_MANIFEST_SIZE + 1))
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)
            manifest.write_bytes(valid_manifest)
            payload = bundle / f"echo-os_0.2.1.root.{self.ROOT_UUID}.raw.zst"
            payload.write_bytes(b"")
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)
            payload.write_bytes(b"x")
            with payload.open("r+b") as stream:
                stream.truncate(update.MAX_ROOT_PAYLOAD_SIZE + 1)
            with self.assertRaises(update.BundleError):
                update.verify_bundle(bundle, hash_payloads=False)

    def test_dirty_or_credentialed_os_source_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bundle = self.make_bundle(Path(directory))
            source = bundle / update.SOURCE_IDENTITY_NAME
            payload = json.loads(source.read_text(encoding="utf-8"))
            payload["dirty"] = True
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(update.BundleError, "source identity fields"):
                update.verify_bundle(bundle, hash_payloads=False)

            payload["dirty"] = False
            payload["repository"] = "https://user:token@github.com/example/echo-os.git"
            source.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(update.BundleError, "source identity fields"):
                update.verify_bundle(bundle, hash_payloads=False)


if __name__ == "__main__":
    unittest.main()
