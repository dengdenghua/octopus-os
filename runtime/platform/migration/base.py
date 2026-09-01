"""Canonical model for migrating other AI coding tools into echo.

The first stage is **read-only planning** ("dry run"): each adapter scans a
source tool's config directory and returns a :class:`MigrationPlan` describing
what *could* be imported — without touching either side. Applying a plan
(writing skills/memory/MCP into echo, behind a trust gate) is a separate
step built on top of these plans.

Stdlib-only and side-effect-free on purpose, so the planner is safe to run
anywhere and easy to unit-test against temp directories.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationItem:
    """One importable artifact discovered in a source tool."""

    kind: str  # skill | memory | rule | mcp_server | agent | command
    name: str
    source: str  # codex | claude | ...
    summary: str = ""
    origin: str = ""  # absolute path it was found at
    portable: bool = True  # can echo use it as-is?
    needs: tuple[str, ...] = ()  # required to actually work, e.g. ("credentials",)


@dataclass(frozen=True)
class MigrationPlan:
    """What a single source tool offers for import (read-only)."""

    source: str
    items: tuple[MigrationItem, ...] = ()
    available: bool = True  # was the source tool even installed?

    def kinds(self) -> dict[str, int]:
        return dict(Counter(i.kind for i in self.items))

    def needing_attention(self) -> tuple[MigrationItem, ...]:
        """Items that import but won't fully work without follow-up
        (e.g. an MCP server that needs credentials / a runtime)."""
        return tuple(i for i in self.items if not i.portable or i.needs)


def read_skill_meta(skill_md: Path) -> tuple[str, str]:
    """Best-effort ``(name, description)`` from a SKILL.md / memory-md
    frontmatter. Falls back to the parent dir / file stem on any error.

    A tiny inline parse (no YAML dep, no cross-layer import) — both Codex and
    Claude skills, and Claude memory files, share the ``name:`` / ``description:``
    frontmatter shape.
    """
    name = skill_md.parent.name if skill_md.name == "SKILL.md" else skill_md.stem
    desc = ""
    try:
        text = skill_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return name, desc
    if not text.startswith("---"):
        return name, desc
    end = text.find("\n---", 3)
    fm = text[3:end] if end > 0 else ""
    for line in fm.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("name:"):
            name = s.split(":", 1)[1].strip().strip("\"'") or name
        elif low.startswith("description:"):
            desc = s.split(":", 1)[1].strip().strip("\"'")
    return name, desc


def mcp_needs(spec: object) -> tuple[str, ...]:
    """Infer what an MCP server entry needs before it can actually run.

    Importing the *config* never makes a server work on its own — this flags
    what the user must still supply (so the dry-run is honest, and the later
    apply step imports it disabled/review-required rather than auto-launching).
    """
    needs: list[str] = []
    if isinstance(spec, dict):
        if spec.get("env"):
            needs.append("credentials")
        if spec.get("url") or spec.get("type") == "http":
            needs.append("auth")
        cmd = str(spec.get("command", "")).lower()
        if "npx" in cmd or "node" in cmd:
            needs.append("node")
        if "uvx" in cmd or "python" in cmd:
            needs.append("python")
    return tuple(needs) or ("review",)
