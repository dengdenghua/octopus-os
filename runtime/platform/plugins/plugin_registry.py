from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from runtime.platform.plugins.codex_discovery import discover_codex_plugins
from runtime.platform.plugins.plugin_lifecycle import install_local_plugin

REGISTRY_SCHEMA = "echo.plugin_registry.v1"
UPDATE_SCHEMA = "echo.plugin_registry_updates.v1"
_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KNOWN_SURFACES = {"skills", "apps", "mcp", "commands", "capabilities"}


def discover_plugin_registry_updates(
    *,
    registry_path: str | Path,
    plugin_root: str | Path,
    publisher_trust_store_path: str | Path | None = None,
) -> dict[str, Any]:
    """Compare a signed-fixture registry with locally managed plugins."""

    registry_file = Path(registry_path).expanduser().resolve(strict=False)
    registry = _registry(registry_file)
    installed = {
        str(plugin.get("id") or ""): plugin
        for plugin in discover_codex_plugins(
            [Path(plugin_root)],
            publisher_trust_store_path=publisher_trust_store_path,
        )
    }
    entries = [
        _entry_status(
            registry_file,
            entry,
            installed.get(str(entry.get("id") or "")),
            publisher_trust_store_path=publisher_trust_store_path,
        )
        for entry in registry["plugins"]
    ]
    return {
        "schema": UPDATE_SCHEMA,
        "registry_schema": REGISTRY_SCHEMA,
        "registry_path": str(registry_file),
        "total": len(entries),
        "update_count": sum(row["status"] == "update_available" for row in entries),
        "install_count": sum(row["status"] == "not_installed" for row in entries),
        "blocked_count": sum(not row["installable"] for row in entries),
        "ready": all(row["fixture_verified"] for row in entries),
        "plugins": entries,
    }


def install_registry_plugin(
    plugin_id: str,
    *,
    registry_path: str | Path,
    plugin_root: str | Path,
    publisher_trust_store_path: str | Path | None = None,
    confirm_install: bool = False,
) -> dict[str, Any]:
    if not confirm_install:
        raise ValueError("confirm_install=true is required")
    report = discover_plugin_registry_updates(
        registry_path=registry_path,
        plugin_root=plugin_root,
        publisher_trust_store_path=publisher_trust_store_path,
    )
    entry = next((row for row in report["plugins"] if row["id"] == plugin_id), None)
    if entry is None:
        raise ValueError("plugin is not present in the configured registry")
    if entry["status"] == "current":
        raise ValueError("registry plugin is already current")
    if not entry["installable"]:
        raise ValueError(
            "registry compatibility fixture is not installable: " + "; ".join(entry["blockers"])
        )
    result = install_local_plugin(
        entry["source_path"],
        plugin_root=plugin_root,
        publisher_trust_store_path=publisher_trust_store_path,
        confirm_install=True,
    )
    return {
        **result,
        "registry_schema": REGISTRY_SCHEMA,
        "registry_path": report["registry_path"],
        "registry_fixture_digest": entry["content_digest"],
        "registry_surfaces": entry["surfaces"],
    }


def _registry(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("plugin registry is unavailable or malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != REGISTRY_SCHEMA:
        raise ValueError("plugin registry schema is unsupported")
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        raise ValueError("plugin registry plugins must be a list")
    seen: set[str] = set()
    for entry in plugins:
        if not isinstance(entry, dict):
            raise ValueError("plugin registry entry must be an object")
        plugin_id = str(entry.get("id") or "")
        if not _PLUGIN_ID.fullmatch(plugin_id) or plugin_id in seen:
            raise ValueError("plugin registry contains an unsafe or duplicate plugin id")
        seen.add(plugin_id)
        if not str(entry.get("version") or "") or not str(entry.get("source_path") or ""):
            raise ValueError("plugin registry entry requires version and source_path")
    return payload


def _entry_status(
    registry_path: Path,
    entry: dict[str, Any],
    installed: dict[str, Any] | None,
    *,
    publisher_trust_store_path: str | Path | None,
) -> dict[str, Any]:
    plugin_id = str(entry["id"])
    version = str(entry["version"])
    source = (registry_path.parent / str(entry["source_path"])).resolve(strict=False)
    surfaces = sorted(
        {str(item) for item in entry.get("surfaces") or [] if str(item) in _KNOWN_SURFACES}
    )
    blockers: list[str] = []
    candidate = _source_candidate(
        source,
        plugin_id=plugin_id,
        publisher_trust_store_path=publisher_trust_store_path,
    )
    smoke = candidate.get("smoke") if candidate else {}
    provenance = smoke.get("content_provenance") if isinstance(smoke, dict) else {}
    publisher = smoke.get("publisher_provenance") if isinstance(smoke, dict) else {}
    actual_digest = str(provenance.get("digest") or "") if isinstance(provenance, dict) else ""
    expected_digest = str(entry.get("content_digest") or "")
    if not candidate:
        blockers.append("registry source plugin could not be discovered")
    elif str(candidate.get("version") or "") != version:
        blockers.append("registry version does not match source manifest")
    if not expected_digest or actual_digest != expected_digest:
        blockers.append("registry content digest does not match source fixture")
    smoke_surfaces = smoke.get("surfaces") if isinstance(smoke, dict) else {}
    missing_surfaces = [surface for surface in surfaces if not bool(smoke_surfaces.get(surface))]
    if missing_surfaces:
        blockers.append("source fixture is missing surfaces: " + ", ".join(missing_surfaces))
    signature_required = entry.get("publisher_signature_required") is not False
    publisher_verified = isinstance(publisher, dict) and publisher.get("verified") is True
    if signature_required and not publisher_verified:
        blockers.append("trusted publisher signature is required")

    installed_version = str((installed or {}).get("version") or "")
    if not installed_version:
        status = "not_installed"
    else:
        comparison = _compare_versions(version, installed_version)
        status = (
            "update_available"
            if comparison > 0
            else "current"
            if comparison == 0
            else "installed_newer"
        )
    fixture_verified = not blockers
    return {
        "id": plugin_id,
        "version": version,
        "installed_version": installed_version,
        "status": status,
        "source_path": str(source),
        "surfaces": surfaces,
        "content_digest": expected_digest,
        "actual_content_digest": actual_digest,
        "publisher_signature_required": signature_required,
        "publisher_verified": publisher_verified,
        "fixture_verified": fixture_verified,
        "installable": fixture_verified and status in {"not_installed", "update_available"},
        "one_click_install": fixture_verified and status in {"not_installed", "update_available"},
        "blockers": blockers,
    }


def _source_candidate(
    source: Path,
    *,
    plugin_id: str,
    publisher_trust_store_path: str | Path | None,
) -> dict[str, Any] | None:
    if not source.is_dir():
        return None
    for plugin in discover_codex_plugins(
        [source.parent],
        publisher_trust_store_path=publisher_trust_store_path,
    ):
        if str(plugin.get("id") or "") != plugin_id:
            continue
        try:
            if Path(str(plugin.get("path") or "")).resolve() == source.resolve():
                return plugin
        except OSError:
            continue
    return None


def _compare_versions(left: str, right: str) -> int:
    def parts(value: str) -> tuple[tuple[int, Any], ...]:
        return tuple(
            (0, int(token)) if token.isdigit() else (1, token.lower())
            for token in re.split(r"[.+-]", value)
            if token
        )

    return (parts(left) > parts(right)) - (parts(left) < parts(right))


__all__ = [
    "REGISTRY_SCHEMA",
    "UPDATE_SCHEMA",
    "discover_plugin_registry_updates",
    "install_registry_plugin",
]
