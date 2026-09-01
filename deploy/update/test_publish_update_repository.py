#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

MODULE_PATH = Path(__file__).with_name("publish_update_repository.py")
SPEC = importlib.util.spec_from_file_location("publish_update_repository", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublishUpdateRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.repository = self.root / "repository"
        self.repository.mkdir(mode=0o700)
        self.keyring = self.root / "update-keyring.gpg"
        # One bounded new-format primary-public-key packet. The strict parser
        # intentionally validates packet type/shape, while cryptographic
        # validity is exercised by the Linux GPG smoke gate.
        self.keyring.write_bytes(b"\xc6\x01\x04")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def source_identity(commit: str = "a" * 40) -> bytes:
        return (
            json.dumps(
                {
                    "schema": 1,
                    "kind": "echo-os-source-identity",
                    "repository": "https://github.com/example/echo-os.git",
                    "commit": commit,
                    "tree": "b" * 40,
                    "commit_time": "2024-01-01T00:00:00+00:00",
                    "source_date_epoch": 1704067200,
                    "dirty": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    def create_bundle(
        self,
        version: str,
        *,
        commit: str = "a" * 40,
        signature: bytes = b"detached-signature",
    ) -> Path:
        bundle = self.root / f"bundle-{version}-{commit[0]}-{len(list(self.root.glob('bundle-*')))}"
        bundle.mkdir()
        payloads = {
            f"echo-os_{version}.root.11111111-2222-3333-4444-555555555555.raw.zst": b"root-"
            + version.encode(),
            f"echo-os_{version}.root-verity.66666666-7777-8888-9999-aaaaaaaaaaaa.raw.zst": b"verity-"
            + version.encode(),
            f"echo-os_{version}.root-verity-sig.bbbbbbbb-cccc-dddd-eeee-ffffffffffff.raw.zst": b"verity-signature-"
            + version.encode(),
            f"echo-os_{version}.efi": b"uki-" + version.encode(),
            "OS-SOURCE-IDENTITY.json": self.source_identity(commit),
        }
        for name, contents in payloads.items():
            (bundle / name).write_bytes(contents)
        manifest = "".join(
            f"{hashlib.sha256(contents).hexdigest()}  {name}\n"
            for name, contents in payloads.items()
        ).encode()
        (bundle / "SHA256SUMS").write_bytes(manifest)
        (bundle / "SHA256SUMS.gpg").write_bytes(signature)
        return bundle

    @staticmethod
    def accept_signature(_bundle: Path, _keyring: Path) -> None:
        return None

    def publish(self, bundle: Path, sequence: int) -> dict[str, object]:
        return MODULE.publish_repository(
            bundle,
            self.keyring,
            self.repository,
            sequence,
            signature_verifier=self.accept_signature,
        )

    def test_first_release_is_immutable_and_atomically_visible(self) -> None:
        bundle = self.create_bundle("0.2.1")
        result = self.publish(bundle, 1)
        channel = self.repository / "stable" / "x86-64"
        release = Path(str(result["release"]))

        self.assertTrue(channel.is_symlink())
        self.assertEqual(
            os.readlink(channel),
            "../releases/x86-64/00000000000000000001-0.2.1",
        )
        self.assertEqual(channel.resolve(strict=True), release)
        self.assertEqual(
            (channel / "SHA256SUMS").read_bytes(), (bundle / "SHA256SUMS").read_bytes()
        )
        self.assertEqual(oct(release.stat().st_mode & 0o777), "0o555")
        self.assertTrue(all((entry.stat().st_mode & 0o777) == 0o444 for entry in release.iterdir()))
        self.assertFalse(
            any(entry.name.startswith(".incoming-") for entry in release.parent.iterdir())
        )

        verified = MODULE.verify_current_repository(
            self.keyring,
            self.repository,
            signature_verifier=self.accept_signature,
        )
        self.assertEqual(verified["sequence"], 1)
        self.assertEqual(verified["version"], "0.2.1")
        self.assertEqual(verified["source_commit"], "a" * 40)

    def test_exact_same_sequence_and_bytes_are_idempotent(self) -> None:
        bundle = self.create_bundle("0.2.1")
        first = self.publish(bundle, 1)
        before_target = os.readlink(self.repository / "stable" / "x86-64")
        second = self.publish(bundle, 1)
        self.assertEqual(first, second)
        self.assertEqual(os.readlink(self.repository / "stable" / "x86-64"), before_target)

    def test_same_sequence_or_version_cannot_be_replaced(self) -> None:
        original = self.create_bundle("0.2.1")
        self.publish(original, 1)
        channel = self.repository / "stable" / "x86-64"
        original_target = os.readlink(channel)

        changed = self.create_bundle("0.2.1", commit="c" * 40, signature=b"other-signature")
        with self.assertRaisesRegex(MODULE.PublishError, "different bytes"):
            self.publish(changed, 1)
        with self.assertRaisesRegex(MODULE.PublishError, "cannot replace the same version"):
            self.publish(original, 2)
        self.assertEqual(os.readlink(channel), original_target)

    def test_publication_sequence_cannot_skip_or_move_backward(self) -> None:
        first = self.create_bundle("0.2.1")
        second = self.create_bundle("0.2.2", commit="c" * 40)
        self.publish(first, 1)
        with self.assertRaisesRegex(MODULE.PublishError, "exactly one"):
            self.publish(second, 3)
        self.publish(second, 2)
        with self.assertRaisesRegex(MODULE.PublishError, "exactly one"):
            self.publish(first, 1)
        self.assertIn(
            "00000000000000000002-0.2.2", os.readlink(self.repository / "stable" / "x86-64")
        )

    def test_signature_failure_publishes_nothing_and_cleans_staging(self) -> None:
        bundle = self.create_bundle("0.2.1")

        def reject(_bundle: Path, _keyring: Path) -> None:
            raise MODULE.PublishError("signature rejected")

        with self.assertRaisesRegex(MODULE.PublishError, "signature rejected"):
            MODULE.publish_repository(
                bundle,
                self.keyring,
                self.repository,
                1,
                signature_verifier=reject,
            )
        self.assertFalse((self.repository / "stable" / "x86-64").exists())
        release_root = self.repository / "releases" / "x86-64"
        self.assertFalse(
            any(entry.name.startswith(".incoming-") for entry in release_root.iterdir())
        )

    def test_retry_recovers_release_rename_before_channel_switch(self) -> None:
        first = self.create_bundle("0.2.1")
        second = self.create_bundle("0.2.2", commit="c" * 40)
        self.publish(first, 1)
        real_switch = MODULE.atomic_switch_channel

        with (
            mock.patch.object(
                MODULE,
                "atomic_switch_channel",
                side_effect=MODULE.PublishError("simulated power loss before channel switch"),
            ),
            self.assertRaisesRegex(MODULE.PublishError, "simulated power loss"),
        ):
            self.publish(second, 2)

        release = self.repository / "releases" / "x86-64" / "00000000000000000002-0.2.2"
        self.assertTrue(release.is_dir())
        self.assertIn(
            "00000000000000000001-0.2.1", os.readlink(self.repository / "stable" / "x86-64")
        )

        with mock.patch.object(MODULE, "atomic_switch_channel", side_effect=real_switch):
            recovered = self.publish(second, 2)
        self.assertEqual(recovered["sequence"], 2)
        self.assertIn(
            "00000000000000000002-0.2.2", os.readlink(self.repository / "stable" / "x86-64")
        )

    def test_concurrent_publisher_and_unsafe_channel_are_rejected(self) -> None:
        bundle = self.create_bundle("0.2.1")
        releases = self.repository / "releases"
        releases.mkdir(mode=0o755)
        lock_path = releases / ".publish.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(MODULE.PublishError, "already running"):
                self.publish(bundle, 1)
        finally:
            os.close(descriptor)

        self.publish(bundle, 1)
        channel = self.repository / "stable" / "x86-64"
        channel.unlink()
        channel.symlink_to("../../outside")
        with self.assertRaisesRegex(MODULE.PublishError, "unexpected target"):
            self.publish(bundle, 1)

    def test_current_release_tampering_is_detected(self) -> None:
        bundle = self.create_bundle("0.2.1")
        result = self.publish(bundle, 1)
        release = Path(str(result["release"]))
        manifest = release / "SHA256SUMS"
        os.chmod(release, 0o755)
        os.chmod(manifest, 0o644)
        manifest.write_bytes(manifest.read_bytes() + b"tampered\n")
        os.chmod(manifest, 0o444)
        os.chmod(release, 0o555)

        with self.assertRaises(ValueError):
            MODULE.verify_current_repository(
                self.keyring,
                self.repository,
                signature_verifier=self.accept_signature,
            )

    def test_abandoned_private_staging_is_removed_on_retry(self) -> None:
        bundle = self.create_bundle("0.2.1")
        release_root = self.repository / "releases" / "x86-64"
        release_root.mkdir(parents=True)
        abandoned = release_root / ".incoming-abandoned"
        abandoned.mkdir()
        (abandoned / "partial").write_bytes(b"partial")
        stable = self.repository / "stable"
        stable.mkdir()
        stale_link = stable / ".x86-64.incoming-abandoned"
        stale_link.symlink_to("../releases/x86-64/missing")

        self.publish(bundle, 1)
        self.assertFalse(abandoned.exists())
        self.assertFalse(stale_link.exists() or stale_link.is_symlink())


if __name__ == "__main__":
    unittest.main()
