"""``echo migrate`` — one-click migrate plugins/memory/MCP from other AI tools.

Thin CLI wrapper over ``runtime.platform.migration``:

* default            → preview (read-only dry-run plan)
* ``--apply``        → stage into ``.echo/imported/``
* ``--apply --activate`` → also activate memory + emit MCP config snippets
"""

from __future__ import annotations

from pathlib import Path


def run_migrate(
    *,
    sources: str | None = None,
    apply: bool = False,
    activate: bool = False,
    kinds: str | None = None,
    project_root: Path | None = None,
) -> int:
    from runtime.platform.migration import (
        activate_plan,
        apply_plan,
        build_migration_plans,
        render_plan_summary,
    )

    root = Path(project_root) if project_root else Path.cwd()
    src = [s.strip() for s in sources.split(",") if s.strip()] if sources else None
    kindset = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None

    plans = build_migration_plans(src)
    print(render_plan_summary(plans))

    if not apply:
        print(
            "\n(preview only — re-run with `--apply` to stage, "
            "or `--apply --activate` to also activate memory + emit MCP snippets)",
        )
        return 0

    print("\n— applying (staging into .echo/imported/) —")
    for plan in plans:
        if not plan.available:
            continue
        report = apply_plan(plan, project_root=root, kinds=kindset)
        print(f"  [{plan.source}] applied={report.applied} skipped={report.skipped}")

    if activate:
        print("\n— activating —")
        report = activate_plan(root, sources=set(src) if src else None)
        print(
            f"  memory: +{report.memory_added} entries into .echo/MEMORY.md "
            f"(skipped {report.memory_skipped} already-indexed)",
        )
        if report.mcp_snippets:
            for source, path in report.mcp_snippets.items():
                print(f"  mcp [{source}]: review + add credentials, then paste → {path}")
        else:
            print("  mcp: none to activate")

    return 0
