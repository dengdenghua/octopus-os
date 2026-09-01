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
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VerifyUpdateBundleTest(unittest.TestCase):
    ROOT_UUID = "11111111-2222-3333-4444-555555555555"
    VERITY_UUID = "66666666-7777-8888-9999-aaaaaaaaaaaa"
    VERITY_SIG_UUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"

    def make_bundle(
        self,
        root_name=f"echo-os_0.3.0.root.{ROOT_UUID}.raw.zst",
        uki_name="echo-os_0.3.0.efi",
    ):
        temporary = tempfile.TemporaryDirectory()
        bundle = Path(temporary.name)
        root_version = root_name.removeprefix("echo-os_").split(".root.", 1)[0]
        artifacts = {
            root_name: b"root-image",
            f"echo-os_{root_version}.root-verity.{self.VERITY_UUID}.raw.zst": b"verity-tree",
            f"echo-os_{root_version}.root-verity-sig.{self.VERITY_SIG_UUID}.raw.zst": b"verity-signature",
            uki_name: b"unified-kernel",
            MODULE.SOURCE_IDENTITY_NAME: (
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
        for name, content in artifacts.items():
            (bundle / name).write_bytes(content)
            lines.append(f"{hashlib.sha256(content).hexdigest()}  {name}")
        (bundle / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (bundle / "SHA256SUMS.gpg").write_bytes(b"detached-signature-test")
        return temporary, bundle

    def test_accepts_matching_root_and_uki(self):
        temporary, bundle = self.make_bundle()
        self.addCleanup(temporary.cleanup)
        self.assertEqual(MODULE.verify_bundle(bundle), "0.3.0")

    def test_rejects_mismatched_versions(self):
        temporary, bundle = self.make_bundle(uki_name="echo-os_0.4.0.efi")
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(MODULE.BundleError, "versions do not match"):
            MODULE.verify_bundle(bundle)

    def test_rejects_path_traversal(self):
        temporary, bundle = self.make_bundle()
        self.addCleanup(temporary.cleanup)
        manifest = bundle / "SHA256SUMS"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + f"{'0' * 64}  ../outside\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(MODULE.BundleError, "invalid SHA256SUMS line"):
            MODULE.verify_bundle(bundle)

    def test_rejects_modified_payload(self):
        temporary, bundle = self.make_bundle()
        self.addCleanup(temporary.cleanup)
        (bundle / f"echo-os_0.3.0.root.{self.ROOT_UUID}.raw.zst").write_bytes(b"tampered")
        with self.assertRaisesRegex(MODULE.BundleError, "SHA-256 mismatch"):
            MODULE.verify_bundle(bundle)

    def test_rejects_symlink_payload(self):
        temporary, bundle = self.make_bundle()
        self.addCleanup(temporary.cleanup)
        root = bundle / f"echo-os_0.3.0.root.{self.ROOT_UUID}.raw.zst"
        root.unlink()
        root.symlink_to(bundle / "echo-os_0.3.0.efi")
        with self.assertRaisesRegex(MODULE.BundleError, "non-symlink"):
            MODULE.verify_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
