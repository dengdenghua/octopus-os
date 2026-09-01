"""Migration adapter for OpenAI Codex (``~/.codex``).

Read-only scan that maps a Codex install onto echo's importable surfaces:

* **skills**  — ``plugins/cache/<marketplace>/<plugin>/skills/<skill>/SKILL.md``
  (Anthropic SKILL.md format — echo already loads it natively).
* **memory**  — ``AGENTS.md`` (global instructions, skipped when empty).
* **rules**   — ``rules/*.rules``.
* **mcp**     — ``config.toml`` ``[mcp_servers.*]`` (imported disabled; the
  server still needs its runtime/credentials, never auto-launched).
"""

from __future__ import annotations

from pathlib import Path

from .base import MigrationItem, MigrationPlan, mcp_needs, read_skill_meta


def _mcp_servers(config_toml: Path) -> list[tuple[str, tuple[str, ...]]]:
    if not config_toml.is_file():
        return []
    try:
        import tomllib  # py3.11+

        data = tomllib.loads(config_toml.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return []
    servers = data.get("mcp_servers")
    if not isinstance(servers, dict):
        return []
    return [(str(name), mcp_needs(spec)) for name, spec in servers.items()]


def scan_codex(home: Path | None = None) -> MigrationPlan:
    root = (home or Path.home()) / ".codex"
    if not root.is_dir():
        return MigrationPlan("codex", (), available=False)

    items: list[MigrationItem] = []

    # ── skills (SKILL.md bundles inside installed plugins) ──
    # Codex caches plugins as cache/<marketplace>/<plugin>/<version>/ — anchor
    # on the .codex-plugin manifest so the version dir (any depth) doesn't matter.
    plugin_cache = root / "plugins" / "cache"
    seen_skills: set[str] = set()
    if plugin_cache.is_dir():
        for manifest in sorted(plugin_cache.rglob(".codex-plugin/plugin.json")):
            plugin_root = manifest.parent.parent
            for skill_md in sorted((plugin_root / "skills").glob("*/SKILL.md")):
                name, desc = read_skill_meta(skill_md)
                if name in seen_skills:  # dedup across cached versions
                    continue
                seen_skills.add(name)
                items.append(
                    MigrationItem("skill", name, "codex", desc, str(skill_md.parent)),
                )

    # ── memory / rules (markdown-ish instruction files) ──
    agents = root / "AGENTS.md"
    if agents.is_file() and agents.stat().st_size > 0:
        items.append(
            MigrationItem("memory", "AGENTS.md", "codex", "global agent instructions", str(agents)),
        )
    rules_dir = root / "rules"
    if rules_dir.is_dir():
        for rule in sorted(rules_dir.glob("*.rules")):
            items.append(MigrationItem("rule", rule.name, "codex", "agent rules", str(rule)))

    # ── MCP servers (config only — imported disabled, never auto-launched) ──
    config_toml = root / "config.toml"
    for name, needs in _mcp_servers(config_toml):
        items.append(
            MigrationItem(
                "mcp_server",
                name,
                "codex",
                "MCP server — import disabled; supply runtime/credentials to enable",
                str(config_toml),
                portable=True,
                needs=needs,
            ),
        )

    return MigrationPlan("codex", tuple(items))
