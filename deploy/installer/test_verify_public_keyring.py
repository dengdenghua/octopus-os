#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("verify_public_keyring.py")
MODULE_SPEC = importlib.util.spec_from_file_location("verify_public_keyring", MODULE_PATH)
assert MODULE_SPEC is not None and MODULE_SPEC.loader is not None
keyring_module = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(keyring_module)


def new_packet(tag: int, body: bytes) -> bytes:
    if len(body) >= 192:
        raise ValueError("test helper only emits one-octet lengths")
    return bytes((0xC0 | tag, len(body))) + body


def old_packet(tag: int, body: bytes) -> bytes:
    if tag > 15 or len(body) > 255:
        raise ValueError("test helper only emits old one-octet lengths")
    return bytes((0x80 | (tag << 2), len(body))) + body


class PublicKeyringVerifierTests(unittest.TestCase):
    def test_accepts_public_key_user_id_signature_and_subkey(self) -> None:
        keyring = b"".join(
            (
                new_packet(6, b"public-primary"),
                new_packet(13, b"Echo OS release"),
                new_packet(2, b"self-signature"),
                new_packet(14, b"public-subkey"),
            )
        )
        self.assertEqual(keyring_module.verify_public_keyring_bytes(keyring), 4)

    def test_accepts_old_format_public_key_packet(self) -> None:
        self.assertEqual(keyring_module.verify_public_keyring_bytes(old_packet(6, b"public")), 1)

    def test_rejects_secret_primary_key(self) -> None:
        with self.assertRaisesRegex(keyring_module.PublicKeyringError, "secret-key"):
            keyring_module.verify_public_keyring_bytes(new_packet(5, b"secret"))

    def test_rejects_secret_subkey(self) -> None:
        keyring = new_packet(6, b"public") + new_packet(7, b"secret-subkey")
        with self.assertRaisesRegex(keyring_module.PublicKeyringError, "secret-key"):
            keyring_module.verify_public_keyring_bytes(keyring)

    def test_rejects_compressed_or_opaque_packets(self) -> None:
        keyring = new_packet(6, b"public") + new_packet(8, b"opaque")
        with self.assertRaisesRegex(keyring_module.PublicKeyringError, "tag 8"):
            keyring_module.verify_public_keyring_bytes(keyring)

    def test_rejects_truncated_packet(self) -> None:
        with self.assertRaisesRegex(keyring_module.PublicKeyringError, "truncated"):
            keyring_module.verify_public_keyring_bytes(bytes((0xC6, 5)) + b"xx")

    def test_rejects_keyring_without_primary_public_key(self) -> None:
        with self.assertRaisesRegex(keyring_module.PublicKeyringError, "no primary"):
            keyring_module.verify_public_keyring_bytes(new_packet(13, b"user"))


if __name__ == "__main__":
    unittest.main()
