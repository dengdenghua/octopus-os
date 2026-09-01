"""Apply a (reviewed) migration plan into echo — the write step.

Staging-first and safe by default:

* **skills** → copied into ``.echo/imported/<source>/skills/<name>/`` and
  become callable (registered *search-only*, like codex plugin skills): they
  return their SKILL.md instructions; actual execution still goes through the
  gated tool path.
* **memory / rules / agents / commands** → copied into
  ``.echo/imported/<source>/<kind>/`` for review. NOT auto-merged into
  echo's live memory — semantics differ per tool, so activation is explicit.
* **mcp servers** → recorded to ``.echo/imported/<source>/mcp.disabled.json``.
  Never auto-launched (that would be startup RCE) and never carrying credentials.

Idempotent: an item whose target already exists is skipped. ``dry_run=True``
counts what *would* happen without writing anything.
"""

from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .base import MigrationPlan

_FILE_KINDS = {"memory", "rule", "agent", "command"}


def _safe(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    return cleaned.strip("._") or "item"


@dataclass(frozen=True)
class ApplyReport:
    source: str
    target_root: str
    applied: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False


def apply_plan(
    plan: MigrationPlan,
    *,
    project_root: Path,
    kinds: set[str] | None = None,
    dry_run: bool = False,
) -> ApplyReport:
    """Materialize ``plan`` under ``<project_root>/.echo/imported/<source>/``.

    ``kinds`` (optional) restricts which item kinds to apply (e.g. ``{"skill"}``).
    """
    base = Path(project_root) / ".echo" / "imported" / plan.source
    applied: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    mcp_records: list[dict[str, object]] = []

    for item in plan.items:
        if kinds is not None and item.kind not in kinds:
            continue
        origin = Path(item.origin)

        if item.kind == "skill":
            dest = base / "skills" / _safe(item.name)
            if dest.exists():
                skipped["skill"] += 1
                continue
            if not dry_run:
                if not origin.is_dir():
                    skipped["skill"] += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(origin, dest)
            applied["skill"] += 1

        elif item.kind in _FILE_KINDS:
            stem = _safe(item.name)
            dest = base / item.kind / (stem if stem.endswith(".md") else f"{stem}.md")
            if dest.exists():
                skipped[item.kind] += 1
                continue
            if not dry_run:
                if not origin.is_file():
                    skipped[item.kind] += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, dest)
            applied[item.kind] += 1

        elif item.kind == "mcp_server":
            mcp_records.append(
                {
                    "name": item.name,
                    "needs": list(item.needs),
                    "origin": item.origin,
                    "enabled": False,  # never auto-launch; supply creds + enable explicitly
                }
            )
            applied["mcp_server"] += 1

    if mcp_records and not dry_run:
        base.mkdir(parents=True, exist_ok=True)
        (base / "mcp.disabled.json").write_text(
            json.dumps({"mcp_servers": mcp_records}, indent=2),
            encoding="utf-8",
        )

    return ApplyReport(
        source=plan.source,
        target_root=str(base),
        applied=dict(applied),
        skipped=dict(skipped),
        dry_run=dry_run,
    )
