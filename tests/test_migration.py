"""Read-only migration planner: Codex + Claude adapters.

Hermetic — each test builds a fake ``~/.codex`` / ``~/.claude`` under tmp_path
and asserts the dry-run plan, so nothing touches the real machine.
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime.platform.migration import (
    build_migration_plans,
    render_plan_summary,
    scan_claude,
    scan_codex,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fake_codex(home: Path) -> None:
    c = home / ".codex"
    # mirror real codex layout: cache/<marketplace>/<plugin>/<version>/ with a
    # .codex-plugin manifest beside the skills dir.
    plugin = c / "plugins" / "cache" / "openai-primary-runtime" / "pdf" / "26.1"
    _write(plugin / ".codex-plugin" / "plugin.json", '{"name": "pdf"}')
    _write(
        plugin / "skills" / "fill-form" / "SKILL.md",
        "---\nname: fill-form\ndescription: Fill a PDF form\n---\nDo it.\n",
    )
    _write(c / "AGENTS.md", "Always write tests.\n")  # non-empty → memory
    _write(c / "rules" / "default.rules", "rule: be careful\n")
    _write(
        c / "config.toml",
        '[mcp_servers.node_repl]\ncommand = "node"\n[mcp_servers.node_repl.env]\nTOKEN = "x"\n',
    )


def _fake_claude(home: Path) -> None:
    c = home / ".claude"
    base = c / "plugins" / "marketplaces" / "official" / "plugins" / "gh"
    _write(
        base / "skills" / "pr-review" / "SKILL.md",
        "---\nname: pr-review\ndescription: Review PRs\n---\nx\n",
    )
    _write(base / "agents" / "reviewer.md", "# reviewer\n")
    _write(base / "commands" / "ship.md", "# ship\n")
    _write(
        c / "projects" / "proj" / "memory" / "pref.md",
        "---\nname: user-pref\ndescription: prefers chinese\n---\nbody\n",
    )
    _write(c / "projects" / "proj" / "memory" / "MEMORY.md", "- index\n")  # must be skipped
    _write(
        home / ".claude.json",
        json.dumps(
            {
                "mcpServers": {"globalsrv": {"command": "npx -y x"}},
                "projects": {"/p": {"mcpServers": {"projsrv": {"url": "http://x"}}}},
            }
        ),
    )


def test_codex_adapter_maps_all_surfaces(tmp_path: Path) -> None:
    _fake_codex(tmp_path)
    plan = scan_codex(home=tmp_path)

    assert plan.available
    assert plan.kinds() == {"skill": 1, "memory": 1, "rule": 1, "mcp_server": 1}
    skill = next(i for i in plan.items if i.kind == "skill")
    assert skill.name == "fill-form" and skill.source == "codex"
    mcp = next(i for i in plan.items if i.kind == "mcp_server")
    # env + node command → both flagged as follow-up needs
    assert "credentials" in mcp.needs and "node" in mcp.needs


def test_claude_adapter_maps_skills_agents_commands_memory_mcp(tmp_path: Path) -> None:
    _fake_claude(tmp_path)
    plan = scan_claude(home=tmp_path)

    assert plan.available
    k = plan.kinds()
    assert k.get("skill") == 1
    assert k.get("agent") == 1
    assert k.get("command") == 1
    assert k.get("memory") == 1  # MEMORY.md index skipped
    assert k.get("mcp_server") == 2  # global + project
    mem = next(i for i in plan.items if i.kind == "memory")
    assert mem.name == "user-pref"  # read from frontmatter, not filename


def test_absent_sources_marked_unavailable(tmp_path: Path) -> None:
    for plan in build_migration_plans(home=tmp_path):  # empty home
        assert plan.available is False
        assert plan.items == ()


def test_service_builds_both_and_renders(tmp_path: Path) -> None:
    _fake_codex(tmp_path)
    _fake_claude(tmp_path)
    plans = build_migration_plans(("codex", "claude"), home=tmp_path)
    assert {p.source for p in plans} == {"codex", "claude"}

    summary = render_plan_summary(plans)
    assert "[codex]" in summary and "[claude]" in summary
    assert "needs follow-up" in summary  # the MCP servers

