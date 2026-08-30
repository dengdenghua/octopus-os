from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from runtime.adapters.instrumentation import trace_stage
from runtime.core.cerebrum.completion_receipt import build_completion_receipt
from runtime.execution.misc.multiagent_contracts import validate_work_plan
from runtime.execution.swarm._runtime_helpers import (
    _agent_assigned_events,
    _agent_finished_events,
    _agent_handoffs,
    _agent_started_events,
    _assignment_node_ids,
    _make_contract,
    _node_sort_key,
    _phase_report,
    _PreparedLayer,
    _split_per_node,
    _split_single,
    _split_topo_layers,
)
from runtime.execution.swarm.models import (
    AgentHandoff,
    SplitStrategy,
    SwarmEvent,
    SwarmEventLane,
    SwarmPhase,
    SwarmPhaseReport,
    SwarmPlan,
    SwarmResult,
    WorkContract,
)
from runtime.memory.journal import Journal
from runtime.platform.models import (
    ArmAssignment,
    ArmId,
    ArmResult,
    Budget,
    TaskGraph,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
    new_id,
    now_utc,
)

if TYPE_CHECKING:
    from runtime.execution.arms.base import ArmPool, Worker
    from runtime.safety.chromatophores.boids import BoidsArbitrator
    from runtime.safety.chromatophores.signal_bus import SignalBus


# ═══════════════════════════════════════════════════════════
# SwarmRuntime
# ═══════════════════════════════════════════════════════════


_DEFAULT_MAX_WORKERS = 4


class SwarmRuntime:
    def __init__(
        self,
        arm_pool: ArmPool,
        signal_bus: SignalBus | None = None,
        boids: BoidsArbitrator | None = None,
        journal: Journal | None = None,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        skill_resources: Callable[[str], list[str]] | None = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError(f"max_workers must be >= 1, got {max_workers}")
        self._arm_pool = arm_pool
        self._signal_bus = signal_bus
        self._boids = boids
        self._journal = journal
        self._max_workers = max_workers
        # ADR-010 Phase 2 · resolves a node's skill_ref → its exclusive
        # resource URIs (built from the SkillRegistry at the call site). None
        # ⇒ assignments keep whatever exclusive_resources they were split with
        # (empty by default), so behaviour is unchanged.
        self._skill_resources = skill_resources

    def run(
        self,
        graph: TaskGraph,
        budget: Budget,
        *,
        split_strategy: SplitStrategy = "per_node",
    ) -> SwarmResult:
        with trace_stage(
            "swarm.run",
            task_id=str(graph.task_id),
        ) as span:
            span.set_attribute("echo.swarm.strategy", split_strategy)
            span.set_attribute("echo.swarm.max_workers", self._max_workers)

            layers = self._split_layers(graph, split_strategy)
            if self._skill_resources is not None:
                layers = [[self._with_resources(a) for a in lyr] for lyr in layers]
            total_assignments = sum(len(lyr) for lyr in layers)
            span.set_attribute("echo.swarm.assignment_count", total_assignments)
            span.set_attribute("echo.swarm.layer_count", len(layers))

            prepared_layers, plan = self._prepare_plan(
                graph=graph,
                layers=layers,
                strategy=split_strategy,
            )
            validation = validate_work_plan(plan)
            plan = plan.model_copy(
                update={
                    "validation_issues": list(validation.errors),
                    "validation_warnings": list(validation.warnings),
                }
            )
            events: list[SwarmEvent] = [
                SwarmEvent(
                    type="plan_created",
                    lane="workflow",
                    task_id=graph.task_id,
                    payload={
                        "strategy": split_strategy,
                        "phase_count": len(plan.phases),
                        "contract_count": len(plan.contracts),
                    },
                )
            ]
            started_at = time.perf_counter()
            all_results: list[ArmResult] = []
            # Outputs accumulate across layers so a later node can resolve
            # template refs to an earlier layer's node (e.g. {n1.content}).
            # Topo layers guarantee earlier layers ran first, so this is filled
            # before any dependent node dispatches.
            accumulated_outputs: dict[str, Any] = {}
            all_handoffs: list[AgentHandoff] = []
            phase_reports: list[SwarmPhaseReport] = []
            max_layer_parallelism = 0
            for prepared in prepared_layers:
                phase = prepared.phase
                phase_started_at = time.perf_counter()
                events.append(
                    SwarmEvent(
                        type="phase_started",
                        lane="workflow",
                        task_id=graph.task_id,
                        phase_index=phase.phase_index,
                        node_ids=phase.node_ids,
                        payload={
                            "phase_index": phase.phase_index,
                            "parallel": phase.parallel,
                        },
                    )
                )
                events.extend(
                    _agent_assigned_events(
                        graph.task_id,
                        phase.phase_index,
                        prepared.pairs,
                    )
                )
                events.extend(
                    _agent_started_events(
                        graph.task_id,
                        phase.phase_index,
                        prepared.pairs,
                    )
                )
                dispatched_results = self._dispatch(
                    prepared.pairs,
                    budget,
                    seed_outputs=accumulated_outputs,
                )
                layer_results = [
                    *dispatched_results,
                    *(result for _assignment, result in prepared.unmatched),
                ]
                all_results.extend(layer_results)
                # Feed this layer's outputs forward for the next layer's refs.
                for _r in layer_results:
                    accumulated_outputs.update(getattr(_r, "outputs", None) or {})
                phase_handoffs = _agent_handoffs(
                    graph.task_id,
                    phase.phase_index,
                    [
                        *(
                            (assignment, result)
                            for (assignment, _arm), result in zip(
                                prepared.pairs, dispatched_results, strict=False
                            )
                        ),
                        *prepared.unmatched,
                    ],
                )
                all_handoffs.extend(phase_handoffs)
                events.extend(
                    _agent_finished_events(
                        graph.task_id,
                        phase.phase_index,
                        layer_results,
                    )
                )
                max_layer_parallelism = max(
                    max_layer_parallelism,
                    self._count_parallelism(prepared.pairs),
                )
                phase_report = _phase_report(
                    phase=phase,
                    results=layer_results,
                    handoffs=phase_handoffs,
                    wall_ms=(time.perf_counter() - phase_started_at) * 1000.0,
                )
                phase_reports.append(phase_report)
                events.append(
                    SwarmEvent(
                        type="phase_finished",
                        lane="workflow",
                        task_id=graph.task_id,
                        phase_index=phase.phase_index,
                        node_ids=phase.node_ids,
                        payload={
                            "phase_index": phase.phase_index,
                            "result_count": len(layer_results),
                            "succeeded": phase_report.succeeded,
                            "failed": phase_report.failed,
                            "handoff_count": phase_report.handoff_count,
                            "cost_usd": phase_report.cost_usd,
                            "wall_ms": phase_report.wall_ms,
                        },
                    )
                )
            elapsed_ms = (time.perf_counter() - started_at) * 1000.0

            if self._journal is not None:
                self._record_trajectories(graph.task_id, all_results)

            total_cost_usd = sum(r.cost.usd for r in all_results)
            all_success = bool(all_results) and all(r.status == "success" for r in all_results)

            span.set_attribute(
                "echo.swarm.parallelism",
                max_layer_parallelism,
            )
            span.set_attribute("echo.swarm.total_cost_usd", total_cost_usd)
            span.set_attribute("echo.swarm.all_successful", all_success)
            events.append(
                SwarmEvent(
                    type="swarm_finished",
                    lane="timeline",
                    task_id=graph.task_id,
                    payload={
                        "all_successful": all_success,
                        "total_cost_usd": total_cost_usd,
                        "total_wall_ms": elapsed_ms,
                        "parallelism_achieved": max_layer_parallelism,
                        "handoff_count": len(all_handoffs),
                        "phase_report_count": len(phase_reports),
                    },
                )
            )

            return SwarmResult(
                task_id=graph.task_id,
                arm_results=all_results,
                parallelism_achieved=max_layer_parallelism,
                total_wall_ms=elapsed_ms,
                total_cost_usd=total_cost_usd,
                all_successful=all_success,
                plan=plan,
                events=events,
                handoffs=all_handoffs,
                phase_reports=phase_reports,
                contract_validation_issues=list(validation.errors),
                contract_validation_warnings=list(validation.warnings),
                completion_receipt=build_completion_receipt(
                    ["completed" if r.status == "success" else r.status for r in all_results],
                    contract_issues=list(validation.errors),
                    contract_warnings=list(validation.warnings),
                    artifact_count=sum(len(h.artifacts) for h in all_handoffs),
                    output_present=any(bool(h.summary) for h in all_handoffs),
                ).to_dict(),
            )

    def _split_layers(
        self,
        graph: TaskGraph,
        strategy: SplitStrategy,
    ) -> list[list[ArmAssignment]]:
        if strategy == "per_node":
            return [_split_per_node(graph)]
        if strategy == "single":
            return [_split_single(graph)]
        if strategy == "topo_layers":
            return _split_topo_layers(graph)
        raise ValueError(f"unknown split_strategy: {strategy!r}")

    def _prepare_plan(
        self,
        *,
        graph: TaskGraph,
        layers: list[list[ArmAssignment]],
        strategy: SplitStrategy,
    ) -> tuple[list[_PreparedLayer], SwarmPlan]:
        prepared_layers: list[_PreparedLayer] = []
        phases: list[SwarmPhase] = []
        contracts: list[WorkContract] = []
        all_node_ids = [node.node_id for node in graph.nodes]

        for phase_index, layer in enumerate(layers):
            pairs: list[tuple[ArmAssignment, Worker]] = []
            unmatched: list[tuple[ArmAssignment, ArmResult]] = []
            assignment_ids: list[str] = []
            phase_node_ids: list[str] = []

            for assignment_index, assignment in enumerate(layer):
                node_ids = _assignment_node_ids(assignment)
                phase_node_ids.extend(node_ids)
                arm = self._arm_pool.pick_for(assignment)
                agent_id = str(getattr(arm, "arm_id", ArmId("unassigned")))
                assignment_id = f"p{phase_index}:a{assignment_index}"
                assignment_ids.append(assignment_id)
                contracts.append(
                    _make_contract(
                        assignment_id=assignment_id,
                        assignment=assignment,
                        agent_id=agent_id,
                        all_node_ids=all_node_ids,
                        graph=graph,
                    )
                )
                if arm is None:
                    unmatched.append(
                        (
                            assignment,
                            ArmResult(
                                arm_id=ArmId("unassigned"),
                                task_id=assignment.subgraph.task_id,
                                status="failed",
                                reason="no_arm_matched",
                            ),
                        )
                    )
                else:
                    pairs.append((assignment, arm))

            phase = SwarmPhase(
                phase_index=phase_index,
                assignment_ids=assignment_ids,
                node_ids=phase_node_ids,
                parallel=len(layer) > 1,
            )
            phases.append(phase)
            prepared_layers.append(_PreparedLayer(phase=phase, pairs=pairs, unmatched=unmatched))

        return prepared_layers, SwarmPlan(
            task_id=graph.task_id,
            strategy=strategy,
            max_workers=self._max_workers,
            phases=phases,
            contracts=contracts,
        )

    def _dispatch(
        self,
        pairs: list[tuple[ArmAssignment, Worker]],
        budget: Budget,
        seed_outputs: dict[str, Any] | None = None,
    ) -> list[ArmResult]:
        if not pairs:
            return []

        # ── Alignment (Boids 原则 2):同 affinity 分组,发布对齐事件 ──
        # Group arms by affinity so downstream observers can see which arms
        # are expected to converge. The barrier itself is implicit — all
        # pairs in this layer dispatch together via ThreadPoolExecutor below.
        if self._boids is not None and self._signal_bus is not None:
            arm_affinities = [
                (arm.arm_id, list(getattr(arm, "affinity", []))) for _assignment, arm in pairs
            ]
            groups = self._boids.alignment_groups(arm_affinities)
            for affinity, arm_ids in groups.items():
                if affinity and len(arm_ids) > 1:
                    self._publish(
                        "arm.alignment",
                        {
                            "affinity": affinity,
                            "arm_ids": [str(a) for a in arm_ids],
                            "layer_size": len(pairs),
                        },
                    )

        results: list[ArmResult] = []
        # Audit T-08: an arm hung past the layer budget must not pin the
        # whole swarm. Each layer gets the budget's latency ceiling; on
        # timeout the hung arm is failed with an explicit reason, pending
        # futures are cancelled, and the executor is released without
        # waiting for the (uninterruptible) Python worker thread.
        layer_timeout_s = self._layer_budget_timeout_s(budget)
        executor = ThreadPoolExecutor(max_workers=self._max_workers)
        try:
            spawned: list[tuple[Future[ArmResult], Any, Any]] = [
                (
                    executor.submit(self._run_one, assignment, arm, budget, seed_outputs),
                    assignment,
                    arm,
                )
                for assignment, arm in pairs
            ]
            start = time.monotonic()
            slot_results: dict[int, ArmResult] = {}
            for slot, (fut, _assignment, _arm) in enumerate(spawned):
                remaining = (
                    None
                    if layer_timeout_s <= 0
                    else max(0.0, layer_timeout_s - (time.monotonic() - start))
                )
                try:
                    slot_results[slot] = fut.result(timeout=remaining)
                except TimeoutError:
                    break
                except Exception as e:  # Implementation note.
                    slot_results[slot] = ArmResult(
                        arm_id=ArmId("unknown"),
                        task_id=TaskId(new_id()),
                        status="failed",
                        reason=f"executor_error:{e!s}",
                    )
            # Sweep: futures without a result are either still running (hung
            # past the layer budget) or completed between the timeout and
            # this sweep.
            for slot, (fut, assignment, arm) in enumerate(spawned):
                if slot in slot_results:
                    continue
                fut.cancel()
                if fut.done():
                    try:
                        slot_results[slot] = fut.result(timeout=0)
                    except Exception as e:  # noqa: BLE001
                        slot_results[slot] = ArmResult(
                            arm_id=getattr(arm, "arm_id", ArmId("unknown")),
                            task_id=assignment.subgraph.task_id,
                            status="failed",
                            reason=f"executor_error:{e!s}",
                        )
                else:
                    slot_results[slot] = ArmResult(
                        arm_id=getattr(arm, "arm_id", ArmId("unknown")),
                        task_id=assignment.subgraph.task_id,
                        status="failed",
                        reason=f"layer_budget_timeout after {layer_timeout_s:g}s",
                    )
            results = [slot_results[i] for i in range(len(spawned))]
        finally:
            # wait=False: a hung worker thread cannot be force-killed; the
            # swarm must proceed without it (the layer already recorded the
            # timeout). Queued futures are cancelled.
            executor.shutdown(wait=False, cancel_futures=True)
        return results

    def _layer_budget_timeout_s(self, budget: Any) -> float:
        """Layer wall-clock ceiling derived from the budget's latency limit
        (audit T-08). ``<=0`` keeps the legacy indefinite wait."""
        latency_ms = getattr(getattr(budget, "limits", None), "latency_ms", 0) or 0
        if latency_ms <= 0:
            return 0.0
        return max(1.0, latency_ms / 1000.0)

    def _run_one(
        self,
        assignment: ArmAssignment,
        arm: Worker,
        budget: Budget,
        seed_outputs: dict[str, Any] | None = None,
    ) -> ArmResult:
        arm_id = getattr(arm, "arm_id", ArmId("unknown"))
        task_id = assignment.subgraph.task_id

        # ADR-010 · serialise on declared exclusive resources. Empty list (the
        # default) ⇒ no claim ⇒ this is exactly the prior behaviour.
        claimed = self._claim_resources(arm_id, assignment.exclusive_resources)
        self._publish(
            "arm.busy",
            {"arm_id": str(arm_id), "task_id": str(task_id)},
        )
        try:
            if not seed_outputs:
                # No cross-layer outputs to thread → keep the original call so
                # custom/legacy Worker.handle implementations are unaffected.
                return arm.handle(assignment, budget)
            try:
                return arm.handle(assignment, budget, seed_outputs=seed_outputs)
            except TypeError as exc:
                # A Worker.handle that doesn't accept the kwarg (test doubles,
                # third-party arms) — fall back; it just won't see prior outputs.
                if "seed_outputs" not in str(exc):
                    raise
                return arm.handle(assignment, budget)
        except Exception as e:
            return ArmResult(
                arm_id=arm_id,
                task_id=task_id,
                status="failed",
                reason=f"arm_exception:{e!s}",
            )
        finally:
            self._publish(
                "arm.idle",
                {"arm_id": str(arm_id), "task_id": str(task_id)},
            )
            self._release_resources(arm_id, claimed)

    # ─── ADR-010 · resource contention ────────────────────────

    # ttl generous enough to cover a normal handle(); ``finally`` releases
    # promptly on any exit, so the ttl only bounds a hard crash that skips it.
    _CLAIM_TTL_MS = 600_000
    _CLAIM_TIMEOUT_S = 30.0
    _CLAIM_POLL_S = 0.02

    def _claim_resources(self, arm_id: ArmId, resources: list[str]) -> list[str]:
        """Claim each exclusive resource via the BoidsArbitrator, serialising on
        ``lose`` (poll until the holder releases, bounded by a timeout).

        Returns the URIs actually held (to release later). No-op — returns
        ``[]`` — when no arbitrator is wired or nothing is declared, so the hot
        path is untouched for the common case.
        """
        if self._boids is None or not resources:
            return []
        from runtime.safety.chromatophores.boids import ResourceClaim

        held: list[str] = []
        for uri in resources:
            deadline = time.monotonic() + self._CLAIM_TIMEOUT_S
            while True:
                verdict = self._boids.arbitrate(
                    ResourceClaim(arm_id=arm_id, resource_uri=uri, ttl_ms=self._CLAIM_TTL_MS)
                )
                if verdict in ("win", "coexist"):
                    held.append(uri)
                    break
                if time.monotonic() >= deadline:
                    # Degrade rather than deadlock the pool: proceed without the
                    # claim but make the contention observable (ADR-010 v1 policy).
                    self._publish(
                        "alert.contention",
                        {"arm_id": str(arm_id), "resource_uri": uri},
                    )
                    break
                time.sleep(self._CLAIM_POLL_S)
        return held

    def _release_resources(self, arm_id: ArmId, resources: list[str]) -> None:
        if self._boids is None or not resources:
            return
        for uri in resources:
            with contextlib.suppress(Exception):
                self._boids.release(arm_id, uri)

    def _with_resources(self, assignment: ArmAssignment) -> ArmAssignment:
        """Populate ``exclusive_resources`` from the skills the assignment's
        nodes use (ADR-010 Phase 2). Returns the assignment unchanged when no
        node declares a resource, so the common case allocates nothing."""
        uris: list[str] = []
        for node in assignment.subgraph.nodes:
            ref = getattr(node, "skill_ref", None)
            if ref is None:
                continue
            with contextlib.suppress(Exception):
                uris.extend(self._skill_resources(str(ref)))
        if not uris:
            return assignment
        # dedupe, preserve first-seen order; merge with any pre-set resources.
        merged = list(assignment.exclusive_resources) + uris
        return assignment.model_copy(update={"exclusive_resources": list(dict.fromkeys(merged))})

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._signal_bus is None:
            return
        with contextlib.suppress(Exception):
            self._signal_bus.publish(topic, payload, publisher="swarm")

    def _count_parallelism(
        self,
        pairs: list[tuple[ArmAssignment, Worker]],
    ) -> int:
        if not pairs:
            return 0
        return min(len(pairs), self._max_workers)

    def _record_trajectories(
        self,
        task_id: TaskId,
        results: list[ArmResult],
    ) -> None:
        """Write a single aggregated Trajectory per task.

        Before 2026-04 this wrote one ``Trajectory(steps=[])`` per
        ArmResult · the empty-steps form made the event useless to
        SkillForge (``step_count < 2`` gate). Worse: when ``per_node``
        split dispatched N nodes to N arms, each arm's internal
        GraphRuntime wrote a 1-step Trajectory too — all of which
        SkillForge also filtered — so **multi-step patterns
        executed in parallel were never learned**.

        Fix: each ArmResult now carries the steps its Arm executed
        (see ``arms/base.py``). Here we collect them from all arms
        of the same task, sort by ``step_id`` to recover the
        original node order, and emit one Trajectory that
        represents the whole task at once. SkillForge clusters on
        this aggregated form and can learn the full sequence.

        The per-arm GraphRuntime-internal Trajectories remain in
        the journal (useful for per-arm debugging), but the forging
        loop only clusters on aggregates with ``step_count >= 2``.
        """
        if self._journal is None:
            return
        now = now_utc()

        # Collect every step from every arm of this task, then order
        # by step_id so the aggregated Trajectory looks like a
        # single-arm sequential run to SkillForge's signature hasher.
        # Ties on step_id (two arms each assigned step_id=0 after
        # per-node split) must use the numeric node index, not raw
        # string order, otherwise ``n10`` sorts before ``n2`` and the
        # learned path signature becomes unstable once graphs exceed
        # 9 nodes.
        all_steps = []
        total_tokens_in = 0
        total_tokens_out = 0
        total_usd = 0.0
        total_latency_ms = 0.0
        all_success = True
        degraded = False
        for r in results:
            all_steps.extend(r.steps)
            total_tokens_in += r.cost.tokens_in
            total_tokens_out += r.cost.tokens_out
            total_usd += r.cost.usd
            total_latency_ms += r.cost.latency_ms
            if r.status != "success":
                all_success = False
            if r.status in {"partial", "failed"}:
                degraded = True

        all_steps.sort(key=lambda s: (s.step_id, _node_sort_key(s.node_id), s.node_id))

        # Aggregated swarm trajectories represent the swarm as a
        # whole, not any one contributing arm. Using a "lead" arm_id
        # here fragments downstream consumers that group by arm_id
        # (for example MemoryConsolidator), because equivalent swarm
        # runs can land in different buckets depending on scheduling.
        aggregate_arm_id = ArmId("swarm")

        # Wire the accumulated cost into the Trajectory outcome ·
        # pre-2026-05 this file accumulated ``total_usd`` etc into
        # local variables and then silently dropped them when
        # building ``TrajectoryOutcome`` · making every swarm-run
        # trajectory appear free to downstream consumers (memory
        # consolidation / recipe evaluation). Caught in second-pass
        # review · fix is just "pass the accumulation in".
        from runtime.platform.models import CostEntry

        aggregated_cost = CostEntry(
            tokens_in=total_tokens_in,
            tokens_out=total_tokens_out,
            usd=total_usd,
            latency_ms=total_latency_ms,
        )

        aggregated = Trajectory(
            task_id=task_id,
            arm_id=aggregate_arm_id,
            strategy_id="swarm",
            steps=all_steps,
            outcome=TrajectoryOutcome(
                success=all_success,
                cost=aggregated_cost,
                degraded=degraded,
            ),
            started_at=now,
            completed_at=now,
        )
        self._journal.write_trajectory(aggregated)


# Re-export public names so `from runtime.execution.swarm.runtime import X`
# continues to work after the split. Models come from the canonical
# ``models`` module; helpers stay private (``_`` prefix) and are imported
# above for use by ``SwarmRuntime``. ``__all__`` also silences ruff F401 on
# re-export-only names (e.g. ``SwarmEventLane``).
__all__ = [
    "AgentHandoff",
    "SplitStrategy",
    "SwarmEvent",
    "SwarmEventLane",
    "SwarmPhase",
    "SwarmPhaseReport",
    "SwarmPlan",
    "SwarmResult",
    "SwarmRuntime",
    "WorkContract",
]
