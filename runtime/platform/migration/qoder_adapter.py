"""Migration adapter for Qoder (Alibaba's agentic AI IDE, ``~/.qoder``).

Read-only scan mapping a Qoder install onto echo's importable surfaces:

* **skills**   — ``plugins/cache/<market>/<plugin>/[<version>/]skills/<skill>/SKILL.md``
  anchored on the ``.qoder-plugin/plugin.json`` manifest (so the optional
  version dir at any depth doesn't matter). Anthropic SKILL.md format —
  echo loads it natively. Same shape as Codex plugins.
* **memory**   — ``memories/<id>/{projects,global}/**/*.md`` (Qoder's persisted
  project/global memory; markdown, frontmatter optional).
* **recipes**  — ``canvas/recipes/*.recipe.md`` (Qoder Canvas flows; imported as
  reference commands — Canvas-specific, so flagged non-portable).
* **mcp**      — ``<AppSupport>/Qoder/SharedClientCache/mcp.json`` ``mcpServers``
  (standard MCP shape, like Claude; imported disabled, never auto-launched).

Stdlib-only and side-effect-free, like the other adapters.
"""

from __future__ import annotations

import json
from pathlib import Path

from .base import MigrationItem, MigrationPlan, mcp_needs, read_skill_meta

# Safety cap — a long-running Qoder install can accrue a lot of memory md.
_MAX_MEMORY = 200


def _qoder_mcp(mcp_json: Path) -> list[MigrationItem]:
    if not mcp_json.is_file():
        return []
    try:
        data = json.loads(mcp_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return []
    return [
        MigrationItem(
            "mcp_server",
            str(name),
            "qoder",
            "MCP server — import disabled; supply runtime/credentials to enable",
            str(mcp_json),
            portable=True,
            needs=mcp_needs(spec),
        )
        for name, spec in servers.items()
    ]


def _user_mcp_path(base: Path) -> Path:
    # Qoder is an Electron/VSCode-family desktop app; its user MCP config lives
    # outside ~/.qoder. macOS path (the only desktop Qoder ships today).
    return base / "Library" / "Application Support" / "Qoder" / "SharedClientCache" / "mcp.json"


def scan_qoder(home: Path | None = None) -> MigrationPlan:
    base = home or Path.home()
    root = base / ".qoder"
    if not root.is_dir():
        return MigrationPlan("qoder", (), available=False)

    items: list[MigrationItem] = []

    # ── skills (SKILL.md bundles inside installed plugins) ──
    plugin_cache = root / "plugins" / "cache"
    seen_skills: set[str] = set()
    if plugin_cache.is_dir():
        for manifest in sorted(plugin_cache.rglob(".qoder-plugin/plugin.json")):
            plugin_root = manifest.parent.parent
            for skill_md in sorted((plugin_root / "skills").glob("*/SKILL.md")):
                name, desc = read_skill_meta(skill_md)
                if name in seen_skills:  # dedup across cached versions
                    continue
                seen_skills.add(name)
                items.append(
                    MigrationItem("skill", name, "qoder", desc, str(skill_md.parent)),
                )

    # ── memory (persisted project/global memory, markdown) ──
    memories = root / "memories"
    if memories.is_dir():
        for i, mem_md in enumerate(sorted(memories.rglob("*.md"))):
            if i >= _MAX_MEMORY:
                break
            name, desc = read_skill_meta(mem_md)
            items.append(MigrationItem("memory", name, "qoder", desc, str(mem_md)))

    # ── recipes (Canvas flows — reference commands, Canvas-specific) ──
    recipes = root / "canvas" / "recipes"
    if recipes.is_dir():
        for recipe in sorted(recipes.glob("*.recipe.md")):
            stem = recipe.name[: -len(".recipe.md")]
            _, desc = read_skill_meta(recipe)
            items.append(
                MigrationItem(
                    "command",
                    stem,
                    "qoder",
                    desc or "Qoder Canvas recipe",
                    str(recipe),
                    portable=False,
                    needs=("canvas",),
                ),
            )

    # ── MCP servers (user config, outside ~/.qoder; imported disabled) ──
    items.extend(_qoder_mcp(_user_mcp_path(base)))

    return MigrationPlan("qoder", tuple(items))
