"""Validated installed workbench-package manifests and static assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.plugins.publisher_provenance import (
    verify_plugin_publisher_provenance,
)
from runtime.platform.process.paths import app_paths

WORKBENCH_APP_SCHEMA = "echo.workbench_app.v1"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_SAFE_RUNTIME_PLUGIN_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_MAX_MANIFEST_BYTES = 256 * 1024
WORKBENCH_SIGNATURE_RELATIVE_PATH = Path(".echo-plugin/provenance.json")
_MAX_PROVENANCE_FILES = 10_000
_MAX_PROVENANCE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class WorkbenchPackageManifest:
    id: str
    name: str
    description: str
    route: str
    module_id: str
    version: str
    release_summary: str
    entry: str
    isolation: str
    permissions: tuple[str, ...]
    data_paths: tuple[str, ...]
    runtime_plugin: str | None
    host_api: str | None
    dependencies: tuple[str, ...]

    def to_public(self, *, asset_base: str) -> dict[str, Any]:
        return {
            "schema": WORKBENCH_APP_SCHEMA,
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "route": self.route,
            "module_id": self.module_id,
            "version": self.version,
            "release_summary": self.release_summary,
            "entry": self.entry,
            "entry_url": f"{asset_base}/{self.entry}",
            "isolation": self.isolation,
            "permissions": list(self.permissions),
            "data_paths": list(self.data_paths),
            "runtime_plugin": self.runtime_plugin,
            "host_api": self.host_api,
            "dependencies": list(self.dependencies),
        }


class WorkbenchPackageStore:
    """Read-only view of mutable workbench packages below app data."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        require_integrity: bool = False,
    ) -> None:
        self.root = Path(root or (app_paths().data_dir / "plugins" / "workbench")).resolve()
        self.require_integrity = require_integrity

    @staticmethod
    def validate_id(plugin_id: str) -> str:
        if not _SAFE_ID.fullmatch(plugin_id):
            raise ValueError(f"invalid workbench plugin id: {plugin_id!r}")
        return plugin_id

    def package_dir(self, plugin_id: str) -> Path:
        safe = self.validate_id(plugin_id)
        target = (self.root / safe).resolve()
        if self.root not in target.parents:
            raise ValueError(f"unsafe workbench plugin id: {plugin_id!r}")
        if target.is_symlink():
            raise ValueError(f"workbench package root cannot be a symlink: {plugin_id}")
        return target

    def load_manifest(self, plugin_id: str) -> WorkbenchPackageManifest:
        package_dir = self.package_dir(plugin_id)
        path = package_dir / "app.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"workbench app manifest not found: {plugin_id}")
        if path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise ValueError(f"workbench app manifest is too large: {plugin_id}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid workbench app manifest: {plugin_id}") from exc
        if not isinstance(raw, dict) or raw.get("schema") != WORKBENCH_APP_SCHEMA:
            raise ValueError(f"unsupported workbench app manifest schema: {plugin_id}")

        manifest_id = str(raw.get("id") or "").strip()
        if manifest_id != plugin_id:
            raise ValueError(
                f"workbench app manifest id mismatch: expected {plugin_id}, got {manifest_id}"
            )
        route = str(raw.get("route") or "").strip()
        if not route.startswith("/workspace/") or "\x00" in route:
            raise ValueError(f"invalid workbench route: {route!r}")
        module_id = str(raw.get("module_id") or "").strip()
        if not module_id or len(module_id) > 128:
            raise ValueError(f"invalid workbench module id: {module_id!r}")
        entry = self._validate_asset_path(str(raw.get("entry") or ""))
        if not entry.startswith("dist/") or not entry.endswith(".html"):
            raise ValueError("workbench entry must be an HTML file below dist/")
        entry_path = self.asset_path(plugin_id, entry)
        if not entry_path.is_file():
            raise FileNotFoundError(f"workbench entry is missing: {plugin_id}/{entry}")
        isolation = str(raw.get("isolation") or "iframe")
        if isolation != "iframe":
            raise ValueError(f"unsupported workbench isolation mode: {isolation}")
        permissions_raw = raw.get("permissions") or []
        if not isinstance(permissions_raw, list) or not all(
            isinstance(permission, str) and permission.strip() for permission in permissions_raw
        ):
            raise ValueError("workbench permissions must be a list of non-empty strings")
        data_paths_raw = raw.get("data_paths") or []
        if not isinstance(data_paths_raw, list) or not all(
            isinstance(data_path, str) and data_path.strip() for data_path in data_paths_raw
        ):
            raise ValueError("workbench data_paths must be a list of non-empty strings")
        data_paths = tuple(
            dict.fromkeys(self._validate_data_path(data_path) for data_path in data_paths_raw)
        )
        for index, data_path in enumerate(data_paths):
            for other in data_paths[index + 1 :]:
                if other.startswith(data_path + "/") or data_path.startswith(other + "/"):
                    raise ValueError("workbench data_paths cannot overlap")
        runtime_plugin_raw = raw.get("runtime_plugin")
        runtime_plugin = None
        if runtime_plugin_raw is not None:
            runtime_plugin = str(runtime_plugin_raw).strip()
            if not _SAFE_RUNTIME_PLUGIN_ID.fullmatch(runtime_plugin):
                raise ValueError(f"invalid workbench runtime plugin id: {runtime_plugin!r}")
        host_api_raw = raw.get("host_api")
        host_api = None if host_api_raw is None else str(host_api_raw).strip()
        if host_api is not None and (not host_api or len(host_api) > 160):
            raise ValueError("workbench host_api must be a non-empty version specifier")
        dependencies_raw = raw.get("dependencies") or []
        if not isinstance(dependencies_raw, list) or not all(
            isinstance(dependency, str) and dependency.strip() for dependency in dependencies_raw
        ):
            raise ValueError("workbench dependencies must be a list of package ids")
        dependencies = tuple(
            dict.fromkeys(self.validate_id(dependency.strip()) for dependency in dependencies_raw)
        )
        if manifest_id in dependencies:
            raise ValueError("workbench cannot depend on itself")
        release_summary = str(raw.get("release_summary") or "").strip()
        if len(release_summary) > 1_000 or any(
            ord(character) < 32 or ord(character) == 127 for character in release_summary
        ):
            raise ValueError("workbench release summary is invalid")
        manifest = WorkbenchPackageManifest(
            id=manifest_id,
            name=str(raw.get("name") or manifest_id).strip() or manifest_id,
            description=str(raw.get("description") or "").strip(),
            route=route,
            module_id=module_id,
            version=str(raw.get("version") or "0.0.0").strip() or "0.0.0",
            release_summary=release_summary,
            entry=entry,
            isolation=isolation,
            permissions=tuple(dict.fromkeys(permission.strip() for permission in permissions_raw)),
            data_paths=data_paths,
            runtime_plugin=runtime_plugin,
            host_api=host_api,
            dependencies=dependencies,
        )
        if self.require_integrity:
            self.verify_installed_integrity(plugin_id)
        return manifest

    def verify_installed_integrity(self, plugin_id: str) -> dict[str, Any]:
        package_dir = self.package_dir(plugin_id)
        record_path = self.root / ".lifecycle" / "trust" / f"{plugin_id}.json"
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"workbench integrity record is unavailable: {plugin_id}") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema") != "echo.workbench_package_trust.v1"
            or record.get("plugin_id") != plugin_id
        ):
            raise ValueError(f"workbench integrity record is invalid: {plugin_id}")
        provenance = compute_workbench_content_provenance(package_dir)
        if provenance.get("complete") is not True:
            raise ValueError(f"workbench integrity scan is incomplete: {plugin_id}")
        if provenance.get("digest") != record.get("content_digest"):
            raise ValueError(f"workbench package integrity check failed: {plugin_id}")
        return record

    def asset_path(self, plugin_id: str, relative_path: str) -> Path:
        package_dir = self.package_dir(plugin_id)
        relative = self._validate_asset_path(relative_path)
        target = (package_dir / relative).resolve()
        if package_dir not in target.parents:
            raise ValueError(f"unsafe workbench asset path: {relative_path!r}")
        cursor = package_dir
        for part in Path(relative).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"workbench assets cannot contain symlinks: {relative_path!r}")
        return target

    @staticmethod
    def _validate_asset_path(relative_path: str) -> str:
        value = relative_path.strip().replace("\\", "/")
        path = Path(value)
        if (
            not value
            or "\x00" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"unsafe workbench asset path: {relative_path!r}")
        return "/".join(path.parts)

    @staticmethod
    def _validate_data_path(relative_path: str) -> str:
        value = relative_path.strip().replace("\\", "/")
        path = Path(value)
        if (
            not value
            or "\x00" in value
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError(f"unsafe workbench data path: {relative_path!r}")
        return "/".join(path.parts)


def compute_workbench_content_provenance(package_dir: str | Path) -> dict[str, Any]:
    """Build the bounded path-and-content digest covered by a publisher signature."""

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
            if name in {"__pycache__", ".git"}:
                continue
            if candidate.is_symlink():
                issues.append(f"symlink directory excluded: {candidate.relative_to(root)}")
                complete = False
                continue
            safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in sorted(files):
            candidate = current_path / name
            relative = candidate.relative_to(root)
            if relative == WORKBENCH_SIGNATURE_RELATIVE_PATH:
                continue
            if candidate.is_symlink():
                issues.append(f"symlink file excluded: {relative}")
                complete = False
                continue
            if file_count >= _MAX_PROVENANCE_FILES:
                issues.append(f"workbench exceeds {_MAX_PROVENANCE_FILES} provenance files")
                complete = False
                limit_exceeded = True
                break
            try:
                size = candidate.stat().st_size
            except OSError as exc:
                issues.append(f"unreadable provenance file {relative}: {type(exc).__name__}")
                complete = False
                continue
            if total_bytes + size > _MAX_PROVENANCE_BYTES:
                issues.append(f"workbench exceeds {_MAX_PROVENANCE_BYTES} provenance bytes")
                complete = False
                limit_exceeded = True
                break
            try:
                digest.update(relative.as_posix().encode("utf-8"))
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
        "algorithm": "sha256:path-null-content",
        "digest": digest.hexdigest() if complete else "",
        "complete": complete,
        "file_count": file_count,
        "total_bytes": total_bytes,
        "issues": issues,
    }


def verify_workbench_package_trust(
    package_dir: str | Path,
    manifest: WorkbenchPackageManifest,
    *,
    trust_store_path: str | Path | None = None,
    require_trusted: bool = False,
) -> dict[str, Any]:
    """Verify content completeness and optional Ed25519 publisher provenance."""

    root = Path(package_dir).resolve()
    provenance = compute_workbench_content_provenance(root)
    if provenance.get("complete") is not True:
        raise ValueError(
            "workbench content provenance is incomplete: "
            + "; ".join(str(issue) for issue in provenance.get("issues") or [])
        )
    publisher = verify_plugin_publisher_provenance(
        root,
        {"name": manifest.id, "version": manifest.version},
        provenance,
        trust_store_path=trust_store_path,
        signature_relative_path=WORKBENCH_SIGNATURE_RELATIVE_PATH,
    )
    if publisher.get("present") and publisher.get("verified") is not True:
        raise ValueError("workbench publisher signature rejected: " + str(publisher.get("reason")))
    if require_trusted and not (
        publisher.get("verified") is True and publisher.get("trusted") is True
    ):
        raise ValueError("trusted publisher signature is required: " + str(publisher.get("reason")))
    return {
        "schema": "echo.workbench_package_trust.v1",
        "plugin_id": manifest.id,
        "version": manifest.version,
        "release_summary": manifest.release_summary,
        "content_digest": provenance["digest"],
        "file_count": provenance["file_count"],
        "total_bytes": provenance["total_bytes"],
        "publisher_verified": bool(publisher.get("verified")),
        "publisher_id": str(publisher.get("publisher_id") or ""),
        "key_id": str(publisher.get("key_id") or ""),
        "signature_status": str(publisher.get("status") or "unsigned"),
        "signature_digest": str(publisher.get("signature_digest") or ""),
    }


class WorkbenchPackageDataStore:
    """Recoverable user-data lifecycle for mutable workbench packages."""

    def __init__(
        self,
        root: str | Path | None = None,
        trash_root: str | Path | None = None,
    ) -> None:
        paths = app_paths()
        self.root = Path(root or paths.data_dir).resolve()
        self.trash_root = Path(
            trash_root or (paths.data_dir / "plugins" / ".trash" / "workbench")
        ).resolve()

    def recoveries(self, plugin_id: str) -> list[dict[str, Any]]:
        plugin_id = WorkbenchPackageStore.validate_id(plugin_id)
        base = self.trash_root / plugin_id
        if not base.is_dir() or base.is_symlink():
            return []
        values: list[dict[str, Any]] = []
        for recovery in sorted(base.iterdir(), reverse=True):
            if not recovery.is_dir() or recovery.is_symlink():
                continue
            meta = read_json_with_backup(recovery / "recovery.json", default={})
            if not isinstance(meta, dict) or meta.get("plugin_id") != plugin_id:
                continue
            data_paths = meta.get("data_paths")
            if not isinstance(data_paths, list):
                continue
            values.append(
                {
                    "recovery_id": recovery.name,
                    "created_at": meta.get("created_at"),
                    "data_paths": data_paths,
                }
            )
        return values

    def trash(
        self,
        plugin_id: str,
        data_paths: tuple[str, ...],
        *,
        confirm: bool,
    ) -> dict[str, Any]:
        if not confirm:
            raise ValueError("confirm_data_move=true is required for data_policy=trash")
        if not data_paths:
            return {"status": "absent", "paths": []}
        plugin_id = WorkbenchPackageStore.validate_id(plugin_id)
        recovery_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        recovery = self.trash_root / plugin_id / recovery_id
        recovery.mkdir(parents=True, exist_ok=False)
        moved: list[tuple[Path, Path]] = []
        try:
            for relative in data_paths:
                source = (self.root / relative).resolve()
                if self.root not in source.parents or source.is_symlink():
                    raise ValueError(f"unsafe workbench data path: {relative!r}")
                if not source.exists():
                    continue
                target = recovery / "data" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                moved.append((source, target))
            atomic_write_json(
                recovery / "recovery.json",
                {
                    "schema": "echo.workbench_recovery.v1",
                    "plugin_id": plugin_id,
                    "recovery_id": recovery_id,
                    "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "data_paths": list(data_paths),
                },
                sort_keys=True,
            )
        except Exception:
            for source, target in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            shutil.rmtree(recovery, ignore_errors=True)
            raise
        return {
            "status": "trashed" if moved else "absent",
            "recovery_id": recovery_id,
            "paths": [str(source) for source, _target in moved],
        }

    def restore(
        self,
        plugin_id: str,
        data_paths: tuple[str, ...],
        *,
        recovery_id: str | None = None,
    ) -> dict[str, Any]:
        rows = self.recoveries(plugin_id)
        if recovery_id is None:
            recovery_id = str(rows[0]["recovery_id"]) if rows else None
        if not recovery_id or recovery_id not in {str(row["recovery_id"]) for row in rows}:
            raise KeyError(f"no recoverable workbench data found: {plugin_id}")
        recovery = self.trash_root / plugin_id / recovery_id
        moved: list[tuple[Path, Path]] = []
        try:
            for relative in data_paths:
                source = recovery / "data" / relative
                if not source.exists():
                    continue
                target = (self.root / relative).resolve()
                if self.root not in target.parents or target.is_symlink():
                    raise ValueError(f"unsafe workbench data path: {relative!r}")
                if target.exists():
                    has_content = target.is_file() or any(target.iterdir())
                    if has_content:
                        raise FileExistsError(
                            f"live workbench data already exists; refusing to overwrite: {target}"
                        )
                    target.rmdir()
                target.parent.mkdir(parents=True, exist_ok=True)
                source.replace(target)
                moved.append((source, target))
        except Exception:
            for source, target in reversed(moved):
                source.parent.mkdir(parents=True, exist_ok=True)
                target.replace(source)
            raise
        shutil.rmtree(recovery, ignore_errors=True)
        return {
            "status": "restored",
            "recovery_id": recovery_id,
            "paths": [str(target) for _source, target in moved],
        }


__all__ = [
    "WORKBENCH_APP_SCHEMA",
    "WORKBENCH_SIGNATURE_RELATIVE_PATH",
    "WorkbenchPackageDataStore",
    "WorkbenchPackageManifest",
    "WorkbenchPackageStore",
    "compute_workbench_content_provenance",
    "verify_workbench_package_trust",
]
