"""Cloud Expert Store — WorkBuddy 全量专家商城云端源.

把发布在 GitHub Pages 的 WorkBuddy 专家商城(expert-store.json,421 位专家)
接进 echo 的 agent 市场:浏览/搜索/详情 + 一键下载 bundle 并导入为原生 agent。

数据源(可配置):
  - 远程: https://raw.githubusercontent.com/dengdenghua/workbuddy-expert-market/gh-pages/data/expert-store.json
  - 本地镜像(网络不可用时的回退): extensions/workbuddy-experts/storefront/data/expert-store.json
  - 环境变量 ECHO_CLOUD_EXPERT_URL 可覆盖远程地址

安装流程:
  1) 按 bundleUrl 下载 <plugin>.tar.gz(WorkBuddy 专家 bundle,内含
     .codebuddy-plugin/plugin.json + agents/*.md + skills/**/SKILL.md)
  2) 解压到临时目录
  3) 复用 runtime.execution.misc.agent_packs.import_agent_from_pack
     (已支持 .codebuddy-plugin 格式)导入为 agents/<slug>/ 标准 agent
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from runtime.execution.agents.loader import default_agents_root
from runtime.platform.plugins._secure_fetch import fetch_public_https_bytes
from runtime.platform.process.paths import resources_root
from runtime.sensing.gateway._agent_world_helpers import _category_for

# 远程商城数据(发布脚本 scripts/publish-cloud.py 推到 gh-pages)
REMOTE_STORE_URL = os.environ.get(
    "ECHO_CLOUD_EXPERT_URL",
    "https://raw.githubusercontent.com/dengdenghua/workbuddy-expert-market/gh-pages/data/expert-store.json",
)
LOCAL_MIRROR = (
    Path(__file__).resolve().parents[3]
    / "extensions"
    / "workbuddy-experts"
    / "storefront"
    / "data"
    / "expert-store.json"
)
# 缓存远程数据,避免每次请求都拉全量(796KB)
CACHE_DIR = Path(os.path.expanduser("~/.echo/cache"))
CACHE_FILE = CACHE_DIR / "cloud-expert-store.json"

_MAX_CATALOG_BYTES = 8 * 1024 * 1024
_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 10_000
_MAX_EXTRACTED_BYTES = 256 * 1024 * 1024
_MAX_MEMBER_BYTES = 64 * 1024 * 1024


def _installed_agent_dir(plugin: str) -> str:
    """WorkBuddy 专家 bundle 导入到 agents/<slug> 的目录名。

    与 runtime.execution.misc.agent_packs._slugify_agent_id 保持一致
    (插件 agent md 的 name/frontmatter 与 plugin 同名时)。
    """
    return re.sub(r"[^a-zA-Z0-9]+", "_", plugin.strip().lower()).strip("_") or "imported_agent"


def _load_remote(url: str) -> dict[str, Any] | None:
    """拉取远程 expert-store.json;失败返回 None。"""
    try:
        body = fetch_public_https_bytes(
            url,
            timeout=20,
            max_bytes=_MAX_CATALOG_BYTES,
        )
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001 — 网络/解析失败统一走本地镜像
        return None


def _load_local_mirror() -> dict[str, Any] | None:
    if LOCAL_MIRROR.exists():
        try:
            return json.loads(LOCAL_MIRROR.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):  # noqa: BLE001
            return None
    return None


class CloudExpertStore:
    """WorkBuddy 专家商城云端源。"""

    def __init__(
        self,
        *,
        url: str | None = None,
        use_cache: bool = True,
        use_remote: bool = True,
    ) -> None:
        self._url = url or REMOTE_STORE_URL
        self._use_cache = use_cache
        self._use_remote = use_remote
        self._store: dict[str, Any] | None = None

    # ── 数据加载 ──────────────────────────────────────────────
    def _load(self) -> dict[str, Any]:
        if self._store is not None:
            return self._store

        store = None
        # 1) 内存缓存/磁盘缓存
        if self._use_cache and CACHE_FILE.exists():
            try:
                store = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):  # noqa: BLE001
                store = None

        # 2) 远程
        if store is None and self._use_remote:
            remote = _load_remote(self._url)
            if remote and remote.get("experts"):
                store = remote
                if self._use_cache:
                    CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    with contextlib.suppress(OSError):
                        CACHE_FILE.write_text(
                            json.dumps(store, ensure_ascii=False), encoding="utf-8"
                        )

        # 3) 本地镜像
        if store is None:
            store = _load_local_mirror()

        if not store or not store.get("experts"):
            raise RuntimeError("cloud expert store unavailable (remote + local mirror both failed)")

        self._store = store
        return store

    def refresh(self) -> None:
        self._store = None
        if self._use_cache and CACHE_FILE.exists():
            CACHE_FILE.unlink(missing_ok=True)
        return self._load()

    # ── 元数据 ────────────────────────────────────────────────
    def meta(self) -> dict[str, Any]:
        return dict(self._load().get("meta") or {})

    def categories(self) -> list[dict[str, Any]]:
        return list(self._load().get("categories") or [])

    def get(self, expert_id: str) -> dict[str, Any] | None:
        for e in self._load().get("experts", []):
            if e.get("id") == expert_id or e.get("plugin") == expert_id:
                return e
            # 前端 id 带 wb_ 前缀(wb_<plugin>,见 to_agent_dict),反查去掉前缀。
            if expert_id.startswith("wb_") and expert_id[3:] in (
                e.get("id"),
                e.get("plugin"),
            ):
                return e
        return None

    # ── 转成 agent-market wire 形状(与 _list_local_agents 同构) ──
    def to_agent_dict(self, e: dict[str, Any], *, installed: set[str]) -> dict[str, Any]:
        is_team = e.get("expertType") == "team"
        plugin = e.get("plugin") or e.get("id")
        agent_id = f"wb_{plugin}" if plugin else e.get("id")
        installed_dir = _installed_agent_dir(plugin) if plugin else agent_id

        def zh(o: Any) -> str:
            return (o or {}).get("zh") or (o or {}).get("en") or ""

        tags = [t.get("zh") or t.get("en") or "" for t in (e.get("tags") or [])]
        return {
            "id": agent_id,
            "name": agent_id,
            "display_name": zh(e.get("displayName")) or e.get("id"),
            "description": zh(e.get("description")) or "",
            "author": "WorkBuddy(腾讯)",
            "category": _category_for(e.get("id")) or str(e.get("categoryId") or ""),
            "category_id": e.get("categoryId"),
            "tags": tags,
            "icon": "👥" if is_team else "🧑‍💼",
            "avatar_url": e.get("avatar") or "",
            "visual_urls": [],
            "character_profile": None,
            "model": "",
            "tool_groups": [],
            "extra_affinity": {},
            "private_skills": [],
            "capabilities": {},
            "version": "1.0.0",
            "downloads": 0,
            "rating": 4.6 if not is_team else 4.8,
            "rating_count": 0,
            "is_featured": False,
            "is_official": True,
            "is_installed": agent_id in installed or installed_dir in installed,
            "is_team": is_team,
            "created_at": str(e.get("updatedAt") or ""),
            "bundle_url": e.get("bundleUrl") or "",
            "quick_prompts": [
                p.get("zh") or p.get("en") or "" for p in (e.get("quickPrompts") or [])
            ],
            "profession": zh(e.get("profession")),
            "source": "workbuddy-cloud",
        }

    def list_experts(
        self,
        *,
        category: str | None = None,
        search: str | None = None,
        sort: str = "updated",
        offset: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        store = self._load()
        installed = self._installed_set()
        experts = [self.to_agent_dict(e, installed=installed) for e in store.get("experts", [])]

        if category and category != "all":
            qcat = category
            # 支持按中文分类名或 id
            for c in store.get("categories", []):
                if c.get("id") == category or (c.get("name") or {}).get("zh") == category:
                    qcat = c.get("id")
                    break
            experts = [a for a in experts if a.get("category_id") == qcat]

        if search:
            q = search.lower()
            experts = [
                a
                for a in experts
                if q in a["display_name"].lower()
                or q in a["description"].lower()
                or q in a["id"].lower()
                or any(q in t.lower() for t in a["tags"])
                or q in a["profession"].lower()
            ]

        if sort == "name":
            experts.sort(key=lambda a: a["display_name"].lower())
        elif sort == "rating":
            experts.sort(key=lambda a: a["rating"], reverse=True)
        else:
            experts.sort(key=lambda a: a["created_at"], reverse=True)

        total = len(experts)
        paged = experts[offset : offset + limit]
        page = offset // limit + 1 if limit else 1
        return {"agents": paged, "total": total, "page": page, "page_size": limit}

    def _installed_set(self) -> set[str]:
        installed: set[str] = set()
        root = default_agents_root()
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and not d.name.startswith("_"):
                    installed.add(d.name)
        return installed

    # ── 安装 ──────────────────────────────────────────────────
    def install_expert(
        self,
        expert_id: str,
        *,
        agents_root: str | Path | None = None,
        skills_root: str | Path | None = None,
    ) -> dict[str, Any]:
        from runtime.execution.misc.agent_packs import (
            AgentPackAgentNotFound,
            import_agent_from_pack,
        )

        e = self.get(expert_id)
        if not e:
            raise KeyError(f"expert not found in cloud store: {expert_id}")

        bundle_url = e.get("bundleUrl") or e.get("bundle_url")
        if not bundle_url:
            raise ValueError(f"expert has no bundle url: {expert_id}")

        agents_root = Path(agents_root or default_agents_root())
        skills_root = Path(skills_root or resources_root() / "skills" / "public")

        # 先检查是否已装(wire id 带 wb_ 前缀;磁盘目录是 slugified 名)
        agent_id = f"wb_{e.get('plugin')}" if e.get("plugin") else expert_id
        installed_dir = _installed_agent_dir(e.get("plugin")) if e.get("plugin") else agent_id
        if (agents_root / agent_id).exists() or (agents_root / installed_dir).exists():
            return {
                "installed": True,
                "already_exists": True,
                "agent_id": agent_id,
                "agent_path": str(agents_root / installed_dir),
                "message": f"agent already exists: {installed_dir}",
            }

        tmpdir = tempfile.mkdtemp(prefix="wb-bundle-")
        try:
            bundle_path = self._download(bundle_url, tmpdir, e.get("plugin") or expert_id)
            unpack_root = self._unpack(bundle_path, tmpdir)
            # 找到含 .codebuddy-plugin 的插件根
            pack_root = _find_pack_root(unpack_root)
            agent_name = _find_agent_name(pack_root, e)
            result = import_agent_from_pack(
                pack_root,
                agent_name,
                agents_root=agents_root,
                skills_root=skills_root,
            )
            return {
                "installed": True,
                "already_exists": result.already_exists,
                "agent_id": result.agent_id,
                "agent_name": result.agent_name,
                "agent_path": result.agent_path,
                "copied_skills": result.copied_skills,
                "warnings": result.warnings,
                "source": "workbuddy-cloud",
            }
        except (AgentPackAgentNotFound, ValueError, KeyError):
            raise
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @staticmethod
    def _download(url: str, dest_dir: Path, plugin: str) -> Path:
        safe_plugin = _installed_agent_dir(plugin)
        out = Path(dest_dir) / f"{safe_plugin}.tar.gz"
        body = fetch_public_https_bytes(
            url,
            timeout=120,
            max_bytes=_MAX_BUNDLE_BYTES,
        )
        out.write_bytes(body)
        return out

    @staticmethod
    def _unpack(bundle_path: Path, dest_dir: Path) -> Path:
        out = Path(dest_dir) / "unpack"
        out.mkdir(exist_ok=True)
        with tarfile.open(bundle_path, "r:gz") as tf:
            members = tf.getmembers()
            if len(members) > _MAX_ARCHIVE_MEMBERS:
                raise ValueError("expert bundle contains too many members")
            dest_res = out.resolve()
            extracted_bytes = 0
            validated: list[tuple[tarfile.TarInfo, Path]] = []
            for member in members:
                if "\\" in member.name or "\x00" in member.name:
                    raise ValueError(f"unsafe tar path: {member.name}")
                if not (member.isdir() or member.isreg()):
                    raise ValueError(f"unsupported tar member: {member.name}")
                target = (dest_res / member.name).resolve()
                if dest_res not in target.parents and target != dest_res:
                    raise ValueError(f"unsafe tar path: {member.name}")
                if member.isreg():
                    if member.size < 0 or member.size > _MAX_MEMBER_BYTES:
                        raise ValueError(f"tar member is too large: {member.name}")
                    extracted_bytes += member.size
                    if extracted_bytes > _MAX_EXTRACTED_BYTES:
                        raise ValueError("expert bundle expands beyond the size limit")
                validated.append((member, target))
            for member, target in validated:
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = tf.extractfile(member)
                if source is None:
                    raise ValueError(f"tar member has no file content: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
        return out

    def clear_cache(self) -> None:
        self._store = None
        if CACHE_FILE.exists():
            CACHE_FILE.unlink(missing_ok=True)


def _find_pack_root(unpack_root: Path) -> Path:
    """在解压目录里找含 .codebuddy-plugin 的插件根(可能多套一层)。"""
    for marker in (".codebuddy-plugin", ".claude-plugin", ".codex-plugin"):
        hits = list(unpack_root.rglob(marker))
        if hits:
            return hits[0].parent
    # 没有插件清单时,直接用解压根(仍可能扫到 agents/*.md)
    return unpack_root


def _find_agent_name(pack_root: Path, e: dict[str, Any]) -> str:
    """Resolve the pack entry agent, preferring explicit lead metadata.

    Team bundles contain several ``agents/*.md`` files.  Picking the first
    alphabetically can import an arbitrary member instead of the team lead, so
    consult the plugin manifest and catalog ``promptFile`` before falling back
    to the legacy first-file behaviour.
    """
    agents_dir = pack_root / "agents"
    if not agents_dir.is_dir():
        return e.get("plugin") or e.get("id")

    def existing_agent_name(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        # ``promptFile`` is commonly an absolute-looking marketplace path;
        # only its basename may select a file inside this already validated
        # pack directory.
        filename = value.strip().replace("\\", "/").rsplit("/", 1)[-1]
        name = filename[:-3] if filename.endswith(".md") else filename
        if not name or name in {".", ".."}:
            return None
        candidate = agents_dir / f"{name}.md"
        return name if candidate.is_file() else None

    manifest: dict[str, Any] = {}
    for marker in (".codebuddy-plugin", ".claude-plugin", ".codex-plugin"):
        manifest_path = pack_root / marker / "plugin.json"
        try:
            loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(loaded, dict):
            manifest = loaded
            break

    team_info = manifest.get("teamInfo")
    lead_agent = team_info.get("leadAgent") if isinstance(team_info, dict) else None
    for value in (
        manifest.get("agentName"),
        lead_agent,
        manifest.get("promptFile"),
        e.get("promptFile"),
        e.get("prompt_file"),
    ):
        resolved = existing_agent_name(value)
        if resolved:
            return resolved

    files = sorted(agents_dir.glob("*.md"))
    if files:
        return files[0].stem
    return e.get("plugin") or e.get("id")


__all__ = ["CloudExpertStore", "REMOTE_STORE_URL", "LOCAL_MIRROR"]
