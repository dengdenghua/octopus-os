#!/usr/bin/env python3
"""Create and verify the deterministic identity embedded in a strict NAS ISO."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

SCHEMA = "echo.release-manifest.v1"
HEX_64 = re.compile(r"[0-9a-f]{64}")
GIT_OBJECT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
CODEX_VERSION = re.compile(r"[0-9A-Za-z.+-]+")
PAYLOAD_PATHS = {
    "source": "echo-source.bundle",
    "web": "echo-web-dist.tar.gz",
    "python": "echo-python-wheelhouse.tar.gz",
    "codex": "echo-codex.tar.gz",
    "systemDeb": "echo-system-debs.tar.gz",
}
TOP_LEVEL_KEYS = {
    "schema",
    "product",
    "profile",
    "offline",
    "source",
    "baseIso",
    "runtime",
    "payloads",
    "systemDebRepository",
}


class ManifestError(ValueError):
    """A release identity or extracted payload failed closed validation."""


def _hex64(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{label} must be a 64-character SHA-256")
    normalized = value.casefold()
    if HEX_64.fullmatch(normalized) is None:
        raise ManifestError(f"{label} must be a 64-character SHA-256")
    return normalized


def _git_object(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{label} must be a Git object id")
    normalized = value.casefold()
    if GIT_OBJECT.fullmatch(normalized) is None:
        raise ManifestError(f"{label} must be a Git object id")
    return normalized


def _regular_file(path: Path, label: str, *, maximum: int | None = None) -> Path:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise ManifestError(f"{label} is not a regular file")
    if maximum is not None and metadata.st_size > maximum:
        raise ManifestError(f"{label} exceeds its size limit")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _exact_mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ManifestError(f"{label} has an unsupported shape")
    return value


def _manifest_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if CODEX_VERSION.fullmatch(args.codex_version) is None:
        raise ManifestError("Codex version is invalid")
    payload_hashes = {
        "source": args.source_bundle_sha256,
        "web": args.web_bundle_sha256,
        "python": args.python_bundle_sha256,
        "codex": args.codex_bundle_sha256,
        "systemDeb": args.system_deb_bundle_sha256,
    }
    return {
        "schema": SCHEMA,
        "product": "echo-os",
        "profile": "nas",
        "offline": True,
        "source": {
            "commit": _git_object(args.source_commit, "source commit"),
            "tree": _git_object(args.source_tree, "source tree"),
        },
        "baseIso": {"sha256": _hex64(args.base_iso_sha256, "base ISO")},
        "runtime": {
            "os": "debian-trixie",
            "architecture": "amd64",
            "python": "cpython-313",
            "codex": f"codex-{args.codex_version}",
        },
        "payloads": {
            name: {
                "path": PAYLOAD_PATHS[name],
                "sha256": _hex64(digest, f"{name} payload"),
            }
            for name, digest in payload_hashes.items()
        },
        "systemDebRepository": {
            "sha256": _hex64(args.system_deb_repo_sha256, "system deb repository")
        },
    }


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        staged = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        staged.chmod(0o644)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def create(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.exists() and output.is_symlink():
        raise ManifestError("manifest output cannot be a symbolic link")
    _atomic_json(output, _manifest_from_args(args))


def _load_manifest(root: Path) -> dict[str, Any]:
    path = _regular_file(root / "release-manifest.json", "release manifest", maximum=64 * 1024)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("release manifest is not valid UTF-8 JSON") from exc
    manifest = _exact_mapping(payload, TOP_LEVEL_KEYS, "release manifest")
    if (
        manifest["schema"] != SCHEMA
        or manifest["product"] != "echo-os"
        or manifest["profile"] != "nas"
        or manifest["offline"] is not True
    ):
        raise ManifestError("release manifest identity is invalid")
    return manifest


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    source = _exact_mapping(manifest["source"], {"commit", "tree"}, "source identity")
    source["commit"] = _git_object(source["commit"], "source commit")
    source["tree"] = _git_object(source["tree"], "source tree")
    base_iso = _exact_mapping(manifest["baseIso"], {"sha256"}, "base ISO identity")
    base_iso["sha256"] = _hex64(base_iso["sha256"], "base ISO")
    runtime = _exact_mapping(
        manifest["runtime"], {"os", "architecture", "python", "codex"}, "runtime identity"
    )
    if runtime["os"] != "debian-trixie" or runtime["architecture"] != "amd64":
        raise ManifestError("release runtime OS or architecture is invalid")
    if runtime["python"] != "cpython-313" or not isinstance(runtime["codex"], str):
        raise ManifestError("release runtime version is invalid")
    if not runtime["codex"].startswith("codex-") or CODEX_VERSION.fullmatch(runtime["codex"][6:]) is None:
        raise ManifestError("release Codex version is invalid")
    payloads = _exact_mapping(manifest["payloads"], set(PAYLOAD_PATHS), "payload identities")
    for name, expected_path in PAYLOAD_PATHS.items():
        entry = _exact_mapping(payloads[name], {"path", "sha256"}, f"{name} payload identity")
        if entry["path"] != expected_path:
            raise ManifestError(f"{name} payload path is invalid")
        entry["sha256"] = _hex64(entry["sha256"], f"{name} payload")
    repository = _exact_mapping(
        manifest["systemDebRepository"], {"sha256"}, "system deb repository identity"
    )
    repository["sha256"] = _hex64(repository["sha256"], "system deb repository")


def _strict_preseed(root: Path) -> None:
    path = _regular_file(root / "preseed.cfg", "release preseed", maximum=1024 * 1024)
    text = path.read_text(encoding="utf-8")
    required = (
        "d-i apt-setup/use_mirror boolean false",
        "d-i clock-setup/ntp boolean false",
        "tasksel tasksel/first multiselect",
        "d-i pkgsel/include string",
    )
    lines = text.splitlines()
    if any(lines.count(line) != 1 for line in required):
        raise ManifestError("release preseed does not enforce the offline package policy")
    if "d-i apt-setup/use_mirror boolean true" in lines:
        raise ManifestError("release preseed enables a network mirror")


def _environment(root: Path) -> dict[str, str]:
    path = _regular_file(root / "echo-env.sh", "firstboot environment", maximum=256 * 1024)
    assignments: dict[str, str] = {}
    pattern = re.compile(r'^([A-Z0-9_]+)="([^"\r\n]*)"$')
    for line in path.read_text(encoding="utf-8").splitlines():
        match = pattern.fullmatch(line)
        if match:
            assignments[match.group(1)] = match.group(2)
    return assignments


def verify_directory(args: argparse.Namespace) -> None:
    root = Path(args.root)
    if root.is_symlink() or not root.is_dir():
        raise ManifestError("extracted release payload root is unsafe")
    manifest = _load_manifest(root)
    _validate_manifest_shape(manifest)
    for name, entry in manifest["payloads"].items():
        path = _regular_file(root / entry["path"], f"{name} payload")
        if _sha256(path) != entry["sha256"]:
            raise ManifestError(f"{name} payload SHA-256 does not match the release manifest")
    _strict_preseed(root)
    environment = _environment(root)
    expected = {
        "ECHO_SOURCE_BUNDLE_SHA256": manifest["payloads"]["source"]["sha256"],
        "ECHO_SOURCE_TREE": manifest["source"]["tree"],
        "ECHO_IMAGE_COMMIT": manifest["source"]["commit"],
        "ECHO_WEB_BUNDLE_SHA256": manifest["payloads"]["web"]["sha256"],
        "ECHO_PYTHON_BUNDLE_SHA256": manifest["payloads"]["python"]["sha256"],
        "ECHO_CODEX_BUNDLE_SHA256": manifest["payloads"]["codex"]["sha256"],
        "ECHO_SYSTEM_DEB_BUNDLE_SHA256": manifest["payloads"]["systemDeb"]["sha256"],
        "ECHO_SYSTEM_DEB_REPO_SHA256": manifest["systemDebRepository"]["sha256"],
        "ECHO_PACKAGED_CODEX_VERSION": manifest["runtime"]["codex"][6:],
        "ECHO_RELEASE_OFFLINE": "1",
        "ECHO_INSTALL_PROFILE": "nas",
        "ECHO_HDMI_SHELL": "off",
    }
    mismatched = [name for name, value in expected.items() if environment.get(name) != value]
    if mismatched:
        raise ManifestError(f"firstboot environment disagrees with release manifest: {mismatched[0]}")
    print(
        f"release-manifest=verified source-tree={manifest['source']['tree']} "
        f"base-iso-sha256={manifest['baseIso']['sha256']}"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subcommands = result.add_subparsers(dest="command", required=True)
    create_parser = subcommands.add_parser("create")
    create_parser.add_argument("--output", required=True)
    create_parser.add_argument("--source-commit", required=True)
    create_parser.add_argument("--source-tree", required=True)
    create_parser.add_argument("--base-iso-sha256", required=True)
    create_parser.add_argument("--source-bundle-sha256", required=True)
    create_parser.add_argument("--web-bundle-sha256", required=True)
    create_parser.add_argument("--python-bundle-sha256", required=True)
    create_parser.add_argument("--codex-bundle-sha256", required=True)
    create_parser.add_argument("--system-deb-bundle-sha256", required=True)
    create_parser.add_argument("--system-deb-repo-sha256", required=True)
    create_parser.add_argument("--codex-version", required=True)
    create_parser.set_defaults(handler=create)
    verify_parser = subcommands.add_parser("verify-directory")
    verify_parser.add_argument("--root", required=True)
    verify_parser.set_defaults(handler=verify_directory)
    return result


def main() -> int:
    try:
        args = parser().parse_args()
        args.handler(args)
    except (ManifestError, OSError, UnicodeError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
