"""Activation step: staged memory → project MEMORY.md; MCP → config snippet.

Hermetic — stages a fake ``.echo/imported/`` under tmp_path and activates,
asserting memory is indexed idempotently and MCP produces a paste snippet
*without* touching config.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path

from runtime.cli_migrate import run_migrate
from runtime.platform.migration import (
    activate_mcp_snippet,
    activate_memory,
    activate_plan,
)


def _stage(proj: Path) -> None:
    base = proj / ".echo" / "imported" / "codex"
    (base / "memory").mkdir(parents=True)
    (base / "memory" / "pref.md").write_text(
        "---\nname: pref\ndescription: likes tests\n---\nbody\n",
        encoding="utf-8",
    )
    (base / "mcp.disabled.json").write_text(
        json.dumps(
            {
                "mcp_servers": [
                    {
                        "name": "node_repl",
                        "needs": ["node"],
                        "origin": "/x/config.toml",
                        "enabled": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def test_activate_memory_indexes_into_project_memory_idempotently(tmp_path: Path) -> None:
    _stage(tmp_path)

    added, skipped = activate_memory(tmp_path)
    assert (added, skipped) == (1, 0)

    index = (tmp_path / ".echo" / "MEMORY.md").read_text(encoding="utf-8")
    assert "[codex] pref: likes tests" in index
    assert ".echo/imported/codex/memory/pref.md" in index  # pointer to full text

    # second run is a no-op (already indexed)
    again = activate_memory(tmp_path)
    assert again == (0, 1)


def test_activate_mcp_writes_snippet_not_config(tmp_path: Path) -> None:
    _stage(tmp_path)

    snippets = activate_mcp_snippet(tmp_path)
    assert "codex" in snippets

    snippet = Path(snippets["codex"]).read_text(encoding="utf-8")
    assert "mcp_servers:" in snippet
    assert "node_repl" in snippet
    assert "# needs: node" in snippet
    # never mutates the real config
    assert not (tmp_path / "config.yaml").exists()


def test_activate_plan_combines_both(tmp_path: Path) -> None:
    _stage(tmp_path)
    report = activate_plan(tmp_path)
    assert report.memory_added == 1
    assert "codex" in report.mcp_snippets


def test_run_migrate_preview_returns_zero(tmp_path: Path) -> None:
    # preview is read-only; safe to run against the real machine, returns 0
    assert run_migrate(project_root=tmp_path, apply=False) == 0
    assert not (tmp_path / ".echo").exists()  # preview writes nothing


def test_run_migrate_apply_activate_smoke(tmp_path: Path) -> None:
    # full path into a temp project root (reads real ~/.codex/~/.claude if present,
    # writes only under tmp_path) — must not raise and must return 0.
    assert run_migrate(project_root=tmp_path, apply=True, activate=True) == 0

