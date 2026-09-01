"""One-click migration of other AI coding tools into echo (read-only planning).

Adapters scan a source tool's config dir (``~/.codex``, ``~/.claude``, ...) and
produce a :class:`MigrationPlan` of importable skills / memory / rules / MCP
servers / agents / commands — without touching either side. Applying a plan is a
separate, trust-gated step built on top of these plans.
"""

from __future__ import annotations

from .activate import (
    ActivateReport,
    activate_mcp_snippet,
    activate_memory,
    activate_plan,
)
from .apply import ApplyReport, apply_plan
from .base import MigrationItem, MigrationPlan, mcp_needs, read_skill_meta
from .claude_adapter import scan_claude
from .codex_adapter import scan_codex
from .service import (
    SUPPORTED_SOURCES,
    build_migration_plans,
    render_plan_summary,
)

__all__ = [
    "SUPPORTED_SOURCES",
    "ActivateReport",
    "ApplyReport",
    "MigrationItem",
    "MigrationPlan",
    "activate_mcp_snippet",
    "activate_memory",
    "activate_plan",
    "apply_plan",
    "build_migration_plans",
    "mcp_needs",
    "read_skill_meta",
    "render_plan_summary",
    "scan_claude",
    "scan_codex",
]
