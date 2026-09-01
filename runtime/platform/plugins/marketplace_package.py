"""Trust verification for mutable marketplace plugin and connector packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet

from runtime.platform.plugins.publisher_provenance import (
    SIGNATURE_RELATIVE_PATH,
    verify_plugin_publisher_provenance,
)

CODEX_SIGNATURE_RELATIVE_PATH = SIGNATURE_RELATIVE_PATH
CONNECTOR_MANIFEST_RELATIVE_PATH = Path(".echo-connector/manifest.json")
CONNECTOR_SIGNATURE_RELATIVE_PATH = Path(".echo-connector/provenance.json")
CONNECTOR_MANIFEST_SCHEMA = "echo.connector_package.v1"
CONNECTOR_RELEASE_SUMMARY = "1.0.0：首次纳入 Echo 受信连接器内容包。"
MARKETPLACE_HOST_API = ">=0.2,<0.3"

MARKETPLACE_PERMISSIONS = frozenset(
    {
        "account.credentials",
        "content.read",
        "content.write",
        "interaction.user",
        "network.remote",
        "process.local",
    }
)
MARKETPLACE_AUTH_MODES = frozenset(
    {"connected-account", "mcp", "oauth", "oneid-token", "server-side", "token"}
)

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MAX_MANIFEST_BYTES = 256 * 1024
_MAX_PROVENANCE_FILES = 10_000
_MAX_PROVENANCE_BYTES = 256 * 1024 * 1024


def _bounded_id_list(value: Any, *, field: str, maximum: int = 64) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"marketplace package {field} is invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"marketplace package {field} is invalid")
        normalized = item.strip()
        if not _SAFE_TOKEN.fullmatch(normalized):
            raise ValueError(f"marketplace package {field} is invalid")
        if normalized not in result:
            result.append(normalized)
    return result


def _bounded_label_list(value: Any, *, field: str, maximum: int = 64) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"marketplace package {field} is invalid")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"marketplace package {field} is invalid")
        normalized = item.strip()
        if (
            not normalized
            or len(normalized) > 160
            or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        ):
            raise ValueError(f"marketplace package {field} is invalid")
        if normalized not in result:
            result.append(normalized)
    return result


def derive_codex_package_requirements(
    manifest: dict[str, Any],
    *,
    package_dir: str | Path,
) -> dict[str, Any]:
    """Derive conservative Echo disclosures from one upstream Codex plugin."""

    interface = manifest.get("interface")
    capabilities = interface.get("capabilities") if isinstance(interface, dict) else []
    capability_names = {
        str(value).strip().lower() for value in capabilities or [] if isinstance(value, str)
    }
    permissions: set[str] = set()
    if "read" in capability_names:
        permissions.add("content.read")
    if "write" in capability_names:
        permissions.add("content.write")
    if "interactive" in capability_names:
        permissions.add("interaction.user")
    if capability_names & {"browser.recorder", "chat.recorder"}:
        permissions.update({"content.read", "content.write", "interaction.user"})

    root = Path(package_dir)
    app_backed = (root / ".app.json").is_file() or bool(manifest.get("apps"))
    mcp_backed = (root / ".mcp.json").is_file() or bool(manifest.get("mcpServers"))
    if app_backed:
        permissions.update({"account.credentials", "network.remote"})
    if mcp_backed:
        permissions.add("process.local")
    return {
        "host_api": MARKETPLACE_HOST_API,
        "permissions": sorted(permissions),
        "dependencies": [],
        "runtime_dependencies": [],
        "auth_modes": ["connected-account"] if app_backed else [],
    }


def derive_connector_package_requirements(
    catalog_item: dict[str, Any],
    *,
    package_dir: str | Path,
) -> dict[str, Any]:
    """Derive signed disclosures from a WorkBuddy connector catalog row."""

    connector_type = str(catalog_item.get("type") or "").strip()
    auth_mode = str(catalog_item.get("auth_mode") or "").strip()
    permissions = {"network.remote"}
    if connector_type in {"cli", "mcp", "skill-only"}:
        permissions.add("process.local")
    if auth_mode and auth_mode != "none":
        permissions.add("account.credentials")
    vendor = Path(package_dir) / "vendor"
    runtime_dependencies = (
        sorted(path.name for path in vendor.iterdir() if path.is_file()) if vendor.is_dir() else []
    )
    return {
        "host_api": MARKETPLACE_HOST_API,
        "permissions": sorted(permissions),
        "dependencies": [],
        "runtime_dependencies": runtime_dependencies,
        "auth_modes": [auth_mode] if auth_mode else [],
    }


def compute_marketplace_content_provenance(
    package_dir: str | Path,
    *,
    signature_relative_path: Path,
) -> dict[str, Any]:
    """Build the bounded digest covered by a marketplace publisher signature."""

    root = Path(package_dir).resolve()
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    issues: list[str] = []
    complete = True
    limit_exceeded = False
    for current, dirs, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in sorted(dirs):
            candidate = current_path / name
            if candidate.is_symlink():
                issues.append(f"symlink directory excluded: {candidate.relative_to(root)}")
                complete = False
                continue
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in sorted(files):
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if relative == signature_relative_path:
                continue
            if candidate.is_symlink():
                issues.append(f"symlink file excluded: {relative}")
                complete = False
                continue
            if file_count >= _MAX_PROVENANCE_FILES:
                issues.append(f"package exceeds {_MAX_PROVENANCE_FILES} provenance files")
                complete = False
                limit_exceeded = True
                break
            try:
                file_stat = candidate.lstat()
            except OSError as exc:
                issues.append(f"unreadable provenance file {relative}: {type(exc).__name__}")
                complete = False
                continue
            if not stat.S_ISREG(file_stat.st_mode):
                issues.append(f"non-regular provenance file excluded: {relative}")
                complete = False
                continue
            size = file_stat.st_size
            if total_bytes + size > _MAX_PROVENANCE_BYTES:
                issues.append(f"package exceeds {_MAX_PROVENANCE_BYTES} provenance bytes")
                complete = False
                limit_exceeded = True
                break
            try:
                digest.update(relative.as_posix().encode("utf-8"))
                digest.update(b"\0")
                normalized_mode = 0o755 if stat.S_IMODE(file_stat.st_mode) & 0o111 else 0o644
                digest.update(f"{normalized_mode:04o}".encode("ascii"))
                digest.update(b"\0")
                with candidate.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                digest.update(b"\0")
            except OSError as exc:
                issues.append(f"unreadable provenance file {relative}: {type(exc).__name__}")
                complete = False
                continue
            file_count += 1
            total_bytes += size
        if limit_exceeded:
            break
    return {
        "schema": "echo.plugin_content_provenance.v1",
        "algorithm": "sha256:path-null-normalized-mode-null-content",
        "digest": digest.hexdigest() if complete else "",
        "complete": complete,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "issues": issues,
    }


def load_marketplace_package_manifest(
    package_dir: str | Path,
    *,
    package_kind: str,
) -> dict[str, Any]:
    """Load the signed identity and version for one mutable package."""

    root = Path(package_dir).resolve()
    if package_kind == "codex":
        relative = Path(".codex-plugin/plugin.json")
        expected_schema = None
    elif package_kind == "connector":
        relative = CONNECTOR_MANIFEST_RELATIVE_PATH
        expected_schema = CONNECTOR_MANIFEST_SCHEMA
    else:
        raise ValueError(f"unsupported marketplace package kind: {package_kind}")
    path = root / relative
    if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_MANIFEST_BYTES:
        raise ValueError(f"{package_kind} package manifest is unavailable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{package_kind} package manifest is invalid") from exc
    if not isinstance(payload, dict) or (
        expected_schema is not None and payload.get("schema") != expected_schema
    ):
        raise ValueError(f"{package_kind} package manifest schema is invalid")
    raw_plugin_id = payload.get("name") if package_kind == "codex" else payload.get("id")
    raw_version = payload.get("version")
    if not isinstance(raw_plugin_id, str) or not isinstance(raw_version, str):
        raise ValueError(f"{package_kind} package identity or version is invalid")
    plugin_id = raw_plugin_id.strip()
    version = raw_version.strip()
    if not _SAFE_ID.fullmatch(plugin_id) or not version or len(version) > 64:
        raise ValueError(f"{package_kind} package identity or version is invalid")
    raw_release_summary = (
        payload.get("releaseNotes")
        or payload.get("release_notes")
        or payload.get("release_summary")
        or ""
    )
    if not isinstance(raw_release_summary, str):
        raise ValueError(f"{package_kind} package release summary is invalid")
    release_summary = raw_release_summary.strip()
    if len(release_summary) > 1_000 or any(
        ord(character) < 32 or ord(character) == 127 for character in release_summary
    ):
        raise ValueError(f"{package_kind} package release summary is invalid")
    result = {"name": plugin_id, "version": version}
    if release_summary:
        result["release_summary"] = release_summary
    requirements = payload.get("echo") if package_kind == "codex" else payload
    if requirements is None:
        requirements = {}
    if not isinstance(requirements, dict):
        raise ValueError(f"{package_kind} package requirements are invalid")
    raw_host_api = requirements.get("host_api")
    if raw_host_api is not None:
        if not isinstance(raw_host_api, str):
            raise ValueError(f"{package_kind} package host_api is invalid")
        host_api = raw_host_api.strip()
        if not host_api or len(host_api) > 160:
            raise ValueError(f"{package_kind} package host_api is invalid")
        try:
            SpecifierSet(host_api)
        except InvalidSpecifier as exc:
            raise ValueError(f"{package_kind} package host_api is invalid") from exc
        result["host_api"] = host_api
    permissions = _bounded_id_list(requirements.get("permissions"), field="permissions")
    if any(permission not in MARKETPLACE_PERMISSIONS for permission in permissions):
        raise ValueError(f"{package_kind} package permissions are invalid")
    auth_modes = _bounded_id_list(requirements.get("auth_modes"), field="auth_modes")
    if any(auth_mode not in MARKETPLACE_AUTH_MODES for auth_mode in auth_modes):
        raise ValueError(f"{package_kind} package auth_modes are invalid")
    dependencies = _bounded_id_list(requirements.get("dependencies"), field="dependencies")
    if any(not _SAFE_ID.fullmatch(dependency) for dependency in dependencies):
        raise ValueError(f"{package_kind} package dependencies are invalid")
    runtime_dependencies = _bounded_label_list(
        requirements.get("runtime_dependencies"), field="runtime_dependencies"
    )
    result.update(
        {
            "permissions": permissions,
            "auth_modes": auth_modes,
            "dependencies": dependencies,
            "runtime_dependencies": runtime_dependencies,
        }
    )
    return result


def verify_marketplace_package_trust(
    package_dir: str | Path,
    *,
    package_kind: str,
    plugin_id: str,
    expected_version: str | None = None,
    trust_store_path: str | Path | None = None,
    require_trusted: bool = False,
) -> dict[str, Any]:
    """Verify identity, content digest and optional publisher signature."""

    if not _SAFE_ID.fullmatch(plugin_id):
        raise ValueError(f"invalid marketplace plugin id: {plugin_id!r}")
    signature_path = (
        CODEX_SIGNATURE_RELATIVE_PATH
        if package_kind == "codex"
        else CONNECTOR_SIGNATURE_RELATIVE_PATH
        if package_kind == "connector"
        else None
    )
    if signature_path is None:
        raise ValueError(f"unsupported marketplace package kind: {package_kind}")
    manifest = load_marketplace_package_manifest(package_dir, package_kind=package_kind)
    if manifest["name"] != plugin_id:
        raise ValueError(
            f"{package_kind} package identity mismatch: expected {plugin_id}, "
            f"got {manifest['name']}"
        )
    if expected_version and manifest["version"] != expected_version:
        raise ValueError(
            f"{package_kind} package version mismatch: expected {expected_version}, "
            f"got {manifest['version']}"
        )
    provenance = compute_marketplace_content_provenance(
        package_dir,
        signature_relative_path=signature_path,
    )
    if provenance.get("complete") is not True:
        raise ValueError(
            f"{package_kind} content provenance is incomplete: "
            + "; ".join(str(issue) for issue in provenance.get("issues") or [])
        )
    publisher = verify_plugin_publisher_provenance(
        Path(package_dir).resolve(),
        manifest,
        provenance,
        trust_store_path=trust_store_path,
        signature_relative_path=signature_path,
    )
    if publisher.get("present") and publisher.get("verified") is not True:
        raise ValueError(
            f"{package_kind} publisher signature rejected: " + str(publisher.get("reason"))
        )
    if require_trusted and not (
        publisher.get("verified") is True and publisher.get("trusted") is True
    ):
        raise ValueError(
            f"trusted {package_kind} publisher signature is required: "
            + str(publisher.get("reason"))
        )
    result = {
        "schema": "echo.marketplace_package_trust.v1",
        "plugin_id": plugin_id,
        "kind": package_kind,
        "version": manifest["version"],
        "content_digest": provenance["digest"],
        "file_count": provenance["file_count"],
        "total_bytes": provenance["total_bytes"],
        "publisher_verified": bool(publisher.get("verified")),
        "publisher_id": str(publisher.get("publisher_id") or ""),
        "key_id": str(publisher.get("key_id") or ""),
        "signature_status": str(publisher.get("status") or "unsigned"),
        "signature_digest": str(publisher.get("signature_digest") or ""),
    }
    if manifest.get("release_summary"):
        result["release_summary"] = manifest["release_summary"]
    for field in (
        "host_api",
        "permissions",
        "auth_modes",
        "dependencies",
        "runtime_dependencies",
    ):
        value = manifest.get(field)
        if value not in (None, []):
            result[field] = value
    return result


__all__ = [
    "CODEX_SIGNATURE_RELATIVE_PATH",
    "CONNECTOR_MANIFEST_RELATIVE_PATH",
    "CONNECTOR_MANIFEST_SCHEMA",
    "CONNECTOR_RELEASE_SUMMARY",
    "CONNECTOR_SIGNATURE_RELATIVE_PATH",
    "MARKETPLACE_AUTH_MODES",
    "MARKETPLACE_HOST_API",
    "MARKETPLACE_PERMISSIONS",
    "compute_marketplace_content_provenance",
    "derive_codex_package_requirements",
    "derive_connector_package_requirements",
    "load_marketplace_package_manifest",
    "verify_marketplace_package_trust",
]
