"""echo-runtime · 启动同步 / lockfile(capability-plane.md §B「拉取 pin 好的 lockfile」)。

**外部目录的消费机制**:产品不把 registry 管理的 SKILL.md 缓存提交进 git,而是提交**一个
lockfile**(列出要的技能 slug);启动时 `bootstrap_skills` 从 registry 同步**缺失**项到本地现有
布局,再由产品 loader 加载。Python wheel 另带 package-relative 的只读 fallback catalog,因此
registry 不可达时仍可启动;本模块只负责更新外部目录。

迁移辅助:`write_lockfile` 把现有已打包技能列成 lockfile,便于日后逐步停止打包。
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from .client import DEFAULT_BASE, safe_registry_asset_id, safe_registry_skill_slug
from .materialize import sync_skills


def read_lockfile(path: Path | str) -> dict:
    p = Path(path)
    if not p.is_file():
        return {"skills": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"lockfile must contain a JSON object: {p}")
    skills = data.get("skills", [])
    if skills is None:
        data["skills"] = []
    elif not isinstance(skills, list):
        raise ValueError(f"lockfile skills must be a list: {p}")
    return data


def _lock_slug(entry: Any) -> str | None:
    slug = entry if isinstance(entry, str) else (entry or {}).get("slug")
    if not slug:
        return None
    text = str(slug)
    if "/" in text:
        asset_id = safe_registry_asset_id(text)
        if not asset_id.startswith("skill/"):
            raise ValueError(f"lockfile skill entry must reference a skill asset: {text!r}")
        return asset_id
    return safe_registry_skill_slug(text)


def _lock_slugs(lock: dict) -> list[str]:
    out: list[str] = []
    for entry in lock.get("skills", []) or []:
        slug = _lock_slug(entry)
        if slug:
            out.append(slug)
    return out


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp: Path | None = None
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as f:
        tmp = Path(f.name)
        f.write(content)
        f.flush()
    try:
        tmp.replace(path)
    except Exception:
        if tmp is not None and tmp.exists():
            tmp.unlink()
        raise


def bootstrap_skills(
    lockfile: Path | str,
    skills_dir: Path | str,
    *,
    base_url: str = DEFAULT_BASE,
    force: bool = False,
    request_timeout_s: float | None = None,
    max_workers: int | None = None,
    total_timeout_s: float | None = None,
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """按 lockfile 同步**缺失**的技能到 skills_dir(已存在且非 force 则跳过)。
    返回 (synced, present, errors)。启动时调一发即可。"""
    slugs = _lock_slugs(read_lockfile(lockfile))
    skills_dir = Path(skills_dir)
    todo: list[str] = []
    present: list[str] = []
    for slug in slugs:
        bare = safe_registry_skill_slug(slug)
        if not force and (skills_dir / bare / "SKILL.md").is_file():
            present.append(bare)
        else:
            todo.append(slug)
    if not todo:
        return [], present, []
    if max_workers is None:
        ok, skipped, errors = sync_skills(
            todo,
            skills_dir,
            base_url=base_url,
            request_timeout_s=request_timeout_s,
            total_timeout_s=total_timeout_s,
        )
    else:
        ok, skipped, errors = sync_skills(
            todo,
            skills_dir,
            base_url=base_url,
            max_workers=max_workers,
            request_timeout_s=request_timeout_s,
            total_timeout_s=total_timeout_s,
        )
    errors = [*errors, *[(slug, f"skipped:{why}") for slug, why in skipped]]
    return [s for s, _ in ok], present, errors


def write_lockfile(skills_dir: Path | str, out_path: Path | str) -> list[str]:
    """从现有 skills_dir 生成 lockfile(迁移辅助:把已打包技能列成 lockfile,以便停止打包)。"""
    skills_dir = Path(skills_dir)
    slugs = []
    for d in skills_dir.iterdir():
        if d.is_symlink() or not d.is_dir() or not (d / "SKILL.md").is_file():
            continue
        try:
            slugs.append(safe_registry_skill_slug(d.name))
        except ValueError:
            continue
    slugs = sorted(slugs)
    _atomic_write_text(
        Path(out_path), json.dumps({"skills": slugs}, ensure_ascii=False, indent=2) + "\n"
    )
    return slugs
