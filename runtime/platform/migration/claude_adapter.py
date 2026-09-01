"""Migration adapter for Claude Code (``~/.claude``).

Read-only scan mapping a Claude Code install onto echo's importable
surfaces:

* **skills**   — ``plugins/marketplaces/*/plugins|external_plugins/*/skills/*/SKILL.md``
* **agents**   — those plugins' ``agents/*.md`` (subagents)
* **commands** — those plugins' ``commands/*.md`` (slash commands)
* **memory**   — ``projects/*/memory/*.md`` (frontmatter: name/description/type —
  the same shape echo's own memory files use, so it maps 1:1).
* **mcp**      — ``~/.claude.json`` ``mcpServers`` (global + per-project),
  imported disabled; servers still need their runtime/credentials.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import MigrationItem, MigrationPlan, mcp_needs, read_skill_meta


def _plugin_dirs(root: Path) -> list[Path]:
    markets = root / "plugins" / "marketplaces"
    if not markets.is_dir():
        return []
    out: list[Path] = []
    for sub in ("plugins", "external_plugins"):
        out.extend(p for p in markets.glob(f"*/{sub}/*") if p.is_dir())
    return sorted(out)


def _claude_mcp(claude_json: Path) -> list[MigrationItem]:
    if not claude_json.is_file():
        return []
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    out: list[MigrationItem] = []
    seen: set[str] = set()

    def collect(mcp: object, scope: str) -> None:
        if not isinstance(mcp, dict):
            return
        for name, spec in mcp.items():
            if name in seen:
                continue
            seen.add(name)
            out.append(
                MigrationItem(
                    "mcp_server",
                    str(name),
                    "claude",
                    f"MCP server ({scope}) — import disabled; supply runtime/credentials",
                    str(claude_json),
                    portable=True,
                    needs=mcp_needs(spec),
                ),
            )

    collect(data.get("mcpServers"), "global")
    projects = data.get("projects")
    if isinstance(projects, dict):
        for pdata in projects.values():
            if isinstance(pdata, dict):
                collect(pdata.get("mcpServers"), "project")
    return out


def scan_claude(home: Path | None = None) -> MigrationPlan:
    base = home or Path.home()
    root = base / ".claude"
    if not root.is_dir():
        return MigrationPlan("claude", (), available=False)

    items: list[MigrationItem] = []

    # ── plugins: skills / agents / commands ──
    for plugin_dir in _plugin_dirs(root):
        for skill_md in sorted((plugin_dir / "skills").glob("*/SKILL.md")):
            name, desc = read_skill_meta(skill_md)
            items.append(MigrationItem("skill", name, "claude", desc, str(skill_md.parent)))
        agents_dir = plugin_dir / "agents"
        if agents_dir.is_dir():
            for agent_md in sorted(agents_dir.glob("*.md")):
                items.append(
                    MigrationItem("agent", agent_md.stem, "claude", "subagent", str(agent_md))
                )
        commands_dir = plugin_dir / "commands"
        if commands_dir.is_dir():
            for cmd_md in sorted(commands_dir.glob("*.md")):
                items.append(
                    MigrationItem("command", cmd_md.stem, "claude", "slash command", str(cmd_md))
                )

    # ── memory: projects/*/memory/*.md (skip the MEMORY.md index) ──
    projects = root / "projects"
    if projects.is_dir():
        for mem_dir in sorted(projects.glob("*/memory")):
            for mem_md in sorted(mem_dir.glob("*.md")):
                if mem_md.name == "MEMORY.md":
                    continue
                name, desc = read_skill_meta(mem_md)
                items.append(MigrationItem("memory", name, "claude", desc, str(mem_md)))

    # ── MCP servers (config only — imported disabled) ──
    items.extend(_claude_mcp(base / ".claude.json"))

    return MigrationPlan("claude", tuple(items))
