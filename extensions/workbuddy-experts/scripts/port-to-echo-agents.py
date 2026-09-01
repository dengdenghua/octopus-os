#!/usr/bin/env python3
"""Port forked WorkBuddy experts into echo-agent's agents/ store format.

Reads a WorkBuddy `.codebuddy-plugin` expert package and writes a native
echo agent tree:

    agents/<slug>/
    ├── profile.jsonc          # from plugin.json (displayName/profession/category/tags/avatar)
    ├── avatar.{png|svg}
    ├── agent-core/
    │   ├── SOUL.md            # persona + identity from the agent md
    │   ├── IDENTITY.md        # name / profession / role
    │   ├── AGENTS.md          # full agent md body (working rules)
    │   ├── BOOTSTRAP.md       # empty placeholder
    │   ├── MEMORY.md          # empty placeholder (learned over time)
    │   ├── USER.md            # empty placeholder
    │   └── tool-registry.jsonc# arms + extra_affinity + private_skills
    └── skills/                # copied plugin skills (also mirrored to ~/.echo/skills)

Usage:
  python3 scripts/port-to-echo-agents.py \
      --market extensions/workbuddy-experts/marketplace-experts \
      --agents-root ../agents \
      --names embedded-firmware-engineer workspace-builder senior-developer
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

FORK_ROOT = Path(__file__).resolve().parent.parent

_CATEGORY_MAP = {
    "01-ProductDesign": "design",
    "02-Engineering": "engineering",
    "03-GameSpatial": "game",
    "04-DataAI": "data",
    "05-MarketingGrowth": "marketing",
    "06-ContentCreative": "content",
    "07-SalesCommerce": "commerce",
    "08-FinanceInvestment": "finance",
    "09-OperationsHR": "operations",
    "10-ProjectQuality": "project",
    "11-SecurityCompliance": "security",
    "12-IndustryConsultant": "consultant",
}

_ARM_BY_SKILL = {
    "browser-use": "browser_interact",
    "browser_read": "browser_read",
    "westock-data": "web_read",
    "westock-tool": "web_read",
    "fullstack-dev": "shell",
    "frontend-dev": "fs_writer",
    "content-writing": "web_read",
    "seo-analysis": "web_read",
    "cro-optimization": "web_read",
    "md-to-html": "fs_writer",
}

_FRONTMATTER_RE = re.compile(r"\A\s*---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s or "expert"


def _load_plugin(market: Path, name: str) -> dict:
    pj = market / "plugins" / name / ".codebuddy-plugin" / "plugin.json"
    return json.loads(pj.read_text(encoding="utf-8"))


def _zh(d: object) -> str:
    if isinstance(d, dict):
        return str(d.get("zh") or d.get("en") or "")
    return str(d or "")


def _agent_md(pkg_dir: Path, agent_name: str) -> tuple[str, dict, str]:
    path = pkg_dir / "agents" / f"{agent_name}.md"
    text = path.read_text(encoding="utf-8")
    fm: dict = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            fm = dict(__import__("yaml").safe_load(m.group(1)) or {})
        except Exception:
            fm = {}
        body = text[m.end():].strip()
    return text, fm, body


def _arms(skills: list[str]) -> list[str]:
    arms: list[str] = []
    for s in skills:
        a = _ARM_BY_SKILL.get(s)
        if a and a not in arms:
            arms.append(a)
    if not arms:
        arms = ["web_read", "fs_writer"]
    if "shell" not in arms:
        arms.append("shell")
    return arms


def port_expert(market: Path, name: str, agents_root: Path, skills_hub: Path | None) -> str | None:
    pkg = market / "plugins" / name
    pj = _load_plugin(market, name)
    agent_name = str(pj.get("agentName") or name)
    expert_type = str(pj.get("expertType") or "agent")

    text, fm, body = _agent_md(pkg, agent_name)
    slug = _slug(agent_name)
    agent_dir = agents_root / slug
    if agent_dir.exists():
        return f"skip {name}: {slug} already exists"

    display_name = _zh(pj.get("displayName")) or _zh(fm.get("displayName")) or slug
    profession = _zh(pj.get("profession")) or _zh(fm.get("profession")) or ""
    description = _zh(pj.get("displayDescription")) or str(fm.get("description") or "")
    category_id = str(pj.get("categoryId") or "")
    category = _CATEGORY_MAP.get(category_id, "assistant")
    tags: list[str] = []
    for t in (pj.get("tags") or []):
        zh = _zh(t) if isinstance(t, dict) else str(t)
        if zh and zh not in tags:
            tags.append(zh)
    if not tags:
        tags = [category, "workbuddy", expert_type]

    # skills referenced by the plugin
    skill_names: list[str] = []
    for s in (pj.get("skills") or []):
        if isinstance(s, str):
            skill_names.append(Path(s).name)
    skill_names = [s for s in skill_names if s]

    # build agent tree
    (agent_dir / "agent-core").mkdir(parents=True)
    (agent_dir / "skills").mkdir(parents=True, exist_ok=True)

    profile = {
        "id": slug,
        "templateId": f"workbuddy:{name}",
        "templateVersion": str(pj.get("version") or "1.0.0"),
        "source_kind": "workbuddy-expert-fork",
        "source_plugin": name,
        "expertType": expert_type,
        "name": display_name,
        "icon": "🤖",
        "description": description,
        "avatar": "avatar.png",
        "model": {"provider": "auto", "name": "auto"},
        "runtime": "local",
        "creator": f"workbuddy-fork:{name}",
        "category": category,
        "tags": tags,
        "profession": profession,
    }
    (agent_dir / "profile.jsonc").write_text(
        json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # avatar: png if present else svg placeholder
    avatar_src = pkg / "avatars"
    png = next((avatar_src.glob("expert.png")), None) or next(avatar_src.glob("*.png"), None)
    if png:
        shutil.copy2(png, agent_dir / "avatar.png")
    else:
        (agent_dir / "avatar.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128"><rect width="128" height="128" rx="24" fill="#333"/><text x="64" y="80" font-size="48" text-anchor="middle" fill="#fff">{display_name[:1]}</text></svg>',
            encoding="utf-8",
        )
        profile["avatar"] = "avatar.svg"

    # agent-core files
    (agent_dir / "agent-core" / "SOUL.md").write_text(
        f"# {display_name}\n\n" + (fm.get("description") and f"> {fm.get('description')}\n\n" or "") + body[:6000] + "\n",
        encoding="utf-8",
    )
    (agent_dir / "agent-core" / "IDENTITY.md").write_text(
        f"# Identity\n\n- **Name**: {display_name}\n- **Profession**: {profession}\n- **Role**: {description[:200]}\n- **Source**: WorkBuddy expert `{name}` (fork)\n",
        encoding="utf-8",
    )
    (agent_dir / "agent-core" / "AGENTS.md").write_text(body + "\n", encoding="utf-8")
    for f in ("BOOTSTRAP.md", "MEMORY.md", "USER.md"):
        (agent_dir / "agent-core" / f).write_text("", encoding="utf-8")

    (agent_dir / "agent-core" / "tool-registry.jsonc").write_text(
        json.dumps(
            {
                "arms": _arms(skill_names),
                "extra_affinity": tags + [category],
                "private_skills": skill_names,
                "workbuddy_team": expert_type == "team",
                "workbuddy_lead_agent": agent_name if expert_type == "team" else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # copy skills into the agent tree (mirror to hub skills dir if given)
    for skill_name in skill_names:
        src_skill = pkg / "skills" / skill_name
        if not (src_skill / "SKILL.md").exists():
            continue
        shutil.copytree(src_skill, agent_dir / "skills" / skill_name)
        if skills_hub is not None and not (skills_hub / skill_name).exists():
            shutil.copytree(src_skill, skills_hub / skill_name)

    return f"ported {name} → agents/{slug} [{expert_type}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default=str(FORK_ROOT / "marketplace-experts"))
    ap.add_argument("--agents-root", default="agents")
    ap.add_argument("--skills-hub", default=str(Path.home() / ".echo" / "skills"))
    ap.add_argument("--names", nargs="*", default=None)
    args = ap.parse_args()

    market = Path(args.market).resolve()
    agents_root = Path(args.agents_root).resolve()
    agents_root.mkdir(parents=True, exist_ok=True)
    skills_hub = Path(args.skills_hub).expanduser() if args.skills_hub else None

    available = sorted(d.name for d in (market / "plugins").iterdir() if d.is_dir())
    names = args.names or available
    for name in names:
        if name not in available:
            print(f"  ! unknown plugin: {name}")
            continue
        print("  " + (port_expert(market, name, agents_root, skills_hub) or "ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
