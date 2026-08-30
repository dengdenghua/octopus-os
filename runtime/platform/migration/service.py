"""Run source adapters and render a dry-run migration plan.

This is the read-only entrypoint: ``build_migration_plans()`` returns what each
installed source tool offers; ``render_plan_summary()`` turns that into a short
human preview. Applying a plan (writing into echo, behind a trust gate) is a
separate step layered on top.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from .base import MigrationPlan
from .claude_adapter import scan_claude
from .codex_adapter import scan_codex
from .qoder_adapter import scan_qoder

_ADAPTERS = {
    "codex": scan_codex,
    "claude": scan_claude,
    "qoder": scan_qoder,
}

SUPPORTED_SOURCES = tuple(_ADAPTERS)


def build_migration_plans(
    sources: Iterable[str] | None = None,
    *,
    home: Path | None = None,
) -> list[MigrationPlan]:
    """Scan the requested source tools (default: all supported) and return a
    read-only plan per source. Never mutates either side."""
    chosen = list(sources) if sources is not None else list(_ADAPTERS)
    return [_ADAPTERS[s](home) for s in chosen if s in _ADAPTERS]


def render_plan_summary(plans: Iterable[MigrationPlan]) -> str:
    lines: list[str] = []
    for plan in plans:
        if not plan.available:
            lines.append(f"[{plan.source}] not installed — skipped")
            continue
        breakdown = ", ".join(f"{k}×{v}" for k, v in sorted(plan.kinds().items())) or "nothing"
        lines.append(f"[{plan.source}] {len(plan.items)} importable — {breakdown}")
        attn = plan.needing_attention()
        if attn:
            shown = ", ".join(f"{i.name}[{'/'.join(i.needs) or 'not-portable'}]" for i in attn[:10])
            more = f" (+{len(attn) - 10} more)" if len(attn) > 10 else ""
            lines.append(f"    needs follow-up ({len(attn)}): {shown}{more}")
    return "\n".join(lines)
