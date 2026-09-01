#!/usr/bin/env python3
"""生成可复现、可完整打包的云商城技能目录。

数据源:
  1. Echo 内置技能: runtime/execution/all_skills/<name>/SKILL.md(101 个)
  2. 仓库内 WorkBuddy/Agent 技能树
  3. 用户已安装技能(仅本地预览；CI 发布绝不读取用户目录)

输出: extensions/workbuddy-experts/storefront/data/skill-registry.json
每个条目都必须能从当前发布源打进统一内容包；历史云条目不会凭空保留。

用法:
  python3 extensions/workbuddy-experts/scripts/build-skill-registry.py [--out PATH]
"""

import argparse
import datetime
import json
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
BUILTIN_SKILLS = REPO / "runtime" / "execution" / "all_skills"
USER_SKILLS = Path.home() / ".echo" / "skills"
REPOSITORY_SKILL_TREES = (
    REPO / "extensions" / "workbuddy-experts" / "builtin",
    REPO / "agents",
)
OUT = REPO / "extensions" / "workbuddy-experts" / "storefront" / "data" / "skill-registry.json"

# Echo 技能内容包(发布到 GitHub Release 的单一归档,安装时按 name 解出)。
CONTENT_SKILLS_URL = os.environ.get(
    "ECHO_SKILLS_CONTENT_URL",
    "https://github.com/dengdenghua/workbuddy-expert-market/releases/download/echo-content/echo-skills.tar.gz",
)


def frontmatter(text: str) -> dict:
    """解析 SKILL.md 顶部 frontmatter(name/description/version/author/tags)。"""
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        key = k.strip().lower()
        val = v.strip().strip("\"'")
        if key in ("name", "description", "version", "author", "license", "category"):
            out[key] = val
    return out


def scan_skill(directory: Path, source: str) -> dict | None:
    skill_md = directory / "SKILL.md"
    if not directory.is_dir() or not skill_md.is_file():
        return None
    fm = frontmatter(skill_md.read_text(encoding="utf-8", errors="replace"))
    name = (fm.get("name") or directory.name).strip()
    desc = (fm.get("description") or "").strip()
    if not name or not desc:
        return None
    tags = ["echo"]
    if fm.get("category"):
        tags.append(fm["category"].lower().replace(" ", "-"))
    return {
        "name": name,
        "version": fm.get("version") or "0.1.0",
        "author": fm.get("author") or "echo-agent",
        "description": desc,
        "tags": tags,
        "source": source,
        "download_url": CONTENT_SKILLS_URL,
    }


def scan_dir(skills_dir: Path, source: str) -> list[dict]:
    if not skills_dir.exists():
        return []
    return [
        entry
        for directory in sorted(skills_dir.iterdir())
        if directory.is_dir() and not directory.name.startswith(("__", "."))
        if (entry := scan_skill(directory, source)) is not None
    ]


def scan_tree(skills_dir: Path, source: str) -> list[dict]:
    """Scan nested repository skill trees with deterministic first-wins ids."""

    if not skills_dir.exists():
        return []
    out: list[dict] = []
    for skill_md in sorted(skills_dir.rglob("SKILL.md")):
        directory = skill_md.parent
        if any(
            part in {"node_modules", "release", "build", ".git", "__pycache__"}
            for part in directory.relative_to(skills_dir).parts
        ):
            continue
        entry = scan_skill(directory, source)
        if entry is not None:
            out.append(entry)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="生成云商城技能注册表 skill-registry.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--builtin-dir", type=Path, default=BUILTIN_SKILLS)
    ap.add_argument("--user-dir", type=Path, default=USER_SKILLS)
    args = ap.parse_args()

    builtin = scan_dir(args.builtin_dir, "echo")
    repository = [
        entry for tree in REPOSITORY_SKILL_TREES for entry in scan_tree(tree, "echo-repository")
    ]
    user = (
        []
        if os.environ.get("CI", "").strip().lower() in {"1", "true", "yes"}
        else scan_dir(args.user_dir, "echo-local")
    )
    by_name: dict[str, dict] = {}
    for entry in [*builtin, *repository, *user]:
        by_name.setdefault(entry["name"], entry)
    skills = [by_name[name] for name in sorted(by_name)]
    data = {
        "meta": {
            "title": "Echo Skill Hub — 云商城技能",
            "count": len(skills),
            "workbuddy_skills": sum(1 for s in skills if s.get("source") == "echo-repository"),
            "echo_skills": sum(1 for s in skills if s.get("source", "").startswith("echo")),
            "source": (
                "https://github.com/dengdenghua/workbuddy-expert-market/releases/download/"
                "echo-content/skill-registry.json"
            ),
            "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        },
        "skills": skills,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    print(
        f"✔ {args.out} — 共 {len(skills)} 个技能"
        f"(仓库扩展 {data['meta']['workbuddy_skills']} / Echo 源 {data['meta']['echo_skills']})"
    )


if __name__ == "__main__":
    main()
