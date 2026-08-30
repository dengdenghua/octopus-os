"""云商城插件/技能目录源 —— 读发布到 GitHub Pages 的 plugin-store.json / skill-registry.json。

与 CloudExpertStore(专家)同构:远程 GitHub Pages 数据 + 本地镜像回退 + 磁盘缓存。
发布链路(把我们本地插件/技能带上云):
  extensions/workbuddy-experts/scripts/build-plugin-store.py   → plugin-store.json
  extensions/workbuddy-experts/scripts/build-skill-registry.py → skill-registry.json
  extensions/workbuddy-experts/scripts/publish-cloud.py        → 推到 gh-pages
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from runtime import __version__
from runtime.platform.io import atomic_write_json
from runtime.platform.io.transactional import path_transaction
from runtime.platform.plugins._secure_fetch import fetch_public_https_bytes
from runtime.platform.plugins.catalog_provenance import (
    catalog_signature_path,
    load_catalog_signature,
    verify_marketplace_catalog,
)
from runtime.platform.plugins.marketplace_package import (
    CONNECTOR_RELEASE_SUMMARY,
    compute_marketplace_content_provenance,
    verify_marketplace_package_trust,
)
from runtime.platform.plugins.workbench_activation import (
    WorkbenchActivationStore,
)
from runtime.platform.plugins.workbench_package import (
    WorkbenchPackageDataStore,
    WorkbenchPackageStore,
    verify_workbench_package_trust,
)
from runtime.platform.process.paths import app_paths

REPO = Path(__file__).resolve().parents[3]
LOCAL_MIRROR_DIR = REPO / "extensions" / "workbuddy-experts" / "storefront" / "data"
CACHE_DIR = app_paths().data_dir / "cache"

_REMOTE_BASE = os.environ.get(
    "ECHO_CLOUD_STORE_URL",
    "https://github.com/dengdenghua/workbuddy-expert-market/releases/download/echo-content",
)

_MAX_CATALOG_BYTES = 8 * 1024 * 1024
# The first-party plugin content pack is currently about 140 MiB. Keep enough
# headroom for reviewed built-ins to grow without turning remote reinstall into
# a server error, while retaining a strict bound below the extracted-size cap.
_MAX_ARCHIVE_BYTES = 192 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 10_000
_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
_MAX_MEMBER_BYTES = 64 * 1024 * 1024

_REMOTE_SURFACE_PLUGINS: tuple[dict[str, Any], ...] = (
    {
        "id": "codex_echo-recorder",
        "plugin": "echo-recorder",
        "source": "echo",
        "kind": "plugin",
        "name": "Echo Recorder",
        "name_zh": "REC 录制器",
        "description": "把真人示范和 Agent 操作沉淀为可复用流程",
        "category": "Productivity",
        "author": "EchoAI",
        "version": "1.2.0",
        "release_summary": "1.2.0：统一聊天与浏览器 REC 入口，新增敏感输入自动脱敏、断线降级和可恢复录制。",
        "icon": "./assets/recorder.svg",
        "capabilities": ["chat.recorder", "browser.recorder"],
    },
)

_WORKBENCH_APPS: tuple[dict[str, Any], ...] = (
    {
        "id": "workbench_paper-trading",
        "plugin": "paper-trading",
        "source": "echo",
        "kind": "workbench",
        "name": "Paper Trading",
        "name_zh": "模拟炒股",
        "description": "策略验证与模拟交易",
        "category": "workbench",
        "author": "Echo",
        "version": "1.0.0",
        "release_summary": "1.0.0：提供模拟交易、行情观察与策略验证工作台。",
        "runtime_plugin": "paper_trading",
        "removable": True,
        "data_policies": ["keep", "trash"],
    },
    {
        "id": "workbench_design",
        "plugin": "design",
        "source": "echo",
        "kind": "workbench",
        "name": "Design Canvas",
        "name_zh": "设计画布",
        "description": "视觉创作、素材编排与设计工作流",
        "category": "workbench",
        "author": "Echo",
        "version": "1.0.0",
        "release_summary": "1.0.0：提供视觉创作、素材编排与设计工作流画布。",
    },
    {
        "id": "workbench_narrative",
        "plugin": "narrative_studio",
        "source": "echo",
        "kind": "workbench",
        "name": "Narrative Studio",
        "name_zh": "叙事工坊",
        "description": "角色、世界观、剧情线与叙事资产的统一创作工作台",
        "category": "workbench",
        "author": "Echo",
        "version": "0.2.0",
        "release_summary": "0.2.0：支持角色、世界观、剧情分支与正典资产协作。",
        "runtime_plugin": "narrative_studio",
        "removable": True,
        "data_policies": ["keep", "trash"],
    },
    {
        "id": "workbench_self-evolution",
        "plugin": "self_evolution",
        "source": "echo",
        "kind": "workbench",
        "name": "Self Evolution",
        "name_zh": "自进化",
        "description": "双螺旋、候选基因、治理与审计",
        "category": "workbench",
        "author": "Echo",
        "version": "1.0.0",
        "release_summary": "1.0.0：提供候选基因、双螺旋演进、治理与审计界面。",
    },
    {
        "id": "workbench_intelligence",
        "plugin": "intelligence",
        "source": "echo",
        "kind": "workbench",
        "name": "Intelligence",
        "name_zh": "订阅",
        "description": "持续跟踪主题与情报",
        "category": "workbench",
        "author": "Echo",
        "version": "1.0.0",
        "release_summary": "1.0.0：提供主题订阅、持续跟踪与情报汇总入口。",
    },
    {
        "id": "workbench_community",
        "plugin": "community",
        "source": "echo",
        "kind": "workbench",
        "name": "Community",
        "name_zh": "发现社区",
        "description": "发现并复用社区工作流",
        "category": "workbench",
        "author": "Echo",
        "version": "1.0.0",
        "release_summary": "1.0.0：提供社区工作流发现、浏览与复用入口。",
    },
)

_FACTORY_WORKBENCH_APPS_BY_ID = {
    str(item["id"]): item for item in _WORKBENCH_APPS if item.get("factory_seed")
}
_FACTORY_WORKBENCH_PLUGINS = frozenset(
    str(item["plugin"]) for item in _FACTORY_WORKBENCH_APPS_BY_ID.values()
)


def _load_remote(name: str) -> dict[str, Any] | None:
    try:
        body = fetch_public_https_bytes(
            f"{_REMOTE_BASE.rstrip('/')}/{name}",
            timeout=15,
            max_bytes=_MAX_CATALOG_BYTES,
        )
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


class CloudCatalog:
    """读取云商城插件/技能目录(远程 + 本地镜像回退 + 磁盘缓存)。"""

    KIND_KEYS = {"plugins": "items", "skills": "skills"}

    def __init__(
        self,
        kind: str,
        *,
        use_cache: bool = True,
        use_remote: bool = True,
        trust_store_path: str | Path | None = None,
    ) -> None:
        if kind not in self.KIND_KEYS:
            raise ValueError(f"unknown cloud catalog kind: {kind}")
        self._kind = kind
        self._list_key = self.KIND_KEYS[kind]
        self._file = "plugin-store.json" if kind == "plugins" else "skill-registry.json"
        self._cache_file = CACHE_DIR / f"cloud-{self._file}"
        self._cache_signature_file = catalog_signature_path(self._cache_file)
        self._mirror = LOCAL_MIRROR_DIR / self._file
        self._mirror_signature = catalog_signature_path(self._mirror)
        self._use_cache = use_cache
        self._use_remote = use_remote
        self._trust_store_path = trust_store_path
        self._store: dict[str, Any] | None = None
        self._catalog_trust: dict[str, Any] | None = None
        self._force_remote_once = False

    @staticmethod
    def _read_catalog_file(path: Path) -> dict[str, Any] | None:
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > _MAX_CATALOG_BYTES:
                return None
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _accept_catalog(
        self,
        store: dict[str, Any] | None,
        envelope: dict[str, Any] | None,
        *,
        require_trusted: bool,
    ) -> dict[str, Any] | None:
        if not isinstance(store, dict) or not store.get(self._list_key):
            return None
        try:
            trust = verify_marketplace_catalog(
                store,
                envelope,
                catalog_name=self._file,
                trust_store_path=self._trust_store_path,
                require_trusted=require_trusted,
            )
        except ValueError:
            return None
        self._catalog_trust = trust
        return store

    def _load_mirror(self, *, require_trusted: bool) -> dict[str, Any] | None:
        return self._accept_catalog(
            self._read_catalog_file(self._mirror),
            load_catalog_signature(self._mirror_signature),
            require_trusted=require_trusted,
        )

    def _load_cache(self) -> dict[str, Any] | None:
        if not self._use_cache:
            return None
        return self._accept_catalog(
            self._read_catalog_file(self._cache_file),
            load_catalog_signature(self._cache_signature_file),
            require_trusted=True,
        )

    def _load_verified_remote(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        if not self._use_remote:
            return None
        store = _load_remote(self._file)
        signature_name = catalog_signature_path(Path(self._file)).name
        envelope = _load_remote(signature_name)
        accepted = self._accept_catalog(store, envelope, require_trusted=True)
        if accepted is None or not isinstance(envelope, dict):
            return None
        return accepted, envelope

    def _cache_verified_remote(
        self,
        store: dict[str, Any],
        envelope: dict[str, Any],
    ) -> None:
        if not self._use_cache:
            return
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write_json(self._cache_file, store, sort_keys=True)
            atomic_write_json(self._cache_signature_file, envelope, sort_keys=True)
        except OSError:
            # A failed cache write never changes the already verified in-memory
            # catalog, and a partial pair fails closed on the next process.
            return

    def _load(self) -> dict[str, Any]:
        if self._store is not None:
            return self._store
        store: dict[str, Any] | None = None
        source_checkout = (REPO / ".git").exists()
        prefer_remote = (
            self._force_remote_once or os.environ.get("ECHO_CLOUD_STORE_PREFER_REMOTE") == "1"
        )

        # A source checkout intentionally sees its freshly generated mirror.
        # Packaged builds never trust an unsigned mirror and prefer a verified
        # cache/remote release, so Hub refreshes can actually deliver updates.
        if source_checkout and not prefer_remote:
            store = self._load_mirror(require_trusted=False)
        if store is None:
            store = self._load_cache()
        if store is None:
            remote = self._load_verified_remote()
            if remote is not None:
                store, envelope = remote
                self._cache_verified_remote(store, envelope)
        if store is None:
            store = self._load_mirror(require_trusted=not source_checkout)
        if store is None:
            raise RuntimeError(f"cloud {self._kind} catalog unavailable or untrusted")
        self._store = store
        self._force_remote_once = False
        return store

    def refresh(self) -> None:
        self._store = None
        self._catalog_trust = None
        self._force_remote_once = True
        if self._use_cache and self._cache_file.exists():
            with contextlib.suppress(OSError):
                self._cache_file.unlink()
        if self._use_cache and self._cache_signature_file.exists():
            with contextlib.suppress(OSError):
                self._cache_signature_file.unlink()
        return self._load()

    def meta(self) -> dict[str, Any]:
        metadata = dict(self._load().get("meta") or {})
        if self._catalog_trust is not None:
            metadata["catalog_trust"] = dict(self._catalog_trust)
        return metadata

    def items(self) -> list[dict[str, Any]]:
        items = list(self._load().get(self._list_key) or [])
        if self._kind == "plugins":
            catalog_items: list[dict[str, Any]] = []
            for raw_item in items:
                if not isinstance(raw_item, dict):
                    continue
                item = dict(raw_item)
                if not str(item.get("release_summary") or "").strip():
                    version = str(item.get("version") or "").strip()
                    if item.get("kind") == "connector":
                        item["release_summary"] = CONNECTOR_RELEASE_SUMMARY
                    elif item.get("kind") == "plugin" and version:
                        item["release_summary"] = (
                            f"{version}：由 Echo 受信发布链重新封装当前插件内容。"
                        )
                catalog_items.append(item)
            items = catalog_items
            # Verified release catalogs are closed-world: runtime defaults may
            # not silently add unsigned packages. Source-checkout mirrors keep
            # lightweight fallbacks for local UI development only.
            allow_dev_defaults = (
                self._catalog_trust is None or self._catalog_trust.get("status") == "local_dev"
            )
            if allow_dev_defaults:
                # First-party descriptors are authoritative in a source/local
                # catalog. Replace any colliding unsigned catalog row instead
                # of leaving a shadow package ahead of the official workbench.
                by_id = {str(item.get("id") or ""): item for item in items}
                for official in (*_REMOTE_SURFACE_PLUGINS, *_WORKBENCH_APPS):
                    by_id[str(official["id"])] = dict(official)
                items = list(by_id.values())
        return items

    def list(
        self,
        *,
        search: str | None = None,
        kind: str | None = None,
        offset: int = 0,
        limit: int = 200,
    ) -> dict[str, Any]:
        items = self.items()
        if kind:
            items = [i for i in items if i.get("kind") == kind]
        if search:
            q = search.lower()
            items = [
                i
                for i in items
                if q in str(i.get("name", "")).lower()
                or q in str(i.get("name_zh", "")).lower()
                or q in str(i.get("description", "")).lower()
                or q in str(i.get("id", "")).lower()
            ]
        total = len(items)
        return {"items": items[offset : offset + limit], "total": total}

    # ── 内容包下载 + 安装(从云端下载内容,解包落地) ──────────
    CONTENT_URLS = {
        "plugins": (
            "https://github.com/dengdenghua/workbuddy-expert-market/releases/download/"
            "echo-content/echo-plugins.tar.gz"
        ),
        "skills": (
            "https://github.com/dengdenghua/workbuddy-expert-market/releases/download/"
            "echo-content/echo-skills.tar.gz"
        ),
    }

    def _archive_path(self) -> Path:
        """下载并缓存内容包 tar.gz,返回本地路径。"""
        url = os.environ.get(
            "ECHO_PLUGINS_CONTENT_URL" if self._kind == "plugins" else "ECHO_SKILLS_CONTENT_URL",
            self.CONTENT_URLS[self._kind],
        )
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / f"echo-{self._kind}.tar.gz"
        # 已缓存且非空 → 直接用
        if dest.exists() and dest.stat().st_size > 0:
            if dest.stat().st_size > _MAX_ARCHIVE_BYTES:
                raise ValueError("cached marketplace archive is too large")
            return dest
        tmp = dest.with_suffix(".part")
        try:
            body = fetch_public_https_bytes(
                url,
                timeout=180,
                max_bytes=_MAX_ARCHIVE_BYTES,
            )
            tmp.write_bytes(body)
            tmp.replace(dest)
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()
        return dest

    def _package_archive(
        self,
        item: dict[str, Any],
        *,
        package_kind: str,
        package_id: str,
    ) -> Path:
        """Fetch a connector's own archive, falling back to legacy shared packs."""

        url = str(item.get("download_url") or "").strip()
        shared_url = os.environ.get("ECHO_PLUGINS_CONTENT_URL", self.CONTENT_URLS["plugins"])
        if package_kind != "connector" or not url or url == shared_url:
            return self._archive_path()

        safe = re.sub(r"[^A-Za-z0-9_-]", "_", package_id).strip("_")
        if not safe or safe != package_id:
            raise ValueError(f"unsafe marketplace package id: {package_id!r}")
        version = str(item.get("version") or "").strip()
        fingerprint = hashlib.sha256(f"{url}\0{version}".encode("utf-8")).hexdigest()[:16]
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = CACHE_DIR / f"connector-{safe}-{fingerprint}.tar.gz"
        if dest.is_file() and 0 < dest.stat().st_size <= _MAX_ARCHIVE_BYTES:
            return dest
        temporary = dest.with_suffix(".part")
        try:
            body = fetch_public_https_bytes(url, timeout=180, max_bytes=_MAX_ARCHIVE_BYTES)
            temporary.write_bytes(body)
            temporary.replace(dest)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink()
        return dest

    @staticmethod
    def _extract_member(
        archive: Path, member_prefix: str, dest_dir: Path, member_name: str
    ) -> Path | None:
        """从 tar.gz 流式解出 prefix 下的单个目录，返回解出目录或 None。"""
        prefix = f"{member_prefix.rstrip('/')}/{member_name}"
        out = dest_dir / member_name
        out_res = out.resolve()
        extracted_bytes = 0
        matched_members = 0
        try:
            # Streaming mode avoids materializing metadata for every unrelated
            # app in the shared content pack. The Browser package alone lives
            # in a ~140 MiB archive, and ``getmembers()`` previously pushed the
            # long-running server close to 1 GiB RSS during a reinstall.
            with tarfile.open(archive, "r|gz") as tf:
                for m in tf:
                    if m.name != prefix and not m.name.startswith(prefix + "/"):
                        continue
                    matched_members += 1
                    if matched_members > _MAX_ARCHIVE_MEMBERS:
                        raise ValueError("marketplace archive contains too many members")
                    # 安全校验:每个成员相对 prefix 的路径必须落在 out 内。
                    # Links/devices/FIFOs are not needed by skills or plugins
                    # and are rejected before their contents are copied.
                    if "\\" in m.name or "\x00" in m.name:
                        raise ValueError(f"unsafe tar path: {m.name}")
                    if not (m.isdir() or m.isreg()):
                        raise ValueError(f"unsupported tar member: {m.name}")
                    rel = os.path.relpath(m.name, prefix)
                    target = (out / rel).resolve()
                    if out_res not in target.parents and target != out_res:
                        raise ValueError(f"unsafe tar path: {m.name}")
                    if m.isreg():
                        if m.size < 0 or m.size > _MAX_MEMBER_BYTES:
                            raise ValueError(f"tar member is too large: {m.name}")
                        extracted_bytes += m.size
                        if extracted_bytes > _MAX_EXTRACTED_BYTES:
                            raise ValueError("marketplace archive expands beyond the size limit")
                    if m.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = tf.extractfile(m)
                    if source is None:
                        raise ValueError(f"tar member has no file content: {m.name}")
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    target.chmod(0o755 if m.mode & 0o111 else 0o644)
            if matched_members == 0:
                return None
            return out
        except Exception:
            # A late invalid member must not leave a partially installed tree.
            with contextlib.suppress(OSError):
                shutil.rmtree(out)
            raise

    def install_skill(self, name: str, *, skills_dir: str | Path | None = None) -> dict[str, Any]:
        """下载技能内容包,把 skills/<name> 落地到技能目录。"""
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_") or "skill"
        dest_root = Path(skills_dir or self.SKILLS_ROOT)
        dest_root.mkdir(parents=True, exist_ok=True)
        target = dest_root / safe
        if (target / "SKILL.md").exists():
            return {"installed": True, "already_exists": True, "name": safe, "path": str(target)}
        with tempfile.TemporaryDirectory(prefix="echo-skill-") as tmp:
            extracted = self._extract_member(self._archive_path(), "skills", Path(tmp), safe)
            if extracted is None or not (extracted / "SKILL.md").exists():
                raise KeyError(f"skill not found in content pack: {name}")
            if any(child.is_symlink() for child in extracted.rglob("*")):
                raise ValueError(f"skill contains symlinks: {name}")
            shutil.copytree(extracted, target)
        return {"installed": True, "name": safe, "path": str(target), "source": "cloud"}

    # All mutable deployment state follows ECHO_DATA_DIR. In the container
    # that is the /data PVC/bind mount, never the read-only image layer.
    PLUGIN_INSTALL_ROOT = app_paths().data_dir / "plugins"
    # Cloud-catalog skills are mutable runtime state under the data volume.
    SKILLS_ROOT = app_paths().data_dir / "skills"
    # Codex-format plugins use the same writable path as registry consumers.
    CODEX_CACHE_ROOT = app_paths().codex_plugins_path
    # 连接器安装状态(与 connector_registry 的 state.json 同文件,标记已安装)
    CONNECTOR_STATE_FILE = app_paths().data_dir / "connectors" / "state.json"
    CAPABILITY_STATE_FILE = app_paths().data_dir / "capabilities" / "state.json"

    @staticmethod
    def _slug(value: str) -> str:
        return re.sub(r"[^a-z0-9_-]+", "-", value.strip()).strip("-").lower()

    def _marketplace_transaction_lock_path(self) -> Path:
        return self.PLUGIN_INSTALL_ROOT / ".lifecycle" / "marketplace-transaction"

    def installed_skills(self, skills_dir: str | Path | None = None) -> list[str]:
        """本地已安装技能名(目录含 SKILL.md)。"""
        root = Path(skills_dir or self.SKILLS_ROOT)
        if not root.exists():
            return []
        return sorted(
            d.name
            for d in root.iterdir()
            if d.is_dir() and (d / "SKILL.md").exists() and not d.name.startswith((".", "_"))
        )

    @staticmethod
    def is_factory_plugin(plugin_id: str) -> bool:
        """Return whether *plugin_id* is a lifecycle-managed factory seed.

        The catalog also contains remote workbenches whose runtime code is
        bundled only as a source-checkout convenience.  Keeping this check in
        the catalog prevents a stale ``factory_seed`` field from routing a
        remote package through PluginHub's factory-only lifecycle API.
        """

        return str(plugin_id).strip() in _FACTORY_WORKBENCH_PLUGINS

    def installed_plugins(self) -> list[str]:
        """本地已安装插件/连接器(按存档成员名)。

        来源合并四处,保证线上商城对「本地已有」的项直接标已安装:
          1. 随应用发布且已激活的工厂工作台
          2. 云安装落点 ~/.echo/plugins/<kind>/<id>(本功能装的)
          3. Codex 格式插件 ~/.echo/plugins/codex/<plugin>/.codex-plugin/plugin.json
             (首次自动从旧 ~/.codex/plugins/cache 同步)
          4. 连接器安装状态 ~/.echo/connectors/state.json(installed=true)
        """
        with path_transaction(self._marketplace_transaction_lock_path()):
            self._recover_incomplete_marketplace_transactions()
        names: set[str] = set()
        activation_store = self._workbench_activation_store()
        for plugin_id in _FACTORY_WORKBENCH_PLUGINS:
            if activation_store.state(plugin_id)["installed"]:
                names.add(plugin_id)
        root = self.PLUGIN_INSTALL_ROOT
        if root.exists():
            for kind_dir in root.iterdir():
                if kind_dir.is_dir() and not kind_dir.name.startswith((".", "_")):
                    for d in kind_dir.iterdir():
                        if d.is_dir() and not (
                            kind_dir.name == "workbench" and d.name in _FACTORY_WORKBENCH_PLUGINS
                        ):
                            names.add(d.name)
        codex_cache = self.CODEX_CACHE_ROOT
        if not codex_cache.is_dir():
            from runtime.platform.plugins.codex_discovery import (
                sync_codex_cache_to_echo,
            )

            sync_codex_cache_to_echo(dest=codex_cache)
        if codex_cache.is_dir():
            for manifest in codex_cache.glob("*/.codex-plugin/plugin.json"):
                try:
                    meta = json.loads(manifest.read_text("utf-8"))
                    pid = str(meta.get("name") or "")
                except (OSError, json.JSONDecodeError):  # noqa: BLE001
                    continue
                if pid:
                    names.add(pid)
        if self.CONNECTOR_STATE_FILE.exists():
            try:
                state = json.loads(self.CONNECTOR_STATE_FILE.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                state = {}
            for cid, v in state.items():
                if isinstance(v, dict) and v.get("installed"):
                    names.add(cid)
        return sorted(names)

    def plugin_statuses(self) -> dict[str, dict[str, Any]]:
        """Return the unified lifecycle projection for catalog plugins.

        Keys are install-package ids (the ``plugin`` field consumed by the
        frontend). Catalog ids remain present in each row so callers never have
        to infer whether an item came from a factory seed or a mutable cloud
        package. Transient downloading/uninstalling phases are owned by the
        request/UI operation; this projection is the durable post-operation
        truth.
        """

        if self._kind != "plugins":
            return {}
        installed = set(self.installed_plugins())
        activation_store = self._workbench_activation_store()
        data_store = self._workbench_data_store()
        permission_store = self._marketplace_permission_store()
        statuses: dict[str, dict[str, Any]] = {}
        for item in self.items():
            package_id = str(item.get("plugin") or item.get("id") or "").strip()
            if not package_id:
                continue
            catalog_id = str(item.get("id") or package_id)
            kind = str(item.get("kind") or "connector")
            if bool(item.get("factory_seed")) and activation_store.is_factory(package_id):
                state = activation_store.state(package_id)
                if state.get("error"):
                    lifecycle_state = "broken"
                elif not state["installed"]:
                    lifecycle_state = "available"
                elif not state["enabled"]:
                    lifecycle_state = "disabled"
                else:
                    lifecycle_state = "enabled"
                statuses[package_id] = {
                    "plugin_id": package_id,
                    "catalog_id": catalog_id,
                    "kind": kind,
                    "source": "factory",
                    "installed": bool(state["installed"]),
                    "enabled": bool(state["enabled"]),
                    "lifecycle_state": lifecycle_state,
                    "version": state.get("version") or item.get("version"),
                    "path": state.get("factory_path"),
                    "data_path": state.get("data_path"),
                    "recoveries": state.get("recoveries", []),
                    "error": state.get("error"),
                    "trust": {
                        "level": "system",
                        "integrity_verified": False,
                        "publisher_verified": False,
                    },
                    "compatibility": {
                        "status": "compatible",
                        "host_api": None,
                    },
                    "permissions_granted": [],
                    "permission_review_required": False,
                    "permission_active": bool(state["enabled"]),
                    "release_summary": str(item.get("release_summary") or "").strip(),
                }
                continue

            is_installed = package_id in installed
            install_kind = (
                "codex" if kind == "plugin" else "workbench" if kind == "workbench" else "connector"
            )
            lifecycle_state = "enabled" if is_installed else "available"
            enabled = (
                self._workbench_package_enabled(package_id)
                if is_installed and install_kind == "workbench"
                else self._marketplace_package_enabled(
                    package_id,
                    plugin_kind=install_kind,
                )
                if is_installed and install_kind in {"codex", "connector"}
                else is_installed
            )
            if is_installed and not enabled:
                lifecycle_state = "disabled"
            error = None
            installed_version: str | None = None
            release_summary = str(item.get("release_summary") or "").strip()
            trust: dict[str, Any] = {
                "level": "unverified" if is_installed else "catalog",
                "integrity_verified": False,
                "publisher_verified": False,
            }
            compatibility: dict[str, Any] = {
                "status": "not_checked",
                "host_api": None,
            }
            requirements: dict[str, Any] = {
                "permissions": list(item.get("permissions") or []),
                "auth_modes": list(item.get("auth_modes") or []),
                "dependencies": list(item.get("dependencies") or []),
                "runtime_dependencies": list(item.get("runtime_dependencies") or []),
                "connectors": list(item.get("connectors") or []),
            }
            if is_installed and install_kind == "workbench":
                try:
                    package_store = WorkbenchPackageStore(self.PLUGIN_INSTALL_ROOT / "workbench")
                    manifest = package_store.load_manifest(package_id)
                    trust_record = package_store.verify_installed_integrity(package_id)
                    installed_version = manifest.version
                    release_summary = manifest.release_summary
                except (FileNotFoundError, ValueError) as exc:
                    lifecycle_state = "broken"
                    enabled = False
                    error = str(exc)
                else:
                    publisher_verified = trust_record.get("publisher_verified") is True
                    trust = {
                        "level": "publisher" if publisher_verified else "local_integrity",
                        "integrity_verified": True,
                        "publisher_verified": publisher_verified,
                    }
                    publisher_id = str(trust_record.get("publisher_id") or "").strip()
                    if publisher_id:
                        trust["publisher_id"] = publisher_id
                    try:
                        self._validate_workbench_compatibility(
                            manifest,
                            install_root=self.PLUGIN_INSTALL_ROOT / "workbench",
                        )
                    except (FileNotFoundError, ValueError) as exc:
                        lifecycle_state = "broken"
                        enabled = False
                        error = str(exc)
                        compatibility = {
                            "status": "incompatible",
                            "host_api": manifest.host_api,
                        }
                    else:
                        compatibility = {
                            "status": "compatible",
                            "host_api": manifest.host_api,
                        }
                        catalog_version = str(item.get("version") or "").strip()
                        if (
                            enabled
                            and catalog_version
                            and self._version_is_newer(
                                catalog_version,
                                installed_version,
                            )
                        ):
                            lifecycle_state = "update_available"
            elif is_installed and install_kind in {"codex", "connector"}:
                trust_record: dict[str, Any] | None = None
                try:
                    package_path = self.PLUGIN_INSTALL_ROOT / install_kind / package_id
                    if install_kind == "codex" and not package_path.is_dir():
                        package_path = self.CODEX_CACHE_ROOT / package_id
                    trust_record = verify_marketplace_package_trust(
                        package_path,
                        package_kind=install_kind,
                        plugin_id=package_id,
                        require_trusted=not (REPO / ".git").exists(),
                    )
                    installed_version = str(trust_record["version"])
                    release_summary = str(trust_record.get("release_summary") or "").strip()
                    self._verify_marketplace_skill_projection(
                        package_path,
                        plugin_id=package_id,
                    )
                except (FileNotFoundError, ValueError) as exc:
                    lifecycle_state = "broken"
                    enabled = False
                    error = str(exc)
                else:
                    publisher_verified = trust_record.get("publisher_verified") is True
                    trust = {
                        "level": "publisher" if publisher_verified else "local_integrity",
                        "integrity_verified": True,
                        "publisher_verified": publisher_verified,
                    }
                    publisher_id = str(trust_record.get("publisher_id") or "").strip()
                    if publisher_id:
                        trust["publisher_id"] = publisher_id
                    requirements = {
                        "permissions": list(trust_record.get("permissions") or []),
                        "auth_modes": list(trust_record.get("auth_modes") or []),
                        "dependencies": list(trust_record.get("dependencies") or []),
                        "runtime_dependencies": list(
                            trust_record.get("runtime_dependencies") or []
                        ),
                        "connectors": list(item.get("connectors") or []),
                    }
                    try:
                        self._validate_marketplace_compatibility(
                            trust_record,
                            plugin_id=package_id,
                        )
                    except ValueError as exc:
                        lifecycle_state = "broken"
                        enabled = False
                        error = str(exc)
                        compatibility = {
                            "status": "incompatible",
                            "host_api": trust_record.get("host_api"),
                        }
                    else:
                        compatibility = {
                            "status": "compatible",
                            "host_api": trust_record.get("host_api"),
                        }
                    catalog_version = str(item.get("version") or "").strip()
                    if (
                        lifecycle_state != "broken"
                        and enabled
                        and catalog_version
                        and self._version_is_newer(catalog_version, installed_version)
                    ):
                        lifecycle_state = "update_available"
            rollback = None
            if is_installed:
                if install_kind == "workbench":
                    rollback = self._latest_workbench_transaction(package_id)
                elif install_kind in {"codex", "connector"}:
                    rollback = self._latest_marketplace_transaction(
                        package_id,
                        plugin_kind=install_kind,
                    )
            permission_granted: list[str] = []
            permission_active = False
            permission_review_required = False
            if is_installed and install_kind in {"codex", "connector"}:
                try:
                    permission_record = permission_store.get(package_id)
                    permission_generation_current = permission_store.generation_current(package_id)
                except RuntimeError:
                    permission_record = None
                    permission_generation_current = False
                    permission_review_required = True
                if isinstance(permission_record, dict) and permission_generation_current:
                    permission_granted = list(permission_record.get("granted") or [])
                    permission_active = permission_record.get("active") is True
                    permission_review_required = set(requirements["permissions"]) != set(
                        permission_granted
                    )
                else:
                    permission_review_required = True
            statuses[package_id] = {
                "plugin_id": package_id,
                "catalog_id": catalog_id,
                "kind": kind,
                "source": "cloud",
                "installed": is_installed,
                "enabled": enabled,
                "lifecycle_state": lifecycle_state,
                "version": installed_version or item.get("version"),
                "available_version": item.get("version"),
                "path": str(self.PLUGIN_INSTALL_ROOT / install_kind / package_id)
                if is_installed
                else None,
                "data_path": None,
                "recoveries": data_store.recoveries(package_id)
                if install_kind == "workbench"
                else [],
                "error": error,
                "runtime_plugin": item.get("runtime_plugin"),
                "rollback_available": rollback is not None,
                "transaction_id": rollback.get("transaction_id") if rollback else None,
                "rollback_operation": rollback.get("operation") if rollback else None,
                "trust": trust,
                "compatibility": compatibility,
                **requirements,
                "permissions_granted": permission_granted,
                "permission_review_required": permission_review_required,
                "permission_active": permission_active,
                "release_summary": release_summary,
            }
        return statuses

    def set_workbench_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Persistently activate/deactivate a frontend-only workbench package."""

        safe = WorkbenchPackageStore.validate_id(plugin_id)
        root = self.PLUGIN_INSTALL_ROOT / "workbench"
        WorkbenchPackageStore(root, require_integrity=True).load_manifest(safe)
        if not enabled:
            self._ensure_workbench_not_required(safe, enabled_only=True)
        state = self._workbench_package_state()
        state[safe] = bool(enabled)
        atomic_write_json(self._workbench_package_state_path(), state, sort_keys=True)
        return {
            "plugin_id": safe,
            "installed": True,
            "enabled": bool(enabled),
            "lifecycle_state": "enabled" if enabled else "disabled",
            "restart_required": False,
        }

    def _copy_bundled_skills(self, plugin_dir: Path, plugin_id: str) -> list[str]:
        """把插件捆绑的 skills/ 复制到 ~/.echo/skills/<id>__<skill> 并登记。"""
        skills_dir = plugin_dir / "skills"
        if not skills_dir.exists():
            return []
        skills_root = self.SKILLS_ROOT
        skills_root.mkdir(parents=True, exist_ok=True)
        registry_path = skills_root / "registry.json"
        registry: list[dict[str, Any]] = []
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                registry = []
        by_name = {e.get("name"): e for e in registry}
        copied: list[str] = []
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            slug = self._slug(f"{plugin_id}__{skill_md.parent.name}")
            dest = skills_root / slug
            if dest.exists():
                copied.append(slug)
                continue
            shutil.copytree(skill_md.parent, dest)
            meta = {"name": slug, "author": f"cloud-plugin:{plugin_id}", "source": "cloud"}
            (dest / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), "utf-8")
            by_name[slug] = {
                "name": slug,
                "version": "0.1.0",
                "author": meta["author"],
                "description": f"云插件 {plugin_id} 捆绑技能",
                "tags": [plugin_id, "cloud"],
                "source": "cloud",
            }
            copied.append(slug)
        atomic_write_json(registry_path, list(by_name.values()), sort_keys=True)
        return copied

    def _bundled_skill_ids(self, plugin_id: str) -> set[str]:
        prefix = f"{plugin_id}__"
        if not self.SKILLS_ROOT.is_dir():
            return set()
        return {
            candidate.name
            for candidate in self.SKILLS_ROOT.iterdir()
            if candidate.is_dir() and candidate.name.startswith(prefix)
        }

    def _remove_bundled_skill_ids(self, skill_ids: set[str]) -> None:
        if not skill_ids:
            return
        for skill_id in skill_ids:
            target = self.SKILLS_ROOT / skill_id
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
        registry_path = self.SKILLS_ROOT / "registry.json"
        try:
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return
        if isinstance(registry, list):
            filtered = [
                entry
                for entry in registry
                if not isinstance(entry, dict) or str(entry.get("name") or "") not in skill_ids
            ]
            atomic_write_json(registry_path, filtered, sort_keys=True)

    def _skill_registry(self) -> list[dict[str, Any]]:
        path = self.SKILLS_ROOT / "registry.json"
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("skill registry is invalid") from exc
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise ValueError("skill registry is invalid")
        return payload

    @staticmethod
    def _marketplace_skill_record(plugin_id: str, skill_id: str) -> dict[str, Any]:
        return {
            "name": skill_id,
            "version": "0.1.0",
            "author": f"cloud-plugin:{plugin_id}",
            "description": f"云插件 {plugin_id} 捆绑技能",
            "tags": [plugin_id, "cloud"],
            "source": "cloud",
        }

    def _commit_marketplace_skill_generation(
        self,
        plugin_dir: Path,
        *,
        plugin_id: str,
        transaction_id: str,
    ) -> dict[str, Any]:
        """Atomically replace the plugin-owned skill namespace."""

        if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
            raise ValueError("invalid transaction_id")
        self.SKILLS_ROOT.mkdir(parents=True, exist_ok=True)
        lifecycle = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
        staging = lifecycle / "staging"
        backup = lifecycle / "backup"
        if lifecycle.exists():
            raise FileExistsError(f"marketplace skill transaction already exists: {transaction_id}")
        desired: dict[str, Path] = {}
        skills_dir = plugin_dir / "skills"
        if skills_dir.is_dir():
            for skill_md in sorted(skills_dir.rglob("SKILL.md")):
                skill_id = self._slug(f"{plugin_id}__{skill_md.parent.name}")
                if skill_id in desired:
                    raise ValueError(f"duplicate bundled skill identity: {skill_id}")
                desired[skill_id] = skill_md.parent
                if len(desired) > 256:
                    raise ValueError("marketplace package contains too many bundled skills")
        previous_ids = self._bundled_skill_ids(plugin_id)
        for skill_id in previous_ids:
            candidate = self.SKILLS_ROOT / skill_id
            if candidate.is_symlink():
                raise ValueError(f"bundled skill target cannot be a symlink: {skill_id}")
        registry = self._skill_registry()
        prefix = f"{plugin_id}__"
        previous_registry = [
            dict(row) for row in registry if str(row.get("name") or "").startswith(prefix)
        ]
        previous_registry_ids = [str(row.get("name") or "") for row in previous_registry]
        if set(previous_registry_ids) != previous_ids or len(previous_registry_ids) != len(
            previous_ids
        ):
            raise ValueError("bundled skill registry does not match installed skill directories")
        other_registry = [
            row for row in registry if not str(row.get("name") or "").startswith(prefix)
        ]
        desired_registry = [
            self._marketplace_skill_record(plugin_id, skill_id) for skill_id in sorted(desired)
        ]
        generation = {
            "schema": "echo.marketplace_skill_generation.v1",
            "skill_ids": sorted(desired),
            "previous_skill_ids": sorted(previous_ids),
            "registry_entries": desired_registry,
            "previous_registry_entries": previous_registry,
        }
        try:
            lifecycle.mkdir(parents=True, exist_ok=False)
            atomic_write_json(lifecycle / "generation.json", generation, sort_keys=True)
            staging.mkdir(parents=True, exist_ok=False)
            for skill_id, source in desired.items():
                target = staging / skill_id
                shutil.copytree(source, target)
                meta_path = target / "meta.json"
                if not meta_path.exists():
                    meta_path.write_text(
                        json.dumps(
                            {
                                "name": skill_id,
                                "author": f"cloud-plugin:{plugin_id}",
                                "source": "cloud",
                            },
                            ensure_ascii=False,
                            indent=2,
                            sort_keys=True,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            if previous_ids:
                backup.mkdir(parents=True, exist_ok=False)
                for skill_id in sorted(previous_ids):
                    (self.SKILLS_ROOT / skill_id).replace(backup / skill_id)
            for skill_id in sorted(desired):
                (staging / skill_id).replace(self.SKILLS_ROOT / skill_id)
            atomic_write_json(
                self.SKILLS_ROOT / "registry.json",
                [*other_registry, *desired_registry],
                sort_keys=True,
            )
        except Exception:
            for skill_id in sorted(desired):
                current = self.SKILLS_ROOT / skill_id
                if current.is_dir() and not current.is_symlink():
                    shutil.rmtree(current)
            if backup.is_dir():
                for previous in sorted(backup.iterdir()):
                    previous.replace(self.SKILLS_ROOT / previous.name)
            shutil.rmtree(lifecycle, ignore_errors=True)
            raise
        shutil.rmtree(staging, ignore_errors=True)
        return generation

    @staticmethod
    def _validated_marketplace_skill_generation(
        payload: Any,
        *,
        plugin_id: str,
    ) -> tuple[set[str], set[str], list[dict[str, Any]], list[dict[str, Any]]]:
        if not isinstance(payload, dict) or payload.get("schema") != (
            "echo.marketplace_skill_generation.v1"
        ):
            raise ValueError("marketplace skill generation is invalid")
        prefix = f"{plugin_id}__"

        def ids(field: str) -> set[str]:
            rows = payload.get(field)
            if (
                not isinstance(rows, list)
                or len(rows) > 256
                or any(
                    not isinstance(row, str)
                    or not row.startswith(prefix)
                    or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,255}", row)
                    for row in rows
                )
                or len(set(rows)) != len(rows)
            ):
                raise ValueError("marketplace skill generation ids are invalid")
            return set(rows)

        def entries(field: str, expected: set[str]) -> list[dict[str, Any]]:
            rows = payload.get(field)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("marketplace skill registry generation is invalid")
            names = [str(row.get("name") or "") for row in rows]
            if set(names) != expected or len(names) != len(expected):
                raise ValueError("marketplace skill registry generation does not match skills")
            return [dict(row) for row in rows]

        current_ids = ids("skill_ids")
        previous_ids = ids("previous_skill_ids")
        return (
            current_ids,
            previous_ids,
            entries("registry_entries", current_ids),
            entries("previous_registry_entries", previous_ids),
        )

    def _marketplace_package_skill_sources(
        self,
        package_dir: Path,
        *,
        plugin_id: str,
    ) -> dict[str, Path]:
        sources: dict[str, Path] = {}
        skills_dir = package_dir / "skills"
        if not skills_dir.is_dir():
            return sources
        for skill_md in sorted(skills_dir.rglob("SKILL.md")):
            skill_id = self._slug(f"{plugin_id}__{skill_md.parent.name}")
            if skill_id in sources:
                raise ValueError(f"duplicate bundled skill identity: {skill_id}")
            sources[skill_id] = skill_md.parent
            if len(sources) > 256:
                raise ValueError("marketplace package contains too many bundled skills")
        return sources

    def _verify_marketplace_skill_projection(
        self,
        package_dir: Path,
        *,
        plugin_id: str,
        skills_container: Path | None = None,
        registry_entries: list[dict[str, Any]] | None = None,
    ) -> None:
        expected = self._marketplace_package_skill_sources(
            package_dir,
            plugin_id=plugin_id,
        )
        container = skills_container or self.SKILLS_ROOT
        prefix = f"{plugin_id}__"
        if container.is_dir():
            observed = {
                candidate.name
                for candidate in container.iterdir()
                if candidate.is_dir() and candidate.name.startswith(prefix)
            }
        else:
            observed = set()
        if observed != set(expected):
            raise ValueError("bundled skill projection does not match signed package")
        for skill_id, source in expected.items():
            projected = container / skill_id
            source_meta = source / "meta.json"
            if source_meta.is_file():
                ignored_path = Path(".echo-no-signature")
            else:
                ignored_path = Path("meta.json")
                try:
                    projected_meta = json.loads(
                        (projected / "meta.json").read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(f"bundled skill metadata is invalid: {skill_id}") from exc
                if projected_meta != {
                    "name": skill_id,
                    "author": f"cloud-plugin:{plugin_id}",
                    "source": "cloud",
                }:
                    raise ValueError(f"bundled skill metadata is invalid: {skill_id}")
            source_provenance = compute_marketplace_content_provenance(
                source,
                signature_relative_path=ignored_path,
            )
            projected_provenance = compute_marketplace_content_provenance(
                projected,
                signature_relative_path=ignored_path,
            )
            if (
                source_provenance.get("complete") is not True
                or projected_provenance.get("complete") is not True
                or source_provenance.get("digest") != projected_provenance.get("digest")
            ):
                raise ValueError(f"bundled skill projection integrity failed: {skill_id}")
        expected_registry = [
            self._marketplace_skill_record(plugin_id, skill_id) for skill_id in sorted(expected)
        ]
        if registry_entries is None:
            registry_entries = [
                dict(row)
                for row in self._skill_registry()
                if str(row.get("name") or "").startswith(prefix)
            ]
        if registry_entries != expected_registry:
            raise ValueError("bundled skill registry does not match signed package")

    def _restore_marketplace_skill_generation(
        self,
        payload: Any,
        *,
        plugin_id: str,
        transaction_id: str,
        keep_failed: bool,
    ) -> None:
        current_ids, previous_ids, _current_registry, previous_registry = (
            self._validated_marketplace_skill_generation(payload, plugin_id=plugin_id)
        )
        observed = self._bundled_skill_ids(plugin_id)
        if observed != current_ids:
            raise ValueError("installed bundled skill generation was modified")
        lifecycle = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
        backup = lifecycle / "backup"
        failed = lifecycle / "failed"
        if any((self.SKILLS_ROOT / skill_id).is_symlink() for skill_id in current_ids):
            raise ValueError("refusing to rollback symlinked bundled skill")
        if any((backup / skill_id).is_symlink() for skill_id in previous_ids):
            raise ValueError("refusing to restore symlinked bundled skill")
        if previous_ids and (
            not backup.is_dir()
            or {path.name for path in backup.iterdir() if path.is_dir()} != previous_ids
        ):
            raise ValueError("previous bundled skill generation is unavailable")
        registry = self._skill_registry()
        prefix = f"{plugin_id}__"
        other_registry = [
            row for row in registry if not str(row.get("name") or "").startswith(prefix)
        ]
        if failed.exists():
            raise FileExistsError(f"marketplace skill rollback already staged: {transaction_id}")
        try:
            if current_ids:
                failed.mkdir(parents=True, exist_ok=False)
                for skill_id in sorted(current_ids):
                    (self.SKILLS_ROOT / skill_id).replace(failed / skill_id)
            if previous_ids:
                for skill_id in sorted(previous_ids):
                    (backup / skill_id).replace(self.SKILLS_ROOT / skill_id)
            atomic_write_json(
                self.SKILLS_ROOT / "registry.json",
                [*other_registry, *previous_registry],
                sort_keys=True,
            )
        except Exception:
            for skill_id in sorted(previous_ids):
                restored = self.SKILLS_ROOT / skill_id
                if restored.is_dir() and not restored.is_symlink():
                    backup.mkdir(parents=True, exist_ok=True)
                    restored.replace(backup / skill_id)
            if failed.is_dir():
                for skill_id in sorted(current_ids):
                    candidate = failed / skill_id
                    if candidate.is_dir():
                        candidate.replace(self.SKILLS_ROOT / skill_id)
            raise
        if not keep_failed:
            shutil.rmtree(lifecycle, ignore_errors=True)

    def _recover_partial_marketplace_skill_generation(
        self,
        payload: Any,
        *,
        plugin_id: str,
        transaction_id: str,
    ) -> None:
        current_ids, previous_ids, _current_registry, previous_registry = (
            self._validated_marketplace_skill_generation(payload, plugin_id=plugin_id)
        )
        lifecycle = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
        backup = lifecycle / "backup"
        failed = lifecycle / "failed"
        failed.mkdir(parents=True, exist_ok=True)
        for skill_id in sorted(current_ids - previous_ids):
            current = self.SKILLS_ROOT / skill_id
            if current.is_symlink():
                raise ValueError("refusing to recover symlinked bundled skill")
            if current.is_dir():
                current.replace(failed / skill_id)
        for skill_id in sorted(previous_ids):
            previous = backup / skill_id
            current = self.SKILLS_ROOT / skill_id
            if previous.is_symlink() or current.is_symlink():
                raise ValueError("refusing to recover symlinked bundled skill")
            if previous.is_dir():
                if current.is_dir():
                    current.replace(failed / skill_id)
                previous.replace(current)
            elif not current.is_dir():
                raise ValueError("partial bundled skill transaction lost previous generation")
        registry = self._skill_registry()
        prefix = f"{plugin_id}__"
        other_registry = [
            row for row in registry if not str(row.get("name") or "").startswith(prefix)
        ]
        atomic_write_json(
            self.SKILLS_ROOT / "registry.json",
            [*other_registry, *previous_registry],
            sort_keys=True,
        )

    def _marketplace_state_payload(self, *, plugin_kind: str) -> dict[str, Any]:
        path = (
            self.CONNECTOR_STATE_FILE
            if plugin_kind == "connector"
            else self.CAPABILITY_STATE_FILE
            if plugin_kind == "codex"
            else None
        )
        if path is None:
            raise ValueError(f"unsupported marketplace package kind: {plugin_kind}")
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{plugin_kind} state is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{plugin_kind} state is invalid")
        return payload

    def _marketplace_package_enabled(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
    ) -> bool:
        try:
            payload = self._marketplace_state_payload(plugin_kind=plugin_kind)
        except ValueError:
            return False
        row = payload.get(plugin_id)
        if isinstance(row, dict) and isinstance(row.get("enabled"), bool):
            return bool(row["enabled"])
        return False

    def _marketplace_state_snapshot(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
    ) -> dict[str, Any]:
        payload = self._marketplace_state_payload(plugin_kind=plugin_kind)
        present = plugin_id in payload
        row = payload.get(plugin_id)
        if present and not isinstance(row, dict):
            raise ValueError(f"{plugin_kind} package state row is invalid: {plugin_id}")
        return {
            "schema": "echo.marketplace_state_snapshot.v1",
            "present": present,
            "row": dict(row) if isinstance(row, dict) else None,
        }

    def _commit_marketplace_installed_state(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
        previous: dict[str, Any],
    ) -> None:
        payload = self._marketplace_state_payload(plugin_kind=plugin_kind)
        previous_row = previous.get("row") if previous.get("present") is True else None
        if previous_row is not None and not isinstance(previous_row, dict):
            raise ValueError("marketplace state snapshot is invalid")
        row = dict(previous_row or {})
        row.update(
            id=plugin_id,
            installed=True,
            source="cloud",
        )
        if not isinstance(row.get("enabled"), bool):
            row["enabled"] = False
        payload[plugin_id] = row
        path = (
            self.CONNECTOR_STATE_FILE if plugin_kind == "connector" else self.CAPABILITY_STATE_FILE
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload, sort_keys=True)

    def _marketplace_permission_store(self) -> Any:
        from runtime.platform.capabilities.permission_grants import (
            CapabilityPermissionStore,
        )

        return CapabilityPermissionStore(
            self.CAPABILITY_STATE_FILE.parent / "permission-grants.json"
        )

    @staticmethod
    def _marketplace_runtime_sources(
        package_path: Path,
        *,
        plugin_id: str,
        plugin_kind: str,
    ) -> list[str]:
        sources = [f"plugin://{plugin_id}/"] if plugin_kind == "codex" else []
        for filename in ("mcp.json", ".mcp.json"):
            path = package_path / filename
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            servers = payload.get("mcpServers") if isinstance(payload, dict) else None
            if not isinstance(servers, dict):
                continue
            for server in sorted(servers):
                if isinstance(server, str) and server.strip():
                    sources.append(f"mcp://{server.strip()}/")
        return sorted(set(sources))

    def _restore_marketplace_state(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
        snapshot: Any,
    ) -> None:
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("schema") != "echo.marketplace_state_snapshot.v1"
            or not isinstance(snapshot.get("present"), bool)
            or (snapshot.get("present") is True and not isinstance(snapshot.get("row"), dict))
            or (snapshot.get("present") is False and snapshot.get("row") is not None)
        ):
            raise ValueError("marketplace state snapshot is invalid")
        payload = self._marketplace_state_payload(plugin_kind=plugin_kind)
        if snapshot["present"]:
            if payload.get(plugin_id) == snapshot["row"]:
                return
            payload[plugin_id] = dict(snapshot["row"])
        else:
            if plugin_id not in payload:
                return
            payload.pop(plugin_id, None)
        path = (
            self.CONNECTOR_STATE_FILE if plugin_kind == "connector" else self.CAPABILITY_STATE_FILE
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, payload, sort_keys=True)

    def install_plugin(
        self,
        plugin_id: str,
        *,
        plugin_kind: str = "connector",
        dest_root: str | Path | None = None,
        enabled: bool = True,
        restore_data: bool = False,
        recovery_id: str | None = None,
        _dependency_chain: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if plugin_kind in {"connector", "codex"}:
            with path_transaction(self._marketplace_transaction_lock_path()):
                return self._install_plugin_unlocked(
                    plugin_id,
                    plugin_kind=plugin_kind,
                    dest_root=dest_root,
                    enabled=enabled,
                    restore_data=restore_data,
                    recovery_id=recovery_id,
                    _dependency_chain=_dependency_chain,
                )
        return self._install_plugin_unlocked(
            plugin_id,
            plugin_kind=plugin_kind,
            dest_root=dest_root,
            enabled=enabled,
            restore_data=restore_data,
            recovery_id=recovery_id,
            _dependency_chain=_dependency_chain,
        )

    def _install_plugin_unlocked(
        self,
        plugin_id: str,
        *,
        plugin_kind: str = "connector",
        dest_root: str | Path | None = None,
        enabled: bool = True,
        restore_data: bool = False,
        recovery_id: str | None = None,
        _dependency_chain: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """下载插件内容包,把 plugins/<kind>/<id> 落地 + 复制捆绑技能到本地技能库。"""
        if plugin_kind not in {"connector", "codex", "workbench"}:
            raise ValueError(f"unsupported cloud plugin kind: {plugin_kind}")
        if plugin_kind in {"connector", "codex"}:
            self._recover_incomplete_marketplace_transactions(plugin_kind=plugin_kind)
        if plugin_kind == "workbench" and plugin_id in _FACTORY_WORKBENCH_PLUGINS:
            state = self._workbench_activation_store().install(
                plugin_id,
                enabled=enabled,
                restore_data=restore_data,
                recovery_id=recovery_id,
            )
            return {
                **state,
                "plugin_id": plugin_id,
                "kind": plugin_kind,
                "path": state["factory_path"],
                "copied_skills": [],
                "source": "factory",
                "restart_required": True,
            }
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", plugin_id).strip("_") or "plugin"
        if safe in _dependency_chain:
            chain = " -> ".join((*_dependency_chain, safe))
            domain = "workbench" if plugin_kind == "workbench" else "marketplace"
            raise ValueError(f"cyclic {domain} dependency: {chain}")
        member_prefix = f"plugins/{plugin_kind}"
        dest = Path(dest_root or (self.PLUGIN_INSTALL_ROOT / plugin_kind))
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / safe
        transaction: dict[str, Any] | None = None
        marketplace_trust: dict[str, Any] | None = None
        installed_dependencies: list[dict[str, Any]] = []
        catalog_item = next(
            (
                item
                for item in self.items()
                if str(item.get("plugin") or item.get("id") or "") == safe
            ),
            None,
        )
        if plugin_kind in {"codex", "connector"} and catalog_item is None:
            raise KeyError(f"plugin is missing from the trusted catalog: {plugin_id}")
        with tempfile.TemporaryDirectory(prefix="echo-plugin-") as tmp:
            # Source checkouts can exercise the complete install flow before a
            # release asset is published. Packaged builds do not carry ``.git``
            # and therefore always download the exact same directory layout.
            dev_source = (
                REPO / "extensions" / "workbench-apps" / safe
                if plugin_kind == "workbench"
                else REPO / "extensions" / "codex-plugins" / safe
            )
            dev_install = (
                plugin_kind in {"workbench", "codex"}
                and (REPO / ".git").exists()
                and dev_source.is_dir()
            )
            if dev_install:
                extracted = Path(tmp) / safe
                remote_backend = {
                    "narrative_studio": "narrative_studio",
                    "paper-trading": "paper_trading",
                }.get(safe)
                if plugin_kind == "workbench" and remote_backend:
                    backend_source = (
                        REPO / "runtime" / "platform" / "plugins" / "bundled" / remote_backend
                    )
                    if not backend_source.is_dir():
                        raise KeyError(f"{safe} runtime source is unavailable")
                    shutil.copytree(
                        backend_source,
                        extracted,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                    shutil.copytree(dev_source, extracted, dirs_exist_ok=True)
                else:
                    shutil.copytree(dev_source, extracted)
            else:
                extracted = self._extract_member(
                    self._package_archive(
                        catalog_item or {},
                        package_kind=plugin_kind,
                        package_id=safe,
                    ),
                    member_prefix,
                    Path(tmp),
                    safe,
                )
            if extracted is None:
                raise KeyError(f"plugin not found in content pack: {plugin_id}")
            if any(child.is_symlink() for child in extracted.rglob("*")):
                raise ValueError(f"plugin contains symlinks: {plugin_id}")
            if plugin_kind == "workbench":
                candidate = WorkbenchPackageStore(extracted.parent).load_manifest(safe)
                try:
                    for dependency in candidate.dependencies:
                        dependency_path = dest / dependency
                        if dependency_path.is_dir():
                            WorkbenchPackageStore(
                                dest,
                                require_integrity=True,
                            ).load_manifest(dependency)
                            continue
                        dependency_item = next(
                            (
                                item
                                for item in self.items()
                                if item.get("kind") == "workbench"
                                and item.get("plugin") == dependency
                            ),
                            None,
                        )
                        if dependency_item is None:
                            raise KeyError(f"workbench dependency is unavailable: {dependency}")
                        dependency_result = self.install_plugin(
                            dependency,
                            plugin_kind="workbench",
                            enabled=True,
                            _dependency_chain=(*_dependency_chain, safe),
                        )
                        installed_dependencies.extend(
                            list(dependency_result.get("installed_dependencies") or [])
                        )
                        installed_dependencies.append(
                            {
                                "plugin_id": dependency,
                                "runtime_plugin": dependency_result.get("runtime_plugin"),
                                "transaction_id": dependency_result.get("transaction_id"),
                            }
                        )
                    transaction = self._commit_workbench_package(
                        extracted,
                        target=target,
                        dest=dest,
                        plugin_id=safe,
                        require_trusted=not dev_install,
                    )
                except Exception:
                    self._rollback_dependency_installs(installed_dependencies)
                    raise
            else:
                assert catalog_item is not None
                require_trusted = not (REPO / ".git").exists()
                try:
                    # Resolve only declarations covered by this package's
                    # verified publisher signature. Missing dependencies are
                    # installed disabled, and every newly introduced
                    # generation is rolled back if the parent cannot commit.
                    candidate_trust = verify_marketplace_package_trust(
                        extracted,
                        package_kind=plugin_kind,
                        plugin_id=safe,
                        expected_version=str(catalog_item.get("version") or "").strip() or None,
                        require_trusted=require_trusted,
                    )
                    for dependency in candidate_trust.get("dependencies") or []:
                        dependency_id = str(dependency)
                        candidates = (
                            (
                                "codex",
                                self.PLUGIN_INSTALL_ROOT / "codex" / dependency_id,
                            ),
                            ("codex", self.CODEX_CACHE_ROOT / dependency_id),
                            (
                                "connector",
                                self.PLUGIN_INSTALL_ROOT / "connector" / dependency_id,
                            ),
                        )
                        ready = False
                        for dependency_kind, dependency_path in candidates:
                            if not dependency_path.is_dir() or dependency_path.is_symlink():
                                continue
                            try:
                                verify_marketplace_package_trust(
                                    dependency_path,
                                    package_kind=dependency_kind,
                                    plugin_id=dependency_id,
                                    require_trusted=require_trusted,
                                )
                            except (FileNotFoundError, ValueError):
                                continue
                            ready = True
                            break
                        if ready:
                            continue
                        dependency_item = next(
                            (
                                item
                                for item in self.items()
                                if str(item.get("plugin") or item.get("id") or "") == dependency_id
                                and item.get("kind") in {"plugin", "connector"}
                            ),
                            None,
                        )
                        if dependency_item is None:
                            raise KeyError(
                                f"marketplace dependency is unavailable: {dependency_id}"
                            )
                        dependency_kind = (
                            "codex" if dependency_item.get("kind") == "plugin" else "connector"
                        )
                        dependency_result = self.install_plugin(
                            dependency_id,
                            plugin_kind=dependency_kind,
                            _dependency_chain=(*_dependency_chain, safe),
                        )
                        installed_dependencies.extend(
                            list(dependency_result.get("installed_dependencies") or [])
                        )
                        installed_dependencies.append(
                            {
                                "plugin_id": dependency_id,
                                "kind": dependency_kind,
                                "transaction_id": dependency_result.get("transaction_id"),
                            }
                        )
                    transaction = self._commit_marketplace_package(
                        extracted,
                        target=target,
                        dest=dest,
                        plugin_id=safe,
                        package_kind=plugin_kind,
                        expected_version=str(catalog_item.get("version") or "").strip() or None,
                        require_trusted=require_trusted,
                    )
                    marketplace_trust = transaction["trust"]
                except Exception:
                    self._rollback_dependency_installs(installed_dependencies)
                    raise
        marketplace_transaction = transaction if plugin_kind in {"codex", "connector"} else None
        if marketplace_transaction is not None:
            try:
                state_before = self._marketplace_state_snapshot(
                    safe,
                    plugin_kind=plugin_kind,
                )
                permission_store = self._marketplace_permission_store()
                permission_before = permission_store.snapshot(safe)
                skill_generation = self._commit_marketplace_skill_generation(
                    target,
                    plugin_id=safe,
                    transaction_id=str(marketplace_transaction["transaction_id"]),
                )
                self._verify_marketplace_skill_projection(
                    target,
                    plugin_id=safe,
                )
                marketplace_transaction.update(
                    {
                        "skills": skill_generation,
                        "state_before": state_before,
                        "permission_before": permission_before,
                        "status": "skills_committed",
                    }
                )
                self._write_marketplace_transaction(marketplace_transaction, dest=dest)
                self._commit_marketplace_installed_state(
                    safe,
                    plugin_kind=plugin_kind,
                    previous=state_before,
                )
                permission_store.stage(
                    safe,
                    kind=plugin_kind,
                    required=(marketplace_transaction.get("trust") or {}).get("permissions", []),
                    manifest_digest=str(
                        (marketplace_transaction.get("trust") or {}).get("content_digest", "")
                    ),
                    runtime_sources=self._marketplace_runtime_sources(
                        target,
                        plugin_id=safe,
                        plugin_kind=plugin_kind,
                    ),
                )
                marketplace_transaction.update(
                    {
                        "status": "committed",
                        "rollback_available": marketplace_transaction.get("rollback_candidate")
                        is True,
                    }
                )
                self._write_marketplace_transaction(marketplace_transaction, dest=dest)
            except Exception:
                self._abort_marketplace_transaction(marketplace_transaction, dest=dest)
                raise
            self._finalize_marketplace_transaction(marketplace_transaction, dest=dest)
            copied = list(skill_generation["skill_ids"])
        else:
            copied = self._copy_bundled_skills(target, safe)
        data_result: dict[str, Any] | None = None
        runtime_plugin: str | None = None
        if plugin_kind == "workbench":
            manifest = WorkbenchPackageStore(dest).load_manifest(safe)
            runtime_plugin = manifest.runtime_plugin
            if restore_data:
                try:
                    data_result = self._workbench_data_store().restore(
                        safe,
                        manifest.data_paths,
                        recovery_id=recovery_id,
                    )
                except Exception:
                    if transaction is not None:
                        self.rollback_plugin(
                            safe,
                            plugin_kind="workbench",
                            transaction_id=str(transaction["transaction_id"]),
                        )
                    raise
            state = self._workbench_package_state()
            state[safe] = bool(enabled)
            atomic_write_json(self._workbench_package_state_path(), state, sort_keys=True)
        return {
            "installed": True,
            "plugin_id": plugin_id,
            "kind": plugin_kind,
            "path": str(target),
            "copied_skills": copied,
            "source": "cloud",
            "restart_required": False if plugin_kind == "workbench" else None,
            "runtime_plugin": runtime_plugin,
            "operation": transaction.get("operation") if transaction else "install",
            "transaction_id": transaction.get("transaction_id") if transaction else None,
            "rollback_available": bool(transaction and transaction.get("rollback_available")),
            "trust": transaction.get("trust") if transaction else marketplace_trust,
            "data": data_result,
            "recoveries": self._workbench_data_store().recoveries(safe)
            if plugin_kind == "workbench"
            else [],
            "installed_dependencies": installed_dependencies,
        }

    def uninstall_plugin(
        self,
        plugin_id: str,
        *,
        plugin_kind: str = "connector",
        data_policy: str = "keep",
        confirm_data_move: bool = False,
    ) -> dict[str, Any]:
        if plugin_kind in {"connector", "codex"}:
            with path_transaction(self._marketplace_transaction_lock_path()):
                self._recover_incomplete_marketplace_transactions(plugin_kind=plugin_kind)
                return self._uninstall_plugin_unlocked(
                    plugin_id,
                    plugin_kind=plugin_kind,
                    data_policy=data_policy,
                    confirm_data_move=confirm_data_move,
                )
        return self._uninstall_plugin_unlocked(
            plugin_id,
            plugin_kind=plugin_kind,
            data_policy=data_policy,
            confirm_data_move=confirm_data_move,
        )

    def _uninstall_plugin_unlocked(
        self,
        plugin_id: str,
        *,
        plugin_kind: str = "connector",
        data_policy: str = "keep",
        confirm_data_move: bool = False,
    ) -> dict[str, Any]:
        """Remove one mutable cloud package and its copied skills.

        The target is always resolved below ``PLUGIN_INSTALL_ROOT``; bundled
        runtime plugins and application source directories cannot be removed
        through this method.
        """
        if plugin_kind not in {"connector", "codex", "workbench"}:
            raise ValueError(f"unsupported cloud plugin kind: {plugin_kind}")
        if plugin_kind == "workbench" and plugin_id in _FACTORY_WORKBENCH_PLUGINS:
            state = self._workbench_activation_store().uninstall(
                plugin_id,
                data_policy=data_policy,
                confirm_data_move=confirm_data_move,
            )
            return {
                **state,
                "plugin_id": plugin_id,
                "kind": plugin_kind,
                "removed_skills": [],
                "source": "factory",
                "restart_required": True,
            }
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", plugin_id).strip("_") or "plugin"
        kind_root = (self.PLUGIN_INSTALL_ROOT / plugin_kind).resolve()
        target = (kind_root / safe).resolve()
        if kind_root not in target.parents:
            raise ValueError(f"unsafe plugin id: {plugin_id}")
        if not target.is_dir():
            raise KeyError(f"cloud plugin is not installed: {plugin_id}")
        data_result: dict[str, Any] = {"status": "kept", "paths": []}
        manifest = None
        runtime_plugin: str | None = None
        if plugin_kind == "workbench":
            if data_policy not in {"keep", "trash"}:
                raise ValueError("data_policy must be 'keep' or 'trash'")
            self._ensure_workbench_not_required(safe)
            try:
                manifest = WorkbenchPackageStore(kind_root).load_manifest(safe)
                runtime_plugin = manifest.runtime_plugin
            except (FileNotFoundError, ValueError):
                # Broken packages remain removable with the safe keep policy.
                # The caller may already know the runtime id from its catalog
                # descriptor, but untrusted/broken manifest data is ignored.
                manifest = None
            if data_policy == "trash":
                # A broken package must remain removable with the safe default
                # keep policy. Moving declared data is different: it requires a
                # valid signed/validated manifest so an attacker cannot name an
                # arbitrary application-data path.
                manifest = WorkbenchPackageStore(kind_root).load_manifest(safe)
                runtime_plugin = manifest.runtime_plugin
                data_result = self._workbench_data_store().trash(
                    safe,
                    manifest.data_paths,
                    confirm=confirm_data_move,
                )
        try:
            shutil.rmtree(target)
        except Exception:
            if data_result.get("status") == "trashed" and manifest is not None:
                self._workbench_data_store().restore(
                    safe,
                    manifest.data_paths,
                    recovery_id=str(data_result["recovery_id"]),
                )
            raise
        trust_path = kind_root / ".lifecycle" / "trust" / f"{safe}.json"
        with contextlib.suppress(OSError):
            trust_path.unlink()
        if plugin_kind == "workbench":
            state = self._workbench_package_state()
            state.pop(safe, None)
            atomic_write_json(self._workbench_package_state_path(), state, sort_keys=True)

        removed_skills: list[str] = []
        if self.SKILLS_ROOT.is_dir():
            prefix = f"{safe}__"
            for skill_dir in self.SKILLS_ROOT.iterdir():
                if skill_dir.is_dir() and skill_dir.name.startswith(prefix):
                    shutil.rmtree(skill_dir)
                    removed_skills.append(skill_dir.name)
        registry_path = self.SKILLS_ROOT / "registry.json"
        if registry_path.exists() and removed_skills:
            try:
                registry = json.loads(registry_path.read_text("utf-8"))
            except (OSError, json.JSONDecodeError):
                registry = []
            if isinstance(registry, list):
                registry = [
                    entry
                    for entry in registry
                    if not isinstance(entry, dict)
                    or str(entry.get("name") or "") not in removed_skills
                ]
                atomic_write_json(registry_path, registry, sort_keys=True)

        if plugin_kind in {"connector", "codex"}:
            self._mark_marketplace_package_uninstalled(safe, plugin_kind=plugin_kind)
            self._invalidate_marketplace_transactions(safe, plugin_kind=plugin_kind)
            self._marketplace_permission_store().mark_uninstalled(safe)
        return {
            "uninstalled": True,
            "plugin_id": plugin_id,
            "kind": plugin_kind,
            "removed_skills": sorted(removed_skills),
            "data_policy": data_policy if plugin_kind == "workbench" else None,
            "data": data_result,
            "runtime_plugin": runtime_plugin,
            "recoveries": self._workbench_data_store().recoveries(safe)
            if plugin_kind == "workbench"
            else [],
        }

    def rollback_plugin(
        self,
        plugin_id: str,
        *,
        plugin_kind: str = "workbench",
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        """Atomically restore the package generation replaced by a transaction."""

        if plugin_kind in {"codex", "connector"}:
            with path_transaction(self._marketplace_transaction_lock_path()):
                return self._rollback_marketplace_package(
                    plugin_id,
                    plugin_kind=plugin_kind,
                    transaction_id=transaction_id,
                )
        if plugin_kind != "workbench":
            raise ValueError("rollback is unsupported for this package kind")
        safe = WorkbenchPackageStore.validate_id(plugin_id)
        dest = (self.PLUGIN_INSTALL_ROOT / "workbench").resolve()
        lifecycle = dest / ".lifecycle"
        transactions = lifecycle / "transactions"
        if transaction_id is None:
            latest = self._latest_workbench_transaction(safe)
            if latest is None:
                raise KeyError(f"no rollback transaction found: {safe}")
            transaction_path = transactions / f"{latest['transaction_id']}.json"
        else:
            if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
                raise ValueError("invalid transaction_id")
            transaction_path = transactions / f"{transaction_id}.json"
        try:
            record = json.loads(transaction_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KeyError(f"rollback transaction is unavailable: {safe}") from exc
        if (
            not isinstance(record, dict)
            or record.get("plugin_id") != safe
            or record.get("status") != "committed"
        ):
            raise ValueError("rollback transaction is invalid or already consumed")

        target = dest / safe
        backup_text = str(record.get("backup") or "")
        backup = Path(backup_text).resolve() if backup_text else None
        failed = lifecycle / "failed" / str(record["transaction_id"]) / safe
        trust_path = lifecycle / "trust" / f"{safe}.json"
        previous_trust = backup.parent / "trust.json" if backup is not None else None
        if target.is_symlink() or (backup is not None and backup.is_symlink()):
            raise ValueError("refusing to rollback symlinked workbench package")
        failed.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.replace(failed)
        try:
            if backup is not None and backup.is_dir():
                backup.replace(target)
                restored = WorkbenchPackageStore(dest).load_manifest(safe)
                if previous_trust is not None and previous_trust.is_file():
                    trust_path.parent.mkdir(parents=True, exist_ok=True)
                    previous_trust.replace(trust_path)
                else:
                    with contextlib.suppress(OSError):
                        trust_path.unlink()
                runtime_plugin = restored.runtime_plugin
                operation = "restored_previous"
            else:
                with contextlib.suppress(OSError):
                    trust_path.unlink()
                runtime_plugin = None
                operation = "removed_new_install"
        except Exception:
            if failed.exists() and not target.exists():
                failed.replace(target)
            raise
        record.update(
            {
                "status": "rolled_back",
                "rolled_back_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "failed_generation": str(failed) if failed.exists() else None,
            }
        )
        atomic_write_json(transaction_path, record, sort_keys=True)
        return {
            "ok": True,
            "plugin_id": safe,
            "kind": plugin_kind,
            "transaction_id": record["transaction_id"],
            "operation": operation,
            "installed": target.is_dir(),
            "runtime_plugin": runtime_plugin,
            "path": str(target) if target.is_dir() else None,
        }

    def _rollback_marketplace_package(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
        transaction_id: str | None,
    ) -> dict[str, Any]:
        safe = WorkbenchPackageStore.validate_id(plugin_id)
        dest = (self.PLUGIN_INSTALL_ROOT / plugin_kind).resolve()
        lifecycle = dest / ".lifecycle"
        transactions = lifecycle / "transactions"
        if transaction_id is None:
            latest = self._latest_marketplace_transaction(safe, plugin_kind=plugin_kind)
            if latest is None:
                raise KeyError(f"no rollback transaction found: {safe}")
            transaction_id = str(latest["transaction_id"])
        if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
            raise ValueError("invalid transaction_id")
        transaction_path = transactions / f"{transaction_id}.json"
        try:
            record = json.loads(transaction_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KeyError(f"rollback transaction is unavailable: {safe}") from exc
        if (
            not isinstance(record, dict)
            or record.get("schema") != "echo.marketplace_package_transaction.v1"
            or record.get("plugin_id") != safe
            or record.get("kind") != plugin_kind
            or record.get("status") != "committed"
            or record.get("rollback_available") is not True
        ):
            raise ValueError("rollback transaction is invalid, unavailable, or already consumed")

        target = dest / safe
        operation = self._restore_marketplace_transaction_generations(record, dest=dest)
        failed = lifecycle / "failed" / transaction_id / safe
        failed_skills = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id / "failed"
        record.update(
            {
                "status": "rolled_back",
                "rolled_back_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "failed_generation": str(failed) if failed.exists() else None,
                "failed_skill_generation": str(failed_skills) if failed_skills.exists() else None,
                "rollback_available": False,
            }
        )
        atomic_write_json(transaction_path, record, sort_keys=True)
        return {
            "ok": True,
            "plugin_id": safe,
            "kind": plugin_kind,
            "transaction_id": transaction_id,
            "operation": operation,
            "installed": target.is_dir(),
            "path": str(target) if target.is_dir() else None,
        }

    def _commit_workbench_package(
        self,
        source: Path,
        *,
        target: Path,
        dest: Path,
        plugin_id: str,
        require_trusted: bool,
    ) -> dict[str, Any]:
        if target.is_symlink():
            raise ValueError(f"workbench package target cannot be a symlink: {plugin_id}")
        transaction_id = uuid.uuid4().hex
        lifecycle = dest / ".lifecycle"
        staging_container = lifecycle / "staging" / transaction_id
        staging = staging_container / plugin_id
        backup = lifecycle / "backups" / transaction_id / plugin_id
        transaction_path = lifecycle / "transactions" / f"{transaction_id}.json"
        trust_path = lifecycle / "trust" / f"{plugin_id}.json"
        previous_trust = backup.parent / "trust.json"
        operation = "update" if target.exists() else "install"
        try:
            staging_container.mkdir(parents=True, exist_ok=False)
            shutil.copytree(source, staging)
            manifest = WorkbenchPackageStore(staging_container).load_manifest(plugin_id)
            self._validate_workbench_compatibility(manifest, install_root=dest)
            trust = verify_workbench_package_trust(
                staging,
                manifest,
                require_trusted=require_trusted,
            )
            if target.exists():
                backup.parent.mkdir(parents=True, exist_ok=False)
                if trust_path.is_file():
                    shutil.copy2(trust_path, previous_trust)
                else:
                    previous_manifest = WorkbenchPackageStore(dest).load_manifest(plugin_id)
                    previous_record = verify_workbench_package_trust(
                        target,
                        previous_manifest,
                        require_trusted=False,
                    )
                    previous_record.update(
                        {
                            "source": "legacy_local",
                            "installed_at": None,
                            "transaction_id": None,
                        }
                    )
                    atomic_write_json(previous_trust, previous_record, sort_keys=True)
                target.replace(backup)
            try:
                staging.replace(target)
            except Exception:
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
        except Exception:
            shutil.rmtree(staging_container, ignore_errors=True)
            raise
        record = {
            "schema": "echo.workbench_package_transaction.v1",
            "transaction_id": transaction_id,
            "plugin_id": plugin_id,
            "operation": operation,
            "status": "committed",
            "version": manifest.version,
            "runtime_plugin": manifest.runtime_plugin,
            "destination": str(target),
            "backup": str(backup) if backup.exists() else "",
            "committed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "rollback_available": True,
        }
        trust.update(
            {
                "transaction_id": transaction_id,
                "installed_at": record["committed_at"],
                "source": "publisher" if trust["publisher_verified"] else "local_dev",
            }
        )
        try:
            transaction_path.parent.mkdir(parents=True, exist_ok=True)
            trust_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(transaction_path, record, sort_keys=True)
            atomic_write_json(trust_path, trust, sort_keys=True)
        except Exception:
            if target.exists() and not staging.exists():
                target.replace(staging)
            if backup.exists() and not target.exists():
                backup.replace(target)
            if previous_trust.is_file():
                previous_trust.replace(trust_path)
            else:
                with contextlib.suppress(OSError):
                    trust_path.unlink()
            with contextlib.suppress(OSError):
                transaction_path.unlink()
            shutil.rmtree(staging_container, ignore_errors=True)
            raise
        shutil.rmtree(staging_container, ignore_errors=True)
        record["trust"] = trust
        return record

    def _commit_marketplace_package(
        self,
        source: Path,
        *,
        target: Path,
        dest: Path,
        plugin_id: str,
        package_kind: str,
        expected_version: str | None,
        require_trusted: bool,
    ) -> dict[str, Any]:
        """Stage and atomically switch one Codex or connector generation."""

        if package_kind not in {"codex", "connector"}:
            raise ValueError(f"unsupported marketplace package kind: {package_kind}")
        if target.is_symlink():
            raise ValueError(f"marketplace package target cannot be a symlink: {plugin_id}")
        transaction_id = uuid.uuid4().hex
        lifecycle = dest / ".lifecycle"
        staging_container = lifecycle / "staging" / transaction_id
        staging = staging_container / plugin_id
        backup = lifecycle / "backups" / transaction_id / plugin_id
        transaction_path = lifecycle / "transactions" / f"{transaction_id}.json"
        trust_path = lifecycle / "trust" / f"{plugin_id}.json"
        previous_trust = backup.parent / "trust.json"
        operation = "update" if target.exists() else "install"
        previous_valid = False
        previous_version: str | None = None
        had_target = target.exists()
        try:
            staging_container.mkdir(parents=True, exist_ok=False)
            shutil.copytree(source, staging)
            trust = verify_marketplace_package_trust(
                staging,
                package_kind=package_kind,
                plugin_id=plugin_id,
                expected_version=expected_version,
                require_trusted=require_trusted,
            )
            self._validate_marketplace_compatibility(
                trust,
                plugin_id=plugin_id,
            )
            if had_target:
                backup.parent.mkdir(parents=True, exist_ok=False)
                try:
                    old_trust = verify_marketplace_package_trust(
                        target,
                        package_kind=package_kind,
                        plugin_id=plugin_id,
                        require_trusted=require_trusted,
                    )
                except (FileNotFoundError, ValueError):
                    old_trust = None
                else:
                    previous_valid = True
                    previous_version = str(old_trust["version"])
                if trust_path.is_file():
                    shutil.copy2(trust_path, previous_trust)
                elif old_trust is not None:
                    atomic_write_json(previous_trust, old_trust, sort_keys=True)
            atomic_write_json(
                staging_container / "intent.json",
                {
                    "schema": "echo.marketplace_package_intent.v1",
                    "transaction_id": transaction_id,
                    "plugin_id": plugin_id,
                    "kind": package_kind,
                    "operation": operation,
                },
                sort_keys=True,
            )
            if had_target:
                target.replace(backup)
            try:
                staging.replace(target)
            except Exception:
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
        except Exception:
            shutil.rmtree(staging_container, ignore_errors=True)
            raise

        committed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        rollback_candidate = not had_target or previous_valid
        record = {
            "schema": "echo.marketplace_package_transaction.v1",
            "transaction_id": transaction_id,
            "plugin_id": plugin_id,
            "kind": package_kind,
            "operation": operation,
            "status": "package_committed",
            "version": trust["version"],
            "previous_version": previous_version,
            "destination": str(target),
            "backup": str(backup) if backup.exists() else "",
            "committed_at": committed_at,
            "rollback_candidate": rollback_candidate,
            "rollback_available": False,
            "skills": None,
            "state_before": None,
        }
        trust.update(
            {
                "transaction_id": transaction_id,
                "installed_at": committed_at,
                "source": "publisher" if trust["publisher_verified"] else "local_dev",
            }
        )
        try:
            transaction_path.parent.mkdir(parents=True, exist_ok=True)
            trust_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(transaction_path, record, sort_keys=True)
            atomic_write_json(trust_path, trust, sort_keys=True)
        except Exception:
            if target.exists() and not staging.exists():
                target.replace(staging)
            if backup.exists() and not target.exists():
                backup.replace(target)
            if previous_trust.is_file():
                previous_trust.replace(trust_path)
            else:
                with contextlib.suppress(OSError):
                    trust_path.unlink()
            with contextlib.suppress(OSError):
                transaction_path.unlink()
            shutil.rmtree(staging_container, ignore_errors=True)
            raise
        shutil.rmtree(staging_container, ignore_errors=True)
        record["trust"] = trust
        return record

    def _write_marketplace_transaction(
        self,
        record: dict[str, Any],
        *,
        dest: Path,
    ) -> None:
        transaction_id = str(record.get("transaction_id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
            raise ValueError("invalid marketplace transaction record")
        transaction_path = dest / ".lifecycle" / "transactions" / f"{transaction_id}.json"
        atomic_write_json(transaction_path, record, sort_keys=True)

    def _finalize_marketplace_transaction(
        self,
        record: dict[str, Any],
        *,
        dest: Path | None = None,
    ) -> None:
        if record.get("rollback_available") is True:
            return
        transaction_id = str(record.get("transaction_id") or "")
        plugin_id = str(record.get("plugin_id") or "")
        package_kind = str(record.get("kind") or "")
        if (
            not re.fullmatch(r"[a-f0-9]{32}", transaction_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", plugin_id)
            or package_kind not in {"codex", "connector"}
        ):
            raise ValueError("invalid marketplace transaction record")
        kind_root = dest or (self.PLUGIN_INSTALL_ROOT / package_kind)
        backup_parent = kind_root / ".lifecycle" / "backups" / transaction_id
        shutil.rmtree(backup_parent, ignore_errors=True)
        skill_lifecycle = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
        shutil.rmtree(skill_lifecycle, ignore_errors=True)

    def _abort_marketplace_transaction(
        self,
        record: dict[str, Any],
        *,
        dest: Path | None = None,
    ) -> None:
        transaction_id, plugin_id, package_kind = self._marketplace_transaction_identity(record)
        kind_root = dest or (self.PLUGIN_INSTALL_ROOT / package_kind)
        self._restore_marketplace_transaction_generations(record, dest=kind_root)
        record.update(
            {
                "status": "aborted",
                "aborted_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "rollback_available": False,
            }
        )
        transaction_path = kind_root / ".lifecycle" / "transactions" / f"{transaction_id}.json"
        with contextlib.suppress(OSError, TypeError, ValueError):
            atomic_write_json(transaction_path, record, sort_keys=True)
        shutil.rmtree(
            kind_root / ".lifecycle" / "failed" / transaction_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            kind_root / ".lifecycle" / "backups" / transaction_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            kind_root / ".lifecycle" / "staging" / transaction_id,
            ignore_errors=True,
        )
        shutil.rmtree(
            self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id,
            ignore_errors=True,
        )

    @staticmethod
    def _marketplace_transaction_identity(
        record: Any,
    ) -> tuple[str, str, str]:
        if not isinstance(record, dict):
            raise ValueError("invalid marketplace transaction record")
        transaction_id = str(record.get("transaction_id") or "")
        plugin_id = str(record.get("plugin_id") or "")
        package_kind = str(record.get("kind") or "")
        if (
            not re.fullmatch(r"[a-f0-9]{32}", transaction_id)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", plugin_id)
            or package_kind not in {"codex", "connector"}
        ):
            raise ValueError("invalid marketplace transaction record")
        return transaction_id, plugin_id, package_kind

    def _restore_marketplace_transaction_generations(
        self,
        record: dict[str, Any],
        *,
        dest: Path,
    ) -> str:
        transaction_id, plugin_id, package_kind = self._marketplace_transaction_identity(record)
        current_state = self._marketplace_state_snapshot(
            plugin_id,
            plugin_kind=package_kind,
        )
        permission_store = self._marketplace_permission_store()
        restore_permission = "permission_before" in record
        current_permission = permission_store.snapshot(plugin_id) if restore_permission else None
        skills_restored = False
        package_restored = False
        permission_restored = False
        try:
            skills_payload = record.get("skills")
            if skills_payload is None:
                skill_journal = (
                    self.SKILLS_ROOT
                    / ".lifecycle"
                    / "marketplace"
                    / transaction_id
                    / "generation.json"
                )
                if skill_journal.is_file():
                    try:
                        skills_payload = json.loads(skill_journal.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError("marketplace skill recovery journal is invalid") from exc
                    record["skills"] = skills_payload
            if skills_payload is not None:
                if (
                    record.get("status") != "package_committed"
                    and record.get("operation") == "update"
                    and record.get("rollback_candidate") is True
                ):
                    _current_ids, _previous_ids, _current_registry, previous_registry = (
                        self._validated_marketplace_skill_generation(
                            skills_payload,
                            plugin_id=plugin_id,
                        )
                    )
                    self._verify_marketplace_skill_projection(
                        dest / ".lifecycle" / "backups" / transaction_id / plugin_id,
                        plugin_id=plugin_id,
                        skills_container=(
                            self.SKILLS_ROOT
                            / ".lifecycle"
                            / "marketplace"
                            / transaction_id
                            / "backup"
                        ),
                        registry_entries=previous_registry,
                    )
                try:
                    self._restore_marketplace_skill_generation(
                        skills_payload,
                        plugin_id=plugin_id,
                        transaction_id=transaction_id,
                        keep_failed=True,
                    )
                except ValueError:
                    if record.get("status") != "package_committed":
                        raise
                    self._recover_partial_marketplace_skill_generation(
                        skills_payload,
                        plugin_id=plugin_id,
                        transaction_id=transaction_id,
                    )
                skills_restored = True
            operation = self._restore_marketplace_package_generation(record, dest=dest)
            package_restored = True
            if record.get("state_before") is not None:
                self._restore_marketplace_state(
                    plugin_id,
                    plugin_kind=package_kind,
                    snapshot=record["state_before"],
                )
            if restore_permission:
                permission_store.restore(plugin_id, record.get("permission_before"))
                permission_restored = True
        except Exception:
            if permission_restored:
                with contextlib.suppress(Exception):
                    permission_store.restore(plugin_id, current_permission)
            if package_restored:
                with contextlib.suppress(Exception):
                    self._undo_marketplace_package_restore(record, dest=dest)
            if skills_restored:
                with contextlib.suppress(Exception):
                    self._undo_marketplace_skill_restore(
                        skills_payload,
                        plugin_id=plugin_id,
                        transaction_id=transaction_id,
                    )
            with contextlib.suppress(Exception):
                self._restore_marketplace_state(
                    plugin_id,
                    plugin_kind=package_kind,
                    snapshot=current_state,
                )
            raise
        return operation

    def _restore_marketplace_package_generation(
        self,
        record: dict[str, Any],
        *,
        dest: Path,
    ) -> str:
        transaction_id, plugin_id, package_kind = self._marketplace_transaction_identity(record)
        lifecycle = dest / ".lifecycle"
        target = dest / plugin_id
        backup = lifecycle / "backups" / transaction_id / plugin_id
        failed = lifecycle / "failed" / transaction_id / plugin_id
        trust_path = lifecycle / "trust" / f"{plugin_id}.json"
        previous_trust = backup.parent / "trust.json"
        failed_trust = failed.parent / "trust.json"
        if target.is_symlink() or backup.is_symlink() or failed.is_symlink():
            raise ValueError("refusing to restore symlinked marketplace package")
        if record.get("operation") == "update" and not backup.is_dir():
            raise ValueError("previous marketplace package generation is unavailable")
        if backup.is_dir() and record.get("rollback_candidate") is True:
            verify_marketplace_package_trust(
                backup,
                package_kind=package_kind,
                plugin_id=plugin_id,
                expected_version=str(record.get("previous_version") or "").strip() or None,
                require_trusted=not (REPO / ".git").exists(),
            )
        if failed.parent.exists():
            raise FileExistsError(f"marketplace package rollback already staged: {transaction_id}")
        failed.parent.mkdir(parents=True, exist_ok=True)
        if trust_path.is_file():
            shutil.copy2(trust_path, failed_trust)
        if target.exists():
            target.replace(failed)
        try:
            if backup.is_dir():
                backup.replace(target)
                if previous_trust.is_file():
                    previous_trust.replace(trust_path)
                else:
                    with contextlib.suppress(OSError):
                        trust_path.unlink()
                operation = "restored_previous"
            else:
                with contextlib.suppress(OSError):
                    trust_path.unlink()
                operation = "removed_new_install"
        except Exception:
            if target.exists() and record.get("operation") == "update" and not backup.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            if failed.exists():
                failed.replace(target)
            if failed_trust.is_file():
                failed_trust.replace(trust_path)
            raise
        return operation

    def _undo_marketplace_package_restore(
        self,
        record: dict[str, Any],
        *,
        dest: Path,
    ) -> None:
        transaction_id, plugin_id, _package_kind = self._marketplace_transaction_identity(record)
        lifecycle = dest / ".lifecycle"
        target = dest / plugin_id
        backup = lifecycle / "backups" / transaction_id / plugin_id
        failed = lifecycle / "failed" / transaction_id / plugin_id
        trust_path = lifecycle / "trust" / f"{plugin_id}.json"
        previous_trust = backup.parent / "trust.json"
        failed_trust = failed.parent / "trust.json"
        if record.get("operation") == "update" and target.is_dir():
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.replace(backup)
            if trust_path.is_file():
                trust_path.replace(previous_trust)
        if failed.is_dir():
            failed.replace(target)
        if failed_trust.is_file():
            failed_trust.replace(trust_path)

    def _undo_marketplace_skill_restore(
        self,
        payload: Any,
        *,
        plugin_id: str,
        transaction_id: str,
    ) -> None:
        current_ids, previous_ids, current_registry, _previous_registry = (
            self._validated_marketplace_skill_generation(payload, plugin_id=plugin_id)
        )
        lifecycle = self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id
        backup = lifecycle / "backup"
        failed = lifecycle / "failed"
        observed = self._bundled_skill_ids(plugin_id)
        if observed != previous_ids:
            raise ValueError("restored bundled skill generation was modified")
        for skill_id in sorted(previous_ids):
            backup.mkdir(parents=True, exist_ok=True)
            (self.SKILLS_ROOT / skill_id).replace(backup / skill_id)
        for skill_id in sorted(current_ids):
            candidate = failed / skill_id
            if not candidate.is_dir() or candidate.is_symlink():
                raise ValueError("failed bundled skill generation is unavailable")
            candidate.replace(self.SKILLS_ROOT / skill_id)
        registry = self._skill_registry()
        prefix = f"{plugin_id}__"
        other_registry = [
            row for row in registry if not str(row.get("name") or "").startswith(prefix)
        ]
        atomic_write_json(
            self.SKILLS_ROOT / "registry.json",
            [*other_registry, *current_registry],
            sort_keys=True,
        )

    def _validate_marketplace_compatibility(
        self,
        manifest: dict[str, Any],
        *,
        plugin_id: str,
    ) -> None:
        """Gate ordinary packages on their signed host and dependency contract."""

        host_api = str(manifest.get("host_api") or "").strip()
        if host_api:
            try:
                compatible = Version(__version__) in SpecifierSet(host_api)
            except (InvalidSpecifier, InvalidVersion) as exc:
                raise ValueError(f"invalid marketplace host_api: {host_api}") from exc
            if not compatible:
                raise ValueError(
                    f"marketplace package requires host_api {host_api}; host is {__version__}"
                )
        for dependency in manifest.get("dependencies") or []:
            if dependency == plugin_id:
                raise ValueError(f"marketplace package depends on itself: {plugin_id}")
            candidates = (
                ("codex", self.PLUGIN_INSTALL_ROOT / "codex" / dependency),
                ("codex", self.CODEX_CACHE_ROOT / dependency),
                ("connector", self.PLUGIN_INSTALL_ROOT / "connector" / dependency),
            )
            available = False
            for kind, path in candidates:
                if not path.is_dir() or path.is_symlink():
                    continue
                try:
                    verify_marketplace_package_trust(
                        path,
                        package_kind=kind,
                        plugin_id=dependency,
                        require_trusted=not (REPO / ".git").exists(),
                    )
                except (FileNotFoundError, ValueError):
                    continue
                available = True
                break
            if not available:
                raise ValueError(f"missing trusted marketplace dependency: {dependency}")

    def _validate_workbench_compatibility(
        self,
        manifest: Any,
        *,
        install_root: Path,
    ) -> None:
        official = next(
            (
                item
                for item in self.items()
                if item.get("kind") == "workbench" and item.get("plugin") == manifest.id
            ),
            None,
        )
        if official is not None:
            expected_version = str(official.get("version") or "").strip()
            if expected_version and manifest.version != expected_version:
                raise ValueError(
                    f"workbench version mismatch: expected {expected_version}, "
                    f"got {manifest.version}"
                )
            expected_runtime = str(official.get("runtime_plugin") or "").strip() or None
            if expected_runtime != manifest.runtime_plugin:
                raise ValueError("workbench runtime plugin identity does not match catalog")
        if "host.same_origin" in manifest.permissions and not (
            official is not None and official.get("source") == "echo"
        ):
            raise ValueError("host.same_origin is reserved for trusted first-party workbenches")
        if manifest.host_api:
            try:
                compatible = Version(__version__) in SpecifierSet(manifest.host_api)
            except (InvalidSpecifier, InvalidVersion) as exc:
                raise ValueError(f"invalid workbench host_api: {manifest.host_api}") from exc
            if not compatible:
                raise ValueError(
                    f"workbench requires host_api {manifest.host_api}; host is {__version__}"
                )
        for dependency in manifest.dependencies:
            dependency_path = install_root / dependency
            if not dependency_path.is_dir():
                raise ValueError(f"missing workbench dependency: {dependency}")
            WorkbenchPackageStore(install_root).load_manifest(dependency)

    @staticmethod
    def _transaction_matches(path: Path, *, plugin_id: str, status: str) -> bool:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("plugin_id") == plugin_id
            and payload.get("status") == status
        )

    @staticmethod
    def _version_is_newer(candidate: str, installed: str) -> bool:
        try:
            return Version(candidate) > Version(installed)
        except InvalidVersion:
            return candidate != installed

    def _workbench_package_state_path(self) -> Path:
        return self.PLUGIN_INSTALL_ROOT / "workbench" / ".lifecycle" / "enabled.json"

    def _workbench_package_state(self) -> dict[str, bool]:
        path = self._workbench_package_state_path()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(plugin_id): enabled
            for plugin_id, enabled in payload.items()
            if isinstance(plugin_id, str) and isinstance(enabled, bool)
        }

    def _workbench_package_enabled(self, plugin_id: str) -> bool:
        return self._workbench_package_state().get(plugin_id, True)

    def _ensure_workbench_not_required(
        self,
        plugin_id: str,
        *,
        enabled_only: bool = False,
    ) -> None:
        root = self.PLUGIN_INSTALL_ROOT / "workbench"
        if not root.is_dir():
            return
        dependents: list[str] = []
        store = WorkbenchPackageStore(root, require_integrity=True)
        for candidate in root.iterdir():
            if not candidate.is_dir() or candidate.name.startswith("."):
                continue
            if enabled_only and not self._workbench_package_enabled(candidate.name):
                continue
            try:
                manifest = store.load_manifest(candidate.name)
            except (FileNotFoundError, ValueError):
                continue
            if plugin_id in manifest.dependencies:
                dependents.append(candidate.name)
        if dependents:
            raise ValueError(
                f"workbench is required by installed packages: {', '.join(sorted(dependents))}"
            )

    def _rollback_dependency_installs(self, rows: list[dict[str, Any]]) -> None:
        consumed: set[str] = set()
        for row in reversed(rows):
            transaction_id = str(row.get("transaction_id") or "")
            plugin_id = str(row.get("plugin_id") or "")
            plugin_kind = str(row.get("kind") or "workbench")
            if not transaction_id or transaction_id in consumed:
                continue
            self.rollback_plugin(
                plugin_id,
                plugin_kind=plugin_kind,
                transaction_id=transaction_id,
            )
            consumed.add(transaction_id)

    def _latest_workbench_transaction(self, plugin_id: str) -> dict[str, Any] | None:
        transactions = self.PLUGIN_INSTALL_ROOT / "workbench" / ".lifecycle" / "transactions"
        candidates: list[dict[str, Any]] = []
        for path in transactions.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("plugin_id") == plugin_id
                and payload.get("status") == "committed"
                and payload.get("rollback_available") is True
            ):
                candidates.append(payload)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda payload: (
                str(payload.get("committed_at") or ""),
                str(payload.get("transaction_id") or ""),
            ),
        )

    def _latest_marketplace_transaction(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
    ) -> dict[str, Any] | None:
        transactions = self.PLUGIN_INSTALL_ROOT / plugin_kind / ".lifecycle" / "transactions"
        candidates: list[dict[str, Any]] = []
        for path in transactions.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if (
                isinstance(payload, dict)
                and payload.get("schema") == "echo.marketplace_package_transaction.v1"
                and payload.get("plugin_id") == plugin_id
                and payload.get("kind") == plugin_kind
                and payload.get("status") == "committed"
                and payload.get("rollback_available") is True
            ):
                candidates.append(payload)
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda payload: (
                str(payload.get("committed_at") or ""),
                str(payload.get("transaction_id") or ""),
            ),
        )

    def _recover_incomplete_marketplace_transactions(
        self,
        *,
        plugin_kind: str | None = None,
    ) -> None:
        kinds = (plugin_kind,) if plugin_kind is not None else ("codex", "connector")
        for kind in kinds:
            if kind not in {"codex", "connector"}:
                raise ValueError(f"unsupported marketplace package kind: {kind}")
            dest = self.PLUGIN_INSTALL_ROOT / kind
            self._recover_orphaned_marketplace_staging(dest, plugin_kind=kind)
            transactions = dest / ".lifecycle" / "transactions"
            for path in sorted(transactions.glob("*.json")):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"marketplace transaction journal is invalid: {path.name}"
                    ) from exc
                if not isinstance(record, dict):
                    raise ValueError(f"marketplace transaction journal is invalid: {path.name}")
                if record.get("status") not in {"package_committed", "skills_committed"}:
                    continue
                if record.get("kind") != kind:
                    raise ValueError(f"marketplace transaction kind mismatch: {path.name}")
                self._abort_marketplace_transaction(record, dest=dest)

    def _recover_orphaned_marketplace_staging(
        self,
        dest: Path,
        *,
        plugin_kind: str,
    ) -> None:
        staging_root = dest / ".lifecycle" / "staging"
        for container in sorted(staging_root.iterdir()) if staging_root.is_dir() else []:
            if not container.is_dir() or container.is_symlink():
                raise ValueError("marketplace staging transaction is invalid")
            transaction_id = container.name
            if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
                raise ValueError("marketplace staging transaction id is invalid")
            transaction_path = dest / ".lifecycle" / "transactions" / f"{transaction_id}.json"
            if transaction_path.is_file():
                continue
            intent_path = container / "intent.json"
            try:
                intent = json.loads(intent_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("marketplace staging intent is invalid") from exc
            plugin_id = str(intent.get("plugin_id") or "") if isinstance(intent, dict) else ""
            if (
                not isinstance(intent, dict)
                or intent.get("schema") != "echo.marketplace_package_intent.v1"
                or intent.get("transaction_id") != transaction_id
                or intent.get("kind") != plugin_kind
                or intent.get("operation") not in {"install", "update"}
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", plugin_id)
            ):
                raise ValueError("marketplace staging intent is invalid")
            target = dest / plugin_id
            staged = container / plugin_id
            backup_parent = dest / ".lifecycle" / "backups" / transaction_id
            backup = backup_parent / plugin_id
            failed = dest / ".lifecycle" / "failed" / transaction_id / plugin_id
            if any(path.is_symlink() for path in (target, staged, backup, failed)):
                raise ValueError("refusing to recover symlinked marketplace staging")
            if intent["operation"] == "update":
                if backup.is_dir():
                    failed.parent.mkdir(parents=True, exist_ok=True)
                    if target.is_dir():
                        target.replace(failed)
                    backup.replace(target)
                elif not target.is_dir():
                    raise ValueError("orphaned marketplace update lost the previous generation")
            elif not staged.is_dir() and target.is_dir():
                failed.parent.mkdir(parents=True, exist_ok=True)
                target.replace(failed)
            shutil.rmtree(container, ignore_errors=True)
            shutil.rmtree(backup_parent, ignore_errors=True)
            shutil.rmtree(failed.parent, ignore_errors=True)

    def _invalidate_marketplace_transactions(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
    ) -> None:
        dest = self.PLUGIN_INSTALL_ROOT / plugin_kind
        transactions = dest / ".lifecycle" / "transactions"
        for path in sorted(transactions.glob("*.json")):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or record.get("plugin_id") != plugin_id:
                continue
            transaction_id = str(record.get("transaction_id") or "")
            if not re.fullmatch(r"[a-f0-9]{32}", transaction_id):
                continue
            record.update(
                {
                    "status": "invalidated_uninstalled",
                    "rollback_available": False,
                    "invalidated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                }
            )
            atomic_write_json(path, record, sort_keys=True)
            shutil.rmtree(
                dest / ".lifecycle" / "backups" / transaction_id,
                ignore_errors=True,
            )
            shutil.rmtree(
                self.SKILLS_ROOT / ".lifecycle" / "marketplace" / transaction_id,
                ignore_errors=True,
            )

    def _mark_marketplace_package_uninstalled(
        self,
        plugin_id: str,
        *,
        plugin_kind: str,
    ) -> None:
        if plugin_kind == "connector" and self.CONNECTOR_STATE_FILE.exists():
            try:
                state = json.loads(self.CONNECTOR_STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict) and isinstance(state.get(plugin_id), dict):
                state[plugin_id].update(installed=False, enabled=False)
                atomic_write_json(self.CONNECTOR_STATE_FILE, state, sort_keys=True)
        elif plugin_kind == "codex":
            try:
                state = json.loads(self.CAPABILITY_STATE_FILE.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                state = {}
            if isinstance(state, dict):
                state.pop(plugin_id, None)
                self.CAPABILITY_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_json(self.CAPABILITY_STATE_FILE, state, sort_keys=True)

    def _workbench_activation_store(self) -> WorkbenchActivationStore:
        return WorkbenchActivationStore(
            root=self.PLUGIN_INSTALL_ROOT / "workbench",
            data_root=self.PLUGIN_INSTALL_ROOT.parent,
            factory_root=REPO / "runtime" / "platform" / "plugins" / "bundled",
            trash_root=self.PLUGIN_INSTALL_ROOT / ".trash",
        )

    def _workbench_data_store(self) -> WorkbenchPackageDataStore:
        return WorkbenchPackageDataStore(
            root=self.PLUGIN_INSTALL_ROOT.parent,
            trash_root=self.PLUGIN_INSTALL_ROOT / ".trash" / "workbench",
        )
