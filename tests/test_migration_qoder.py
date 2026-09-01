"""Tests for the Qoder migration adapter."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.platform.migration.qoder_adapter import scan_qoder
from runtime.platform.migration.service import (
    SUPPORTED_SOURCES,
    build_migration_plans,
)


def test_qoder_registered_as_source() -> None:
    assert "qoder" in SUPPORTED_SOURCES


def test_scan_qoder_not_installed(tmp_path: Path) -> None:
    plan = scan_qoder(home=tmp_path)
    assert plan.source == "qoder"
    assert plan.available is False
    assert plan.items == ()


def _make_qoder(base: Path) -> None:
    q = base / ".qoder"
    # skill: plugin anchored on .qoder-plugin/plugin.json + skills/<n>/SKILL.md
    # (version dir present, like a real marketplace install)
    plug = q / "plugins" / "cache" / "mkt" / "meoo" / "0.1.0"
    (plug / ".qoder-plugin").mkdir(parents=True)
    (plug / ".qoder-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    sk = plug / "skills" / "meoo-cli"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text(
        "---\nname: meoo-cli\ndescription: meoo cli skill\n---\nbody\n",
        encoding="utf-8",
    )
    # memory (markdown under memories/<id>/...)
    mem = q / "memories" / "abc" / "projects" / "p1" / "project_introduction"
    mem.mkdir(parents=True)
    (mem / "overview.md").write_text("# project overview", encoding="utf-8")
    # recipe (Canvas flow)
    rec = q / "canvas" / "recipes"
    rec.mkdir(parents=True)
    (rec / "code-review.recipe.md").write_text(
        "---\ndescription: review flow\n---\n",
        encoding="utf-8",
    )
    # MCP (user config OUTSIDE ~/.qoder — macOS app-support path)
    mcp = base / "Library" / "Application Support" / "Qoder" / "SharedClientCache"
    mcp.mkdir(parents=True)
    (mcp / "mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "x"], "env": {"K": "v"}}}},
        ),
        encoding="utf-8",
    )


def test_scan_qoder_maps_all_surfaces(tmp_path: Path) -> None:
    _make_qoder(tmp_path)
    plan = scan_qoder(home=tmp_path)

    assert plan.available is True
    kinds = plan.kinds()
    assert kinds.get("skill") == 1
    assert kinds.get("memory") == 1
    assert kinds.get("command") == 1  # recipe → command
    assert kinds.get("mcp_server") == 1

    skill = next(i for i in plan.items if i.kind == "skill")
    assert skill.name == "meoo-cli"
    assert skill.summary == "meoo cli skill"
    assert skill.source == "qoder"

    recipe = next(i for i in plan.items if i.kind == "command")
    assert recipe.name == "code-review"  # ".recipe.md" stripped
    assert recipe.portable is False
    assert "canvas" in recipe.needs

    mcp = next(i for i in plan.items if i.kind == "mcp_server")
    assert mcp.portable is True
    assert "credentials" in mcp.needs  # env present
    assert "node" in mcp.needs  # npx command


def test_build_migration_plans_includes_qoder(tmp_path: Path) -> None:
    _make_qoder(tmp_path)
    plans = build_migration_plans(sources=["qoder"], home=tmp_path)
    assert len(plans) == 1
    assert plans[0].source == "qoder"
    assert plans[0].available is True
    # recipe is the one needing follow-up (Canvas-specific)
    flagged = plans[0].needing_attention()
    assert any(i.kind == "command" for i in flagged)

