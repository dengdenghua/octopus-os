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
from runtime.memory.knowledge_graph import KnowledgeGraph
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.recovery import (
    ConsolidatorConfig,
    ExtractorConfig,
    ForgeConfig,
    KGUpdater,
    MemoryConsolidator,
    RecipeEvaluator,
    RuleExtractor,
    SkillForge,
    WorkflowRewriter,
)

from .bugfix_demo import build_bugfix_graph, setup_buggy_project


class _C:
    """Minimal color helper."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _w(self, code, s):
        return f"\033[{code}m{s}\033[0m" if self.enabled else s

    def green(self, s): return self._w("32", s)
    def cyan(self, s): return self._w("36", s)
    def yellow(self, s): return self._w("33", s)
    def bold(self, s): return self._w("1", s)
    def dim(self, s): return self._w("2", s)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _run_bugfix_n_times(root: Path, registry: SkillRegistry, n: int) -> Path:
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
            caller="demos/reflection",
            arm_id=ArmId("demo_arm"),
        )
    return journal_path


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _run_kg(journal: JSONLJournal, c: _C, verbose: bool) -> dict[str, Any]:
    kg = KnowledgeGraph()
    updater = KGUpdater(journal, kg)
    report = updater.update()
    if verbose:
        print(
            f"  {c.green('KGUpdater')}        · "
            f"accepted: {c.bold(str(report.triples_accepted))}  "
            f"proposed: {report.triples_proposed}  "
            f"ignored: {report.triples_ignored}  "
            f"kg total: {c.bold(str(kg.count()))}"
        )
        if kg.count() > 0:
            triples = list(kg.query())[:3]
            for t in triples:
                print(
                    f"    {c.dim('-')} ({t.subject}, {t.predicate}, {t.object})",
                )
    return {
        "accepted": report.triples_accepted,
        "total": kg.count(),
        "kg": kg,
    }


def _run_rules(journal: JSONLJournal, c: _C, verbose: bool) -> dict[str, Any]:
    extractor = RuleExtractor(
        journal,
        config=ExtractorConfig(min_hits=1),
    )
    report = extractor.extract()
    if verbose:
        print(
            f"  {c.green('RuleExtractor')}    · "
            f"rules: {c.bold(str(len(report.rules_produced)))}  "
            f"trajectories: {report.trajectories_scanned}  "
            f"failures: {report.failure_count}  "
            f"clusters: {report.clusters_formed}"
        )
        for rule in report.rules_produced[:3]:
            print(
                f"    {c.dim('-')} {rule.rule_id} · "
                f"{rule.mitigation}"[:120],
            )
    return {"rules": report.rules_produced}


def _run_memory(
    journal: JSONLJournal, c: _C, verbose: bool,
) -> dict[str, Any]:
    consolidator = MemoryConsolidator(
        journal,
        config=ConsolidatorConfig(min_samples_per_cluster=2),
    )
    report = consolidator.consolidate()
    if verbose:
        print(
            f"  {c.green('MemoryConsolidator')} · "
            f"trajectories scanned: {report.trajectories_scanned}  "
            f"memories produced: {c.bold(str(len(report.memories_produced)))}"
        )
        for m in report.memories_produced[:3]:
            print(
                f"    {c.dim('-')} [{m.tier}] {m.pattern_key} · "
                f"{m.trajectories_count} runs · "
                f"{m.success_rate * 100:.0f}% success",
            )
    return {"memories": report.memories_produced}


def _run_forge(
    journal: JSONLJournal, registry: SkillRegistry,
    c: _C, verbose: bool,
) -> dict[str, Any]:
    forge = SkillForge(
        journal=journal, registry=registry,
        config=ForgeConfig(min_hits=2, min_success_rate=0.5),
    )
    candidates = forge.propose()
    if verbose:
        print(
            f"  {c.green('SkillForge')}      · "
            f"candidates proposed: {c.bold(str(len(candidates)))}"
        )
        for cand in candidates[:3]:
            print(
                f"    {c.dim('-')} {cand.name} · "
                f"samples: {cand.source_sample_count} · "
                f"success: {cand.source_success_rate * 100:.0f}% · "
                f"seq len: {len(cand.underlying_sequence)}",
            )
    return {"candidates": candidates}


def _run_rewriter(
    journal: JSONLJournal, c: _C, verbose: bool,
) -> dict[str, Any]:
    rewriter = WorkflowRewriter(journal)
    report = rewriter.analyze(rules=None)
    if verbose:
        print(
            f"  {c.green('WorkflowRewriter')} · "
            f"proposals: {c.bold(str(len(report.proposals)))}"
        )
        for prop in report.proposals[:3]:
            print(
                f"    {c.dim('-')} [{prop.severity}] "
                f"{getattr(prop, 'kind', '?')} · hit={prop.hit_count}",
            )
    return {"proposals": report.proposals}


def _run_recipe(
    journal: JSONLJournal, c: _C, verbose: bool,
) -> dict[str, Any]:
    evaluator = RecipeEvaluator(journal)
    report = evaluator.evaluate()
    losing = [s for s in report.scores if s.verdict == "losing"]
    if verbose:
        print(
            f"  {c.green('RecipeEvaluator')}  · "
            f"recipes: {c.bold(str(report.recipes_found))}  "
            f"scores: {len(report.scores)}  "
            f"losing: {c.bold(str(len(losing)))}"
        )
        for score in report.scores[:3]:
            print(
                f"    {c.dim('-')} {score.recipe_id[:12]}… "
                f"verdict={score.verdict} · "
                f"success={score.success_rate * 100:.0f}% · "
                f"uses={score.uses}",
            )
    return {"scores": report.scores, "losing": losing}


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
    """Implementation note."""
    c = _C(color)
    if shutil.which("git") is None:
        raise RuntimeError("git not on PATH · demo requires git")

    tmp_ctx = None
    if workdir is None:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="echo-reflect-")
        root = Path(tmp_ctx.name)
    else:
        root = Path(workdir)
        root.mkdir(parents=True, exist_ok=True)

    try:
        if verbose:
            print(c.bold("╭─────────────────────────────────────────────────╮"))
            print(c.bold("│ Echo Agent · Reflection Closure Demo             │"))
            print(c.bold("╰─────────────────────────────────────────────────╯"))
            print()
            print(c.dim(f"  workdir: {root}"))
            print(c.dim(f"  bugfix runs: {runs}"))
            print()

        # Implementation note.
        registry = SkillRegistry()
        register_all(registry)
        register_exec_skill(registry)

        if verbose:
            print(c.bold(f"  ▸ running bugfix demo {runs}× to populate journal ..."))
        journal_path = _run_bugfix_n_times(root, registry, runs)
        journal = JSONLJournal(journal_path)

        total_events = len(journal.read_all())
        if verbose:
            print(c.dim(f"    journal events: {total_events}"))
            print()

        # Implementation note.
        if verbose:
            print(c.bold("  ▸ running all 6 reflection producers ..."))
            print()

        kg_out = _run_kg(journal, c, verbose)
        rules_out = _run_rules(journal, c, verbose)
        mem_out = _run_memory(journal, c, verbose)
        forge_out = _run_forge(journal, registry, c, verbose)
        rewrite_out = _run_rewriter(journal, c, verbose)
        recipe_out = _run_recipe(journal, c, verbose)

        if verbose:
            print()
            print(c.bold("  ▸ summary"))
            print(
                c.dim(
                    f"    journal events: {total_events}  ·  "
                    f"kg triples: {kg_out['total']}  ·  "
                    f"rules: {len(rules_out['rules'])}  ·  "
                    f"memories: {len(mem_out['memories'])}  ·  "
                    f"forge candidates: {len(forge_out['candidates'])}  ·  "
                    f"rewrite proposals: {len(rewrite_out['proposals'])}  ·  "
                    f"recipe scores: {len(recipe_out['scores'])}"
                ),
            )

            # Implementation note.
            non_empty_producers = sum([
                kg_out["accepted"] > 0,
                len(rules_out["rules"]) > 0,
                len(mem_out["memories"]) > 0,
                len(forge_out["candidates"]) > 0,
                len(rewrite_out["proposals"]) > 0,
                len(recipe_out["scores"]) > 0,
            ])
            print()
            if non_empty_producers >= 3:
                print(
                    c.green(c.bold(
                        f"  ✓ reflection closure verified: "
                        f"{non_empty_producers}/6 producers gave non-empty output",
                    ))
                )
            else:
                print(
                    c.yellow(c.bold(
                        f"  ⚠ reflection closure partial: "
                        f"only {non_empty_producers}/6 producers gave output "
                        "(need more/varied runs for full coverage)",
                    ))
                )

        return {
            "success": True,
            "journal_path": str(journal_path),
            "event_count": total_events,
            "kg_triples": kg_out["total"],
            "rules_count": len(rules_out["rules"]),
            "memories_count": len(mem_out["memories"]),
            "forge_candidates_count": len(forge_out["candidates"]),
            "rewrite_proposals_count": len(rewrite_out["proposals"]),
            "recipe_scores_count": len(recipe_out["scores"]),
            "non_empty_producers": sum([
                kg_out["accepted"] > 0,
                len(rules_out["rules"]) > 0,
                len(mem_out["memories"]) > 0,
                len(forge_out["candidates"]) > 0,
                len(rewrite_out["proposals"]) > 0,
                len(recipe_out["scores"]) > 0,
            ]),
        }
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def main() -> int:
    result = run_demo(verbose=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
