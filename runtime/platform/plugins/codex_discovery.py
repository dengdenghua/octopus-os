"""Discover Codex-format plugins from disk and normalise their manifests.

This scanning/parsing logic lived in ``sensing/gateway/plugins_router``,
so the execution-layer capability skills had to reach up into the web
layer to enumerate installed plugins. It is pure (stdlib + project_root)
and is plugin-domain logic, so it belongs under ``platform.plugins``
alongside the plugin hub. ``plugins_router`` re-exports
``discover_codex_plugins`` (and helpers, for its asset endpoints).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from runtime.platform.plugins.publisher_provenance import (
    SIGNATURE_RELATIVE_PATH,
    verify_plugin_publisher_provenance,
)
from runtime.platform.process.paths import project_root, resources_root

_PROVENANCE_IGNORED_DIRS = frozenset({".git", ".pytest_cache", "__pycache__", "node_modules"})
_PROVENANCE_MAX_FILES = 2048
_PROVENANCE_MAX_BYTES = 64 * 1024 * 1024
_PUBLIC_IMAGE_SUFFIXES = frozenset(
    {".avif", ".gif", ".ico", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
)
_PUBLIC_ASSET_FORBIDDEN_PARTS = frozenset(
    {
        ".codex-plugin",
        ".git",
        ".github",
        "commands",
        "node_modules",
        "scripts",
        "skills",
        "source",
        "src",
        "tests",
    }
)
_SENSITIVE_ASSET_SUFFIXES = frozenset(
    {
        ".bash",
        ".cfg",
        ".cjs",
        ".class",
        ".conf",
        ".go",
        ".ini",
        ".jar",
        ".java",
        ".js",
        ".json",
        ".jsonc",
        ".jsx",
        ".key",
        ".lock",
        ".mjs",
        ".pem",
        ".php",
        ".ps1",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".toml",
        ".ts",
        ".tsx",
        ".wasm",
        ".yaml",
        ".yml",
        ".zsh",
    }
)


def _default_plugin_roots() -> list[Path]:
    root = project_root(Path(__file__))
    return [
        resources_root() / ".echo" / "plugins" / "codex",
        root / ".echo" / "plugins" / "codex",
        Path.home() / ".echo" / "plugins" / "codex",
    ]


# 我们自己的 Codex 格式插件目录(echo 名下,不再直接读 ~/.codex)。
ECHO_CODEX_PLUGIN_ROOT = Path.home() / ".echo" / "plugins" / "codex"
# Codex 应用的原始插件缓存(只作为一次性的迁移来源)。
LEGACY_CODEX_PLUGIN_CACHE = Path.home() / ".codex" / "plugins" / "cache"


def echo_codex_plugin_root() -> Path:
    """返回 echo 自有的 Codex 格式插件目录(不存在则创建)。"""
    ECHO_CODEX_PLUGIN_ROOT.mkdir(parents=True, exist_ok=True)
    return ECHO_CODEX_PLUGIN_ROOT


def _version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for seg in version.replace("-", ".").split("."):
        parts.append(int(seg) if seg.isdigit() else 0)
    return tuple(parts)


def sync_codex_cache_to_echo(
    *,
    source: str | Path | None = None,
    dest: str | Path | None = None,
) -> int:
    """把旧 Codex 缓存(~/.codex/plugins/cache)一次性同步到我们 echo 插件目录。

    幂等:echo 目录里已有同名插件(.codex-plugin/plugin.json)就跳过,不覆盖
    本地已装的。返回本次复制成功的插件数。
    """
    src = Path(source or LEGACY_CODEX_PLUGIN_CACHE)
    target_root = Path(dest or echo_codex_plugin_root())
    if not src.is_dir():
        return 0
    target_root.mkdir(parents=True, exist_ok=True)
    # src/<family>/<plugin>/<version>/.codex-plugin/plugin.json → 每个插件保留最新版本
    by_plugin: dict[str, tuple[tuple[int, ...], Path]] = {}
    for manifest_path in sorted(src.rglob(".codex-plugin/plugin.json")):
        try:
            meta = json.loads(manifest_path.read_text("utf-8"))
            pid = str(meta.get("name") or "")
        except (OSError, json.JSONDecodeError):  # noqa: BLE001
            continue
        if not pid:
            continue
        version = _version_tuple(str(meta.get("version") or "0.0.0"))
        plugin_root = manifest_path.parent.parent
        cur = by_plugin.get(pid)
        if cur is None or version >= cur[0]:
            by_plugin[pid] = (version, plugin_root)
    copied = 0
    for pid, (_, plugin_root) in by_plugin.items():
        target = target_root / pid
        if (target / ".codex-plugin" / "plugin.json").exists():
            continue
        shutil.copytree(plugin_root, target)
        copied += 1
    return copied


def _read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _string(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _author_name(author: Any) -> str:
    if isinstance(author, str):
        return author
    if isinstance(author, dict):
        return _string(author.get("name"))
    return ""


def _capability_records(raw: Any, plugin_name: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            name = _string(item.get("name") or item.get("type"), "capability")
            out.append(
                {
                    "name": name,
                    "type": _string(item.get("type"), "codex"),
                    "description": _string(item.get("description")),
                    "version": _string(item.get("version"), "1.0.0"),
                    "requires": item.get("requires")
                    if isinstance(item.get("requires"), list)
                    else [],
                    "provider": plugin_name,
                }
            )
        elif isinstance(item, str) and item.strip():
            out.append(
                {
                    "name": item.strip(),
                    "type": "codex",
                    "description": "",
                    "version": "1.0.0",
                    "requires": [],
                    "provider": plugin_name,
                }
            )
    return out


def _dependencies(manifest: dict[str, Any]) -> list[str]:
    deps: list[str] = []
    for key in ("requires", "dependencies"):
        raw = manifest.get(key)
        if isinstance(raw, list):
            deps.extend(_string(item) for item in raw if _string(item))
    if manifest.get("mcpServers"):
        deps.append("mcp")
    if manifest.get("apps"):
        deps.append("app")
    if manifest.get("skills"):
        deps.append("skills")
    return sorted(set(deps))


def _public_interface_asset_path(plugin_dir: Path, raw_path: Any) -> Path | None:
    """Resolve a manifest-declared image that is safe to expose anonymously.

    The public surface is intentionally narrower than the authenticated asset
    endpoint: only image-shaped logo/composer metadata may cross it.  In
    particular, declaring source, configuration, or ``.env`` content as a logo
    must not turn that file into an unauthenticated same-origin download.
    """

    rel = _string(raw_path).strip()
    if not rel:
        return None
    asset_path = Path(rel)
    if asset_path.is_absolute() or ".." in asset_path.parts:
        return None
    if is_sensitive_plugin_asset_path(asset_path):
        return None
    if asset_path.suffix.casefold() not in _PUBLIC_IMAGE_SUFFIXES:
        return None

    root = plugin_dir.resolve()
    unresolved = plugin_dir.joinpath(*asset_path.parts)
    current = plugin_dir
    for part in asset_path.parts:
        current /= part
        if current.is_symlink():
            return None
    candidate = unresolved.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def is_sensitive_plugin_asset_path(asset_path: str | Path) -> bool:
    """Whether an asset path must never be exposed by the file-serving route."""

    requested = Path(asset_path)
    if requested.is_absolute() or ".." in requested.parts:
        return True
    lowered_parts = tuple(part.casefold() for part in requested.parts)
    if any(part in _PUBLIC_ASSET_FORBIDDEN_PARTS or part.startswith(".") for part in lowered_parts):
        return True
    return requested.suffix.casefold() in _SENSITIVE_ASSET_SUFFIXES


def public_plugin_asset_paths(
    plugin_dir: Path,
    manifest: dict[str, Any],
) -> frozenset[str]:
    """Return exact relative paths allowed on the anonymous asset surface."""

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        return frozenset()
    root = plugin_dir.resolve()
    paths: set[str] = set()
    for key in ("logo", "composerIcon"):
        candidate = _public_interface_asset_path(plugin_dir, interface.get(key))
        if candidate is not None:
            paths.add(candidate.relative_to(root).as_posix())
    return frozenset(paths)


def _asset_url(plugin_dir: Path, plugin_id: str, raw_path: Any) -> str | None:
    candidate = _public_interface_asset_path(plugin_dir, raw_path)
    if candidate is None:
        return None
    posix_rel = candidate.relative_to(plugin_dir.resolve()).as_posix()
    return f"/api/plugins/{quote(plugin_id, safe='')}/assets/{quote(posix_rel, safe='/')}"


def _has_skill_files(plugin_dir: Path) -> bool:
    skills_dir = plugin_dir / "skills"
    return skills_dir.is_dir() and any(skills_dir.glob("*/SKILL.md"))


def _has_app_manifest(plugin_dir: Path) -> bool:
    return any(
        (plugin_dir / name).is_file() for name in (".app.json", "echo-app.jsonc", "app.json")
    )


def _plugin_content_provenance(plugin_dir: Path) -> dict[str, Any]:
    """Build a bounded, reproducible digest of local plugin contents.

    The publisher envelope is deliberately excluded: it signs this digest and
    cannot be part of the bytes it covers. The verifier binds the digest back
    to the manifest identity, version, trusted publisher, and key.
    """

    root = plugin_dir.resolve()
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
            if name in _PROVENANCE_IGNORED_DIRS:
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
            if relative == SIGNATURE_RELATIVE_PATH:
                continue
            if candidate.is_symlink():
                issues.append(f"symlink file excluded: {relative}")
                complete = False
                continue
            if file_count >= _PROVENANCE_MAX_FILES:
                issues.append(f"plugin exceeds {_PROVENANCE_MAX_FILES} provenance files")
                complete = False
                limit_exceeded = True
                break
            try:
                size = candidate.stat().st_size
            except OSError as exc:
                issues.append(f"unreadable provenance file {relative}: {type(exc).__name__}")
                complete = False
                continue
            if total_bytes + size > _PROVENANCE_MAX_BYTES:
                issues.append(f"plugin exceeds {_PROVENANCE_MAX_BYTES} provenance bytes")
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
        "signed": False,
        "issues": issues,
    }


def _plugin_smoke_check(
    plugin_dir: Path,
    manifest: dict[str, Any],
    *,
    info: dict[str, Any],
    logo_url: str | None,
    composer_icon_url: str | None,
    publisher_trust_store_path: str | Path | None,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    issues: list[str] = []
    warnings: list[str] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            issues.append(detail or name)

    manifest_path = plugin_dir / ".codex-plugin" / "plugin.json"
    add("manifest", manifest_path.is_file(), "missing .codex-plugin/plugin.json")
    add("name", bool(_string(manifest.get("name")).strip()), "manifest is missing name")
    add(
        "version",
        bool(_string(manifest.get("version")).strip()),
        "manifest is missing version",
    )

    raw_interface = manifest.get("interface")
    interface: dict[str, Any] = raw_interface if isinstance(raw_interface, dict) else {}
    has_capabilities = bool(info.get("capabilities"))
    has_skills = _has_skill_files(plugin_dir)
    has_apps = bool(manifest.get("apps")) or _has_app_manifest(plugin_dir)
    has_mcp = bool(manifest.get("mcpServers")) or (plugin_dir / ".mcp.json").is_file()
    has_commands = (plugin_dir / "commands").is_dir()
    surface_count = sum(
        1 for present in (has_capabilities, has_skills, has_apps, has_mcp, has_commands) if present
    )
    add(
        "capability_surface",
        surface_count > 0,
        "plugin exposes no capabilities, skills, apps, MCP servers, or commands",
    )

    if interface.get("logo") and logo_url is None:
        warnings.append("logo path is missing, unsafe, or outside plugin root")
    if interface.get("composerIcon") and composer_icon_url is None:
        warnings.append("composer icon path is missing, unsafe, or outside plugin root")
    if has_mcp and not manifest.get("permissions"):
        warnings.append("MCP-capable plugin has no explicit permissions declaration")

    permission_resolution = _permission_resolution(
        manifest,
        has_mcp=has_mcp,
        warnings=warnings,
    )
    content_provenance = _plugin_content_provenance(plugin_dir)
    if not content_provenance["complete"]:
        warnings.extend(content_provenance["issues"])
    publisher_provenance = verify_plugin_publisher_provenance(
        plugin_dir,
        manifest,
        content_provenance,
        trust_store_path=publisher_trust_store_path,
    )
    if publisher_provenance["present"]:
        add(
            "publisher_signature",
            bool(publisher_provenance["verified"]),
            str(publisher_provenance["reason"]),
        )
    content_provenance["signed"] = bool(publisher_provenance["verified"])
    content_provenance["signature_status"] = publisher_provenance["status"]
    ok = not issues
    publisher_verified = bool(publisher_provenance["verified"])
    if publisher_verified and ok and not warnings:
        trust_level = "publisher_verified"
    elif publisher_verified:
        trust_level = "publisher_verified_review_required"
    elif ok and not warnings and not publisher_provenance["present"]:
        trust_level = "local_verified"
    else:
        trust_level = "local_review_required"
    return {
        "schema": "echo.codex_plugin_smoke.v1",
        "ok": ok,
        "checks": checks,
        "issues": issues,
        "warnings": warnings,
        "surfaces": {
            "capabilities": has_capabilities,
            "skills": has_skills,
            "apps": has_apps,
            "mcp": has_mcp,
            "commands": has_commands,
        },
        "trust": {
            "level": trust_level,
            "signed": publisher_verified,
            "reason": str(publisher_provenance["reason"]),
        },
        "content_provenance": content_provenance,
        "publisher_provenance": publisher_provenance,
        "permission_resolution": permission_resolution,
    }


def _permission_resolution(
    manifest: dict[str, Any],
    *,
    has_mcp: bool,
    warnings: list[str],
) -> dict[str, Any]:
    raw_permissions = manifest.get("permissions")
    explicit_permissions = raw_permissions if isinstance(raw_permissions, list) else []
    if explicit_permissions:
        return {
            "schema": "echo.codex_plugin_permission_resolution.v1",
            "status": "explicit",
            "review_required": False,
            "accepted_risk": False,
            "permissions": explicit_permissions,
            "reason": "plugin declares explicit permissions",
        }
    inferred: list[str] = []
    if has_mcp:
        inferred.append("mcp:execute:review_required")
    if manifest.get("apps"):
        inferred.append("app:render:review_required")
    if manifest.get("interface"):
        inferred.append("ui:metadata:local")
    status = "review_required" if inferred or warnings else "none"
    return {
        "schema": "echo.codex_plugin_permission_resolution.v1",
        "status": status,
        "review_required": status == "review_required",
        "accepted_risk": False,
        "permissions": inferred,
        "reason": (
            "default permissions inferred from plugin surfaces"
            if inferred
            else "no permission-bearing surfaces detected"
        ),
    }


def _plugin_info(
    plugin_dir: Path,
    manifest: dict[str, Any],
    *,
    publisher_trust_store_path: str | Path | None = None,
) -> dict[str, Any]:
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        interface = {}
    name = _string(manifest.get("name"), plugin_dir.name)
    display_name = _string(interface.get("displayName"), name)
    capabilities = _capability_records(interface.get("capabilities"), name)
    logo_url = _asset_url(plugin_dir, name, interface.get("logo"))
    composer_icon_url = _asset_url(plugin_dir, name, interface.get("composerIcon"))
    error = ""
    if not (plugin_dir / ".codex-plugin" / "plugin.json").is_file():
        error = "missing .codex-plugin/plugin.json"
    author = (
        _author_name(manifest.get("author")) or _string(interface.get("developerName")) or "echo"
    )
    info = {
        "id": name,
        "name": display_name,
        "version": _string(manifest.get("version"), "0.1.0"),
        "description": _string(
            interface.get("shortDescription") or manifest.get("description"),
        ),
        "author": author,
        "capabilities": capabilities,
        "dependencies": _dependencies(manifest),
        "enabled": not error,
        "state": "registered" if not error else "error",
        "error": error or None,
        "logo_url": logo_url,
        "icon_url": composer_icon_url or logo_url,
        "brand_color": _string(interface.get("brandColor")) or None,
        "source": "codex",
        "path": str(plugin_dir),
    }
    info["smoke"] = _plugin_smoke_check(
        plugin_dir,
        manifest,
        info=info,
        logo_url=logo_url,
        composer_icon_url=composer_icon_url,
        publisher_trust_store_path=publisher_trust_store_path,
    )
    return info


def discover_codex_plugins(
    roots: list[Path] | None = None,
    *,
    publisher_trust_store_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for root in roots or _default_plugin_roots():
        if not root.is_dir():
            continue
        for manifest_path in sorted(root.glob("*/.codex-plugin/plugin.json")):
            manifest = _read_manifest(manifest_path)
            if manifest is None:
                continue
            info = _plugin_info(
                manifest_path.parent.parent,
                manifest,
                publisher_trust_store_path=publisher_trust_store_path,
            )
            out[info["id"]] = info
    return sorted(out.values(), key=lambda item: item["name"].lower())


__all__ = [
    "discover_codex_plugins",
    "is_sensitive_plugin_asset_path",
    "public_plugin_asset_paths",
]
