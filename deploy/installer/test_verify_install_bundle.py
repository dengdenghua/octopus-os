#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify_install_bundle.py")
SPEC = importlib.util.spec_from_file_location("verify_install_bundle", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
bundle_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_module)


class InstallBundleVerifierTests(unittest.TestCase):
    def fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        bundle = Path(temporary.name)
        factory_key = bundle / bundle_module.FACTORY_KEY_NAME
        factory_key.write_bytes(b"f" * 32)
        payload = bundle / "echo-os_0.2.0.raw.zst"
        payload.write_bytes(b"compressed-test-payload")
        manifest = {
            "schema": 3,
            "product": "echo-os",
            "architecture": "x86-64",
            "version": "0.2.0",
            "source": {
                "repository": "https://github.com/example/echo-os.git",
                "commit": "a" * 40,
                "tree": "b" * 40,
                "manifest_sha256": "c" * 64,
            },
            "payload": {
                "filename": payload.name,
                "compression": "zstd",
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "uncompressed_sha256": "1" * 64,
                "uncompressed_size": 512,
            },
            "disk": {
                "partition_table": "gpt",
                "partition_labels": [
                    "echo-esp",
                    "echo-root-0.2.0",
                    "echo-root-0.2.0-verity",
                    "echo-root-0.2.0-verity-sig",
                    "_empty",
                    "_empty",
                    "_empty",
                    "echo-var",
                    "echo-swap",
                    "echo-home",
                ],
            },
            "data_protection": {
                "scheme": "luks2-factory-key",
                "factory_key_filename": factory_key.name,
                "factory_key_sha256": hashlib.sha256(factory_key.read_bytes()).hexdigest(),
                "encrypted_partitions": ["echo-var", "echo-swap", "echo-home"],
                "tpm2_policy": {
                    "direct_pcrs": [],
                    "signed_pcrs": [11],
                    "public_key_sha256": "2" * 64,
                },
            },
        }
        manifest_path = bundle / bundle_module.MANIFEST_NAME
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        (bundle / bundle_module.SIGNATURE_NAME).write_bytes(b"test-signature")
        return temporary, bundle, manifest_path

    def test_accepts_exact_bundle(self) -> None:
        temporary, bundle, _manifest = self.fixture()
        self.addCleanup(temporary.cleanup)
        result = bundle_module.verify_install_bundle(bundle)
        self.assertEqual(result["version"], "0.2.0")
        self.assertEqual(result["uncompressed_size"], 512)

    def test_rejects_payload_hash_mismatch(self) -> None:
        temporary, bundle, _manifest = self.fixture()
        self.addCleanup(temporary.cleanup)
        (bundle / "echo-os_0.2.0.raw.zst").write_bytes(b"tampered")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "SHA-256 mismatch"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_symlink_payload(self) -> None:
        temporary, bundle, _manifest = self.fixture()
        self.addCleanup(temporary.cleanup)
        payload = bundle / "echo-os_0.2.0.raw.zst"
        target = bundle / "outside"
        target.write_bytes(payload.read_bytes())
        payload.unlink()
        payload.symlink_to(target)
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "non-symlink"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_extra_artifact(self) -> None:
        temporary, bundle, _manifest = self.fixture()
        self.addCleanup(temporary.cleanup)
        (bundle / "unexpected.txt").write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "exactly"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_factory_key_hash_mismatch(self) -> None:
        temporary, bundle, _manifest = self.fixture()
        self.addCleanup(temporary.cleanup)
        (bundle / bundle_module.FACTORY_KEY_NAME).write_bytes(b"x" * 32)
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "factory data key"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_symlink_or_terminated_factory_key(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        factory_key = bundle / bundle_module.FACTORY_KEY_NAME
        outside = Path(f"{temporary.name}.outside")
        outside.write_bytes(factory_key.read_bytes())
        self.addCleanup(outside.unlink)
        factory_key.unlink()
        factory_key.symlink_to(outside)
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "non-symlink"):
            bundle_module.verify_install_bundle(bundle)
        factory_key.unlink()
        factory_key.write_bytes(b"f" * 32 + b"\n")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["data_protection"]["factory_key_sha256"] = hashlib.sha256(
            factory_key.read_bytes()
        ).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "terminator"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_partition_order_change(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        labels = manifest["disk"]["partition_labels"]
        labels[-1], labels[-2] = labels[-2], labels[-1]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "label/order"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_direct_or_unsigned_tpm_policy(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["data_protection"]["tpm2_policy"]["direct_pcrs"] = [7]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "signed PCR 11"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_unknown_manifest_key(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["download_url"] = "https://attacker.invalid/image"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "keys must be exactly"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_unpinned_or_credentialed_os_source(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["source"]["commit"] = "main"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "source identity"):
            bundle_module.verify_install_bundle(bundle)

        manifest["source"]["commit"] = "a" * 40
        manifest["source"]["repository"] = "https://user:token@github.com/example/echo-os.git"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "source identity"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_unbounded_uncompressed_size(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["payload"]["uncompressed_size"] = bundle_module.MAX_UNCOMPRESSED_SIZE + 512
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "64 TiB"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_oversized_signature(self) -> None:
        temporary, bundle, _manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        (bundle / bundle_module.SIGNATURE_NAME).write_bytes(
            b"x" * (bundle_module.MAX_SIGNATURE_SIZE + 1)
        )
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "1 MiB"):
            bundle_module.verify_install_bundle(bundle)

    def test_rejects_implausibly_large_compressed_payload(self) -> None:
        temporary, bundle, manifest_path = self.fixture()
        self.addCleanup(temporary.cleanup)
        payload_path = bundle / "echo-os_0.2.0.raw.zst"
        payload_path.write_bytes(b"x" * (bundle_module.COMPRESSED_OVERHEAD_FLOOR + 1024))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["payload"]["sha256"] = hashlib.sha256(payload_path.read_bytes()).hexdigest()
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(bundle_module.InstallBundleError, "inconsistent"):
            bundle_module.verify_install_bundle(bundle)


if __name__ == "__main__":
    unittest.main()
