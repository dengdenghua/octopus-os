"""统一资产仓库 —— 插件 / 技能 / 角色,无论来源(WorkBuddy / Codex / 本地 / 内置)归一到一个文件夹。

统一目录(``~/.echo/assets/``)::

    ~/.echo/assets/
      index.json                 # 统一索引:每个资产 kind/source/id/名称/状态/位置/目录
      plugins/<id>/              # 所有插件+连接器平铺(plugin.json / cli.json + skills 清单)
      skills/<id>/               # 所有技能平铺(SKILL.md 全量)
      agents/<id>/               # 所有角色平铺(profile + agent-core + 技能,略去 media/sessions)

    * 按类别平铺:插件/技能/角色各自一个文件夹,不按来源嵌套;
    * source 只作为元数据写入 index.json;
    * 平铺 id 冲突时(如 github 同时存在 codex 插件与 workbuddy 连接器),
      后到者用 ``<id>-<source>`` 作为目录名,index 里用 ``dir`` 记录真实目录。

来源聚合:
  * 插件:Codex 插件(~/.echo/plugins/codex) + WorkBuddy 连接器(extensions/workbuddy-connectors)
  * 技能:本地技能(~/.echo/skills) + 内置技能(runtime/execution/all_skills) + 迁移导入(.echo/imported)
  * 角色:本地 agents(<repo>/agents) + WorkBuddy 专家/专家团(expert-store.json)

sync 是幂等的(不覆盖已有、不删除源),index.json 是唯一动态产物,可随时重建。
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import resources_root

REPO = resources_root()
UNIFIED_ROOT = Path.home() / ".echo" / "assets"
INDEX_FILE = UNIFIED_ROOT / "index.json"

# 各来源根
CODEX_PLUGIN_ROOT = REPO / ".echo" / "plugins" / "codex"
CONNECTOR_ROOT = REPO / "extensions" / "workbuddy-connectors" / "connectors"
CONNECTOR_MANIFEST = REPO / "extensions" / "workbuddy-connectors" / "echo-manifest.json"
LOCAL_SKILLS = Path.home() / ".echo" / "skills"
BUILTIN_SKILLS = REPO / "runtime" / "execution" / "all_skills"
IMPORTED_ROOT = Path.home() / ".echo" / "imported"
AGENTS_ROOT = REPO / "agents"
EXPERT_STORE = (
    REPO / "extensions" / "workbuddy-experts" / "storefront" / "data" / "expert-store.json"
)

_SKIP_DIRS = {
    "node_modules",
    "dist",
    "build",
    ".git",
    "__pycache__",
    "sessions",
    "visuals",
    "_shared",
}
_KIND_DIR = {"plugin": "plugins", "skill": "skills", "agent": "agents", "team": "agents"}
_SLUG_RE = re.compile(r"[^a-z0-9_.-]+", re.I)
_INDEX_CACHE_LOCK = threading.RLock()
_INDEX_CACHE_MAX_ENTRIES = 16
_INDEX_CACHE: dict[
    Path,
    tuple[tuple[int, int, int, int], dict[str, Any]],
] = {}


def _slug(value: str) -> str:
    return _SLUG_RE.sub("_", value.strip()).strip("_.") or "asset"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _file_stamp(path: Path) -> tuple[int, int, int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)


def _cache_index(path: Path, index: dict[str, Any]) -> None:
    stamp = _file_stamp(path)
    if stamp is None:
        return
    key = path.absolute()
    with _INDEX_CACHE_LOCK:
        if key not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_MAX_ENTRIES:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        _INDEX_CACHE[key] = (stamp, index)


def _copy_light(src: Path, dest: Path) -> int:
    """复制目录内非 _SKIP_DIRS 的文件(保留结构),返回文件数。"""
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for p in sorted(src.rglob("*")):
        rel = p.relative_to(src)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        if p.is_file():
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                shutil.copy2(p, target)
            n += 1
    return n


# ── 插件收集 ───────────────────────────────────────────────
def _codex_plugins() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not CODEX_PLUGIN_ROOT.is_dir():
        return out
    for plugin_json in sorted(CODEX_PLUGIN_ROOT.glob("*/.codex-plugin/plugin.json")):
        meta = _read_json(plugin_json)
        if not meta or not meta.get("name"):
            continue
        root = plugin_json.parent.parent
        iface = meta.get("interface") or {}
        skills = (
            [p.parent.name for p in (root / "skills").rglob("SKILL.md")]
            if (root / "skills").is_dir()
            else []
        )
        out.append(
            {
                "id": str(meta.get("name")),
                "kind": "plugin",
                "source": "codex",
                "type": "codex-plugin",
                "name": str(iface.get("displayName") or meta.get("name")),
                "name_zh": str(iface.get("displayName") or meta.get("name")),
                "description": str(iface.get("shortDescription") or meta.get("description") or ""),
                "version": str(meta.get("version") or "0.1.0"),
                "author": str(
                    (meta.get("author") or {}).get("name", "")
                    if isinstance(meta.get("author"), dict)
                    else meta.get("author") or ""
                ),
                "skills": skills,
                "skills_count": len(skills),
                "origin": str(root),
            }
        )
    return out


def _mcp_names(raw: Any) -> list[str]:
    """mcp_servers 兼容 dict(name→spec) 与 list([{name:...}] 或字符串)。"""
    if isinstance(raw, dict):
        return list(raw.keys())
    if isinstance(raw, list):
        names: list[str] = []
        for x in raw:
            if isinstance(x, dict):
                n = str(x.get("name") or x.get("id") or "")
                if n:
                    names.append(n)
            elif isinstance(x, str):
                names.append(x)
        return names
    return []


def _workbuddy_connectors() -> list[dict[str, Any]]:
    manifest = _read_json(CONNECTOR_MANIFEST)
    if not manifest:
        return []
    out: list[dict[str, Any]] = []
    for c in manifest.get("connectors", []):
        cid = str(c.get("id") or "")
        if not cid:
            continue
        cdir = CONNECTOR_ROOT / cid
        skills = [p.parent.name for p in cdir.rglob("SKILL.md")] if cdir.is_dir() else []
        out.append(
            {
                "id": cid,
                "kind": "plugin",
                "source": "workbuddy",
                "type": "connector",
                "name": str(c.get("name") or cid),
                "name_zh": str(c.get("name_zh") or c.get("name") or cid),
                "description": str(c.get("description_zh") or c.get("description") or ""),
                "version": "1.0.0",
                "author": "WorkBuddy(腾讯)",
                "auth_mode": str(c.get("auth_mode") or ""),
                "mcp_servers": _mcp_names(c.get("mcp_servers")),
                "skills": skills,
                "skills_count": len(skills),
                "origin": str(cdir) if cdir.is_dir() else "",
            }
        )
    return out


# ── 技能收集 ───────────────────────────────────────────────
def _scan_skills(root: Path, source: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for md in sorted(root.glob("*/SKILL.md")):
        name = md.parent.name
        text = md.read_text("utf-8", errors="replace")[:400]
        m = re.search(r"name:\s*[\"']?([^\n\"']+)", text)
        display = m.group(1).strip() if m else name
        out.append(
            {
                "id": name,
                "kind": "skill",
                "source": source,
                "name": display,
                "description": "",
                "version": "0.1.0",
                "origin": str(md.parent),
            }
        )
    return out


def _all_skills() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    # 本地优先,内置其次,迁移导入兜底
    for root, source in [
        (LOCAL_SKILLS, "local"),
        (BUILTIN_SKILLS, "builtin"),
        (IMPORTED_ROOT, "imported"),
    ]:
        if source == "imported":
            # imported 下按 <source>/skills/<name>
            for skills_dir in sorted(IMPORTED_ROOT.glob("*/skills")):
                for item in _scan_skills(skills_dir, "imported"):
                    if item["id"] in seen:
                        continue
                    seen.add(item["id"])
                    out.append(item)
            continue
        for item in _scan_skills(root, source):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            out.append(item)
    return out


# ── 角色收集 ───────────────────────────────────────────────
def _local_agents() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not AGENTS_ROOT.is_dir():
        return out
    for adir in sorted(AGENTS_ROOT.iterdir()):
        if not adir.is_dir() or adir.name.startswith(".") or adir.name in _SKIP_DIRS:
            continue
        profile = adir / "profile.jsonc"
        name = adir.name
        description = ""
        if profile.is_file():
            try:
                from runtime.platform.process.utils import parse_jsonc

                p = parse_jsonc(profile.read_text("utf-8"))
                name = str(p.get("name") or adir.name)
                description = str(p.get("description") or p.get("bio") or "")
            except Exception:  # noqa: BLE001
                pass
        out.append(
            {
                "id": adir.name,
                "kind": "agent",
                "source": "local",
                "name": name,
                "name_zh": name,
                "description": description,
                "version": "1.0.0",
                "origin": str(adir),
            }
        )
    return out


def _workbuddy_experts() -> list[dict[str, Any]]:
    store = _read_json(EXPERT_STORE)
    if not store:
        return []
    out: list[dict[str, Any]] = []
    for e in store.get("experts", []):
        pid = str(e.get("plugin") or e.get("id") or "")
        if not pid:
            continue
        out.append(
            {
                "id": pid,
                "kind": "agent" if str(e.get("expertType")) == "agent" else "team",
                "source": "workbuddy",
                "name": str(
                    (e.get("displayName") or {}).get("zh")
                    or (e.get("displayName") or {}).get("en")
                    or pid
                ),
                "name_zh": str(
                    (e.get("displayName") or {}).get("zh")
                    or (e.get("displayName") or {}).get("en")
                    or pid
                ),
                "description": str(
                    (e.get("description") or {}).get("zh")
                    or (e.get("description") or {}).get("en")
                    or ""
                ),
                "version": "1.0.0",
                "category": str(e.get("categoryId") or ""),
                "origin": str(EXPERT_STORE),
            }
        )
    return out


# ── 平铺目录名(冲突加 source 后缀) ───────────────────────
def _assign_dirs(assets: list[dict[str, Any]]) -> None:
    """给每个资产分配平铺目录名(写入 item["dir"])。

    同一 kind 下 id 唯一则目录名=id;冲突时后到者用 ``<id>-<source>``。
    """
    used: dict[str, set[str]] = {}
    for item in assets:
        kind = item["kind"]
        base = str(item["id"])
        seen = used.setdefault(kind, set())
        name = base
        if name in seen:
            name = f"{base}-{item.get('source', 'x')}"
            n = 2
            while name in seen:
                name = f"{base}-{n}"
                n += 1
        seen.add(name)
        item["dir"] = name


# ── 统一同步 ───────────────────────────────────────────────
def sync_assets(*, dest_root: str | Path | None = None) -> dict[str, Any]:
    """聚合所有来源 → 写统一 index.json + 归一子目录快照。幂等,不删源。"""
    root = Path(dest_root or UNIFIED_ROOT)
    root.mkdir(parents=True, exist_ok=True)

    plugins = _codex_plugins() + _workbuddy_connectors()
    skills = _all_skills()
    agents = _local_agents() + _workbuddy_experts()

    # 归一子目录(轻量快照):按类别平铺,冲突 id 用 <id>-<source> 后缀
    all_assets = plugins + skills + agents
    _assign_dirs(all_assets)

    files_copied = 0
    for item in all_assets:
        src = Path(item.get("origin") or "")
        if not src.is_dir():
            continue  # 连接器/expert 的 origin 是 json 清单文件,不建快照
        dest = root / _KIND_DIR.get(item["kind"], f"{item['kind']}s") / item["dir"]
        files_copied += _copy_light(src, dest)

    index: dict[str, Any] = {
        "schema": "echo.assets.v1",
        "meta": {
            "title": "Echo 统一资产仓库(插件/技能/角色)",
            "sources": ["codex", "workbuddy", "local", "builtin", "imported"],
            "updated_at": __import__("datetime").datetime.now().isoformat(),
            "counts": {
                "plugin": len(plugins),
                "skill": len(skills),
                "agent": sum(1 for a in agents if a["kind"] == "agent"),
                "team": sum(1 for a in agents if a["kind"] == "team"),
            },
        },
        "assets": all_assets,
    }
    index_path = root / "index.json"
    index_path.write_text(json.dumps(index, ensure_ascii=False, indent=1), "utf-8")
    _cache_index(index_path, index)
    return {
        "root": str(root),
        # The same index object now backs the in-process read cache. Do not
        # expose a mutable reference that a sync caller could accidentally
        # use to corrupt subsequent summary/list responses.
        "counts": copy.deepcopy(index["meta"]["counts"]),
        "files_copied": files_copied,
        "updated_at": index["meta"]["updated_at"],
    }


# ── 读取 ───────────────────────────────────────────────────
def _load_index(root: str | Path | None = None) -> dict[str, Any] | None:
    path = Path(root or UNIFIED_ROOT) / "index.json"
    key = path.absolute()
    with _INDEX_CACHE_LOCK:
        # Retry once if another process replaces the index while it is read.
        # A stable invalid/missing index remains a normal cache miss.
        data: dict[str, Any] | None = None
        for _attempt in range(2):
            before = _file_stamp(path)
            if before is None:
                _INDEX_CACHE.pop(key, None)
                return None
            cached = _INDEX_CACHE.get(key)
            if cached is not None and cached[0] == before:
                return cached[1]

            data = _read_json(path)
            after = _file_stamp(path)
            if before == after:
                if data is None:
                    _INDEX_CACHE.pop(key, None)
                else:
                    if key not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_MAX_ENTRIES:
                        _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
                    _INDEX_CACHE[key] = (after, data)
                return data
        return data


def summary(root: str | Path | None = None) -> dict[str, Any] | None:
    idx = _load_index(root)
    if not idx:
        return None
    return {"root": str(Path(root or UNIFIED_ROOT)), **copy.deepcopy(idx["meta"])}


def list_assets(
    *,
    kind: str | None = None,
    source: str | None = None,
    search: str | None = None,
    root: str | Path | None = None,
) -> list[dict[str, Any]]:
    idx = _load_index(root)
    if not idx:
        return []
    items = idx.get("assets", [])
    if kind:
        items = [i for i in items if i.get("kind") == kind]
    if source:
        items = [i for i in items if i.get("source") == source]
    if search:
        q = search.lower()
        items = [
            i
            for i in items
            if q in str(i.get("name", "")).lower()
            or q in str(i.get("id", "")).lower()
            or q in str(i.get("description", "")).lower()
        ]
    return copy.deepcopy(items)


def get_asset(kind: str, asset_id: str, root: str | Path | None = None) -> dict[str, Any] | None:
    for item in list_assets(kind=kind, root=root):
        if item.get("id") == asset_id:
            return item
    return None
