#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("verify_uki_pcr_policy.py")
SPEC = importlib.util.spec_from_file_location("verify_uki_pcr_policy", MODULE_PATH)
assert SPEC and SPEC.loader
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


class UKIPCRPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.private_key = cls.root / "policy.key"
        cls.public_key = cls.root / "policy.pem"
        cls.other_private_key = cls.root / "other.key"
        cls.other_public_key = cls.root / "other.pem"
        for private_key, public_key in (
            (cls.private_key, cls.public_key),
            (cls.other_private_key, cls.other_public_key),
        ):
            subprocess.run(
                [
                    "openssl",
                    "genpkey",
                    "-algorithm",
                    "RSA",
                    "-pkeyopt",
                    "rsa_keygen_bits:2048",
                    "-out",
                    str(private_key),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                [
                    "openssl",
                    "pkey",
                    "-in",
                    str(private_key),
                    "-pubout",
                    "-out",
                    str(public_key),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        cls.fingerprint = policy.hashlib.sha256(
            policy.rsa_public_key_fingerprint_der(cls.public_key, "test key")
        ).hexdigest()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def setUp(self) -> None:
        self.signature = self.root / f"signature-{self.id().rsplit('.', 1)[-1]}.json"

    def payload(self) -> dict[str, list[dict[str, object]]]:
        return {
            "sha256": [
                {
                    "pcrs": [11],
                    "pkfp": self.fingerprint,
                    "pol": "a" * 64,
                    "sig": base64.b64encode(b"s" * 256).decode("ascii"),
                }
            ]
        }

    def write(self, payload: object) -> None:
        self.signature.write_text(json.dumps(payload), encoding="utf-8")

    def test_accepts_release_key_and_signed_pcr11(self) -> None:
        self.write(self.payload())
        fingerprint, count = policy.verify_policy(
            self.public_key, self.public_key, self.signature
        )
        self.assertEqual(fingerprint, self.fingerprint)
        self.assertEqual(count, 1)

    def test_systemd_fingerprint_is_not_the_spki_digest(self) -> None:
        spki_fingerprint = policy.hashlib.sha256(
            policy.rsa_public_key_spki_der(self.public_key, "test key")
        ).hexdigest()
        self.assertNotEqual(self.fingerprint, spki_fingerprint)

    def test_rejects_direct_or_different_pcr(self) -> None:
        payload = self.payload()
        payload["sha256"][0]["pcrs"] = [7, 11]
        self.write(payload)
        with self.assertRaisesRegex(policy.PolicyError, "restricted to PCR 11"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

    def test_rejects_signature_from_another_key(self) -> None:
        payload = self.payload()
        payload["sha256"][0]["pkfp"] = "b" * 64
        self.write(payload)
        with self.assertRaisesRegex(policy.PolicyError, "unauthorized signing key"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

    def test_rejects_different_embedded_public_key(self) -> None:
        self.write(self.payload())
        with self.assertRaisesRegex(policy.PolicyError, "release PCR identity"):
            policy.verify_policy(self.public_key, self.other_public_key, self.signature)

    def test_rejects_malformed_base64(self) -> None:
        payload = self.payload()
        payload["sha256"][0]["sig"] = "not base64!"
        self.write(payload)
        with self.assertRaisesRegex(policy.PolicyError, "valid base64"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

    def test_rejects_extra_bank_or_signature_fields(self) -> None:
        payload = self.payload()
        payload["sha1"] = payload["sha256"]
        self.write(payload)
        with self.assertRaisesRegex(policy.PolicyError, "only the SHA-256 bank"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

        payload = self.payload()
        payload["sha256"][0]["phase"] = "ready"
        self.write(payload)
        with self.assertRaisesRegex(policy.PolicyError, "unexpected fields"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

    def test_rejects_duplicate_json_fields_and_symlinks(self) -> None:
        self.signature.write_text('{"sha256": [], "sha256": []}', encoding="utf-8")
        with self.assertRaisesRegex(policy.PolicyError, "repeats field"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)

        target = self.root / f"target-{self.signature.name}"
        target.write_text(json.dumps(self.payload()), encoding="utf-8")
        self.signature.unlink()
        self.signature.symlink_to(target)
        with self.assertRaisesRegex(policy.PolicyError, "non-symlink"):
            policy.verify_policy(self.public_key, self.public_key, self.signature)


if __name__ == "__main__":
    unittest.main()
