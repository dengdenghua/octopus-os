"""连接器注册表 — 加载云目录/已安装定义 + 安装/启用状态。

产品运行时的数据源:
  1. 签名云目录中的轻量元数据(未安装连接器也可浏览)
  2. ``ECHO_DATA_DIR/plugins/connector/<id>`` 中按需安装的受信内容包

显式传入 ``marketplace_root`` 时仍支持旧 WorkBuddy 目录，供迁移工具、
发布流水线和隔离测试使用；桌面发行不再依赖或携带整座市场源码。

安装到:
  - skills   → ~/.echo/skills/<connector>__<skill>/  并登记 registry.json
  - MCP      → 合并进 echo MCP 配置(默认禁用,需显式启用)
  - state    → ~/.echo/connectors/state.json
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runtime.platform.io import JsonMutation, mutate_json_file, read_json_file
from runtime.platform.process.paths import app_paths, resources_root

CONNECTOR_ROOT = app_paths().data_dir / "connectors"
STATE_FILE = CONNECTOR_ROOT / "state.json"
_SLUG_RE = re.compile(r"[^a-z0-9_-]+", re.I)


def _slug(value: str) -> str:
    return _SLUG_RE.sub("-", value.strip()).strip("-").lower()


def _validate_state(data: Any) -> None:
    if not isinstance(data, dict):
        raise RuntimeError("connector state file must contain a JSON object")


def _validate_skill_registry(data: Any) -> None:
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise RuntimeError("skill registry file must contain a JSON array of objects")


class ConnectorDefinition:
    """规范化后的连接器定义(兼收 WorkBuddy / 我们的格式)。"""

    def __init__(
        self,
        *,
        id: str,
        name: str = "",
        name_zh: str = "",
        description: str = "",
        description_zh: str = "",
        type: str = "mcp",  # mcp | cli | plugin | skill-only | other
        auth_mode: str = "token",  # token | oauth | server-side | oneid-token | none
        source: str = "workbuddy",
        provider_id: str = "",
        mcp_servers: dict[str, Any] | None = None,
        cli: dict[str, Any] | None = None,
        model_provider: dict[str, Any] | None = None,
        skills_dir: Path | None = None,
        examples_zh: list[str] | None = None,
        examples_en: list[str] | None = None,
        visible_in: list[str] | None = None,
        min_version: str = "",
        version: str = "1.0.0",
    ) -> None:
        self.id = id
        self.name = name or id
        self.name_zh = name_zh or name or id
        self.description = description
        self.description_zh = description_zh or description
        self.type = type
        self.auth_mode = auth_mode
        self.source = source
        self.provider_id = provider_id
        self.mcp_servers = mcp_servers or {}
        self.cli = cli or {}
        self.model_provider = model_provider or {}
        self.skills_dir = skills_dir
        self.examples_zh = examples_zh or []
        self.examples_en = examples_en or []
        self.visible_in = visible_in or []
        self.min_version = min_version
        self.version = version

    def to_dict(self, *, installed: bool = False, enabled: bool = False) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "name_zh": self.name_zh,
            "description": self.description,
            "description_zh": self.description_zh,
            "type": self.type,
            "auth_mode": self.auth_mode,
            "source": self.source,
            "provider_id": self.provider_id,
            "mcp_servers": [
                {
                    "name": name,
                    "url": str(cfg.get("url", "")) if isinstance(cfg, dict) else "",
                }
                for name, cfg in self.mcp_servers.items()
            ],
            "model_provider": dict(self.model_provider) if self.model_provider else None,
            "skill_count": self.skill_count(),
            "examples_zh": self.examples_zh[:3],
            "installed": installed,
            "enabled": enabled,
            "version": self.version,
            # CLI 连接器是否带 auth 登录命令(设备流/网页授权码登录)。
            # 有 auth 命令 → 可跳网页登录,无需手动填 token。
            "has_cli_auth": bool(self.cli.get("auth")) or bool(self.cli.get("authDeviceFlow")),
        }

    def skill_count(self) -> int:
        if not self.skills_dir or not self.skills_dir.exists():
            return 0
        return sum(1 for p in self.skills_dir.rglob("SKILL.md"))


class ConnectorRegistry:
    """从云目录 + 已安装内容包加载连接器，并维护安装状态。"""

    def __init__(
        self,
        *,
        marketplace_root: str | Path | None = None,
        installed_root: str | Path | None = None,
        skills_root: str | Path | None = None,
        state_file: str | Path | None = None,
        permission_store: Any = None,
    ) -> None:
        # 显式 marketplace_root 是旧市场快照兼容入口。默认产品路径只把
        # 云目录当索引，把真正内容放入可写的数据目录。
        self._use_cloud_catalog = marketplace_root is None
        if marketplace_root is None:
            marketplace_root = resources_root() / "extensions" / "workbuddy-connectors"
        self._root = Path(marketplace_root)
        self._installed_root = Path(
            installed_root or (app_paths().data_dir / "plugins" / "connector")
        )
        self._skills_root = Path(skills_root or (app_paths().data_dir / "skills"))
        self._state_file = Path(state_file or STATE_FILE)
        if permission_store is None:
            from runtime.platform.capabilities.permission_grants import (
                CapabilityPermissionStore,
            )

            permission_path = (
                self._state_file.parent / "permission-grants.json"
                if state_file is not None
                else None
            )
            permission_store = CapabilityPermissionStore(permission_path)
        self._permissions = permission_store

    def _requirements(self, connector_id: str) -> dict[str, Any]:
        conn = self.get(connector_id)
        if conn is None:
            raise KeyError(f"connector not found: {connector_id}")
        from runtime.platform.plugins.marketplace_package import (
            derive_connector_package_requirements,
        )

        return derive_connector_package_requirements(
            {
                "type": conn.type,
                "auth_mode": conn.auth_mode,
            },
            package_dir=self._connector_dir(connector_id),
        )

    @staticmethod
    def _runtime_sources(conn: ConnectorDefinition) -> list[str]:
        return [f"mcp://{name}/" for name in sorted(conn.mcp_servers)]

    def _permission_projection(
        self,
        connector_id: str,
        *,
        installed: bool = False,
        required: Any = (),
    ) -> dict[str, Any]:
        record = self._permissions.get(connector_id)
        if not isinstance(record, dict) or not self._permissions.generation_current(connector_id):
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

    # ── 定义加载 ──────────────────────────────────────────────
    def _manifest(self) -> dict[str, Any]:
        if self._use_cloud_catalog:
            rows: list[dict[str, Any]] = []
            try:
                from runtime.platform.plugins.cloud_catalog import CloudCatalog

                for item in CloudCatalog("plugins").items():
                    if item.get("kind") != "connector":
                        continue
                    connector_id = str(item.get("plugin") or "").strip()
                    if not connector_id:
                        continue
                    rows.append(
                        {
                            "id": connector_id,
                            "name": str(item.get("name") or connector_id),
                            "name_zh": str(item.get("name_zh") or item.get("name") or connector_id),
                            "description": str(item.get("description") or ""),
                            "description_zh": str(
                                item.get("description_zh") or item.get("description") or ""
                            ),
                            "type": str(item.get("type") or item.get("category") or "mcp"),
                            "auth_mode": str(item.get("auth_mode") or "none"),
                            "source": str(item.get("source") or "workbuddy"),
                            "provider_id": str(item.get("provider_id") or ""),
                            "examples_zh": list(item.get("examples_zh") or []),
                            "examples_en": list(item.get("examples_en") or []),
                            "version": str(item.get("version") or "1.0.0"),
                        }
                    )
            except Exception:  # noqa: BLE001 - installed packages remain usable offline
                rows = []

            # A cached catalog is optional once a package is installed. Keep a
            # minimal offline row so authentication and uninstall still work.
            known = {str(row.get("id") or "") for row in rows}
            if self._installed_root.is_dir():
                for package in sorted(self._installed_root.iterdir()):
                    if not package.is_dir() or package.is_symlink() or package.name in known:
                        continue
                    rows.append(
                        {
                            "id": package.name,
                            "name": package.name,
                            "name_zh": package.name,
                            "source": "cloud",
                        }
                    )
            if rows:
                return {"connectors": rows}

            # Developer/migration fallback only. Packaged builds no longer
            # contain this directory, so it cannot silently become the product
            # marketplace again.
        manifest_path = self._root / ".codebuddy-connector" / "connectors.json"
        if not manifest_path.exists():
            return {"connectors": []}
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # noqa: BLE001
            return {"connectors": []}

    def _connector_dir(self, connector_id: str) -> Path:
        installed = self._installed_root / connector_id
        if installed.is_dir() and not installed.is_symlink():
            return installed
        return self._root / "connectors" / connector_id

    def _load_one(self, meta: dict[str, Any]) -> ConnectorDefinition | None:
        cid = str(meta.get("id") or "")
        if not cid:
            return None
        cdir = self._connector_dir(cid)
        mcp: dict[str, Any] = {}
        cli: dict[str, Any] = {}
        model_provider: dict[str, Any] = {}
        if (cdir / "mcp.json").exists():
            try:
                mcp = json.loads((cdir / "mcp.json").read_text(encoding="utf-8")).get(
                    "mcpServers", {}
                )
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                mcp = {}
        if (cdir / "cli.json").exists():
            try:
                cli = json.loads((cdir / "cli.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                cli = {}
        if (cdir / "model-provider.json").exists():
            try:
                loaded = json.loads((cdir / "model-provider.json").read_text(encoding="utf-8"))
                model_provider = loaded if isinstance(loaded, dict) else {}
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                model_provider = {}
        ctype = str(meta.get("type") or "")
        if not ctype:
            if cli:
                ctype = "cli"
            elif mcp:
                ctype = "mcp"
            else:
                ctype = "skill-only"
        return ConnectorDefinition(
            id=cid,
            name=str(meta.get("name") or cid),
            name_zh=str(meta.get("name_zh") or meta.get("name") or cid),
            description=str(meta.get("description") or ""),
            description_zh=str(meta.get("description_zh") or ""),
            type=ctype,
            auth_mode=str(meta.get("auth_mode") or ("none" if ctype == "skill-only" else "token")),
            source=str(meta.get("source") or "workbuddy"),
            provider_id=str(meta.get("provider_id") or ""),
            mcp_servers=mcp,
            cli=cli,
            model_provider=model_provider,
            skills_dir=cdir / "skills" if (cdir / "skills").exists() else None,
            examples_zh=meta.get("examples_zh") or [],
            examples_en=meta.get("examples_en") or [],
            visible_in=meta.get("visible_in") or [],
            min_version=str(meta.get("minWorkbuddyVersion") or ""),
            version=str(meta.get("version") or "1.0.0"),
        )

    def list(self) -> list[dict[str, Any]]:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        state = self._state()
        out = []
        for meta in self._manifest().get("connectors", []):
            conn = self._load_one(meta)
            if conn is None:
                continue
            st = state.get(conn.id) or {}
            item = conn.to_dict(
                installed=bool(st.get("installed")),
                enabled=bool(st.get("enabled")),
            )
            item.update(self._requirements(conn.id))
            projection = self._permission_projection(
                conn.id,
                installed=bool(st.get("installed")),
                required=item.get("permissions") or [],
            )
            item.update(projection)
            if current_capability_scope() is not None:
                item["runtime_enabled"] = bool(st.get("enabled"))
                item["enabled"] = bool(st.get("enabled")) and bool(projection["permission_active"])
            out.append(item)
        return out

    def get(self, connector_id: str) -> ConnectorDefinition | None:
        for meta in self._manifest().get("connectors", []):
            if str(meta.get("id")) == connector_id:
                return self._load_one(meta)
        return None

    # ── 状态 ──────────────────────────────────────────────────
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

    def _set_state(self, connector_id: str, **fields: Any) -> None:
        def update(state: dict[str, Any]) -> JsonMutation[None]:
            state.setdefault(connector_id, {})["id"] = connector_id
            state[connector_id].update(fields)
            return JsonMutation(None)

        self._mutate_state(update)

    # ── 安装 / 卸载 / 启停 ────────────────────────────────────
    def install(self, connector_id: str) -> dict[str, Any]:
        conn = self.get(connector_id)
        if conn is None:
            raise KeyError(f"connector not found: {connector_id}")
        if self._use_cloud_catalog and not (self._installed_root / connector_id).is_dir():
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            result = CloudCatalog("plugins").install_plugin(
                connector_id,
                plugin_kind="connector",
                dest_root=self._installed_root,
                enabled=False,
            )
            installed = self.get(connector_id)
            if installed is None or not (self._installed_root / connector_id).is_dir():
                raise RuntimeError(f"connector package did not materialize: {connector_id}")
            return {
                **result,
                "connector_id": connector_id,
                "type": installed.type,
                "auth_mode": installed.auth_mode,
                "mcp_servers": list(installed.mcp_servers),
                "enabled": False,
                "message": "连接器已从受信市场按需下载；授权后方可启用。",
            }
        copied = self._install_skills(conn)

        # Installation is filesystem-only. CLI detection/init/version checks may
        # execute local processes, so they are deferred until explicit grant.
        cli_life: dict[str, Any] = {"has_cli": bool(conn.cli)}
        if conn.cli:
            cli_life["deferred"] = True
        # Filesystem/CLI setup is the forward phase of this small saga.  The
        # authoritative state commit comes last and propagates any failure, so
        # callers can never receive an installed=true result without that state.
        self._set_state(connector_id, installed=True, enabled=False, installed_at=None)
        requirements = self._requirements(connector_id)
        permission = self._permissions.stage(
            connector_id,
            kind="connector",
            required=requirements["permissions"],
            manifest_digest=conn.version,
            runtime_sources=self._runtime_sources(conn),
        )
        if conn.model_provider:
            msg = "模型适配器已安装；填写服务商 API Key 后才会连接并检测可用模型。"
        elif conn.mcp_servers:
            msg = "已安装技能与 MCP 定义。MCP 默认禁用,连接后(connect)按需启用。"
        else:
            msg = "已安装技能(纯技能连接器无需 MCP)。"
        if cli_life.get("deferred"):
            msg = "CLI 准备将在确认本机进程权限后执行。" + msg
        return {
            "installed": True,
            "connector_id": connector_id,
            "type": conn.type,
            "auth_mode": conn.auth_mode,
            "copied_skills": copied,
            "mcp_servers": list(conn.mcp_servers.keys()),
            "cli_lifecycle": cli_life,
            "min_version": conn.min_version,
            "enabled": False,
            "permissions": list(permission["required"]),
            "permission_review_required": bool(permission["required"]),
            "message": msg,
        }

    def grant_permissions(self, connector_id: str, permissions: Any) -> dict[str, Any]:
        if connector_id not in self.installed_ids():
            raise KeyError(f"connector not installed: {connector_id}")
        if not self._permissions.generation_current(connector_id):
            try:
                self._permissions.stage_principal(connector_id)
            except KeyError:
                conn = self.get(connector_id)
                if conn is None:
                    raise KeyError(f"connector not found: {connector_id}") from None
                requirements = self._requirements(connector_id)
                self._permissions.stage(
                    connector_id,
                    kind="connector",
                    required=requirements["permissions"],
                    manifest_digest=conn.version,
                    runtime_sources=self._runtime_sources(conn),
                )
        return self._permissions.grant(connector_id, permissions)

    def require_permissions(
        self,
        connector_id: str,
        permissions: Any = (),
        *,
        require_active: bool = False,
    ) -> dict[str, Any]:
        return self._permissions.require_granted(
            connector_id,
            permissions,
            require_active=require_active,
        )

    def prepare_runtime(self, connector_id: str) -> dict[str, Any]:
        conn = self.get(connector_id)
        if conn is None:
            raise KeyError(f"connector not found: {connector_id}")
        if not conn.cli:
            return {"has_cli": False, "deferred": False}
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        if current_capability_scope() is not None:
            raise ValueError(
                "共享部署暂不允许启动使用主机级 HOME 的 CLI 连接器；"
                "请使用令牌/OAuth 连接器或管理员设备级连接。"
            )
        self.require_permissions(connector_id, ["process.local"])
        from runtime.platform.connectors import cli_lifecycle

        detection_before = cli_lifecycle.detect_command(conn)
        runtime_res = cli_lifecycle.check_runtime(conn)
        init_res = cli_lifecycle.run_init(conn, env=None)
        detection_after = cli_lifecycle.detect_command(conn)
        version_res = cli_lifecycle.check_version(conn)
        return {
            "has_cli": True,
            "deferred": False,
            "detection_before": detection_before,
            "detection": detection_after,
            "runtime": runtime_res,
            "init": init_res,
            "version": version_res,
            "auth_device_flow": bool(conn.cli.get("authDeviceFlow")),
            "min_version": str(conn.cli.get("versionCheck") or {}).strip()
            and ((conn.cli.get("versionCheck") or {}).get("minVersion") or ""),
        }

    def _install_skills(self, conn: ConnectorDefinition) -> list[str]:
        if not conn.skills_dir or not conn.skills_dir.exists():
            return []
        self._skills_root.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        registry_path = self._skills_root / "registry.json"
        additions: dict[str, dict[str, Any]] = {}

        for skill_md in sorted(conn.skills_dir.rglob("SKILL.md")):
            slug = _slug(f"{conn.id}__{skill_md.parent.name}")
            dest = self._skills_root / slug
            meta = {"name": slug, "author": f"workbuddy-connector:{conn.id}", "source": "connector"}
            if not dest.exists():
                shutil.copytree(skill_md.parent, dest)
                # A state-commit failure can leave this directory as a safe,
                # discoverable orphan; retrying install converges it forward.
                (dest / "meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=1), "utf-8"
                )
            additions[slug] = {
                "name": slug,
                "version": "0.1.0",
                "author": meta["author"],
                "description": f"WorkBuddy 连接器 {conn.id} 捆绑技能",
                "tags": [conn.id, "connector", "workbuddy"],
                "source": "connector",
            }
            copied.append(slug)

        def merge(registry: list[dict[str, Any]]) -> JsonMutation[None]:
            by_name = {entry.get("name"): entry for entry in registry}
            by_name.update(additions)
            registry[:] = list(by_name.values())
            return JsonMutation(None)

        mutate_json_file(
            registry_path,
            default_factory=list,
            validate=_validate_skill_registry,
            mutate=merge,
        )
        return copied

    def uninstall(self, connector_id: str) -> bool:
        conn = self.get(connector_id)

        if self._use_cloud_catalog and (self._installed_root / connector_id).is_dir():
            from runtime.platform.plugins.cloud_catalog import CloudCatalog

            removed = CloudCatalog("plugins").uninstall_plugin(
                connector_id,
                plugin_kind="connector",
            )
            if removed:
                self._permissions.mark_uninstalled(connector_id)
            return bool(removed)

        def remove(state: dict[str, Any]) -> JsonMutation[bool]:
            if connector_id not in state:
                return JsonMutation(False, changed=False)
            # Cleanup happens before the authoritative state deletion and errors
            # propagate.  Partial filesystem cleanup remains recoverable by a
            # later install; it is never reported as a successful uninstall.
            if conn is not None and conn.skills_dir:
                for skill_md in conn.skills_dir.rglob("SKILL.md"):
                    slug = _slug(f"{conn.id}__{skill_md.parent.name}")
                    dest = self._skills_root / slug
                    if dest.exists():
                        shutil.rmtree(dest)
                self._rebuild_registry(conn.id)
            del state[connector_id]
            return JsonMutation(True)

        removed = bool(self._mutate_state(remove))
        if removed:
            self._permissions.mark_uninstalled(connector_id)
        return removed

    def _rebuild_registry(self, removed_connector: str) -> None:
        registry_path = self._skills_root / "registry.json"

        def remove(registry: list[dict[str, Any]]) -> JsonMutation[None]:
            retained = [
                entry
                for entry in registry
                if entry.get("author") != f"workbuddy-connector:{removed_connector}"
            ]
            if len(retained) == len(registry):
                return JsonMutation(None, changed=False)
            registry[:] = retained
            return JsonMutation(None)

        mutate_json_file(
            registry_path,
            default_factory=list,
            validate=_validate_skill_registry,
            mutate=remove,
        )

    def set_enabled(self, connector_id: str, enabled: bool) -> bool:
        from runtime.platform.capabilities.tenant_context import (
            current_capability_scope,
        )

        if not enabled and current_capability_scope() is not None:
            if connector_id not in self.installed_ids():
                return False
            with contextlib.suppress(KeyError):
                self._permissions.set_active(connector_id, False)
            return True
        if enabled:
            self.require_permissions(connector_id)
            self.prepare_runtime(connector_id)
        else:
            with contextlib.suppress(KeyError):
                self._permissions.set_active(connector_id, False)

        def update(state: dict[str, Any]) -> JsonMutation[bool]:
            if connector_id not in state:
                return JsonMutation(False, changed=False)
            state[connector_id]["enabled"] = bool(enabled)
            return JsonMutation(True)

        changed = bool(self._mutate_state(update))
        if changed and enabled:
            self._permissions.set_active(connector_id, True)
        return changed

    def installed_ids(self) -> set[str]:
        return {cid for cid, st in self._state().items() if st.get("installed")}


__all__ = ["ConnectorRegistry", "ConnectorDefinition", "CONNECTOR_ROOT"]
