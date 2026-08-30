#!/usr/bin/env python3
"""Validate the authenticated Echo OS A/B update manifest and payload hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

HASH_LINE = re.compile(r"^(?P<digest>[0-9a-fA-F]{64}) [ *](?P<name>[^/\\]+)$")
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ARTIFACT_NAME = re.compile(
    rf"^echo-os_(?P<version>[0-9A-Za-z][0-9A-Za-z.+:~_-]*)\."
    rf"(?:(?P<partition>root|root-verity|root-verity-sig)\."
    rf"(?P<uuid>{UUID})\.raw\.zst|(?P<uki>efi))$"
)
SOURCE_IDENTITY_NAME = "OS-SOURCE-IDENTITY.json"
REQUIRED_KINDS = {"root", "root-verity", "root-verity-sig", "efi", "source"}
GIT_ID = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REPOSITORY = re.compile(
    r"^(?:https://[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"ssh://(?:[0-9A-Za-z._-]+@)?[0-9A-Za-z._-]+(?::[0-9]+)?/[^\s?#]+|"
    r"git@[0-9A-Za-z._-]+:[^\s?#]+)$"
)
MAX_MANIFEST_SIZE = 64 * 1024
MAX_SIGNATURE_SIZE = 1024 * 1024
MAX_ROOT_PAYLOAD_SIZE = 16 * 1024**3
MAX_VERITY_PAYLOAD_SIZE = 1024 * 1024**2
MAX_VERITY_SIGNATURE_SIZE = 8 * 1024**2
MAX_UKI_SIZE = 256 * 1024**2
MAX_SOURCE_IDENTITY_SIZE = 16 * 1024
PAYLOAD_LIMITS = {
    "root": MAX_ROOT_PAYLOAD_SIZE,
    "root-verity": MAX_VERITY_PAYLOAD_SIZE,
    "root-verity-sig": MAX_VERITY_SIGNATURE_SIZE,
    "efi": MAX_UKI_SIZE,
    "source": MAX_SOURCE_IDENTITY_SIZE,
}


class BundleError(ValueError):
    """Raised when a bundle violates the fail-closed update contract."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def regular_size(path: Path, label: str, maximum: int) -> int:
    if not path.is_file() or path.is_symlink():
        raise BundleError(f"{label} must be a regular, non-symlink file")
    size = path.stat().st_size
    if not 1 <= size <= maximum:
        raise BundleError(f"{label} size is outside the accepted range")
    return size


def load_source_identity(path: Path) -> dict[str, object]:
    regular_size(path, "OS source identity", MAX_SOURCE_IDENTITY_SIZE)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise BundleError("OS source identity is malformed") from error
    if not isinstance(payload, dict) or set(payload) != {
        "schema",
        "kind",
        "repository",
        "commit",
        "tree",
        "commit_time",
        "source_date_epoch",
        "dirty",
    }:
        raise BundleError("OS source identity top-level contract is invalid")
    schema = payload.get("schema")
    repository = payload.get("repository")
    commit = payload.get("commit")
    tree = payload.get("tree")
    commit_time = payload.get("commit_time")
    source_date_epoch = payload.get("source_date_epoch")
    if (
        not isinstance(schema, int)
        or isinstance(schema, bool)
        or schema != 1
        or payload.get("kind") != "echo-os-source-identity"
        or not isinstance(repository, str)
        or REPOSITORY.fullmatch(repository) is None
        or not isinstance(commit, str)
        or GIT_ID.fullmatch(commit) is None
        or not isinstance(tree, str)
        or GIT_ID.fullmatch(tree) is None
        or not isinstance(commit_time, str)
        or not 1 <= len(commit_time) <= 64
        or not isinstance(source_date_epoch, int)
        or isinstance(source_date_epoch, bool)
        or not 1 <= source_date_epoch < 2**63
        or payload.get("dirty") is not False
    ):
        raise BundleError("OS source identity fields are invalid")
    try:
        parsed_time = datetime.fromisoformat(commit_time)
    except ValueError as error:
        raise BundleError("OS source commit time is invalid") from error
    if parsed_time.tzinfo is None or int(parsed_time.timestamp()) != source_date_epoch:
        raise BundleError("OS source commit time and epoch do not agree")
    return {
        "repository": repository,
        "commit": commit,
        "tree": tree,
        "manifest_sha256": hashlib.sha256(raw).hexdigest(),
    }


def parse_manifest(raw: bytes) -> tuple[dict[str, tuple[str, str]], str]:
    if not 1 <= len(raw) <= MAX_MANIFEST_SIZE:
        raise BundleError("SHA256SUMS size is outside the accepted range")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise BundleError("SHA256SUMS is not UTF-8") from error
    entries: dict[str, tuple[str, str]] = {}
    versions: set[str] = set()
    for line_number, raw_line in enumerate(lines, 1):
        match = HASH_LINE.fullmatch(raw_line)
        if not match:
            raise BundleError(f"invalid SHA256SUMS line {line_number}")

        name = match.group("name")
        if name == SOURCE_IDENTITY_NAME:
            kind = "source"
            artifact = None
        else:
            artifact = ARTIFACT_NAME.fullmatch(name)
            if not artifact:
                raise BundleError(f"unexpected update artifact: {name}")
            kind = artifact.group("partition") or artifact.group("uki")
        if kind in entries:
            raise BundleError(f"duplicate {kind} artifact")
        entries[kind] = (name, match.group("digest").lower())
        if artifact is not None:
            versions.add(artifact.group("version"))

    if set(entries) != REQUIRED_KINDS:
        raise BundleError(
            "bundle must contain exactly root, root-verity, root-verity-sig, efi and source "
            f"artifacts, found {sorted(entries)}"
        )
    if len(versions) != 1:
        raise BundleError("root and UKI versions do not match")
    return entries, versions.pop()


def verify_bundle_identity(bundle: Path, *, hash_payloads: bool = True) -> dict[str, object]:
    if bundle.is_symlink():
        raise BundleError("bundle directory must not be a symlink")
    bundle = bundle.resolve(strict=True)
    if not bundle.is_dir():
        raise BundleError(f"bundle is not a directory: {bundle}")

    manifest = bundle / "SHA256SUMS"
    signature = bundle / "SHA256SUMS.gpg"
    regular_size(manifest, "SHA256SUMS", MAX_MANIFEST_SIZE)
    regular_size(signature, "SHA256SUMS.gpg", MAX_SIGNATURE_SIZE)

    entries, version = parse_manifest(manifest.read_bytes())

    expected_names = {
        "SHA256SUMS",
        "SHA256SUMS.gpg",
        *(name for name, _digest in entries.values()),
    }
    actual_names = {path.name for path in bundle.iterdir()}
    if actual_names != expected_names:
        raise BundleError(
            "bundle directory contains missing or unsigned extra files: "
            f"expected {sorted(expected_names)}, found {sorted(actual_names)}"
        )

    for kind, (name, expected_digest) in sorted(entries.items()):
        path = bundle / name
        maximum = PAYLOAD_LIMITS[kind]
        regular_size(path, f"{kind} payload", maximum)
        if hash_payloads:
            actual_digest = sha256(path)
            if actual_digest != expected_digest:
                raise BundleError(f"SHA-256 mismatch for {name}")

    source = load_source_identity(bundle / SOURCE_IDENTITY_NAME)
    return {"version": version, "source": source}


def verify_bundle(bundle: Path, *, hash_payloads: bool = True) -> str:
    return str(verify_bundle_identity(bundle, hash_payloads=hash_payloads)["version"])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="validate bounded structure and filenames without hashing payloads",
    )
    parser.add_argument(
        "--machine",
        action="store_true",
        help="print version and OS source identity as one tab-separated record",
    )
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        identity = verify_bundle_identity(args.bundle, hash_payloads=not args.preflight)
    except (BundleError, OSError, UnicodeError) as error:
        print(f"Echo OS update bundle rejected: {error}", file=sys.stderr)
        return 1
    source = identity["source"]
    if args.machine:
        print(
            identity["version"],
            source["commit"],
            source["tree"],
            source["manifest_sha256"],
            sep="\t",
        )
    else:
        print(identity["version"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
