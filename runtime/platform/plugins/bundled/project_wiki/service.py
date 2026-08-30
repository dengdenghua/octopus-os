"""Stable, project-independent Wiki storage and manifest contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PLUGIN_ID = "project_wiki"
PLUGIN_VERSION = "1.0.0"
MANIFEST_SCHEMA = "echo.project-wiki.manifest.v1"
GENERATOR_VERSION = "structural-v1"
OUTPUT_DIR_NAME = ".echo-wiki"

_POLICY = {
    "max_files": 5000,
    "max_total_bytes": 50 * 1024 * 1024,
    "max_file_bytes": 1024 * 1024,
    "static_analysis_only": True,
}


def policy_digest() -> str:
    payload = json.dumps(_POLICY, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def project_id(root: Path) -> str:
    canonical = str(root.resolve())
    suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{root.name}-{suffix}"


def contract() -> dict[str, Any]:
    return {
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "manifest_schema": MANIFEST_SCHEMA,
        "generator_version": GENERATOR_VERSION,
        "output_dir": OUTPUT_DIR_NAME,
        "policy": dict(_POLICY),
        "policy_digest": policy_digest(),
        "project_isolation": True,
    }


def manifest_metadata(root: Path) -> dict[str, Any]:
    return {
        "schema": MANIFEST_SCHEMA,
        "plugin_id": PLUGIN_ID,
        "plugin_version": PLUGIN_VERSION,
        "generator_version": GENERATOR_VERSION,
        "policy_digest": policy_digest(),
        "project_id": project_id(root),
    }


def is_current_manifest(root: Path, manifest: dict[str, Any]) -> bool:
    expected = manifest_metadata(root)
    return all(manifest.get(key) == value for key, value in expected.items())


__all__ = [
    "GENERATOR_VERSION",
    "MANIFEST_SCHEMA",
    "OUTPUT_DIR_NAME",
    "PLUGIN_ID",
    "PLUGIN_VERSION",
    "contract",
    "is_current_manifest",
    "manifest_metadata",
    "policy_digest",
    "project_id",
]
