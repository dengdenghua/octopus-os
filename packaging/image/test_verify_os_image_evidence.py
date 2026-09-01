#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify-os-image-evidence.py")
SPEC = importlib.util.spec_from_file_location("verify_os_image_evidence", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ImageEvidenceTests(unittest.TestCase):
    version = "0.2.0"
    source = "a" * 40
    os_commit = "d" * 40
    os_tree = "e" * 40
    os_repository = "https://github.com/example/echo-os.git"
    image_source = "b" * 64
    pcr_public = b"test-pcr11-public-key"

    def os_source_manifest_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "schema": 1,
                    "kind": "echo-os-source-identity",
                    "repository": self.os_repository,
                    "commit": self.os_commit,
                    "tree": self.os_tree,
                    "commit_time": "2024-01-01T00:00:00+00:00",
                    "source_date_epoch": 1704067200,
                    "dirty": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def install_manifest_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "schema": 3,
                    "product": "echo-os",
                    "architecture": "x86-64",
                    "version": self.version,
                    "source": {
                        "repository": self.os_repository,
                        "commit": self.os_commit,
                        "tree": self.os_tree,
                        "manifest_sha256": hashlib.sha256(
                            self.os_source_manifest_bytes()
                        ).hexdigest(),
                    },
                    "payload": {
                        "filename": f"echo-os_{self.version}.raw.zst",
                        "compression": "zstd",
                        "sha256": "c" * 64,
                        "uncompressed_sha256": self.image_source,
                        "uncompressed_size": 512,
                    },
                    "disk": {
                        "partition_table": "gpt",
                        "partition_labels": [
                            "echo-esp",
                            f"echo-root-{self.version}",
                            f"echo-root-{self.version}-verity",
                            f"echo-root-{self.version}-verity-sig",
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
                        "factory_key_filename": "FACTORY-DATA-KEY",
                        "factory_key_sha256": "e" * 64,
                        "encrypted_partitions": ["echo-var", "echo-swap", "echo-home"],
                        "tpm2_policy": {
                            "direct_pcrs": [],
                            "signed_pcrs": [11],
                            "public_key_sha256": hashlib.sha256(self.pcr_public).hexdigest(),
                        },
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

    def install_manifest_sha256(self) -> str:
        return hashlib.sha256(self.install_manifest_bytes()).hexdigest()

    def materialize(self, root: Path) -> None:
        for relative_name, text in MODULE.fixture_logs(
            self.version,
            self.os_commit,
            self.source,
            self.image_source,
            self.install_manifest_sha256(),
        ).items():
            path = root / relative_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")

    def materialize_release(self, root: Path) -> tuple[Path, Path, Path]:
        bundle = root / "install-bundle"
        bundle.mkdir()
        manifest = bundle / "INSTALL-MANIFEST.json"
        signature = bundle / "INSTALL-MANIFEST.json.gpg"
        manifest.write_bytes(self.install_manifest_bytes())
        signature.write_bytes(b"test-detached-signature")
        installed = root / "echo-os-installed.raw"
        installed.write_bytes(b"\0" * 1024)
        return manifest, signature, installed

    def materialize_os_source(self, root: Path) -> Path:
        manifest = root / "echo-os-source-identity.json"
        manifest.write_bytes(self.os_source_manifest_bytes())
        return manifest

    def materialize_trust(self, root: Path) -> tuple[Path, Path, Path]:
        keyring = root / "echo-install-keyring.gpg"
        certificate = root / "echo-secure-boot.crt"
        pcr_public = root / "pcr-policy-public.pem"
        keyring.write_bytes(b"test-public-openpgp-keyring")
        certificate.write_bytes(b"test-public-x509-certificate")
        pcr_public.write_bytes(self.pcr_public)
        return keyring, certificate, pcr_public

    def test_complete_evidence_is_hashed_without_log_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            checks = MODULE.verify_logs(
                root,
                self.version,
                self.os_commit,
                self.source,
                self.image_source,
                self.install_manifest_sha256(),
            )
            self.assertEqual(len(checks), 15)
            self.assertEqual(
                set(checks),
                set(
                    MODULE.requirements(
                        self.version,
                        self.os_commit,
                        self.source,
                        self.image_source,
                        self.install_manifest_sha256(),
                    )
                ),
            )
            serialized = json.dumps(checks)
            self.assertNotIn("ECHO_AGENT_READY", serialized)
            for evidence in checks.values():
                self.assertRegex(str(evidence["sha256"]), r"^[0-9a-f]{64}$")

    def test_missing_or_duplicate_completion_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            backup = root / "echo-user-backup-boot" / "echo-restore-transaction.log"
            marker = backup.read_text(encoding="utf-8")
            backup.write_text(marker + marker, encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_missing_runner_preflight_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            preflight = root / "echo-image-runner-preflight.log"
            preflight.write_text("runner unavailable\n", encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_cold_boot_from_another_os_commit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            direct = root / "echo-os-boot" / "echo-os-boot.log"
            direct.write_text(
                direct.read_text(encoding="utf-8").replace(
                    f"os={self.os_commit}", f"os={'f' * 40}"
                ),
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_direct_desktop_without_functional_core_app_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            direct = root / "echo-os-boot" / "echo-os-boot.log"
            text = direct.read_text(encoding="utf-8")
            direct.write_text(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if not line.startswith("ECHO_CORE_APPS_SESSION_READY ")
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_direct_desktop_without_native_app_ipc_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            direct = root / "echo-os-boot" / "echo-os-boot.log"
            text = direct.read_text(encoding="utf-8")
            direct.write_text(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if not line.startswith("ECHO_NATIVE_APP_IPC_READY ")
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_wayland_login_without_native_app_ipc_marker_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            wayland = root / "echo-wayland-login-boot" / "echo-os-boot.log"
            text = wayland.read_text(encoding="utf-8")
            wayland.write_text(
                "\n".join(
                    line
                    for line in text.splitlines()
                    if not line.startswith("ECHO_NATIVE_APP_IPC_READY ")
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_restore_flow_with_mismatched_transaction_identity_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            backup = root / "echo-user-backup-boot" / "echo-restore-transaction.log"
            text = backup.read_text(encoding="utf-8")
            text = text.replace(
                "ECHO_RESTORE_COMMITTED transaction=111111111111111111111111",
                "ECHO_RESTORE_COMMITTED transaction=222222222222222222222222",
            )
            backup.write_text(text, encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_trial_boot_from_another_restore_transaction_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            trial = root / "echo-user-backup-boot" / "restore-trial-boot" / "echo-os-boot.log"
            text = trial.read_text(encoding="utf-8").replace(
                "transaction=111111111111111111111111",
                "transaction=222222222222222222222222",
            )
            trial.write_text(text, encoding="utf-8")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

    def test_symlinked_log_and_dirty_agent_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.materialize(root)
            direct = root / "echo-os-boot" / "echo-os-boot.log"
            target = root / "redirected.log"
            direct.rename(target)
            direct.symlink_to(target)
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.verify_logs(
                    root,
                    self.version,
                    self.os_commit,
                    self.source,
                    self.image_source,
                    self.install_manifest_sha256(),
                )

            manifest = root / "agent.json"
            manifest.write_text(
                json.dumps({"source": {"source_id": self.source, "dirty": True}}),
                encoding="utf-8",
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.load_agent_source(manifest)

    def test_release_identity_binds_manifest_signature_and_installed_raw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest, signature, installed = self.materialize_release(root)
            identity = MODULE.load_install_identity(manifest, signature, self.version)
            self.assertEqual(identity["source_raw_sha256"], self.image_source)
            self.assertEqual(identity["manifest_sha256"], self.install_manifest_sha256())
            disk = MODULE.hash_installed_image(installed)
            self.assertEqual(disk["size"], 1024)
            self.assertRegex(str(disk["sha256"]), r"^[0-9a-f]{64}$")

            manifest.write_bytes(
                self.install_manifest_bytes().replace(b'"version":"0.2.0"', b'"version":"9.9.9"')
            )
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.load_install_identity(manifest, signature, self.version)

    def test_install_bundle_from_another_os_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            os_source = self.materialize_os_source(root)
            source_identity = MODULE.load_os_source_identity(os_source)
            manifest, signature, _installed = self.materialize_release(root)
            install_identity = MODULE.load_install_identity(manifest, signature, self.version)
            self.assertEqual(
                install_identity["os_source_manifest_sha256"],
                source_identity["manifest_sha256"],
            )

            payload = json.loads(os_source.read_text(encoding="utf-8"))
            payload["tree"] = "f" * 40
            os_source.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            changed = MODULE.load_os_source_identity(os_source)
            with self.assertRaisesRegex(MODULE.EvidenceError, "does not match"):
                MODULE.verify_os_source_binding(changed, install_identity)

    def test_symlinked_installed_image_and_empty_signature_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            manifest, signature, installed = self.materialize_release(root)
            target = root / "redirected.raw"
            installed.rename(target)
            installed.symlink_to(target)
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.hash_installed_image(installed)
            signature.write_bytes(b"")
            with self.assertRaises(MODULE.EvidenceError):
                MODULE.load_install_identity(manifest, signature, self.version)

    def test_cli_binds_crlf_logs_to_a_new_content_free_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for relative_name, text in MODULE.fixture_logs(
                self.version,
                self.os_commit,
                self.source,
                self.image_source,
                self.install_manifest_sha256(),
            ).items():
                path = root / relative_name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes((text.replace("\n", "\r\n") + "\r\n").encode("utf-8"))
            agent_manifest = root / "agent.json"
            agent_manifest.write_text(
                json.dumps(
                    {
                        "source": {
                            "source_id": self.source,
                            "dirty": False,
                        }
                    }
                ),
                encoding="utf-8",
            )
            os_source_manifest = self.materialize_os_source(root)
            install_manifest, install_signature, installed_image = self.materialize_release(root)
            install_keyring, secure_boot_certificate, pcr_public = self.materialize_trust(root)
            output = root / "evidence.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = MODULE.main(
                    (
                        "--image-version",
                        self.version,
                        "--os-source-manifest",
                        str(os_source_manifest),
                        "--agent-manifest",
                        str(agent_manifest),
                        "--install-manifest",
                        str(install_manifest),
                        "--install-signature",
                        str(install_signature),
                        "--install-keyring",
                        str(install_keyring),
                        "--secure-boot-certificate",
                        str(secure_boot_certificate),
                        "--pcr-policy-public-key",
                        str(pcr_public),
                        "--installed-image",
                        str(installed_image),
                        "--logs-root",
                        str(root),
                        "--output",
                        str(output),
                    )
                )
            self.assertEqual(result, 0)
            self.assertIn("ECHO_OS_IMAGE_EVIDENCE_OK", stdout.getvalue())
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["image_version"], self.version)
            self.assertEqual(payload["os_source"]["commit"], self.os_commit)
            self.assertEqual(payload["os_source"]["tree"], self.os_tree)
            self.assertEqual(payload["agent_source_id"], self.source)
            self.assertEqual(payload["install_bundle"]["source_raw_sha256"], self.image_source)
            self.assertEqual(payload["installed_image"]["size"], 1024)
            self.assertEqual(
                payload["release_trust"]["pcr_policy_public_key"]["sha256"],
                hashlib.sha256(self.pcr_public).hexdigest(),
            )
            self.assertEqual(len(payload["checks"]), 15)
            self.assertRegex(payload["evidence_id"], r"^[0-9a-f]{64}$")
            self.assertNotIn("ECHO_AGENT_READY", json.dumps(payload))
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                self.assertEqual(
                    MODULE.main(
                        (
                            "--image-version",
                            self.version,
                            "--os-source-manifest",
                            str(os_source_manifest),
                            "--agent-manifest",
                            str(agent_manifest),
                            "--install-manifest",
                            str(install_manifest),
                            "--install-signature",
                            str(install_signature),
                            "--install-keyring",
                            str(install_keyring),
                            "--secure-boot-certificate",
                            str(secure_boot_certificate),
                            "--pcr-policy-public-key",
                            str(pcr_public),
                            "--installed-image",
                            str(installed_image),
                            "--logs-root",
                            str(root),
                            "--output",
                            str(output),
                        )
                    ),
                    1,
                )


if __name__ == "__main__":
    unittest.main()
