#!/usr/bin/env python3
"""Fail closed on Electron updater metadata and its local release assets."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import yaml

MAX_METADATA_BYTES = 1024 * 1024
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
PLATFORM_CONTRACTS = {
    "mac": ("latest-mac.yml", {".dmg", ".zip"}, ".zip"),
    "windows": ("latest.yml", {".exe"}, ".exe"),
    "linux": ("latest-linux.yml", {".AppImage"}, ".AppImage"),
}


class UpdateMetadataError(RuntimeError):
    """The generated update channel cannot safely describe this release."""


def _regular_file(path: Path, label: str) -> Path:
    try:
        candidate = path.resolve(strict=True)
    except OSError as error:
        raise UpdateMetadataError(f"{label} is missing: {path}") from error
    if path.is_symlink() or not candidate.is_file():
        raise UpdateMetadataError(f"{label} must be a non-symlink regular file: {path}")
    return candidate


def _sha512(path: Path) -> bytes:
    digest = hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _digest(value: object, label: str) -> bytes:
    if not isinstance(value, str):
        raise UpdateMetadataError(f"{label} must be a base64 SHA-512 digest")
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError as error:
        raise UpdateMetadataError(f"{label} is not valid base64") from error
    if len(decoded) != hashlib.sha512().digest_size:
        raise UpdateMetadataError(f"{label} is not a SHA-512 digest")
    return decoded


def verify_metadata(
    metadata_path: Path,
    asset_root: Path,
    *,
    platform: str,
    expected_version: str,
    expected_revision: str,
) -> dict[str, object]:
    try:
        expected_name, expected_extensions, primary_extension = PLATFORM_CONTRACTS[platform]
    except KeyError as error:
        raise UpdateMetadataError(f"unsupported platform: {platform}") from error
    if metadata_path.name != expected_name:
        raise UpdateMetadataError(f"{platform} metadata must be named {expected_name}")
    metadata = _regular_file(metadata_path, "update metadata")
    if metadata.stat().st_size > MAX_METADATA_BYTES:
        raise UpdateMetadataError("update metadata is too large")
    root = asset_root.resolve(strict=True)
    if asset_root.is_symlink() or not root.is_dir():
        raise UpdateMetadataError("asset root must be a non-symlink directory")
    try:
        value = yaml.safe_load(metadata.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise UpdateMetadataError(f"cannot parse update metadata: {error}") from error
    if not isinstance(value, dict):
        raise UpdateMetadataError("update metadata must be an object")
    if value.get("version") != expected_version:
        raise UpdateMetadataError("update metadata version differs from the release")
    release_date = value.get("releaseDate")
    if not isinstance(release_date, str):
        raise UpdateMetadataError("update metadata has no releaseDate")
    try:
        datetime.fromisoformat(release_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise UpdateMetadataError("update metadata releaseDate is invalid") from error

    files = value.get("files")
    if not isinstance(files, list) or not files:
        raise UpdateMetadataError("update metadata has no files")
    verified: dict[str, dict[str, object]] = {}
    extensions: set[str] = set()
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise UpdateMetadataError(f"files[{index}] must be an object")
        name = entry.get("url")
        if not isinstance(name, str) or not SAFE_NAME.fullmatch(name):
            raise UpdateMetadataError(f"files[{index}].url must be one safe basename")
        if expected_revision not in name:
            raise UpdateMetadataError(f"update asset is not bound to revision {expected_revision}: {name}")
        extension = next((suffix for suffix in expected_extensions if name.endswith(suffix)), None)
        if extension is None:
            raise UpdateMetadataError(f"unexpected {platform} update asset: {name}")
        if extension in extensions or name in verified:
            raise UpdateMetadataError(f"duplicate {platform} update asset: {name}")
        asset = _regular_file(root / name, f"update asset {name}")
        if asset.parent != root:
            raise UpdateMetadataError(f"update asset escapes its root: {name}")
        expected_size = entry.get("size")
        if not isinstance(expected_size, int) or expected_size <= 0:
            raise UpdateMetadataError(f"update asset size is invalid: {name}")
        if asset.stat().st_size != expected_size:
            raise UpdateMetadataError(f"update asset size mismatch: {name}")
        if _sha512(asset) != _digest(entry.get("sha512"), f"files[{index}].sha512"):
            raise UpdateMetadataError(f"update asset SHA-512 mismatch: {name}")
        blockmap = _regular_file(root / f"{name}.blockmap", f"update blockmap for {name}")
        if blockmap.parent != root or blockmap.stat().st_size <= 0:
            raise UpdateMetadataError(f"update blockmap is invalid: {name}")
        blockmap_size = entry.get("blockMapSize")
        if blockmap_size is not None and blockmap_size != blockmap.stat().st_size:
            raise UpdateMetadataError(f"update blockmap size mismatch: {name}")
        extensions.add(extension)
        verified[name] = {"sha512": entry["sha512"], "size": expected_size}

    if extensions != expected_extensions:
        raise UpdateMetadataError(
            f"{platform} update assets are incomplete: expected {sorted(expected_extensions)}, "
            f"got {sorted(extensions)}"
        )
    primary = value.get("path")
    if not isinstance(primary, str) or not primary.endswith(primary_extension):
        raise UpdateMetadataError(f"{platform} primary update asset must end in {primary_extension}")
    if primary not in verified:
        raise UpdateMetadataError("primary update asset is absent from files")
    if _digest(value.get("sha512"), "top-level sha512") != _digest(
        verified[primary]["sha512"], "primary sha512"
    ):
        raise UpdateMetadataError("primary update SHA-512 differs from files")
    return {
        "schema": "echo.electron_update_metadata.v1",
        "platform": platform,
        "version": expected_version,
        "revision": expected_revision,
        "primary": primary,
        "files": sorted(verified),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--asset-root", type=Path, required=True)
    parser.add_argument("--platform", choices=sorted(PLATFORM_CONTRACTS), required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    result = verify_metadata(
        args.metadata,
        args.asset_root,
        platform=args.platform,
        expected_version=args.expected_version,
        expected_revision=args.expected_revision,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
