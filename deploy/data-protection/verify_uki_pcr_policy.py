#!/usr/bin/env python3
"""Verify that extracted UKI PCR policy sections authorize only signed PCR 11."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


MAX_PUBLIC_KEY_SIZE = 64 * 1024
MAX_SIGNATURE_SIZE = 1024 * 1024
MAX_SIGNATURE_ENTRIES = 64
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PolicyError(ValueError):
    """Raised when a UKI PCR policy section violates the release contract."""


def regular_file(path: Path, label: str, maximum: int) -> Path:
    if path.is_symlink() or not path.is_file():
        raise PolicyError(f"{label} must be a regular, non-symlink file")
    size = path.stat().st_size
    if not 1 <= size <= maximum:
        raise PolicyError(f"{label} size is outside the accepted range")
    return path.resolve(strict=True)


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PolicyError(f"PCR signature JSON repeats field {key!r}")
        result[key] = value
    return result


def openssl_public_key(
    path: Path,
    label: str,
    arguments: list[str],
) -> bytes:
    try:
        subprocess.run(
            ["openssl", "rsa", "-pubin", "-in", str(path), "-noout"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        result = subprocess.run(
            ["openssl", *arguments, "-pubin", "-in", str(path), "-outform", "DER"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as error:
        raise PolicyError("openssl is required to validate UKI PCR public keys") from error
    except subprocess.CalledProcessError as error:
        raise PolicyError(f"{label} is not a valid PEM-encoded RSA public key") from error
    if not result.stdout:
        raise PolicyError(f"{label} has an empty DER representation")
    return result.stdout


def rsa_public_key_spki_der(path: Path, label: str) -> bytes:
    return openssl_public_key(path, label, ["pkey"])


def rsa_public_key_fingerprint_der(path: Path, label: str) -> bytes:
    # systemd's pubkey_fingerprint() uses OpenSSL i2d_PublicKey(), which for RSA
    # is the algorithm-specific PKCS#1 RSAPublicKey form, not SubjectPublicKeyInfo.
    return openssl_public_key(path, label, ["rsa", "-RSAPublicKey_out"])


def verify_policy(
    release_public_key: Path,
    embedded_public_key: Path,
    signature_path: Path,
) -> tuple[str, int]:
    release_public_key = regular_file(
        release_public_key, "release PCR public key", MAX_PUBLIC_KEY_SIZE
    )
    embedded_public_key = regular_file(
        embedded_public_key, "embedded PCR public key", MAX_PUBLIC_KEY_SIZE
    )
    signature_path = regular_file(
        signature_path, "embedded PCR signature", MAX_SIGNATURE_SIZE
    )

    release_der = rsa_public_key_spki_der(release_public_key, "release PCR public key")
    embedded_der = rsa_public_key_spki_der(embedded_public_key, "embedded PCR public key")
    if not hashlib.sha256(release_der).digest() == hashlib.sha256(embedded_der).digest():
        raise PolicyError("embedded PCR public key does not match the release PCR identity")
    fingerprint_der = rsa_public_key_fingerprint_der(
        release_public_key, "release PCR public key"
    )
    expected_fingerprint = hashlib.sha256(fingerprint_der).hexdigest()

    try:
        signatures = json.loads(
            signature_path.read_text(encoding="utf-8"),
            object_pairs_hook=unique_object,
        )
    except (json.JSONDecodeError, UnicodeError, OSError) as error:
        raise PolicyError("embedded PCR signature is not valid UTF-8 JSON") from error
    if not isinstance(signatures, dict) or set(signatures) != {"sha256"}:
        raise PolicyError("embedded PCR signature must contain only the SHA-256 bank")
    entries = signatures["sha256"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_SIGNATURE_ENTRIES:
        raise PolicyError("embedded SHA-256 PCR signature bank has an invalid entry count")

    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"pcrs", "pkfp", "pol", "sig"}:
            raise PolicyError("embedded PCR signature entry has unexpected fields")
        if entry["pcrs"] != [11]:
            raise PolicyError("embedded PCR signature is not restricted to PCR 11")
        if entry["pkfp"] != expected_fingerprint:
            raise PolicyError("embedded PCR signature uses an unauthorized signing key")
        if not isinstance(entry["pol"], str) or not HEX_SHA256.fullmatch(entry["pol"]):
            raise PolicyError("embedded PCR policy digest is invalid")
        if not isinstance(entry["sig"], str):
            raise PolicyError("embedded PCR signature value must be base64 text")
        try:
            decoded = base64.b64decode(entry["sig"], validate=True)
        except (binascii.Error, ValueError) as error:
            raise PolicyError("embedded PCR signature value is not valid base64") from error
        if not 256 <= len(decoded) <= 1024:
            raise PolicyError("embedded PCR signature value has an invalid RSA size")
        identity = (entry["pol"], entry["sig"])
        if identity in seen:
            raise PolicyError("embedded PCR signature repeats a policy entry")
        seen.add(identity)

    return expected_fingerprint, len(entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_public_key", type=Path)
    parser.add_argument("embedded_public_key", type=Path)
    parser.add_argument("signature", type=Path)
    args = parser.parse_args()
    try:
        fingerprint, count = verify_policy(
            args.release_public_key,
            args.embedded_public_key,
            args.signature,
        )
    except (PolicyError, OSError) as error:
        print(f"Echo OS UKI PCR policy rejected: {error}", file=sys.stderr)
        return 1
    print(
        "ECHO_UKI_PCR_POLICY_OK "
        f"bank=sha256 pcrs=11 signatures={count} key_sha256={fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
