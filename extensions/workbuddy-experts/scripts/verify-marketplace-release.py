#!/usr/bin/env python3
"""Cross-check a Hub catalog against the exact content archive it publishes."""

from __future__ import annotations

import argparse
import json
import tarfile
from pathlib import Path
from typing import Any

_MANIFESTS = {
    "plugin": ("codex", ".codex-plugin/plugin.json"),
    "connector": ("connector", ".echo-connector/manifest.json"),
    "workbench": ("workbench", "app.json"),
}
_MAX_MANIFEST_BYTES = 256 * 1024


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid marketplace catalog: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("items"), list):
        raise ValueError("marketplace catalog items are invalid")
    return value


def _assert_no_private_paths(value: Any, *, location: str = "catalog") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_location = f"{location}.{key}"
            if key == "path" or key.endswith("_path"):
                raise ValueError(f"private filesystem path leaked at {child_location}")
            _assert_no_private_paths(child, location=child_location)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_no_private_paths(child, location=f"{location}[{index}]")


def _member_json(archive: tarfile.TarFile, name: str) -> dict[str, Any]:
    try:
        member = archive.getmember(name)
    except KeyError as exc:
        raise ValueError(f"catalog package is missing from content archive: {name}") from exc
    if not member.isfile() or member.size > _MAX_MANIFEST_BYTES:
        raise ValueError(f"content archive manifest is invalid: {name}")
    handle = archive.extractfile(member)
    if handle is None:
        raise ValueError(f"content archive manifest is unreadable: {name}")
    try:
        value = json.loads(handle.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"content archive manifest is invalid JSON: {name}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"content archive manifest is invalid: {name}")
    return value


def _manifest_requirements(kind: str, manifest: dict[str, Any]) -> dict[str, Any]:
    if kind == "plugin":
        raw = manifest.get("echo")
        return dict(raw) if isinstance(raw, dict) else {}
    return manifest


def verify_release(catalog_path: Path, archive_path: Path) -> dict[str, int]:
    catalog = _load_json(catalog_path)
    _assert_no_private_paths(catalog)
    seen_catalog_ids: set[str] = set()
    seen_packages: dict[str, set[str]] = {value[0]: set() for value in _MANIFESTS.values()}
    counts = {kind: 0 for kind in _MANIFESTS}
    with tarfile.open(archive_path, "r:gz") as archive:
        for raw in catalog["items"]:
            if not isinstance(raw, dict):
                raise ValueError("marketplace catalog item is invalid")
            catalog_id = str(raw.get("id") or "").strip()
            package_id = str(raw.get("plugin") or "").strip()
            kind = str(raw.get("kind") or "").strip()
            if not catalog_id or catalog_id in seen_catalog_ids or not package_id:
                raise ValueError("marketplace catalog identity is missing or duplicated")
            if kind not in _MANIFESTS:
                raise ValueError(f"unsupported marketplace catalog kind: {kind}")
            seen_catalog_ids.add(catalog_id)
            root_kind, relative_manifest = _MANIFESTS[kind]
            if package_id in seen_packages[root_kind]:
                raise ValueError(f"marketplace package is duplicated: {kind}/{package_id}")
            seen_packages[root_kind].add(package_id)
            manifest_name = f"plugins/{root_kind}/{package_id}/{relative_manifest}"
            manifest = _member_json(archive, manifest_name)
            manifest_id = str(
                manifest.get("name") if kind == "plugin" else manifest.get("id")
            ).strip()
            if manifest_id != package_id:
                raise ValueError(f"catalog/package identity mismatch: {kind}/{package_id}")
            if str(manifest.get("version") or "").strip() != str(raw.get("version") or "").strip():
                raise ValueError(f"catalog/package version mismatch: {kind}/{package_id}")
            requirements = _manifest_requirements(kind, manifest)
            for field in (
                "host_api",
                "permissions",
                "auth_modes",
                "dependencies",
                "runtime_dependencies",
            ):
                catalog_value = raw.get(field) or ([] if field != "host_api" else None)
                package_value = requirements.get(field) or ([] if field != "host_api" else None)
                if catalog_value != package_value:
                    raise ValueError(f"catalog/package {field} mismatch: {kind}/{package_id}")
            counts[kind] += 1

        archive_packages: dict[str, set[str]] = {
            "codex": set(),
            "connector": set(),
            "workbench": set(),
        }
        for name in archive.getnames():
            parts = Path(name).parts
            if len(parts) >= 3 and parts[0] == "plugins" and parts[1] in archive_packages:
                archive_packages[parts[1]].add(parts[2])
        for root_kind, package_ids in archive_packages.items():
            if package_ids != seen_packages[root_kind]:
                missing = sorted(package_ids - seen_packages[root_kind])
                extra = sorted(seen_packages[root_kind] - package_ids)
                raise ValueError(
                    f"catalog/archive package set mismatch for {root_kind}: "
                    f"uncatalogued={missing}, missing={extra}"
                )
    return counts


def verify_skill_release(catalog_path: Path, archive_path: Path) -> dict[str, int]:
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid marketplace skill catalog: {catalog_path}") from exc
    rows = catalog.get("skills") if isinstance(catalog, dict) else None
    if not isinstance(rows, list):
        raise ValueError("marketplace skill catalog is invalid")
    _assert_no_private_paths(catalog)
    expected: set[str] = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
        for row in rows:
            name = str(row.get("name") or "").strip() if isinstance(row, dict) else ""
            if not name or name in expected or "/" in name or "\\" in name:
                raise ValueError("marketplace skill identity is missing or duplicated")
            expected.add(name)
            if f"skills/{name}/SKILL.md" not in names:
                raise ValueError(f"catalog skill is missing from content archive: {name}")
        packaged = {
            parts[1]
            for member_name in names
            if len(parts := Path(member_name).parts) >= 3 and parts[0] == "skills"
        }
        if packaged != expected:
            raise ValueError(
                "skill catalog/archive set mismatch: "
                f"uncatalogued={sorted(packaged - expected)}, "
                f"missing={sorted(expected - packaged)}"
            )
    return {"skills": len(expected)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kind", choices=("plugins", "skills"), default="plugins")
    args = parser.parse_args()
    counts = (
        verify_skill_release(args.catalog, args.archive)
        if args.kind == "skills"
        else verify_release(args.catalog, args.archive)
    )
    print(f"verified marketplace catalog/archive closure: {counts}")


if __name__ == "__main__":
    main()
