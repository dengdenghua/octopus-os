from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from runtime.cli_core import (
    _build_arm_pool,
    _build_stack,
    _Colors,
    _graph_has_template_deps,
    _short_output,
    _speedup_estimate,
    _try_reflex,
    print_cost_breakdown,
)
from runtime.core.cerebrum import PlannerError
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms import Arm, ArmPool
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.swarm import SwarmResult, SwarmRuntime
from runtime.execution.swarm.drive import run_swarm
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal, Journal, JSONLJournal
from runtime.platform.config.schema import (
    BUDGET_DEFAULT_MAX_TOKENS,
    BUDGET_DEFAULT_MAX_USD,
)
from runtime.platform.i18n import _
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    IntentType,
    ParsedIntent,
    SkillId,
    TaskGraph,
    TaskNode,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.chromatophores import SignalBus


def run_goal(
    goal: str,
    *,
    intent_type: IntentType = "task",
    max_tokens: int = BUDGET_DEFAULT_MAX_TOKENS,
    max_usd: float = BUDGET_DEFAULT_MAX_USD,
    color: bool = True,
    args_override: dict[str, Any] | None = None,
    planner_type: str = "static",
    planner_model: str = "mock/planner",
    mock_response: str | None = None,
    anthropic_api_key: str | None = None,
    journal_file: str | Path | None = None,
    show_cost: bool = False,
    swarm: bool = False,
    max_workers: int = 3,
    learn_from: str | Path | None = None,
) -> int:
    c = _Colors(color)
    journal: Journal
    journal = JSONLJournal(journal_file) if journal_file is not None else InMemoryJournal()

    planner, executor, journal = _build_stack(
        planner_type=planner_type,
        planner_model=planner_model,
        mock_response=mock_response,
        anthropic_api_key=anthropic_api_key,
        journal=journal,
    )

    if learn_from is not None and planner_type == "llm":
        learn_journal = JSONLJournal(Path(learn_from))
        n_rules = planner.learn_from_journal(learn_journal)  # type: ignore[attr-defined]
        print(
            _Colors(color).cyan("[LEARN]   ")
            + f" · {_('cli.learn.loaded', n=n_rules, path=learn_from)}"
        )

    print(c.bold(_("cli.label.ingest")))
    print(c.dim("─" * 60))

    intent = ParsedIntent(raw=goal, intent_type=intent_type, normalized_goal=goal)
    print(f"{c.cyan(_('cli.label.ingest'))} · {c.bold(intent.intent_type):<10} · {goal!r}")

    reflex_result = _try_reflex(intent, journal)
    if reflex_result is not None:
        print(
            f"{c.cyan(_('cli.label.reflex'))}  · {c.green(_('cli.exec.success'))} "
            f"{_('cli.reflex.rule', rule=c.bold(reflex_result.rule_id))} "
            f"· {_('cli.reflex.kind', kind=reflex_result.kind)} "
            f"· {_('cli.reflex.latency', ms=reflex_result.latency_ms)}"
        )
        print(c.dim("─" * 60))
        print(c.green(f"  {_('cli.status.reflex_hit')} · {reflex_result.response}"))
        print(c.dim(_("cli.status.bypassed")))
        return 0

    try:
        graph = planner.plan(intent)
    except PlannerError as e:
        print(f"{c.red(_('cli.status.plan_failed'))}: {e}", file=sys.stderr)
        return 1

    print(
        f"{c.cyan(_('cli.label.plan'))} · {_('cli.plan.matched', strategy=c.bold(graph.strategy), n=len(graph.nodes), task_type=graph.task_type)}"
    )
    for n in graph.nodes:
        print(f"           └─ {n.node_id}: {n.skill_ref}")

    budget = Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=max_tokens, usd=max_usd),
    )
    base_args = args_override or {"intent_goal": goal, "path": ".", "text": goal}

    if swarm:
        has_deps = _graph_has_template_deps(graph)
        if has_deps:
            strategy = "single"
        elif graph.edges:
            strategy = "topo_layers"
        else:
            strategy = "per_node"

        print(
            f"{c.cyan(_('cli.label.swarm'))} · {_('cli.swarm.dispatching', nodes=len(graph.nodes), workers=max_workers, strategy=strategy)}"
        )
        if has_deps:
            print(c.dim(_("cli.swarm.template_deps")))
        elif strategy == "topo_layers":
            print(c.dim(_("cli.swarm.edges_detected", edges=len(graph.edges))))

        runtime = GraphRuntime(executor=executor, journal=journal)
        signal_bus = SignalBus()
        pool = _build_arm_pool(runtime, signal_bus=signal_bus)

        t_exec_start = time.monotonic()
        result: SwarmResult = run_swarm(
            graph,
            budget,
            arm_pool=pool,
            signal_bus=signal_bus,
            journal=journal,
            max_workers=max_workers,
            split_strategy=strategy,
        )
        total_exec_ms = (time.monotonic() - t_exec_start) * 1000

        for ar in result.arm_results:
            status = ar.status
            marker = (
                c.green(_("cli.exec.success"))
                if status == "success"
                else c.red(_("cli.exec.failed"))
            )
            lat = ar.cost.latency_ms
            print(
                f"           {marker} {ar.arm_id:<12} · {_('cli.swarm.result', arm=ar.arm_id, status=status, ms=lat, usd=ar.cost.usd, reason=ar.reason[:40])}"
            )
        print(
            c.dim(
                _(
                    "cli.status.parallelism",
                    parallelism=result.parallelism_achieved,
                    arms=len(pool),
                    speedup=_speedup_estimate(result),
                )
            )
        )
        overall_success = result.all_successful
        steps = []
    else:
        print(f"{c.cyan(_('cli.label.execute'))} · {_('cli.exec.running', n=len(graph.nodes))}")
        runtime = GraphRuntime(executor=executor, journal=journal)

        t_exec_start = time.monotonic()
        traj = runtime.run(
            graph,
            budget=budget,
            caller="arms/code_arm",
            arm_id=ArmId("code_arm"),
            base_args=base_args,
        )
        total_exec_ms = (time.monotonic() - t_exec_start) * 1000
        steps = traj.steps

        for step in steps:
            status = step.result.status
            marker = (
                c.green(_("cli.exec.success"))
                if status == "success"
                else c.red(_("cli.exec.failed"))
            )
            verdict = c.dim(str(step.immune_verdict or "—"))
            preview = _short_output(step.result.output)
            lat = step.result.cost.latency_ms

            print(
                f"           {marker} {step.node_id:<3} {step.action.sucker_id:<14} "
                f"· {step.result.status} · {verdict} · {lat:.1f}ms · {preview}"
            )

        overall_success = traj.outcome.success

    print(
        f"{c.cyan(_('cli.label.store'))} · {_('cli.status.trajectory_persisted')} · journal={len(journal)} event(s)"
    )
    print(c.dim("─" * 60))

    cost_color = c.green if overall_success else c.red
    if swarm:
        arms_ok = sum(1 for ar in result.arm_results if ar.status == "success")
        count_line = f"·  {arms_ok}/{len(result.arm_results)} arm(s)"
    else:
        count_line = f"·  {len(steps)}/{len(graph.nodes)} step(s)"
    print(
        cost_color(
            f"  {_('cli.status.result', status=_('cli.status.success' if overall_success else 'cli.status.failed'))}  {count_line}"
        )
    )
    print(
        c.dim(
            _(
                "cli.budget.display",
                spent=budget.tokens_spent,
                limit=budget.limits.tokens,
                usd_spent=budget.usd_spent,
                usd_limit=budget.limits.usd,
            )
        )
    )
    print(f"  {_('cli.status.wall_time', ms=total_exec_ms)}")
    print(
        c.dim(
            f"  Events: step={len(journal.read_by_type('step'))} "
            f"· immune={len(journal.read_by_type('immune'))} "
            f"· budget_commit={len(journal.read_by_type('budget_commit'))} "
            f"· trajectory={len(journal.read_by_type('trajectory'))}"
        )
    )
    if journal_file is not None:
        print(c.dim(_("cli.status.journal_file", path=journal_file, events=len(journal))))

    if show_cost and steps:
        print()
        print_cost_breakdown(steps, budget, c)

    return 0 if overall_success else 1


def run_goal_from_config(
    *,
    goal: str,
    config_path: Path,
    intent_type: IntentType = "task",
    color: bool = True,
    show_cost: bool = False,
    swarm: bool = False,
    max_workers: int = 3,
) -> int:
    import time

    from runtime.platform.config import ConfigLoadError, build_from_config, load_from_yaml
    from runtime.platform.models import ArmId, Budget, BudgetLimits, ParsedIntent

    c = _Colors(color)
    try:
        cfg = load_from_yaml(config_path)
    except ConfigLoadError as e:
        print(c.red(_("cli.error.config_error", msg=e)), file=sys.stderr)
        return 2

    stack = build_from_config(cfg)

    print(c.bold(_("cli.config.title", path=config_path)))
    print(c.dim("─" * 60))
    print(
        _(
            "cli.config.info",
            planner=cfg.planner.type,
            model=cfg.planner.model,
            skills=len(stack.registry),
            budget=cfg.budget.max_usd,
        )
    )

    intent = ParsedIntent(raw=goal, intent_type=intent_type, normalized_goal=goal)
    print(f"{c.cyan(_('cli.config.ingest_label'))}  · {intent.intent_type:<10} · {goal!r}")

    reflex_result = _try_reflex(intent, stack.journal)
    if reflex_result is not None:
        print(
            f"{c.cyan(_('cli.config.reflex_label'))}  · {c.green(_('cli.exec.success'))} {_('cli.reflex.rule', rule=reflex_result.rule_id)} "
            f"· {reflex_result.latency_ms:.2f}ms"
        )
        print(c.green(_("cli.config.reflex_resp", response=reflex_result.response)))
        return 0

    try:
        graph = stack.planner.plan(intent)
    except Exception as exc:
        print(c.red(_("cli.error.plan_failed", msg=exc)), file=sys.stderr)
        return 1

    print(
        f"{c.cyan(_('cli.config.plan_label'))}    · {_('cli.config.plan_fmt', strategy=graph.strategy, n=len(graph.nodes), task_type=graph.task_type)}"
    )

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
    marker = c.green(_("cli.status.success")) if ok else c.red(_("cli.status.failed"))
    print(
        f"{c.cyan(_('cli.config.done_label'))}    · {marker} · "
        f"{_('cli.config.done_fmt', steps=traj.step_count, usd=budget.usd_spent, usd_limit=budget.limits.usd, ms=elapsed)}"
    )

    if show_cost:
        print()
        print_cost_breakdown(traj.steps, budget, c)

    return 0 if ok else 1


def run_bench(
    *,
    tasks: int = 4,
    delay_ms: int = 80,
    workers: int = 4,
    color: bool = True,
) -> int:
    import time

    from runtime.safety.chromatophores import BoidsArbitrator, SignalBus

    c = _Colors(color)
    print(c.bold(_("cli.bench.title", tasks=tasks, delay_ms=delay_ms, workers=workers)))
    print(c.dim("─" * 60))

    def _slow_handler(**kw) -> dict[str, Any]:
        time.sleep(delay_ms / 1000)
        return {"ok": True, "slept_ms": delay_ms}

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="slow_task",
            trusted_source="skill://public/slow_task",
            handler=_slow_handler,
        ),
        verify_tests=False,
    )

    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)

    nodes = [
        TaskNode(
            node_id=f"n{i}",
            skill_ref=SkillId("slow_task"),
            args_template={"path": f"task_{i}"},
        )
        for i in range(tasks)
    ]
    graph = TaskGraph(
        nodes=nodes,
        edges=[],
        budget=BudgetSpec(tokens=100_000, usd=1.00),
        task_type="bench",
    )

    serial_budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=100_000, usd=1.00))
    t0 = time.monotonic()
    serial_traj = runtime.run(
        graph,
        budget=serial_budget,
        caller="arms/code_arm",
        arm_id=ArmId("code_arm"),
    )
    serial_wall_ms = (time.monotonic() - t0) * 1000
    serial_ok = serial_traj.outcome.success
    print(
        f"{c.cyan(_('cli.bench.serial_label'))}· {tasks} step(s) "
        f"· {c.green(_('cli.exec.success')) if serial_ok else c.red(_('cli.exec.failed'))} "
        f"· {_('cli.bench.wall_fmt', ms=serial_wall_ms)}"
    )

    bench_signal_bus = SignalBus()
    pool = ArmPool(
        [
            Arm(
                arm_id=ArmId(f"w{i}"),
                affinity=["bench"],
                allowed_skills=[SkillId("slow_task")],
                runtime=runtime,
                signal_bus=bench_signal_bus,
            )
            for i in range(workers)
        ]
    )
    swarm_budget = Budget(task_id=graph.task_id, limits=BudgetLimits(tokens=100_000, usd=1.00))
    swarm_runtime = SwarmRuntime(
        arm_pool=pool,
        signal_bus=bench_signal_bus,
        boids=BoidsArbitrator(),
        journal=journal,
        max_workers=workers,
    )
    t1 = time.monotonic()
    swarm_result = swarm_runtime.run(graph=graph, budget=swarm_budget, split_strategy="per_node")
    swarm_wall_ms = (time.monotonic() - t1) * 1000
    print(
        f"{c.cyan(_('cli.bench.swarm_label'))}· {tasks} task(s) "
        f"· {_('cli.bench.parallelism', p=swarm_result.parallelism_achieved)} "
        f"· {c.green(_('cli.exec.success')) if swarm_result.all_successful else c.red(_('cli.exec.failed'))} "
        f"· {_('cli.bench.wall_fmt', ms=swarm_wall_ms)}"
    )

    print(c.dim("─" * 60))
    speedup = serial_wall_ms / swarm_wall_ms if swarm_wall_ms > 0 else float("inf")
    theoretical = min(tasks, workers)
    efficiency = speedup / theoretical if theoretical > 0 else 0.0
    print(
        f"{c.bold(_('cli.bench.speedup'))}: {speedup:.2f}x "
        f"{_('cli.bench.speedup_fmt', theoretical=theoretical, efficiency=efficiency * 100)}"
    )
    ok = swarm_wall_ms < serial_wall_ms and swarm_result.all_successful
    return 0 if ok else 1


def run_resume(
    *,
    task_id: str,
    journal_path: Path,
    goal: str,
    config_path: Path,
    intent_type: IntentType = "task",
    dry_run: bool = False,
    color: bool = True,
) -> int:
    from uuid import UUID

    from runtime.memory.journal import JSONLJournal, resume_info
    from runtime.platform.config import ConfigLoadError, build_from_config, load_from_yaml
    from runtime.platform.models import ArmId, Budget, BudgetLimits, ParsedIntent, TaskId

    c = _Colors(color)
    if not journal_path.exists():
        print(c.red(f"journal not found: {journal_path}"), file=sys.stderr)
        return 2

    try:
        cfg = load_from_yaml(config_path)
    except ConfigLoadError as e:
        print(c.red(f"config error: {e}"), file=sys.stderr)
        return 2

    journal = JSONLJournal(journal_path)
    info = resume_info(journal, task_id)
    if info is None:
        print(c.red(f"no events for task_id={task_id!r}"), file=sys.stderr)
        return 2

    print(c.bold(_("cli.resume.title_fmt", id=task_id[:12])))
    print(c.dim("─" * 60))
    print(f"  journal: {journal_path} ({len(journal)} events)")
    print(
        _(
            "cli.resume.completed_info",
            completed=len(info.completed_nodes),
            total=info.total_nodes,
            strategy=info.strategy,
        )
    )
    print(
        _(
            "cli.resume.terminated_info",
            terminated=info.task_terminated,
            resumable=info.is_resumable,
        )
    )
    for cn in info.completed_nodes:
        print(c.dim(f"    ✓ {cn.node_id} ({cn.sucker_id})"))

    if info.task_terminated:
        if info.task_success:
            print(c.green("task already completed successfully · nothing to resume"))
        else:
            print(c.yellow("task ended in failure · resume unsupported via this CLI"))
        return 0

    if not info.is_resumable:
        print(c.yellow("no resumable state · aborting"))
        return 0

    if dry_run:
        print(c.dim("--dry-run · no execution"))
        return 0

    cfg = cfg.model_copy(update={"journal_file": str(journal_path)})
    stack = build_from_config(cfg)
    intent = ParsedIntent(raw=goal, intent_type=intent_type, normalized_goal=goal)
    try:
        graph = stack.planner.plan(intent)
    except Exception as exc:
        print(c.red(f"replan failed: {exc}"), file=sys.stderr)
        return 1

    new_skill_refs = [str(n.skill_ref) for n in graph.nodes]
    old_skill_refs = [cn.sucker_id for cn in info.completed_nodes]
    old_args_prefix = [cn.args_template for cn in info.completed_nodes]
    N = info.resume_from_index  # noqa: N806
    if len(new_skill_refs) < N:
        print(
            c.red(f"replanned graph too short ({len(new_skill_refs)} nodes · need ≥ {N})"),
            file=sys.stderr,
        )
        return 1
    if new_skill_refs[:N] != old_skill_refs:
        print(
            c.red("prefix mismatch · replanned skill sequence diverges from journal history"),
            file=sys.stderr,
        )
        print(c.dim(f"  journal prefix: {old_skill_refs}"))
        print(c.dim(f"  replan prefix:  {new_skill_refs[:N]}"))
        return 1

    new_args_prefix = [dict(n.args_template) for n in graph.nodes[:N]]
    if new_args_prefix != old_args_prefix:
        print(
            c.red("prefix mismatch: replanned args_template diverges from journal history"),
            file=sys.stderr,
        )
        print(c.dim(f"  journal args prefix: {old_args_prefix}"))
        print(c.dim(f"  replan args prefix:  {new_args_prefix}"))
        return 1
    graph_with_task_id = graph.model_copy(update={"task_id": TaskId(UUID(task_id))})
    budget = Budget(
        task_id=graph_with_task_id.task_id,
        limits=BudgetLimits(
            tokens=cfg.budget.max_tokens,
            usd=cfg.budget.max_usd,
            latency_ms=cfg.budget.max_latency_ms,
        ),
    )
    print(
        c.cyan(f"[resume] skipping first {N} nodes · running {len(new_skill_refs) - N} remaining")
    )
    t0 = time.monotonic()
    traj = stack.runtime.run(
        graph_with_task_id,
        budget=budget,
        caller=f"arms/{cfg.default_arm_id}",
        arm_id=ArmId(cfg.default_arm_id),
        resume_from=N,
        outputs_seed=info.outputs_by_node,
    )
    elapsed = (time.monotonic() - t0) * 1000
    ok = traj.outcome.success
    marker = c.green("OK") if ok else c.red("FAILED")
    print(
        f"{c.cyan('[done]')}    · {marker} · "
        f"{traj.step_count} step(s) this resume · {elapsed:.1f}ms"
    )
    return 0 if ok else 1
