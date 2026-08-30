"""统一「插件(Capability)」注册表 —— 所有外部能力统一叫插件。

WorkBuddy 连接器(108)、Codex 格式插件(~/.echo/plugins/codex)本质都是「插件」:
元数据 + skills + 工具(MCP/CLI) + 认证编排。本模块把两者归一成同一个 schema、
同一套生命周期,让前端一个市场统一管理。

统一模型(CapabilityItem):
  source: "connector" | "codex_plugin"   (内部来源标识,统一对外叫插件)
  auth_mode: token | oauth | server-side | oneid-token | none
  lifecycle: installed / enabled / connected
  install → 复制 skills 到 ~/.echo/skills + 登记 MCP
  connect → 认证编排(带认证的插件走 token/oauth;其余默认无需认证)
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.platform.io import JsonMutation, mutate_json_file, read_json_file
from runtime.platform.process.paths import resources_root

# ── 默认路径 ────────────────────────────────────────────────
# Codex 格式插件统一放在我们 echo 名下(~/.echo/plugins/codex),
# 不再直接读 Codex 的 ~/.codex/plugins/cache;旧缓存由 codex_discovery 一次性同步。
CODEX_PLUGIN_CACHE = Path.home() / ".echo" / "plugins" / "codex"
CONNECTOR_ROOT = Path(os.path.expanduser("~/.echo/connectors"))
CONNECTOR_STATE_FILE = CONNECTOR_ROOT / "state.json"
CAPABILITY_STATE_FILE = Path(os.path.expanduser("~/.echo/capabilities/state.json"))
SKILLS_ROOT = Path(os.path.expanduser("~/.echo/skills"))
REPO_ROOT = resources_root()
NATIVE_PLUGIN_ICON_ROOT = REPO_ROOT / ".echo" / "plugins" / "codex"
WORKBUDDY_CONNECTOR_ICON_ROOT = REPO_ROOT / "extensions" / "workbuddy-connectors" / "icons"
STOREFRONT_ICON_ROOT = (
    REPO_ROOT / "extensions" / "workbuddy-experts" / "storefront" / "data" / "icons"
)

_CONNECTOR_BRAND_ALIASES = {
    "linear-mcp": "linear",
    "canva-ai": "canva",
    "github": "github",
    "notion": "notion",
    "google-calendar": "google-calendar",
    "google-drive": "google-drive",
    "figma": "figma",
    "slack": "slack",
    "gmail": "gmail",
}

_ICON_PRIORITY = (
    "app-icon.png",
    "icon.svg",
    "icon.png",
    "logo-padded.svg",
    "logo-padded.png",
    "logo.svg",
    "logo.png",
)

_SLUG_RE = re.compile(r"[^a-z0-9_-]+", re.I)


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip()).strip("-").lower()


def _validate_state(data: Any) -> None:
    if not isinstance(data, dict):
        raise RuntimeError("capability state file must contain a JSON object")


class CapabilityRegistry:
    """连接器 + Codex 插件的统一注册表(只读市场 + 统一生命周期)。"""

    def __init__(
        self,
        *,
        connector_registry: Any = None,
        auth_orchestrator: Any = None,
        codex_cache: str | Path | None = None,
        capability_state_file: str | Path | None = None,
        skills_root: str | Path | None = None,
        workbuddy_icon_root: str | Path | None = None,
        native_icon_root: str | Path | None = None,
        storefront_icon_root: str | Path | None = None,
        permission_store: Any = None,
    ) -> None:
        # An injected connector registry owns its lifecycle (tests, OEM forks,
        # and embedders rely on that boundary).  Only the default registry may
        # be replaced by the signed cloud-package installer.
        self._use_cloud_connector_installer = connector_registry is None
        if connector_registry is None:
            from runtime.platform.connectors.connector_registry import ConnectorRegistry

            connector_registry = ConnectorRegistry()
        if auth_orchestrator is None:
            from runtime.platform.connectors.auth_orchestrator import AuthOrchestrator

            auth_orchestrator = AuthOrchestrator()
        self._connectors = connector_registry
        self._auth = auth_orchestrator
        self._codex_cache = Path(codex_cache or CODEX_PLUGIN_CACHE)
        self._state_file = Path(capability_state_file or CAPABILITY_STATE_FILE)
        if permission_store is None:
            from runtime.platform.capabilities.permission_grants import (
                CapabilityPermissionStore,
            )

            permission_path = (
                self._state_file.parent / "permission-grants.json"
                if capability_state_file is not None
                else None
            )
            permission_store = CapabilityPermissionStore(permission_path)
        self._permissions = permission_store
        if hasattr(self._connectors, "_permissions"):
            # Unified and legacy connector routes must consult one authority.
            self._connectors._permissions = permission_store
        self._skills_root = Path(skills_root or SKILLS_ROOT)
        self._workbuddy_icon_root = Path(workbuddy_icon_root or WORKBUDDY_CONNECTOR_ICON_ROOT)
        self._native_icon_root = Path(native_icon_root or NATIVE_PLUGIN_ICON_ROOT)
        self._storefront_icon_root = Path(storefront_icon_root or STOREFRONT_ICON_ROOT)

    # ── 统一列表 ────────────────────────────────────────────
    def list(self) -> list[dict[str, Any]]:
        return [*self._list_connectors(), *self._list_codex_plugins()]

    def get(self, cid: str) -> dict[str, Any] | None:
        for item in self.list():
            if item["id"] == cid:
                return item
        return None

    def install_plan(self, cid: str) -> dict[str, Any]:
        """Return a bounded, side-effect-free plan for one capability install."""

        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        statuses: dict[str, dict[str, Any]] = {}
        catalog_items: dict[str, dict[str, Any]] = {}
        try:
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            catalog = CloudCatalog("plugins", use_cache=True, use_remote=False)
            statuses = catalog.plugin_statuses()
            catalog_items = {
                str(candidate.get("plugin") or candidate.get("id") or ""): candidate
                for candidate in catalog.items()
            }
        except Exception:  # noqa: BLE001 - missing evidence becomes a blocker below
            statuses = {}
        dependencies: list[dict[str, Any]] = []
        blockers: list[str] = []
        visited_dependencies: set[str] = set()
        visiting_dependencies: set[str] = set()

        def resolve_dependency(dependency_id: str, *, required_by: str) -> None:
            dependency_id = str(dependency_id)
            if dependency_id in visiting_dependencies:
                blockers.append(f"dependency_cycle:{dependency_id}")
                return
            if dependency_id in visited_dependencies:
                return
            visiting_dependencies.add(dependency_id)
            status = statuses.get(str(dependency_id)) or {}
            ready = bool(
                status.get("installed") is True
                and status.get("lifecycle_state") not in {"broken", None}
                and status.get("trust", {}).get("integrity_verified") is True
            )
            dependency_item = catalog_items.get(dependency_id)
            available = dependency_item is not None
            if available and not ready:
                for nested in dependency_item.get("dependencies") or []:
                    resolve_dependency(str(nested), required_by=dependency_id)
            dependencies.append(
                {
                    "id": dependency_id,
                    "required_by": required_by,
                    "ready": ready,
                    "will_install": not ready and available,
                    "state": (
                        str(status.get("lifecycle_state") or "missing")
                        if ready or not available
                        else "planned"
                    ),
                }
            )
            if not ready and not available:
                blockers.append(f"dependency_unavailable:{dependency_id}")
            visiting_dependencies.discard(dependency_id)
            visited_dependencies.add(dependency_id)

        for dependency_id in item.get("dependencies") or []:
            resolve_dependency(str(dependency_id), required_by=cid)
        host_api = str(item.get("host_api") or "").strip()
        if host_api:
            try:
                from packaging.specifiers import SpecifierSet
                from packaging.version import Version

                from runtime import __version__

                if Version(__version__) not in SpecifierSet(host_api):
                    blockers.append("host_incompatible")
            except (TypeError, ValueError):
                blockers.append("host_requirement_invalid")
        payload = {
            "schema": "echo.capability_install_plan.v1",
            "capability_id": cid,
            "kind": "connector" if item.get("source") == "connector" else "codex",
            "version": str(item.get("version") or ""),
            "host_api": host_api or None,
            "permissions": list(item.get("permissions") or []),
            "auth_modes": list(item.get("auth_modes") or []),
            "dependencies": dependencies,
            "runtime_dependencies": [
                {"name": str(name), "bundled": True}
                for name in item.get("runtime_dependencies") or []
            ],
            "changes": [
                "verify_publisher_signature",
                "stage_package_generation",
                "project_bundled_skills",
                "record_permissions_inactive",
            ],
            "permission_review_required": bool(item.get("permissions")),
            "can_install": not blockers,
            "blockers": blockers,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        payload["plan_id"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return payload

    def _list_connectors(self) -> list[dict[str, Any]]:
        out = []
        cloud_by_plugin = self._cloud_marketplace_items("connector")
        for c in self._connectors.list():
            item = dict(c)  # ConnectorRegistry.to_dict() 已含 installed/enabled
            cloud_item = cloud_by_plugin.get(str(item.get("id") or ""))
            item["source"] = "connector"
            item["author"] = "WorkBuddy"
            item["category"] = c.get("type", "mcp")
            icon_path = self._connector_icon_path(item)
            if icon_path is not None:
                # The URL used to be stable while the underlying source could
                # change from a generated placeholder to vendor artwork. Add a
                # cheap file fingerprint so browser caches cannot pin the old
                # image for the endpoint's full max-age.
                stat = icon_path.stat()
                version = f"{stat.st_mtime_ns:x}-{stat.st_size:x}"
                item["icon"] = f"/api/capabilities/{item['id']}/icon?v={version}"
            if cloud_item is not None:
                item.update(self._marketplace_requirements(cloud_item))
                item["_cloud_id"] = (
                    str(cloud_item.get("id") or "") if self._use_cloud_connector_installer else ""
                )
            else:
                item.setdefault("host_api", None)
                item.setdefault("permissions", [])
                item.setdefault("auth_modes", [])
                item.setdefault("dependencies", [])
                item.setdefault("runtime_dependencies", [])
            item.update(
                self._permission_projection(
                    str(item["id"]),
                    installed=bool(item.get("installed")),
                    required=item.get("permissions") or [],
                )
            )
            out.append(item)
        return out

    @staticmethod
    def _marketplace_requirements(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "host_api": str(item.get("host_api") or "").strip() or None,
            "permissions": list(item.get("permissions") or []),
            "auth_modes": list(item.get("auth_modes") or []),
            "dependencies": list(item.get("dependencies") or []),
            "runtime_dependencies": list(item.get("runtime_dependencies") or []),
        }

    @staticmethod
    def _cloud_marketplace_items(kind: str) -> dict[str, dict[str, Any]]:
        try:
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            return {
                str(item.get("plugin") or ""): item
                for item in CloudCatalog("plugins", use_cache=True, use_remote=False).items()
                if item.get("kind") == kind and str(item.get("plugin") or "").strip()
            }
        except Exception:  # noqa: BLE001 - local capability discovery remains usable
            return {}

    def _permission_projection(
        self,
        capability_id: str,
        *,
        installed: bool = False,
        required: Any = (),
    ) -> dict[str, Any]:
        try:
            record = self._permissions.get(capability_id)
            current = self._permissions.generation_current(capability_id)
        except RuntimeError:
            return {
                "permissions_granted": [],
                "permission_review_required": True,
                "permission_active": False,
            }
        if not isinstance(record, dict) or not current:
            return {
                "permissions_granted": [],
                "permission_review_required": bool(installed and list(required)),
                "permission_active": False,
            }
        required = list(record.get("required") or [])
        granted = list(record.get("granted") or [])
        return {
            "permissions_granted": granted,
            "permission_review_required": bool(
                record.get("installed") and set(required) != set(granted)
            ),
            "permission_active": record.get("active") is True,
        }

    def _ensure_permission_record(self, item: dict[str, Any]) -> dict[str, Any]:
        capability_id = str(item.get("id") or "")
        record = self._permissions.get(capability_id)
        if (
            isinstance(record, dict)
            and record.get("installed") is True
            and self._permissions.generation_current(capability_id)
        ):
            return record
        try:
            return self._permissions.stage_principal(capability_id)
        except KeyError:
            pass
        kind = "connector" if item.get("source") == "connector" else "codex"
        return self._permissions.stage(
            capability_id,
            kind=kind,
            required=item.get("permissions") or [],
            manifest_digest=str(item.get("version") or ""),
            runtime_sources=self._runtime_sources(item, kind=kind),
        )

    @staticmethod
    def _runtime_sources(item: dict[str, Any], *, kind: str) -> list[str]:
        capability_id = str(item.get("id") or "")
        sources = [f"plugin://{capability_id}/"] if kind == "codex" else []
        for server in item.get("mcp_servers") or []:
            name = (
                str(server.get("name") or "").strip()
                if isinstance(server, dict)
                else str(server).strip()
            )
            if name:
                sources.append(f"mcp://{name}/")
        return sources

    def _connector_icon_path(self, item: dict[str, Any]) -> Path | None:
        connector_id = _slug(str(item.get("id") or ""))

        # The WorkBuddy connector fork already ships the vendors' original
        # artwork. Keep that exact connector asset ahead of cross-source brand
        # matching and the generated storefront placeholder icons.
        for suffix in (".svg", ".png", ".webp", ".jpg", ".jpeg"):
            original = self._workbuddy_icon_root / f"{connector_id}{suffix}"
            if original.is_file():
                return original

        candidates: list[str] = []
        alias = _CONNECTOR_BRAND_ALIASES.get(connector_id)
        if alias:
            candidates.append(alias)
        for value in (
            item.get("provider_id"),
            connector_id,
            item.get("source"),
            item.get("name"),
        ):
            slug = _slug(str(value or ""))
            if slug and slug not in candidates:
                candidates.append(slug)
        for suffix in ("-connector", "-mcp", "-api", "-token", "-ai"):
            if connector_id.endswith(suffix):
                stripped = connector_id[: -len(suffix)]
                if stripped and stripped not in candidates:
                    candidates.append(stripped)

        for candidate in candidates:
            assets = self._native_icon_root / candidate / "assets"
            if not assets.is_dir():
                continue
            # Compact vector marks are ideal in dense marketplace rows and
            # avoid loading megabyte-sized app icons for a 32px thumbnail.
            for pattern in ("*-small.svg", "*icon*.svg"):
                match = next(iter(sorted(assets.glob(pattern))), None)
                if match is not None and match.is_file():
                    return match
            for filename in _ICON_PRIORITY:
                path = assets / filename
                if path.is_file():
                    return path
            for pattern in ("*icon*.png", "*.svg", "*.png"):
                match = next(iter(sorted(assets.glob(pattern))), None)
                if (
                    match is not None
                    and match.is_file()
                    and "dark" not in match.name
                    and "screenshot" not in match.name
                ):
                    return match

        for filename in (f"wb_{connector_id}.svg", f"wb_{connector_id}.png"):
            fallback = self._storefront_icon_root / filename
            if fallback.is_file():
                return fallback
        return None

    def icon_path(self, cid: str) -> Path | None:
        """Return a trusted local brand asset for a capability."""
        connector = next((value for value in self._list_connectors() if value["id"] == cid), None)
        if connector is not None:
            return self._connector_icon_path(connector)
        item = self.get(cid)
        raw = str((item or {}).get("_icon_path") or "")
        path = Path(raw) if raw else None
        return path if path is not None and path.is_file() else None

    def _list_codex_plugins(self) -> list[dict[str, Any]]:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        state = self._state()
        out = []
        cloud_by_plugin = self._cloud_marketplace_items("plugin")
        scanned = self._scan_codex_plugins()
        # 同 id 多版本缓存:保留版本号最大的(避免 chrome 等重复)
        scanned.sort(key=lambda mr: self._version_key(mr[0].get("version", "")), reverse=True)
        seen: set[str] = set()
        for manifest, root in scanned:
            pid = str(manifest.get("name") or root.name)
            if pid in seen:
                continue
            seen.add(pid)
            pid = str(manifest.get("name") or root.name)
            skills_dir = self._plugin_skills_dir(root, manifest)
            st = state.get(pid) or {}
            author = (
                (manifest.get("author") or {}).get("name", "")
                if isinstance(manifest.get("author"), dict)
                else str(manifest.get("author") or "")
            )
            iface = manifest.get("interface") or {}
            cloud_item = cloud_by_plugin.get(pid)
            requirements = self._marketplace_requirements(cloud_item or {})
            try:
                from runtime.platform.plugins.marketplace_package import (
                    load_marketplace_package_manifest,
                )

                requirements.update(
                    self._marketplace_requirements(
                        load_marketplace_package_manifest(root, package_kind="codex")
                    )
                )
            except (OSError, ValueError):
                pass
            raw_icon = str(iface.get("logo") or iface.get("composerIcon") or "")
            icon_path = (root / raw_icon).resolve() if raw_icon else None
            if icon_path is not None and root.resolve() not in icon_path.parents:
                icon_path = None
            row = {
                "id": pid,
                "name": str(iface.get("displayName") or pid),
                "name_zh": str(iface.get("displayName") or pid),
                "description": str(
                    iface.get("shortDescription") or manifest.get("description") or ""
                ),
                "description_zh": str(
                    iface.get("shortDescription") or manifest.get("description") or ""
                ),
                "type": "plugin",
                "auth_mode": "none",
                "source": "codex_plugin",
                "provider_id": pid,
                "author": author,
                "category": str(iface.get("category") or "plugin"),
                "icon": (
                    f"/api/capabilities/{pid}/icon"
                    if icon_path is not None and icon_path.is_file()
                    else raw_icon
                ),
                "surface_capabilities": [
                    str(value)
                    for value in (iface.get("capabilities") or [])
                    if isinstance(value, str) and "." in value
                ],
                "mcp_servers": [],
                "skill_count": self._skill_count(skills_dir),
                "examples_zh": [str(p) for p in (manifest.get("keywords") or [])[:3]],
                "installed": bool(st.get("installed")),
                "enabled": bool(st.get("enabled")),
                "connected": False,
                "version": str(manifest.get("version") or "1.0.0"),
                "_skills_dir": str(skills_dir) if skills_dir else None,
                "_cloud_id": str(cloud_item.get("id")) if cloud_item else None,
                "_icon_path": str(icon_path) if icon_path is not None else None,
                **requirements,
            }
            projection = self._permission_projection(
                pid,
                installed=bool(row.get("installed")),
                required=row.get("permissions") or [],
            )
            row.update(projection)
            if current_capability_scope() is not None:
                row["runtime_enabled"] = bool(st.get("enabled"))
                row["enabled"] = bool(st.get("enabled")) and bool(projection["permission_active"])
            out.append(row)
        for pid, cloud_item in cloud_by_plugin.items():
            if pid in seen:
                continue
            st = state.get(pid) or {}
            capabilities = cloud_item.get("capabilities") or []
            source_root = REPO_ROOT / "extensions" / "codex-plugins" / pid
            raw_icon = str(cloud_item.get("icon") or "")
            icon_path = (source_root / raw_icon).resolve() if raw_icon else None
            if icon_path is not None and source_root.resolve() not in icon_path.parents:
                icon_path = None
            row = {
                "id": pid,
                "name": str(cloud_item.get("name") or pid),
                "name_zh": str(cloud_item.get("name_zh") or cloud_item.get("name") or pid),
                "description": str(cloud_item.get("description") or ""),
                "description_zh": str(cloud_item.get("description") or ""),
                "type": "plugin",
                "auth_mode": "none",
                "source": "codex_plugin",
                "provider_id": pid,
                "author": str(cloud_item.get("author") or ""),
                "category": str(cloud_item.get("category") or "plugin"),
                "icon": (
                    f"/api/capabilities/{pid}/icon"
                    if icon_path is not None and icon_path.is_file()
                    else ""
                ),
                "surface_capabilities": [
                    str(value) for value in capabilities if isinstance(value, str) and "." in value
                ],
                "mcp_servers": [],
                "skill_count": len(cloud_item.get("skills") or []),
                "examples_zh": [],
                "installed": bool(st.get("installed")),
                "enabled": bool(st.get("enabled")),
                "connected": False,
                "version": str(cloud_item.get("version") or "1.0.0"),
                "_skills_dir": None,
                "_cloud_id": str(cloud_item.get("id") or ""),
                "_icon_path": str(icon_path) if icon_path is not None else None,
                **self._marketplace_requirements(cloud_item),
            }
            projection = self._permission_projection(
                pid,
                installed=bool(row.get("installed")),
                required=row.get("permissions") or [],
            )
            row.update(projection)
            if current_capability_scope() is not None:
                row["runtime_enabled"] = bool(st.get("enabled"))
                row["enabled"] = bool(st.get("enabled")) and bool(projection["permission_active"])
            out.append(row)
        return out

    @staticmethod
    def _version_key(version: str) -> tuple[int, ...]:
        parts = []
        for seg in version.replace("-", ".").split("."):
            if seg.isdigit():
                parts.append(int(seg))
            else:
                parts.append(0)
        return tuple(parts)

    # ── Codex 插件扫描 ──────────────────────────────────────
    def _scan_codex_plugins(self) -> list[tuple[dict[str, Any], Path]]:
        """遍历 ~/.echo/plugins/codex/<plugin>/.codex-plugin/plugin.json。

        首次调用会把旧 Codex 缓存(~/.codex/plugins/cache)同步进来,
        保证迁移后本地已有的插件仍能识别。
        """
        if not self._codex_cache.is_dir():
            if not self._codex_cache.exists():
                from runtime.platform.plugins.codex_discovery import (
                    sync_codex_cache_to_echo,
                )

                sync_codex_cache_to_echo(dest=self._codex_cache)
            if not self._codex_cache.is_dir():
                return []
        out = []
        for manifest_path in sorted(self._codex_cache.glob("*/.codex-plugin/plugin.json")):
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                continue
            if not isinstance(manifest, dict) or not manifest.get("name"):
                continue
            out.append((manifest, manifest_path.parent.parent))  # 插件根目录
        return out

    def _plugin_skills_dir(self, root: Path, manifest: dict[str, Any]) -> Path | None:
        rel = str(manifest.get("skills") or "").strip()
        if not rel:
            return None
        d = (root / rel).resolve()
        return d if d.exists() else None

    def _skill_count(self, skills_dir: Path | None) -> int:
        if not skills_dir or not skills_dir.exists():
            return 0
        return sum(1 for p in skills_dir.rglob("SKILL.md"))

    # ── 统一状态(仅 codex_plugin 用;连接器状态以 ConnectorRegistry 为准)────
    def _state(self) -> dict[str, Any]:
        return read_json_file(
            self._state_file,
            default_factory=dict,
            validate=_validate_state,
        )

    def _mutate_state(
        self,
        mutate: Callable[[dict[str, Any]], JsonMutation[Any]],
    ) -> Any:
        return mutate_json_file(
            self._state_file,
            default_factory=dict,
            validate=_validate_state,
            mutate=mutate,
        )

    def _set_state(self, cid: str, **fields: Any) -> None:
        def update(state: dict[str, Any]) -> JsonMutation[None]:
            state.setdefault(cid, {})["id"] = cid
            state[cid].update(fields)
            return JsonMutation(None)

        self._mutate_state(update)

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in item.items() if not k.startswith("_")}

    # ── 统一生命周期:安装/卸载/启停 ─────────────────────────
    def install(self, cid: str) -> dict[str, Any]:
        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] == "connector":
            if item.get("_cloud_id"):
                from runtime.platform.plugins.cloud_catalog import CloudCatalog

                result = CloudCatalog("plugins").install_plugin(cid, plugin_kind="connector")
                trust = dict(result.get("trust") or {})
                permission = self._permissions.stage(
                    cid,
                    kind="connector",
                    required=trust.get("permissions") or item.get("permissions") or [],
                    manifest_digest=str(trust.get("content_digest") or ""),
                    runtime_sources=self._runtime_sources(item, kind="connector"),
                )
                return {
                    **result,
                    "capability_id": cid,
                    "source": "connector",
                    "enabled": False,
                    "permission_review_required": bool(permission["required"]),
                    "permissions": list(permission["required"]),
                }
            conn = self._connectors.get(cid)
            if conn is None:
                raise KeyError(f"connector not found: {cid}")
            result = self._auth.run_connector_lifecycle(
                conn,
                lambda: self._connectors.install(cid),
            )
            permission = self._permissions.stage(
                cid,
                kind="connector",
                required=item.get("permissions") or [],
                runtime_sources=self._runtime_sources(item, kind="connector"),
            )
            return {
                **result,
                "permission_review_required": bool(permission["required"]),
                "permissions": list(permission["required"]),
            }
        return self._install_plugin(item)

    def uninstall(self, cid: str) -> bool:
        item = self.get(cid)
        if item is None:
            return False
        with contextlib.suppress(KeyError):
            self._permissions.set_active(cid, False)
        if item["source"] == "connector":
            conn = self._connectors.get(cid)
            if conn is None:
                return False
            removed = self._auth.run_connector_lifecycle(
                conn,
                lambda: self._connectors.uninstall(cid),
                cancel_device_flow=True,
            )
        else:
            removed = self._uninstall_plugin(cid)
        if removed:
            self._permissions.mark_uninstalled(cid)
        return bool(removed)

    def grant_permissions(self, cid: str, permissions: Any) -> dict[str, Any]:
        item = self.get(cid)
        if item is None or not item.get("installed"):
            raise KeyError(f"capability not installed: {cid}")
        self._ensure_permission_record(item)
        return self._permissions.grant(cid, permissions)

    def require_permissions(
        self,
        cid: str,
        permissions: Any = (),
        *,
        require_active: bool = False,
    ) -> dict[str, Any]:
        item = self.get(cid)
        if item is None or not item.get("installed"):
            raise ValueError(f"capability not installed: {cid}")
        self._ensure_permission_record(item)
        return self._permissions.require_granted(
            cid,
            permissions,
            require_active=require_active,
        )

    def set_enabled(
        self,
        cid: str,
        enabled: bool,
        *,
        revoke_credentials: bool | None = None,
    ) -> bool:
        item = self.get(cid)
        if item is None:
            return False
        if not item.get("installed"):
            return False
        self._ensure_permission_record(item)
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        if not enabled and current_capability_scope() is not None:
            with contextlib.suppress(KeyError):
                self._permissions.set_active(cid, False)
            return True
        if enabled:
            self._permissions.require_granted(cid)
        else:
            with contextlib.suppress(KeyError):
                self._permissions.set_active(cid, False)
        if item["source"] == "connector":
            conn = self._connectors.get(cid)
            if conn is None:
                return False
            changed = self._auth.run_connector_lifecycle(
                conn,
                lambda: self._connectors.set_enabled(cid, enabled),
                cancel_device_flow=(
                    not enabled if revoke_credentials is None else revoke_credentials
                ),
            )
            if changed and enabled:
                self._permissions.set_active(cid, True)
            return bool(changed)

        def update(state: dict[str, Any]) -> JsonMutation[bool]:
            current = state.get(cid)
            if not isinstance(current, dict) or not current.get("installed"):
                return JsonMutation(False, changed=False)
            current["enabled"] = bool(enabled)
            return JsonMutation(True)

        changed = bool(self._mutate_state(update))
        if changed and enabled:
            self._permissions.set_active(cid, True)
        return changed

    def _install_plugin(self, item: dict[str, Any]) -> dict[str, Any]:
        cid = item["id"]
        if item.get("_cloud_id") and not item.get("installed"):
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            result = CloudCatalog("plugins").install_plugin(cid, plugin_kind="codex")
            self._set_state(cid, installed=True, enabled=False, source="cloud")
            trust = dict(result.get("trust") or {})
            permission = self._permissions.stage(
                cid,
                kind="codex",
                required=trust.get("permissions") or item.get("permissions") or [],
                manifest_digest=str(trust.get("content_digest") or ""),
                runtime_sources=self._runtime_sources(item, kind="codex"),
            )
            return {
                **result,
                "capability_id": cid,
                "source": "codex_plugin",
                "type": "plugin",
                "auth_mode": "none",
                "enabled": False,
                "permission_review_required": bool(permission["required"]),
                "permissions": list(permission["required"]),
                "message": "插件已从远端安装，需确认权限后启用。",
            }
        skills_dir = Path(item["_skills_dir"]) if item.get("_skills_dir") else None
        copied = self._install_skills(cid, skills_dir)
        # Skill copies are the forward phase; the state commit comes last and
        # propagates failures, so the API cannot report a false successful install.
        self._set_state(cid, installed=True, enabled=False)
        permission = self._permissions.stage(
            cid,
            kind="codex",
            required=item.get("permissions") or [],
            runtime_sources=self._runtime_sources(item, kind="codex"),
        )
        return {
            "installed": True,
            "capability_id": cid,
            "source": "codex_plugin",
            "type": "plugin",
            "auth_mode": "none",
            "copied_skills": copied,
            "mcp_servers": [],
            "enabled": False,
            "permission_review_required": bool(permission["required"]),
            "permissions": list(permission["required"]),
            "message": f"已安装插件技能({len(copied)} 个)到 ~/.echo/skills。",
        }

    def _uninstall_plugin(self, cid: str) -> bool:
        item = self.get(cid)
        cloud_removed = False
        if item and item.get("_cloud_id") and item.get("installed"):
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            CloudCatalog("plugins").uninstall_plugin(cid, plugin_kind="codex")
            cloud_removed = True

        def remove(state: dict[str, Any]) -> JsonMutation[bool]:
            if cid not in state:
                return JsonMutation(False, changed=False)
            # Keep authoritative state until cleanup completes.  A partial
            # filesystem failure is recoverable by retry and is never reported
            # as a successful uninstall.
            for dest in self._skills_root.glob(f"{_slug(cid)}__*"):
                shutil.rmtree(dest)
            del state[cid]
            return JsonMutation(True)

        return bool(self._mutate_state(remove)) or cloud_removed

    def _install_skills(self, cid: str, skills_dir: Path | None) -> list[str]:
        if not skills_dir or not skills_dir.exists():
            return []
        self._skills_root.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            slug = _slug(f"{cid}__{skill_md.parent.name}")
            dest = self._skills_root / slug
            if dest.exists():
                copied.append(slug)
                continue
            shutil.copytree(skill_md.parent, dest)
            copied.append(slug)
        return copied

    # ── 认证编排(统一入口,连接器走 AuthOrchestrator)────────
    def status(self, cid: str) -> dict[str, Any]:
        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] == "connector":
            conn = self._connectors.get(cid)
            if conn is None:
                raise KeyError(f"connector not found: {cid}")
            st = self._auth.status(conn)
            # CLI 设备流:status 命令确认登录成功 → connected
            cli_conn = (st.get("cli_status") or {}).get("connected")
            if cli_conn:
                st["connected"] = True
            # MCP 型插件:任一 MCP server 已完成网页 OAuth 授权即视为已连接
            try:
                from runtime.adapters.mcp_client import oauth

                oauth_servers = [
                    name for name in conn.mcp_servers if oauth.get_oauth_store().has_tokens(name)
                ]
                if oauth_servers:
                    st["connected"] = True
                    st["oauth_servers"] = oauth_servers
            except Exception:  # noqa: BLE001 - OAuth 检查失败不阻断状态查询
                pass
            return st
        return {
            "capability_id": cid,
            "auth_mode": "none",
            "connected": False,
            "has_token": False,
            "stored_keys": [],
        }

    def connect(
        self, cid: str, *, tokens: dict[str, str] | None = None, run_cli: bool = False
    ) -> dict[str, Any]:
        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        self.require_permissions(cid)
        if item["source"] == "connector":
            conn = self._connectors.get(cid)
            if conn is None:
                raise KeyError(f"connector not found: {cid}")

            def connect_installed() -> dict[str, Any]:
                if cid not in self._connectors.installed_ids():
                    raise ValueError(f"capability not installed: {cid}")
                return self._auth.connect(conn, tokens=tokens, run_cli=run_cli)

            return self._auth.run_connector_lifecycle(conn, connect_installed)
        # 插件无需认证,视为已连接
        return {
            "capability_id": cid,
            "connected": True,
            "message": "插件无需认证,已就绪。",
        }

    def device_flow_status(self, cid: str) -> dict[str, Any]:
        """返回统一能力入口下的 CLI 设备流状态。

        Codex 插件不得借用同名 connector 会话；只有真实的
        connector capability 才能访问 AuthOrchestrator 中的进程。
        """

        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] != "connector":
            raise ValueError("capability does not support connector device flow")
        conn = self._connectors.get(cid)
        if conn is None:
            raise KeyError(f"connector not found: {cid}")
        return self._auth.device_flow_status(conn)

    def cancel_device_flow(
        self,
        cid: str,
        *,
        expected_flow_id: str,
    ) -> dict[str, Any]:
        """幂等取消统一能力入口下的 CLI 设备流。"""

        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] != "connector":
            raise ValueError("capability does not support connector device flow")
        conn = self._connectors.get(cid)
        if conn is None:
            raise KeyError(f"connector not found: {cid}")
        return self._auth.cancel_device_flow(
            conn,
            expected_flow_id=expected_flow_id,
        )

    def disconnect(self, cid: str) -> dict[str, Any]:
        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] == "connector":
            conn = self._connectors.get(cid)
            if conn is None:
                raise KeyError(f"connector not found: {cid}")
            return self._auth.disconnect(conn)
        return {"capability_id": cid, "connected": False}

    def resolve_headers(self, cid: str) -> dict[str, Any]:
        item = self.get(cid)
        if item is None:
            raise KeyError(f"capability not found: {cid}")
        if item["source"] == "connector":
            self.require_permissions(cid, ["account.credentials"])
            conn = self._connectors.get(cid)
            if conn is None:
                raise KeyError(f"connector not found: {cid}")
            return {"headers": self._auth.resolve_headers(conn)}
        return {"headers": {}}


def default_capability_registry() -> CapabilityRegistry:
    return CapabilityRegistry()
