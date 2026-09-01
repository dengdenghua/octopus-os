#!/usr/bin/env python3
"""Verify an Echo OS dm-verity data/hash/signature set and optional UKI."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import uuid
from pathlib import Path


ROOT_HASH = re.compile(r"^[0-9a-f]{64}$")
FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
ARTIFACT_UUID = re.compile(
    r"\.([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.raw(?:\.zst)?$"
)
# systemd's embedded verity-signature loader rejects partitions larger than
# 4 MiB. Enforce the same bound for raw and decompressed update artifacts.
MAX_SIGNATURE_BYTES = 4 * 1024 * 1024


class VerityError(ValueError):
    """Raised when a verity set is incomplete, mismatched or unauthenticated."""


def run(command: list[str], *, input_data: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            command,
            input=input_data,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise VerityError(f"cannot execute {command[0]}: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise VerityError(f"{command[0]} rejected the verity set: {detail}")
    return result.stdout


def require_data_path(path: Path, label: str) -> os.stat_result:
    if path.is_symlink():
        raise VerityError(f"{label} must not be a symlink")
    try:
        metadata = path.stat()
    except OSError as error:
        raise VerityError(f"cannot inspect {label}: {error}") from error
    if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISBLK(metadata.st_mode)):
        raise VerityError(f"{label} must be a regular file or block device")
    if stat.S_ISREG(metadata.st_mode) and metadata.st_size <= 0:
        raise VerityError(f"{label} is empty")
    return metadata


def read_signature_partition(
    path: Path, *, compressed: bool = False
) -> dict[str, str]:
    require_data_path(path, "verity signature")
    if compressed:
        try:
            process = subprocess.Popen(
                ["zstd", "--decompress", "--stdout", "--", str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as error:
            raise VerityError(f"cannot execute zstd: {error}") from error
        assert process.stdout is not None
        encoded = process.stdout.read(MAX_SIGNATURE_BYTES + 1)
        if len(encoded) > MAX_SIGNATURE_BYTES:
            process.kill()
            process.wait()
            raise VerityError("decompressed verity signature exceeds 4 MiB")
        _remaining, errors = process.communicate()
        if process.returncode != 0:
            detail = errors.decode("utf-8", "replace").strip()
            raise VerityError(f"zstd rejected the verity signature: {detail}")
    else:
        with path.open("rb", buffering=0) as stream:
            encoded = stream.read(MAX_SIGNATURE_BYTES + 1)
    if len(encoded) > MAX_SIGNATURE_BYTES:
        raise VerityError("verity signature partition exceeds 4 MiB")
    json_bytes, separator, padding = encoded.partition(b"\x00")
    if separator and any(padding):
        raise VerityError("verity signature partition has non-zero trailing data")
    encoded = json_bytes
    try:
        value = json.loads(encoded.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise VerityError("verity signature partition is not valid UTF-8 JSON") from error
    if not isinstance(value, dict) or set(value) != {
        "rootHash",
        "certificateFingerprint",
        "signature",
    }:
        raise VerityError("verity signature JSON has an unexpected schema")
    if not all(isinstance(item, str) for item in value.values()):
        raise VerityError("verity signature JSON fields must be strings")
    if not ROOT_HASH.fullmatch(value["rootHash"]):
        raise VerityError("verity root hash must be 64 lowercase hexadecimal characters")
    if not FINGERPRINT.fullmatch(value["certificateFingerprint"]):
        raise VerityError("verity certificate fingerprint is invalid")
    try:
        signature = base64.b64decode(value["signature"], validate=True)
    except (ValueError, binascii.Error) as error:
        raise VerityError("verity PKCS#7 signature is not valid base64") from error
    if not signature:
        raise VerityError("verity PKCS#7 signature is empty")
    value["signature"] = base64.b64encode(signature).decode("ascii")
    return value


def certificate_der(certificate: Path) -> bytes:
    require_data_path(certificate, "verity certificate")
    return run(["openssl", "x509", "-in", str(certificate), "-outform", "DER"])


def partition_uuid(path: Path) -> uuid.UUID:
    metadata = require_data_path(path, "verity partition member")
    if stat.S_ISBLK(metadata.st_mode):
        value = run(["blkid", "-s", "PARTUUID", "-o", "value", str(path)])
        candidate = value.decode("ascii", "strict").strip().lower()
    else:
        match = ARTIFACT_UUID.search(path.name)
        if not match:
            raise VerityError(f"split artifact has no GPT UUID in its name: {path.name}")
        candidate = match.group(1)
    try:
        return uuid.UUID(candidate)
    except ValueError as error:
        raise VerityError(f"invalid GPT partition UUID: {candidate}") from error


def verify_signature(signature: dict[str, str], certificate: Path) -> None:
    expected_der = certificate_der(certificate)
    expected_fingerprint = hashlib.sha256(expected_der).hexdigest()
    if signature["certificateFingerprint"] != expected_fingerprint:
        raise VerityError("verity signature uses the wrong release certificate")

    with tempfile.TemporaryDirectory(prefix="echo-verity-signature.") as directory:
        temporary = Path(directory)
        signature_path = temporary / "signature.p7s"
        content_path = temporary / "roothash"
        signer_path = temporary / "signer.pem"
        signature_path.write_bytes(base64.b64decode(signature["signature"], validate=True))
        content_path.write_text(signature["rootHash"], encoding="ascii")
        run(
            [
                "openssl",
                "smime",
                "-verify",
                "-binary",
                "-inform",
                "DER",
                "-in",
                str(signature_path),
                "-content",
                str(content_path),
                "-noverify",
                "-signer",
                str(signer_path),
                "-out",
                os.devnull,
            ]
        )
        signer_der = run(
            ["openssl", "x509", "-in", str(signer_path), "-outform", "DER"]
        )
    if signer_der != expected_der:
        raise VerityError("PKCS#7 signer is not the Echo OS release certificate")


def inspect_uki_roothash(uki: Path) -> str:
    require_data_path(uki, "UKI")
    ukify = "/usr/lib/systemd/ukify" if Path("/usr/lib/systemd/ukify").is_file() else "ukify"
    inspection = run([ukify, "inspect", str(uki)]).decode("utf-8", "replace")
    hashes = re.findall(r"(?<!\S)roothash=([0-9a-f]{64})(?=\s|$)", inspection)
    if len(hashes) != 1:
        raise VerityError("UKI must contain exactly one 64-character roothash")
    if re.search(r"(?<!\S)root=", inspection):
        raise VerityError("UKI must not select root through a mutable root= argument")
    return hashes[0]


def verify_set(
    root: Path,
    verity: Path,
    signature_path: Path,
    certificate: Path,
    uki: Path | None = None,
    *,
    verify_data: bool = True,
    compressed_signature: bool = False,
) -> str:
    signature = read_signature_partition(
        signature_path, compressed=compressed_signature
    )
    root_hash = signature["rootHash"]
    expected_root_uuid = uuid.UUID(root_hash[:32])
    expected_verity_uuid = uuid.UUID(root_hash[-32:])
    if partition_uuid(root) != expected_root_uuid:
        raise VerityError("root partition UUID is not derived from its signed root hash")
    if partition_uuid(verity) != expected_verity_uuid:
        raise VerityError("verity partition UUID is not derived from its signed root hash")
    verify_signature(signature, certificate)
    if verify_data:
        run(["veritysetup", "verify", str(root), str(verity), root_hash])
    if uki is not None and inspect_uki_roothash(uki) != root_hash:
        raise VerityError("UKI roothash does not match the verified root partition")
    return root_hash


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("verity", type=Path)
    parser.add_argument("signature", type=Path)
    parser.add_argument("certificate", type=Path)
    parser.add_argument("--uki", type=Path)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="verify signed metadata, UUIDs and UKI without reading the full data/hash payloads",
    )
    parser.add_argument(
        "--compressed-signature",
        action="store_true",
        help="decompress the signature partition through bounded zstd output",
    )
    args = parser.parse_args()
    try:
        root_hash = verify_set(
            args.root,
            args.verity,
            args.signature,
            args.certificate,
            args.uki,
            verify_data=not args.metadata_only,
            compressed_signature=args.compressed_signature,
        )
    except (VerityError, OSError, UnicodeError) as error:
        print(f"Echo OS verity set rejected: {error}", file=os.sys.stderr)
        return 1
    print(f"ECHO_VERITY_VERIFIED roothash={root_hash}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
