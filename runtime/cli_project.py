"""``echo project`` — drive the milestone-driven Project OS from the terminal.

Ops: plan / run / report / list. Uses deterministic stub hooks by default so it
runs offline; the heavy LLM-backed execution path runs via ``echo serve``
(the API has the configured model router + subagent runner).
"""

from __future__ import annotations

from typing import Any

from runtime.cli_core import _Colors
from runtime.projectos.engine import (
    ProjectEngine,
    stub_decompose_tasks,
    stub_generate_milestones,
)
from runtime.projectos.store import ProjectStore


def _engine(store: ProjectStore) -> ProjectEngine:
    return ProjectEngine(
        store,
        generate_milestones=stub_generate_milestones,
        decompose_tasks=stub_decompose_tasks,
    )


def _print_report(store: ProjectStore, project_id: str, c: _Colors) -> int:
    project = store.get_project(project_id)
    if project is None:
        print(c.red(f"project not found: {project_id}"))
        return 2
    print(c.bold(f"\n{project.name}  [{project.id}]  — {project.status}"))
    print(c.dim(f"goal: {project.goal}"))
    for m in store.milestones_for(project_id):
        dot = c.green("●") if m.status == "done" else c.dim("○")
        print(f"\n  {dot} {c.bold(m.id)} {m.name} — {m.status}")
        if m.success_criteria:
            print(c.dim(f"      criteria: {', '.join(m.success_criteria)}"))
        for t in store.tasks_for_milestone(m.id):
            tdot = c.green("✓") if t.status == "done" else c.dim("·")
            out = (
                (str(t.output)[:80] + "…")
                if t.output and len(str(t.output)) > 80
                else (t.output or "")
            )
            print(f"      {tdot} {t.id} [{t.assigned_role}/{t.type}] {t.status}: {out}")
    return 0


def run_project_command(args: Any, *, color: bool = True) -> int:
    c = _Colors(color and not getattr(args, "no_color", False))
    store = ProjectStore()
    op = getattr(args, "project_op", None)

    if op == "list":
        projects = store.list_projects()
        if not projects:
            print(c.dim('no projects yet — `echo project plan --goal "..."`'))
            return 0
        for p in projects:
            print(f"  {c.bold(p.id)}  {p.name}  — {p.status}  ({len(p.milestone_ids)} milestones)")
        return 0

    if op == "plan":
        project = _engine(store).plan(args.name or args.goal[:40], args.goal)
        print(c.green(f"planned {project.id}") + f" — {len(project.milestone_ids)} milestones")
        return _print_report(store, project.id, c)

    if op == "report":
        return _print_report(store, args.id, c)

    if op == "run":
        project_id = getattr(args, "id", None)
        if not project_id:
            if not getattr(args, "goal", None):
                print(c.red('run needs --id <project> or --goal "..."'))
                return 2
            project = _engine(store).plan(args.name or args.goal[:40], args.goal)
            project_id = project.id
            print(c.dim(f"planned {project_id}"))
        if store.get_project(project_id) is None:
            print(c.red(f"project not found: {project_id}"))
            return 2
        result = _engine(store).run(project_id, max_ticks=getattr(args, "max_ticks", 50))
        print(
            c.bold(f"ran {result['ticks']} ticks → ")
            + (
                c.green(result["final_status"])
                if result["final_status"] == "done"
                else c.red(result["final_status"])
            )
        )
        return _print_report(store, project_id, c)

    print(c.red("unknown project op (plan|run|report|list)"))
    return 2
