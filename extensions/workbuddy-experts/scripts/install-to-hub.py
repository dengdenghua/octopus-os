#!/usr/bin/env python3
"""Install forked WorkBuddy experts/skills into the local echo skill hub.

Populates ~/.echo/skills/ (SKILL.md + meta.json per skill) and rebuilds
~/.echo/skills/registry.json so `python -m runtime skills list|search|info`
and the UI skill market see the WorkBuddy content.

Reversible: re-running rebuilds registry from the same source; `--remove`
deletes only the entries this script installed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

FORK_ROOT = Path(__file__).resolve().parent.parent
SKILL_SOURCES = [
    ("senior-developer", "fullstack-dev", "engineering", "Full-stack development architecture & build guide"),
    ("senior-developer", "frontend-dev", "engineering", "Frontend UI/CSS/component development with AI media generation"),
    ("senior-developer", "browser-use", "automation", "Browser automation: navigate, click, screenshot, extract"),
    ("senior-developer", "capability-evolver", "meta", "AI agent self-evolution engine over run history"),
    ("seo-content-team", "seo-analysis", "marketing", "SEO on-page analysis & keyword clustering"),
    ("seo-content-team", "content-writing", "marketing", "SEO long-form content writing workflow"),
    ("seo-content-team", "cro-optimization", "marketing", "CRO & landing-page conversion optimization"),
    ("stock-partner-team", "westock-data", "finance", "Fetch quotes/financials via Tencent westock connector"),
    ("stock-partner-team", "westock-tool", "finance", "Stock screening & watchlist tools via westock"),
    ("stock-partner-team", "md-to-html", "finance", "Render markdown research reports to styled HTML"),
]

# Builtin tencent-docx writing experts become standalone skills (whole package:
# SKILL.md + references/ + scripts/ assets).
BUILTIN_EXPERTS = [
    "general-writer", "academic-paper-expert", "business-copy-expert",
    "legal-contract-expert", "poetry-prose-expert", "science-writing-expert",
    "stock-research-report-expert", "tech-blog-expert", "work-report-expert",
]


def default_skills_dir() -> Path:
    return Path.home() / ".echo" / "skills"


def build_entries() -> list[dict]:
    entries: list[dict] = []
    # marketplace skills
    for plugin, skill, cat, desc in SKILL_SOURCES:
        src = FORK_ROOT / "marketplace-experts" / "plugins" / plugin / "skills" / skill
        if not (src / "SKILL.md").exists():
            print(f"  ! skip missing {plugin}/{skill}")
            continue
        entries.append({
            "name": skill,
            "version": "0.1.0",
            "author": f"workbuddy-builtin:{plugin}",
            "description": desc,
            "tags": [cat, "workbuddy", plugin],
            "source": "workbuddy-expert-fork",
        })
    # builtin writing experts
    for name in BUILTIN_EXPERTS:
        src = FORK_ROOT / "builtin" / "tencent-docx-experts" / name
        if not (src / "SKILL.md").exists():
            print(f"  ! skip missing builtin {name}")
            continue
        entries.append({
            "name": name,
            "version": "0.1.0",
            "author": "workbuddy-builtin:tencent-docx",
            "description": f"WorkBuddy writing expert: {name}",
            "tags": ["writing", "docx", "workbuddy", "expert"],
            "source": "workbuddy-expert-fork",
        })
    return entries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skills-dir", default=str(default_skills_dir()))
    ap.add_argument("--remove", action="store_true", help="remove only the skills this script installed")
    args = ap.parse_args()

    skills_dir = Path(args.skills_dir).expanduser()
    registry_path = skills_dir / "registry.json"
    installed_names = [e["name"] for e in build_entries()]

    if args.remove:
        if not skills_dir.exists():
            print("hub skills dir does not exist; nothing to remove")
            return 0
        removed = 0
        for name in installed_names:
            if (skills_dir / name).exists():
                shutil.rmtree(skills_dir / name, ignore_errors=True)
                removed += 1
        # prune registry entries whose source is the fork
        if registry_path.exists():
            try:
                reg = json.loads(registry_path.read_text(encoding="utf-8"))
            except Exception:
                reg = []
            kept = [e for e in reg if e.get("source") != "workbuddy-expert-fork"]
            registry_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"removed {removed} skills from hub")
        return 0

    entries = build_entries()
    skills_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for e in entries:
        name = e["name"]
        # locate source
        src = None
        for plugin, skill, *_ in SKILL_SOURCES:
            if skill == name:
                src = FORK_ROOT / "marketplace-experts" / "plugins" / plugin / "skills" / skill
        if src is None and name in BUILTIN_EXPERTS:
            src = FORK_ROOT / "builtin" / "tencent-docx-experts" / name
        if src is None or not src.exists():
            continue
        dest = skills_dir / name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(src, dest)
        # write meta.json
        meta = {
            "name": name,
            "version": e["version"],
            "author": e["author"],
            "description": e["description"],
            "tags": e["tags"],
            "source": e["source"],
        }
        (dest / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        copied += 1

    # merge registry (keep pre-existing entries)
    old = []
    if registry_path.exists():
        try:
            old = json.loads(registry_path.read_text(encoding="utf-8"))
        except Exception:
            old = []
    old_names = {e.get("name") for e in old}
    merged = old + [e for e in entries if e["name"] not in old_names]
    registry_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"installed {copied}/{len(entries)} skills into {skills_dir}")
    print(f"registry entries: {len(merged)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
