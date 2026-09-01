from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from runtime.cli_core import (
    _Colors,
    _slug_query,
    _try_reflex,
)
from runtime.memory.journal import InMemoryJournal, Journal, JSONLJournal
from runtime.platform.i18n import _
from runtime.platform.models import IntentType


def run_reflect(
    *,
    from_journal: Path,
    verbose: bool = False,
    skip: set[str] | None = None,
    color: bool = True,
    kg_db: Path | None = None,
) -> int:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.memory.knowledge_graph import KnowledgeGraph
    from runtime.safety.recovery import (
        ForgeConfig,
        KGUpdater,
        MemoryConsolidator,
        RuleExtractor,
        SkillForge,
        WorkflowRewriter,
        format_memories_for_prompt,
        format_proposals_for_review,
        format_rules_for_prompt,
    )

    c = _Colors(color)
    skip = skip or set()

    if not from_journal.exists():
        print(c.red(_("cli.reflect.not_found", path=from_journal)), file=sys.stderr)
        return 2

    journal = JSONLJournal(from_journal)
    total_events = len(journal)
    total_trajs = len(journal.read_by_type("trajectory"))

    print(c.bold(_("cli.reflect.title", path=from_journal)))
    print(c.dim("─" * 60))
    print(_("cli.reflect.journal_info", events=total_events, trajs=total_trajs))
    print()

    registry = SkillRegistry()
    register_all(registry)

    if "skill_forge" not in skip:
        result = SkillForge(
            journal=journal,
            registry=registry,
            config=ForgeConfig(governed_rollout=True),
        ).run()
        print(
            f"{c.cyan(_('cli.reflect.skillforge_label'))} · "
            f"{_('cli.reflect.skillforge_fmt', promoted=c.green(str(len(result.promoted))), retired=c.red(str(len(result.retired))), total=result.candidates_total)}"
        )
        if verbose and result.promoted:
            for name in result.promoted:
                print(c.dim(_("cli.reflect.skillforge_detail", name=name)))

    if "rule_extractor" not in skip:
        re_report = RuleExtractor(journal).extract()
        print(
            f"{c.cyan(_('cli.reflect.ruleextractor_label'))} · "
            f"{_('cli.reflect.ruleextractor_fmt', rules=len(re_report.rules_produced), failures=re_report.failure_count)}"
        )
        if verbose and re_report.rules_produced:
            prompt = format_rules_for_prompt(re_report.rules_produced, max_total_chars=800)
            print(c.dim("      " + prompt.replace("\n", "\n      ")))

    if "kg_updater" not in skip:
        # With --kg-db the distilled triples PERSIST to a durable store the
        # live agent's KG recall can read back (the apply half of the signal);
        # without it the legacy in-memory graph is analyze-only and discarded.
        if kg_db is not None:
            from runtime.memory.knowledge_graph.sqlite_kg import SqliteKnowledgeGraph

            kg: KnowledgeGraph = SqliteKnowledgeGraph(kg_db)
        else:
            kg = KnowledgeGraph()
        try:
            kg_report = KGUpdater(journal, kg).update()
            print(
                f"{c.cyan(_('cli.reflect.kgupdater_label'))} · "
                f"{_('cli.reflect.kgupdater_fmt', accepted=c.green(str(kg_report.triples_accepted)), ignored=kg_report.triples_ignored, count=kg.count())}"
            )
            if verbose:
                for t in list(kg)[:3]:
                    print(
                        c.dim(
                            _(
                                "cli.reflect.kg_detail",
                                subject=t.subject[:30],
                                predicate=t.predicate,
                                object=t.object[:40],
                            )
                        )
                    )
        finally:
            if hasattr(kg, "close"):
                kg.close()

    if "memory" not in skip:
        mc_report = MemoryConsolidator(journal).consolidate()
        print(
            f"{c.cyan(_('cli.reflect.memory_label'))}· "
            f"{_('cli.reflect.memory_fmt', memories=len(mc_report.memories_produced), clusters=mc_report.clusters_formed)}"
        )
        if verbose and mc_report.memories_produced:
            prompt = format_memories_for_prompt(mc_report.memories_produced, max_total_chars=800)
            print(c.dim("      " + prompt.replace("\n", "\n      ")))

    if "workflow_rewriter" not in skip:
        wr_report = WorkflowRewriter(journal).analyze()
        print(
            f"{c.cyan(_('cli.reflect.workflow_label'))}· "
            f"{_('cli.reflect.workflow_fmt', proposals=len(wr_report.proposals), by_kind=wr_report.proposals_by_kind)}"
        )
        if verbose and wr_report.proposals:
            text = format_proposals_for_review(wr_report.proposals, max_total_chars=1000)
            print(c.dim("      " + text.replace("\n", "\n      ")))

    if "recipe" not in skip:
        from runtime.safety.recovery import RecipeEvaluator, format_recipe_report

        rc_report = RecipeEvaluator(journal).evaluate()
        best = rc_report.best
        best_line = (
            f" · {_('cli.reflect.recipe_best', recipe=c.green(best.recipe_id), score=best.score)}"
            if best
            else ""
        )
        print(
            f"{c.cyan(_('cli.reflect.recipe_label'))}· "
            f"{_('cli.reflect.recipe_fmt', recipes=rc_report.recipes_found)}" + best_line
        )
        if verbose and rc_report.scores:
            text = format_recipe_report(rc_report, max_rows=5)
            print(c.dim("      " + text.replace("\n", "\n      ")))

    print(c.dim("─" * 60))
    print(c.bold(_("cli.reflect.complete")))
    return 0


def run_intel(
    *,
    queries: list[str],
    fetch_top: int = 0,
    max_results: int = 5,
    journal_file: Path | None = None,
    color: bool = True,
) -> int:
    from runtime.execution.suckers import SkillRegistry
    from runtime.execution.suckers.builtins import register_all
    from runtime.safety.recovery import IntelCollector, IntelSource

    c = _Colors(color)
    print(c.bold(_("cli.intel.title", n=len(queries))))
    print(c.dim("─" * 60))

    registry = SkillRegistry()
    register_all(registry)
    if not registry.has("web_search"):
        print(c.red(_("cli.intel.web_search_unavailable")), file=sys.stderr)
        return 2

    journal: Journal = JSONLJournal(journal_file) if journal_file is not None else InMemoryJournal()

    sources = [
        IntelSource(
            source_id=f"q{i}_{_slug_query(q)}",
            query=q,
            max_results=max_results,
            fetch_top_n=fetch_top,
        )
        for i, q in enumerate(queries)
    ]
    report = IntelCollector(sources=sources, journal=journal, registry=registry).run_once()

    print(
        f"{c.cyan('[INTEL] ')} · {_('cli.intel.source_fmt', sources=report.sources_scanned, ok=c.green(str(report.searches_succeeded)), failed=c.red(str(report.searches_failed)), urls=report.urls_fetched, events=report.events_written)}"
    )
    print(c.dim("─" * 60))

    step_events = [
        e
        for e in journal.read_all()
        if e.event_type == "step" and "intel_" in e.step.node_id and "_search" in e.step.node_id
    ]
    for ev in step_events:
        out = ev.step.result.output
        if not isinstance(out, dict):
            continue
        marker = c.green(_("cli.exec.success")) if ev.step.success else c.red(_("cli.exec.failed"))
        query = out.get("query", "?")
        snippet = str(out.get("result", ""))[:80]
        print(
            f"    {marker} {ev.step.node_id:<14} · {_('cli.intel.query_display', query=query[:30])} · {snippet}"
        )

    if journal_file is not None:
        print(c.dim(_("cli.intel.journal_saved", path=journal_file, events=len(journal))))
    return 0 if report.searches_failed == 0 else 1


def run_loop(
    *,
    goal: str,
    config_path: Path,
    journal_path: Path,
    iterations: int,
    intent_type: IntentType = "task",
    color: bool = True,
) -> int:
    from runtime.core.cerebrum import LLMPlanner, StaticPlanner
    from runtime.platform.config import ConfigLoadError, build_from_config, load_from_yaml
    from runtime.platform.models import ArmId, Budget, BudgetLimits, ParsedIntent

    c = _Colors(color)
    try:
        cfg = load_from_yaml(config_path)
    except ConfigLoadError as e:
        print(c.red(f"config error: {e}"), file=sys.stderr)
        return 2

    cfg = cfg.model_copy(update={"journal_file": str(journal_path)})
    stack = build_from_config(cfg)

    print(c.bold(_("cli.loop.title_fmt", iterations=iterations)))
    print(c.dim("─" * 60))
    print(
        _(
            "cli.loop.config_info",
            config=config_path,
            journal=journal_path,
            planner=cfg.planner.type,
            goal=goal,
        )
    )
    print()

    intent = ParsedIntent(raw=goal, intent_type=intent_type, normalized_goal=goal)
    successes = 0
    failures = 0

    for i in range(1, iterations + 1):
        print(c.cyan(_("cli.loop.iter_fmt", i=i, n=iterations)))

        if isinstance(stack.planner, LLMPlanner):
            n_rules = stack.planner.learn_from_journal(stack.journal)
            n_mem = stack.planner.learn_memories_from_journal(stack.journal)
            n_kg = stack.planner.learn_kg_from_journal(stack.journal)
            stack.planner.assess_recipe_from_journal(stack.journal)
            v = stack.planner.current_recipe_verdict
            verdict_str = v.verdict if v is not None else "none"
            print(
                f"  {c.dim(_('cli.loop.learn_label'))} · {_('cli.loop.learn_info', rules=n_rules, mem=n_mem, kg=n_kg, verdict=verdict_str)}"
            )
        elif isinstance(stack.planner, StaticPlanner):
            rw = stack.planner.rewrite_from_journal(stack.journal)
            print(
                f"  {c.dim(_('cli.loop.rewrite_label'))} · {_('cli.loop.rewrite_info', applied=rw.applied_count, skipped=rw.skipped_count, rules=len(stack.planner.rules))}"
            )

        from runtime.safety.recovery import ForgeConfig, SkillForge

        forge = SkillForge(
            journal=stack.journal,
            registry=stack.registry,
            config=ForgeConfig(governed_rollout=True),
        ).run()
        if forge.promoted or forge.retired:
            print(
                f"  {c.dim(_('cli.loop.forge_label'))} · {_('cli.loop.forge_info', promoted=len(forge.promoted), retired=len(forge.retired))}"
            )

        reflex_hit = _try_reflex(intent, stack.journal)
        if reflex_hit is not None:
            print(
                f"  {c.cyan(_('cli.loop.reflex_label'))} · {c.green('✓')} {reflex_hit.rule_id} "
                f"· {reflex_hit.latency_ms:.2f}ms"
            )
            successes += 1
            continue

        try:
            graph = stack.planner.plan(intent)
        except Exception as exc:
            print(c.red(f"plan failed: {exc}"))
            failures += 1
            continue

        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(
                tokens=cfg.budget.max_tokens,
                usd=cfg.budget.max_usd,
                latency_ms=cfg.budget.max_latency_ms,
            ),
        )
        t0 = time.monotonic()
        traj = stack.runtime.run(
            graph,
            budget=budget,
            caller=f"arms/{cfg.default_arm_id}",
            arm_id=ArmId(cfg.default_arm_id),
        )
        elapsed = (time.monotonic() - t0) * 1000
        ok = traj.outcome.success
        if ok:
            successes += 1
        else:
            failures += 1

        marker = c.green("✓") if ok else c.red("✗")
        print(
            _(
                "cli.loop.exec_result",
                marker=marker,
                nodes=len(graph.nodes),
                usd=budget.usd_spent,
                ms=elapsed,
            )
        )

    print()
    print(
        c.bold(
            _(
                "cli.loop.done_summary",
                success=successes,
                total=iterations,
                events=len(stack.journal),
            )
        )
    )
    return 0 if failures == 0 else 1


def run_optimize(
    *,
    goal: str,
    config_path: Path,
    variants_path: Path | None,
    journal_path: Path,
    rounds: int,
    tasks_per_round: int,
    mutator_model: str,
    mutator_response: str | None,
    max_variants: int,
    retire_min_uses: int,
    export_path: Path | None,
    color: bool = True,
) -> int:
    from uuid import uuid4

    from runtime.core.cerebrum import LLMPlanner
    from runtime.platform.config import ConfigLoadError, build_from_config, load_from_yaml
    from runtime.platform.models import ArmId, Budget, BudgetLimits, ParsedIntent
    from runtime.safety.experiments import (
        EvolutionPolicy,
        PromptEvolver,
        PromptMutator,
        PromptOptimizer,
        PromptVariant,
        load_variants_from_yaml,
    )
    from runtime.sensing.model_router import MockModelRouter

    c = _Colors(color)
    try:
        cfg = load_from_yaml(config_path)
    except ConfigLoadError as e:
        print(c.red(_("cli.error.config_error", msg=e)), file=sys.stderr)
        return 2

    cfg = cfg.model_copy(update={"journal_file": str(journal_path)})
    stack = build_from_config(cfg)
    if not isinstance(stack.planner, LLMPlanner):
        print(
            c.red(_("cli.error.optimize_needs_llm")),
            file=sys.stderr,
        )
        return 2

    if variants_path is not None and variants_path.exists():
        initial = load_variants_from_yaml(variants_path)
    else:
        initial = [PromptVariant(name="baseline", system_prompt_suffix="")]

    optimizer = PromptOptimizer(stack, initial)

    if mutator_model.startswith("mock/"):
        mutator_router = MockModelRouter(
            response=mutator_response or "<suffix>prefer smaller plans</suffix>",
        )
    else:
        mutator_router = stack.planner.router

    mutator = PromptMutator(router=mutator_router, model=mutator_model)

    evolver = PromptEvolver(
        optimizer,
        mutator,
        EvolutionPolicy(
            retire_min_uses=retire_min_uses,
            max_total_variants=max_variants,
        ),
    )

    print(c.bold(_("cli.optimize.title_fmt", rounds=rounds, tasks=tasks_per_round)))
    print(c.dim("─" * 60))
    print(_("cli.optimize.config_info", config=config_path, journal=journal_path))
    print(_("cli.optimize.variants_info", variants=optimizer.variant_names))
    print()

    intent = ParsedIntent(raw=goal, intent_type="task", normalized_goal=goal)

    for r in range(1, rounds + 1):
        print(c.bold(_("cli.optimize.round_fmt", r=r, rounds=rounds)))

        round_success = 0
        for __ in range(tasks_per_round):
            tid = uuid4()
            try:
                graph = optimizer.plan(intent, task_id=tid)
            except Exception as exc:
                print(c.red(_("cli.optimize.plan_failed", msg=exc)))
                optimizer.record_outcome(tid, success=False)
                continue
            budget = Budget(
                task_id=graph.task_id,
                limits=BudgetLimits(
                    tokens=cfg.budget.max_tokens,
                    usd=cfg.budget.max_usd,
                    latency_ms=cfg.budget.max_latency_ms,
                ),
            )
            traj = stack.runtime.run(
                graph,
                budget=budget,
                caller=f"arms/{cfg.default_arm_id}",
                arm_id=ArmId(cfg.default_arm_id),
            )
            ok = traj.outcome.success
            if ok:
                round_success += 1
            optimizer.record_outcome(tid, success=ok)

        step = evolver.step()
        variant_count = len(optimizer.variant_names)
        print(f"  · {round_success}/{tasks_per_round} ok · pool={variant_count} · {step.summary}")

    print()
    print(c.bold(_("cli.optimize.ranking")))
    final = optimizer.report()
    ranked = sorted(
        final.items(),
        key=lambda kv: kv[1].success_rate,
        reverse=True,
    )
    for name, rep in ranked:
        verdict_color = (
            c.green if rep.verdict == "winning" else c.red if rep.verdict == "losing" else c.dim
        )
        print(
            f"  {verdict_color(name):<30} "
            f"· {rep.assignments:>3} uses "
            f"· {rep.success_rate:>6.1%} success "
            f"· {verdict_color(rep.verdict)}"
        )

    if export_path is not None:
        _export_winning_variants_cli(export_path, optimizer, final)
        print(c.dim(_("cli.optimize.exported", path=export_path)))

    return 0


def _export_winning_variants_cli(
    path: Path,
    optimizer: Any,
    report: dict[str, Any],
) -> None:
    lines = ["variants:"]
    ranked = sorted(
        report.items(),
        key=lambda kv: kv[1].success_rate,
        reverse=True,
    )
    for name, rep in ranked:
        if name not in optimizer.variant_names:
            continue
        v = optimizer._variants[name]
        suffix_escaped = (v.system_prompt_suffix or "").replace("'", "''")
        lines.append(f"  - name: {v.name}")
        lines.append(f"    system_prompt_suffix: '{suffix_escaped}'")
        lines.append(f"    weight: {v.weight}")
        if rep.verdict != "insufficient_data":
            lines.append(f"    description: '{rep.verdict} · {rep.success_rate:.1%}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
