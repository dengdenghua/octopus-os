#!/usr/bin/env python3
"""Validate an authenticated Echo OS whole-disk installer bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

MANIFEST_NAME = "INSTALL-MANIFEST.json"
SIGNATURE_NAME = "INSTALL-MANIFEST.json.gpg"
FACTORY_KEY_NAME = "FACTORY-DATA-KEY"
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]*$")
PAYLOAD_NAME = re.compile(r"^echo-os_(?P<version>[0-9A-Za-z][0-9A-Za-z.+:~_-]*)\.raw\.zst$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_ID = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(
    r"^(?:https://[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"ssh://(?:[0-9A-Za-z._-]+@)?[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"git@[0-9A-Za-z._-]+:[^\s?#]+)$"
)
EXPECTED_LABELS = [
    "echo-esp",
    "echo-root-{version}",
    "echo-root-{version}-verity",
    "echo-root-{version}-verity-sig",
    "_empty",
    "_empty",
    "_empty",
    "echo-var",
    "echo-swap",
    "echo-home",
]
MAX_UNCOMPRESSED_SIZE = 64 * 1024**4
MAX_MANIFEST_SIZE = 64 * 1024
MAX_SIGNATURE_SIZE = 1024 * 1024
COMPRESSED_OVERHEAD_FLOOR = 16 * 1024 * 1024
FACTORY_KEY_MIN_BYTES = 32
FACTORY_KEY_MAX_BYTES = 256


class InstallBundleError(ValueError):
    """Raised when an install bundle violates its strict contract."""


def exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    if set(value) != expected:
        raise InstallBundleError(
            f"{context} keys must be exactly {sorted(expected)}, found {sorted(value)}"
        )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_install_bundle(bundle_input: Path) -> dict[str, Any]:
    if bundle_input.is_symlink():
        raise InstallBundleError("bundle directory must not be a symlink")
    bundle = bundle_input.resolve(strict=True)
    if not bundle.is_dir():
        raise InstallBundleError(f"bundle is not a directory: {bundle}")

    manifest_path = bundle / MANIFEST_NAME
    signature_path = bundle / SIGNATURE_NAME
    for path, description in (
        (manifest_path, "manifest"),
        (signature_path, "manifest signature"),
    ):
        if not path.is_file() or path.is_symlink():
            raise InstallBundleError(f"{description} must be a regular, non-symlink file")
    manifest_size = manifest_path.stat().st_size
    if manifest_size <= 0 or manifest_size > MAX_MANIFEST_SIZE:
        raise InstallBundleError("install manifest must be 1 byte to 64 KiB")
    signature_size = signature_path.stat().st_size
    if signature_size <= 0 or signature_size > MAX_SIGNATURE_SIZE:
        raise InstallBundleError("install manifest signature must be 1 byte to 1 MiB")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise InstallBundleError(f"invalid install manifest JSON: {error}") from error
    if not isinstance(manifest, dict):
        raise InstallBundleError("install manifest must be a JSON object")
    exact_keys(
        manifest,
        {
            "schema",
            "product",
            "architecture",
            "version",
            "source",
            "payload",
            "disk",
            "data_protection",
        },
        "manifest",
    )
    if manifest["schema"] != 3 or manifest["product"] != "echo-os":
        raise InstallBundleError("unsupported install manifest schema/product")
    if manifest["architecture"] != "x86-64":
        raise InstallBundleError("first-release installer accepts x86-64 images only")

    version = manifest["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise InstallBundleError("invalid image version")

    source = manifest["source"]
    if not isinstance(source, dict):
        raise InstallBundleError("source must be an object")
    exact_keys(
        source,
        {"repository", "commit", "tree", "manifest_sha256"},
        "source",
    )
    if (
        not isinstance(source["repository"], str)
        or REPOSITORY.fullmatch(source["repository"]) is None
        or not isinstance(source["commit"], str)
        or GIT_ID.fullmatch(source["commit"]) is None
        or not isinstance(source["tree"], str)
        or GIT_ID.fullmatch(source["tree"]) is None
        or not isinstance(source["manifest_sha256"], str)
        or SHA256.fullmatch(source["manifest_sha256"]) is None
    ):
        raise InstallBundleError("installer OS source identity is invalid")

    payload = manifest["payload"]
    if not isinstance(payload, dict):
        raise InstallBundleError("payload must be an object")
    exact_keys(
        payload,
        {
            "filename",
            "compression",
            "sha256",
            "uncompressed_sha256",
            "uncompressed_size",
        },
        "payload",
    )
    filename = payload["filename"]
    payload_match = PAYLOAD_NAME.fullmatch(filename) if isinstance(filename, str) else None
    if not payload_match or payload_match.group("version") != version:
        raise InstallBundleError("payload filename/version mismatch")
    if payload["compression"] != "zstd":
        raise InstallBundleError("installer payload compression must be zstd")
    for digest_key in ("sha256", "uncompressed_sha256"):
        digest = payload[digest_key]
        if not isinstance(digest, str) or not SHA256.fullmatch(digest):
            raise InstallBundleError(f"invalid {digest_key}")
    uncompressed_size = payload["uncompressed_size"]
    if (
        not isinstance(uncompressed_size, int)
        or isinstance(uncompressed_size, bool)
        or uncompressed_size <= 0
        or uncompressed_size % 512 != 0
        or uncompressed_size > MAX_UNCOMPRESSED_SIZE
    ):
        raise InstallBundleError(
            "uncompressed_size must be a positive 512-byte multiple no larger than 64 TiB"
        )

    disk = manifest["disk"]
    if not isinstance(disk, dict):
        raise InstallBundleError("disk must be an object")
    exact_keys(disk, {"partition_table", "partition_labels"}, "disk")
    if disk["partition_table"] != "gpt":
        raise InstallBundleError("installer payload must use GPT")
    expected_labels = [item.format(version=version) for item in EXPECTED_LABELS]
    if disk["partition_labels"] != expected_labels:
        raise InstallBundleError(
            f"unexpected partition label/order contract: {disk['partition_labels']!r}"
        )

    data_protection = manifest["data_protection"]
    if not isinstance(data_protection, dict):
        raise InstallBundleError("data_protection must be an object")
    exact_keys(
        data_protection,
        {
            "scheme",
            "factory_key_filename",
            "factory_key_sha256",
            "encrypted_partitions",
            "tpm2_policy",
        },
        "data_protection",
    )
    if data_protection["scheme"] != "luks2-factory-key":
        raise InstallBundleError("unsupported installer data-protection scheme")
    if data_protection["factory_key_filename"] != FACTORY_KEY_NAME:
        raise InstallBundleError("unexpected factory key filename")
    factory_key_digest = data_protection["factory_key_sha256"]
    if not isinstance(factory_key_digest, str) or not SHA256.fullmatch(factory_key_digest):
        raise InstallBundleError("invalid factory_key_sha256")
    if data_protection["encrypted_partitions"] != [
        "echo-var",
        "echo-swap",
        "echo-home",
    ]:
        raise InstallBundleError("unexpected encrypted partition contract")
    tpm2_policy = data_protection["tpm2_policy"]
    if not isinstance(tpm2_policy, dict):
        raise InstallBundleError("tpm2_policy must be an object")
    exact_keys(
        tpm2_policy,
        {"direct_pcrs", "signed_pcrs", "public_key_sha256"},
        "tpm2_policy",
    )
    if tpm2_policy["direct_pcrs"] != [] or tpm2_policy["signed_pcrs"] != [11]:
        raise InstallBundleError("installer TPM2 policy must use only vendor-signed PCR 11")
    policy_key_digest = tpm2_policy["public_key_sha256"]
    if not isinstance(policy_key_digest, str) or not SHA256.fullmatch(policy_key_digest):
        raise InstallBundleError("invalid TPM2 PCR policy public-key SHA-256")

    factory_key_path = bundle / FACTORY_KEY_NAME
    if not factory_key_path.is_file() or factory_key_path.is_symlink():
        raise InstallBundleError("factory key must be a regular, non-symlink file")
    factory_key_size = factory_key_path.stat().st_size
    if not FACTORY_KEY_MIN_BYTES <= factory_key_size <= FACTORY_KEY_MAX_BYTES:
        raise InstallBundleError("factory key size is outside the accepted range")
    factory_key = factory_key_path.read_bytes()
    if b"\x00" in factory_key or b"\n" in factory_key or b"\r" in factory_key:
        raise InstallBundleError("factory key must contain one literal key without a terminator")
    if sha256(factory_key_path) != factory_key_digest:
        raise InstallBundleError("SHA-256 mismatch for factory data key")

    payload_path = bundle / filename
    if not payload_path.is_file() or payload_path.is_symlink():
        raise InstallBundleError("compressed payload must be a regular, non-symlink file")
    compressed_size = payload_path.stat().st_size
    compressed_size_limit = uncompressed_size + uncompressed_size // 100 + COMPRESSED_OVERHEAD_FLOOR
    if compressed_size <= 0 or compressed_size > compressed_size_limit:
        raise InstallBundleError(
            "compressed payload size is inconsistent with the declared raw image"
        )
    actual_names = {item.name for item in bundle.iterdir()}
    expected_names = {MANIFEST_NAME, SIGNATURE_NAME, FACTORY_KEY_NAME, filename}
    if actual_names != expected_names:
        raise InstallBundleError(
            f"bundle must contain exactly {sorted(expected_names)}, found {sorted(actual_names)}"
        )
    if sha256(payload_path) != payload["sha256"]:
        raise InstallBundleError(f"SHA-256 mismatch for {filename}")

    return {
        "version": version,
        "filename": filename,
        "uncompressed_size": uncompressed_size,
        "uncompressed_sha256": payload["uncompressed_sha256"],
        "factory_key_filename": FACTORY_KEY_NAME,
        "factory_key_sha256": factory_key_digest,
        "tpm2_policy_public_key_sha256": policy_key_digest,
        "os_source_repository": source["repository"],
        "os_source_commit": source["commit"],
        "os_source_tree": source["tree"],
        "os_source_manifest_sha256": source["manifest_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument(
        "--machine",
        action="store_true",
        help="print a validated tab-separated record for the installer",
    )
    args = parser.parse_args()
    try:
        result = verify_install_bundle(args.bundle)
    except (InstallBundleError, OSError) as error:
        print(f"Echo OS install bundle rejected: {error}", file=sys.stderr)
        return 1
    if args.machine:
        print(
            result["version"],
            result["filename"],
            result["uncompressed_size"],
            result["uncompressed_sha256"],
            result["factory_key_filename"],
            result["factory_key_sha256"],
            result["tpm2_policy_public_key_sha256"],
            sep="\t",
        )
    else:
        print(f"Echo OS {result['version']} install bundle is structurally valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
