"""Implementation note."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.suckers import SkillRegistry
from runtime.execution.suckers.builtins import register_all
from runtime.execution.suckers.write_skills import register_exec_skill
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import JSONLJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.recovery import (
    ForgeConfig,
    SkillForge,
)

from .bugfix_demo import build_bugfix_graph, setup_buggy_project


class _C:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def green(self, s): return self._w("32", s)
    def red(self, s): return self._w("31", s)
    def cyan(self, s): return self._w("36", s)
    def yellow(self, s): return self._w("33", s)
    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _populate_journal(
    root: Path, registry: SkillRegistry, n: int,
) -> Path:
    """Implementation note."""
    journal_path = root / "events.jsonl"
    journal = JSONLJournal(journal_path)
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)

    for i in range(n):
        proj = setup_buggy_project(root / f"proj_{i}")
        graph = build_bugfix_graph(proj)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        runtime.run(
            graph, budget=budget,
            caller="demos/evolution",
            arm_id=ArmId("demo_arm"),
        )
    return journal_path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _forge_and_promote(
    journal_path: Path,
    registry: SkillRegistry,
    *,
    persist_dir: Path | None = None,
    min_hits: int = 2,
    shadow_runs: int = 3,
    shadow_threshold: float = 0.7,
) -> dict[str, Any]:
    journal = JSONLJournal(journal_path)
    forge = SkillForge(
        journal=journal,
        registry=registry,
        config=ForgeConfig(
            min_hits=min_hits,
            min_success_rate=0.5,
            shadow_runs=shadow_runs,
            shadow_success_threshold=shadow_threshold,
        ),
        auto_persist_dir=persist_dir,
    )
    result = forge.run()
    return {
        "candidates_total": result.candidates_total,
        "promoted": list(result.promoted),
        "shadow_failed": list(result.shadow_failed),
        "quarantined": list(result.quarantined),
        "retired": list(result.retired),
        "reports": {k: v.overall_passed for k, v in result.reports.items()},
    }


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _invoke_forged_skill(
    registry: SkillRegistry, skill_name: str, *, sample_args: dict[str, Any],
) -> dict[str, Any]:
    """Implementation note."""
    try:
        skill = registry.get(skill_name)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"registry.get failed: {e}"}
    try:
        output = skill.handler(**sample_args)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"handler raised: {type(e).__name__}: {e}"}
    return {"ok": True, "output_type": type(output).__name__}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def run_demo(
    *,
    workdir: Path | None = None,
    runs: int = 3,
    color: bool = True,
    verbose: bool = True,
) -> dict[str, Any]:
    c = _C(color)
    if shutil.which("git") is None:
        raise RuntimeError("git not on PATH · demo requires git")

    tmp_ctx = None
    if workdir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="echo-evolve-")
        root = Path(tmp_ctx.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)

    try:
        if verbose:
            print(c.bold("╭─────────────────────────────────────────────────╮"))
            print(c.bold("│ Echo Agent · Self-Evolution Demo                 │"))
            print(c.bold("╰─────────────────────────────────────────────────╯"))
            print()
            print(c.dim(f"  workdir: {root}"))
            print(c.dim(f"  bugfix runs: {runs}"))
            print()

        # Implementation note.
        registry = SkillRegistry()
        register_all(registry)
        register_exec_skill(registry)
        skills_before = set(registry.all_names())
        if verbose:
            print(c.bold(f"  ▸ before: {len(skills_before)} skills in registry"))

        # Implementation note.
        if verbose:
            print(c.bold(f"  ▸ running bugfix × {runs} ..."))
        journal_path = _populate_journal(root, registry, runs)
        journal = JSONLJournal(journal_path)
        total_events = len(journal.read_all())
        if verbose:
            print(c.dim(f"    journal events: {total_events}"))
            print()

        # Implementation note.
        if verbose:
            print(c.bold("  ▸ SkillForge.run() · propose + shadow + promote ..."))
        persist_dir = root / "forged"
        forge_result = _forge_and_promote(
            journal_path, registry,
            persist_dir=persist_dir,
            min_hits=2,
        )

        if verbose:
            print(
                f"    {c.dim('·')} candidates proposed: "
                f"{c.bold(str(forge_result['candidates_total']))}"
            )
            print(
                f"    {c.dim('·')} promoted:  {c.green(str(len(forge_result['promoted'])))} "
                f"· names: {forge_result['promoted']}"
            )
            if forge_result['shadow_failed']:
                print(
                    f"    {c.dim('·')} shadow_failed: "
                    f"{c.yellow(str(len(forge_result['shadow_failed'])))} "
                    f"· names: {forge_result['shadow_failed']}"
                )
            print()

        # Implementation note.
        skills_after = set(registry.all_names())
        new_skills = skills_after - skills_before
        new_skills & set(forge_result["promoted"])
        if verbose:
            print(c.bold(f"  ▸ after:  {len(skills_after)} skills in registry"))
            print(
                f"    {c.dim('·')} new: "
                f"{c.green(str(len(new_skills)))} → {sorted(new_skills)}"
            )
            print()

        # Implementation note.
        invocations: list[dict[str, Any]] = []
        if forge_result["promoted"]:
            if verbose:
                print(c.bold("  ▸ invoking promoted skill ..."))
            first = forge_result["promoted"][0]
            sample_args = {"path": str(root / "proj_0" / "demo_project")}
            inv = _invoke_forged_skill(registry, first, sample_args=sample_args)
            invocations.append({"name": first, **inv})
            if verbose:
                if inv["ok"]:
                    print(
                        f"    {c.green('✓')} {first} · "
                        f"output type: {inv['output_type']}"
                    )
                else:
                    print(f"    {c.red('✗')} {first} · {inv['error']}")
            print()

        # Implementation note.
        persisted = []
        if persist_dir.exists():
            persisted = sorted(str(p.name) for p in persist_dir.glob("*.md"))
        if verbose and persisted:
            print(c.dim(f"  persisted .md files: {persisted}"))
            print()

        # A run is "successful evolution" if the forge proposed at least one
        # candidate AND took a definite action on it — promoted it, rejected
        # it on shadow tests, or quarantined it for approval because it wraps
        # dangerous primitives. All three exercise the forge end-to-end; only
        # "no candidate proposed" is a non-result.
        success = (
            forge_result["candidates_total"] >= 1
            and (
                len(forge_result["promoted"]) >= 1
                or len(forge_result["shadow_failed"]) >= 1
                or len(forge_result.get("quarantined", [])) >= 1
            )
        )
        if verbose:
            if forge_result["promoted"]:
                print(c.green(c.bold(
                    f"  ✓ self-evolution verified: forged + promoted "
                    f"{len(forge_result['promoted'])} new skill(s) into registry",
                )))
            elif forge_result.get("quarantined"):
                print(c.yellow(c.bold(
                    f"  ⚠ forge proposed {forge_result['candidates_total']} "
                    f"candidate(s) · quarantined for approval (wraps dangerous "
                    "primitives) · the immune gate worked",
                )))
            elif forge_result["shadow_failed"]:
                print(c.yellow(c.bold(
                    f"  ⚠ forge proposed {forge_result['candidates_total']} "
                    f"candidate(s) but shadow tests rejected · "
                    "this is also a valid evolution outcome (safety gate worked)",
                )))
            else:
                print(c.red("  ✗ no candidates proposed · try more runs"))

        return {
            "success": success,
            "journal_path": str(journal_path),
            "event_count": total_events,
            "skills_before": len(skills_before),
            "skills_after": len(skills_after),
            "new_skill_count": len(new_skills),
            "new_skill_names": sorted(new_skills),
            "forge": forge_result,
            "invocations": invocations,
            "persisted_files": persisted,
        }
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def main() -> int:
    result = run_demo(verbose=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
