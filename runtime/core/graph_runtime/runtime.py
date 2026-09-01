from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

import re  # noqa: E402
from collections import defaultdict  # noqa: E402
from collections.abc import Callable  # noqa: E402
from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402
from typing import TYPE_CHECKING, Any  # noqa: E402

if TYPE_CHECKING:
    from runtime.core.cerebrum.llm_planner import LLMPlanner

from runtime.adapters.instrumentation import trace_stage  # noqa: E402
from runtime.execution.tool_engine import (  # noqa: E402
    ToolExecutor,
    normalize_task_node_tool_call,
)
from runtime.memory.journal import Journal  # noqa: E402
from runtime.platform.models import (  # noqa: E402
    ArmId,
    Budget,
    ExecutionResult,
    SkillId,
    Step,
    TaskGraph,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)


class TemplateResolutionError(RuntimeError):
    pass


# Step-gap callback (网状 Arm 互通 阶段 2): invoked after each node
# completes in the sequential path, or after each layer completes in
# the layered path. Lets callers (e.g. Worker) poll mailboxes / react
# to peer messages between steps without breaking the run loop.
# Signature: (step_just_completed, node_index, total_nodes) -> None.
# Exceptions are swallowed (log + continue) so a buggy callback can't
# abort the graph — mirrors the nerves/hooks policy.
OnStepCallback = Callable[[Step, int, int], None]


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


_PURE_TEMPLATE_RE = re.compile(r"^\{([a-zA-Z0-9_.]+)\}$")
_INLINE_TEMPLATE_RE = re.compile(r"\{([a-zA-Z0-9_.]+)\}")


def _topo_layers(nodes: list, edges: list) -> list[list[int]]:
    node_ids = [n.node_id for n in nodes]
    id_to_idx = {nid: i for i, nid in enumerate(node_ids)}
    in_degree = [0] * len(nodes)
    children: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        fi = id_to_idx.get(e.from_node)
        ti = id_to_idx.get(e.to_node)
        if fi is not None and ti is not None:
            in_degree[ti] += 1
            children[fi].append(ti)
    layers: list[list[int]] = []
    queue = [i for i in range(len(nodes)) if in_degree[i] == 0]
    while queue:
        layers.append(list(queue))
        next_q: list[int] = []
        for idx in queue:
            for child in children.get(idx, []):
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    next_q.append(child)
        queue = next_q
    return layers


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _lookup(ref: str, outputs: dict[str, Any]) -> Any:
    parts = ref.split(".")
    if not parts or not _IDENT_RE.match(parts[0]):
        raise TemplateResolutionError(f"invalid ref {ref!r}: must be a valid identifier")

    node_id = parts[0]
    if node_id not in outputs:
        raise TemplateResolutionError(f"unknown node id {node_id!r} in template {ref!r}")

    # Skip the transparent ``output`` marker so both
    # ``{s1.field}`` (runtime-style) and ``{s1.output.field}``
    # (meta_skill-style) resolve to the same value.
    if len(parts) > 1 and parts[1] == "output":
        parts = [node_id, *parts[2:]]

    value: Any = outputs[node_id]
    for seg in parts[1:]:
        if isinstance(value, dict):
            if seg not in value:
                raise TemplateResolutionError(
                    f"key {seg!r} not in output of {node_id} (template {ref!r})"
                )
            value = value[seg]
        elif isinstance(value, (list, tuple)):
            try:
                idx = int(seg)
            except ValueError as e:
                raise TemplateResolutionError(
                    f"expected int index for list, got {seg!r} (template {ref!r})"
                ) from e
            if idx >= len(value):
                raise TemplateResolutionError(f"index {idx} out of range in {ref!r}")
            value = value[idx]
        else:
            raise TemplateResolutionError(
                f"cannot descend into {type(value).__name__} at {seg!r} (template {ref!r})"
            )
    return value


def resolve_templates(
    args_template: dict[str, Any],
    prev_outputs: dict[str, Any],
) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    for key, value in args_template.items():
        resolved[key] = _resolve_value(value, prev_outputs)
    return resolved


def _resolve_value(value: Any, prev_outputs: dict[str, Any]) -> Any:
    if not isinstance(value, str):
        return value
    m = _PURE_TEMPLATE_RE.match(value)
    if m:
        return _lookup(m.group(1), prev_outputs)
    if "{" not in value:
        return value
    return _INLINE_TEMPLATE_RE.sub(
        lambda m: str(_lookup(m.group(1), prev_outputs)),
        value,
    )


# ═══════════════════════════════════════════════════════════
# GraphRuntime
# ═══════════════════════════════════════════════════════════


class GraphRuntime:
    def __init__(
        self,
        executor: ToolExecutor,
        journal: Journal | None = None,
        *,
        checkpoint_every_n_nodes: int = 0,
        max_parallel: int = 4,
        canary_config: Any = None,
        evolution_metadata_root: Any = None,
    ) -> None:
        self.executor = executor
        self.journal = journal if journal is not None else executor.journal
        self.checkpoint_every_n_nodes = checkpoint_every_n_nodes
        self.max_parallel = max_parallel
        self.canary_config = canary_config
        self.evolution_metadata_root = evolution_metadata_root
        if (
            self.canary_config is not None
            and getattr(self.canary_config, "rollback_handler", None) is None
        ):
            try:

                def _auto_rollback_handler(skill_name: str, _state: Any, reason: str) -> Any:
                    from runtime.safety.evolution.rollback_coordinator import RollbackCoordinator

                    coordinator = RollbackCoordinator(canary_config=self.canary_config)
                    return coordinator.execute_rollback(
                        f"canary:{skill_name}",
                        reason=reason,
                        strategy="auto",
                    )

                self.canary_config.rollback_handler = _auto_rollback_handler
            except Exception as exc:  # noqa: BLE001
                _logger.debug("canary rollback handler injection skipped: %s", exc)

    def _execute_node(
        self,
        i: int,
        node: Any,
        resolved: dict[str, Any],
        graph: TaskGraph,
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        actor: str | None,
    ) -> Step:
        call = normalize_task_node_tool_call(node, resolved, node_index=i)
        self.journal.write_node_started(
            task_id=graph.task_id,
            arm_id=arm_id,
            actor=actor,
            node_id=node.node_id,
            skill_ref=call.name,
            node_index=i,
        )
        step = self.executor.execute_step(
            step_id=i,
            node_id=node.node_id,
            sucker_id=SkillId(call.name),
            args=call.arguments,
            caller=caller,
            task_id=graph.task_id,
            arm_id=arm_id,
            budget=budget,
            predicted_cost=None,
            actor=actor,
        )
        if node.args_template:
            step = step.model_copy(
                update={"args_template": dict(node.args_template)},
            )
        return step

    def _fire_step_callback(
        self,
        callback: OnStepCallback | None,
        step: Step,
        node_index: int,
        total_nodes: int,
    ) -> None:
        """Invoke on_step_callback with exception swallowing (log + continue).

        Mirrors the nerves/hooks policy: a buggy observer must never abort
        the graph run. None callback is a no-op (the common case).
        """
        if callback is None:
            return
        try:
            callback(step, node_index, total_nodes)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("on_step_callback raised, continuing: %s", exc)

    def run(
        self,
        graph: TaskGraph,
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        base_args: dict[str, Any] | None = None,
        stop_on_failure: bool = True,
        resume_from: int = 0,
        outputs_seed: dict[str, Any] | None = None,
        actor: str | None = None,
        planner: LLMPlanner | None = None,
        max_replans: int = 1,
        on_step_callback: OnStepCallback | None = None,
    ) -> Trajectory:
        base_args = base_args or {}
        steps: list[Step] = []
        outputs_by_node: dict[str, Any] = dict(outputs_seed or {})

        with trace_stage(
            "ganglia.run_graph",
            task_id=str(graph.task_id),
            arm_id=arm_id,
        ) as span:
            span.set_attribute("echo.graph.node_count", len(graph.nodes))

            self.journal.write_task_started(
                task_id=graph.task_id,
                arm_id=arm_id,
                actor=actor,
                total_nodes=len(graph.nodes),
                strategy=graph.strategy or "",
                task_type=graph.task_type or "",
                recipe_hash=graph.recipe_hash,
            )

            use_parallel = self.max_parallel > 1 and len(graph.nodes) > 1 and len(graph.edges) > 0
            if use_parallel:
                layers = _topo_layers(graph.nodes, graph.edges)
                steps, outputs_by_node = self._run_layered(
                    layers,
                    graph,
                    steps,
                    outputs_by_node,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    base_args=base_args,
                    stop_on_failure=stop_on_failure,
                    resume_from=resume_from,
                    actor=actor,
                    planner=planner,
                    max_replans=max_replans,
                    span=span,
                    on_step_callback=on_step_callback,
                )
            else:
                steps, outputs_by_node = self._run_sequential(
                    graph,
                    steps,
                    outputs_by_node,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    base_args=base_args,
                    stop_on_failure=stop_on_failure,
                    resume_from=resume_from,
                    actor=actor,
                    planner=planner,
                    max_replans=max_replans,
                    span=span,
                    on_step_callback=on_step_callback,
                )

            nodes_by_id = {node.node_id: node for node in graph.nodes}
            overall_ok = len({step.node_id for step in steps}) >= len(graph.nodes) and all(
                step.success
                or bool(getattr(nodes_by_id.get(step.node_id), "continue_on_failure", False))
                for step in steps
            )
            traj = Trajectory(
                task_id=graph.task_id,
                arm_id=arm_id,
                strategy_id=graph.strategy or "default",
                recipe_id=graph.recipe_hash,
                steps=steps,
                outcome=TrajectoryOutcome(success=overall_ok),
            )
            self.journal.write_trajectory(traj, actor=actor)
            try:
                from runtime.safety.recovery.gepa_bridge import (
                    record_winner_canary_outcome,
                )

                outcome = record_winner_canary_outcome(
                    graph.recipe_hash,
                    success=overall_ok,
                    metadata_root=self.evolution_metadata_root,
                    canary_config=self.canary_config,
                )
                if outcome.get("ok"):
                    span.set_attribute("echo.gepa.canary_key", outcome.get("canary_key", ""))
                    span.set_attribute("echo.gepa.canary_phase", outcome.get("phase", ""))
            except Exception as exc:  # noqa: BLE001
                _logger.debug("winner canary outcome record skipped: %s", exc)

            span.set_attribute("echo.graph.completed", overall_ok)
            span.set_attribute("echo.graph.steps_run", len(steps))
            return traj

    def _run_sequential(
        self,
        graph: TaskGraph,
        steps: list[Step],
        outputs_by_node: dict[str, Any],
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        base_args: dict[str, Any],
        stop_on_failure: bool,
        resume_from: int,
        actor: str | None,
        planner: LLMPlanner | None,
        max_replans: int,
        span: Any,
        on_step_callback: OnStepCallback | None = None,
    ) -> tuple[list[Step], dict[str, Any]]:
        total_nodes = len(graph.nodes)
        for i, node in enumerate(graph.nodes):
            if i < resume_from:
                continue
            merged = {**base_args, **node.args_template}
            try:
                resolved = resolve_templates(merged, outputs_by_node)
            except TemplateResolutionError as e:
                span.set_attribute("echo.graph.template_error", str(e))
                failed_step = self._make_template_error_step(
                    i,
                    node,
                    merged,
                    e,
                    caller,
                )
                self.journal.write_step(
                    task_id=graph.task_id,
                    arm_id=arm_id,
                    step=failed_step,
                    actor=actor,
                )
                steps.append(failed_step)
                self._fire_step_callback(on_step_callback, failed_step, i, total_nodes)
                break

            step = self._execute_node(
                i,
                node,
                resolved,
                graph,
                budget=budget,
                caller=caller,
                arm_id=arm_id,
                actor=actor,
            )
            steps.append(step)
            if step.success:
                outputs_by_node[node.node_id] = step.result.output
            elif node.continue_on_failure:
                # Preserve the diagnostic payload for downstream templates.
                # The Step remains failed, so telemetry does not pretend the
                # command passed; only graph control flow is allowed onward.
                outputs_by_node[node.node_id] = step.result.output
            elif stop_on_failure:
                retry_step = self.executor.execute_step(
                    step_id=i,
                    node_id=node.node_id,
                    sucker_id=node.skill_ref,
                    args=resolved,
                    caller=caller,
                    task_id=graph.task_id,
                    arm_id=arm_id,
                    budget=budget,
                    predicted_cost=None,
                    actor=actor,
                )
                if retry_step.success:
                    steps[-1] = retry_step
                    outputs_by_node[node.node_id] = retry_step.result.output
                else:
                    self._try_replan(
                        i,
                        node,
                        retry_step,
                        graph,
                        steps,
                        outputs_by_node,
                        budget=budget,
                        caller=caller,
                        arm_id=arm_id,
                        base_args=base_args,
                        stop_on_failure=stop_on_failure,
                        actor=actor,
                        planner=planner,
                        max_replans=max_replans,
                        on_step_callback=on_step_callback,
                    )
                    break

            self._fire_step_callback(on_step_callback, step, i, total_nodes)

            if self.checkpoint_every_n_nodes > 0 and (i + 1) % self.checkpoint_every_n_nodes == 0:
                self.journal.write_checkpoint(
                    task_id=graph.task_id,
                    arm_id=arm_id,
                    actor=actor,
                    nodes_completed=i + 1,
                    total_nodes=total_nodes,
                    tokens_spent=budget.tokens_spent,
                    usd_spent=budget.usd_spent,
                )
        return steps, outputs_by_node

    def _run_layered(
        self,
        layers: list[list[int]],
        graph: TaskGraph,
        steps: list[Step],
        outputs_by_node: dict[str, Any],
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        base_args: dict[str, Any],
        stop_on_failure: bool,
        resume_from: int,
        actor: str | None,
        planner: LLMPlanner | None,
        max_replans: int,
        span: Any,
        on_step_callback: OnStepCallback | None = None,
    ) -> tuple[list[Step], dict[str, Any]]:
        global_idx = 0
        total_nodes = len(graph.nodes)
        for _layer_idx, layer_indices in enumerate(layers):
            layer_nodes = []
            for idx in layer_indices:
                if idx < resume_from:
                    global_idx += 1
                    continue
                node = graph.nodes[idx]
                merged = {**base_args, **node.args_template}
                try:
                    resolved = resolve_templates(
                        merged,
                        outputs_by_node,
                    )
                except TemplateResolutionError as e:
                    span.set_attribute(
                        "echo.graph.template_error",
                        str(e),
                    )
                    failed_step = self._make_template_error_step(
                        global_idx,
                        node,
                        merged,
                        e,
                        caller,
                    )
                    self.journal.write_step(
                        task_id=graph.task_id,
                        arm_id=arm_id,
                        step=failed_step,
                        actor=actor,
                    )
                    steps.append(failed_step)
                    self._fire_step_callback(on_step_callback, failed_step, global_idx, total_nodes)
                    return steps, outputs_by_node
                layer_nodes.append((global_idx, node, resolved))
                global_idx += 1

            if not layer_nodes:
                global_idx += len(layer_indices)
                continue

            if len(layer_nodes) == 1:
                gi, node, resolved = layer_nodes[0]
                step = self._execute_node(
                    gi,
                    node,
                    resolved,
                    graph,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    actor=actor,
                )
                steps.append(step)
                if step.success or node.continue_on_failure:
                    outputs_by_node[node.node_id] = step.result.output
                elif stop_on_failure:
                    self._retry_or_replan(
                        gi,
                        node,
                        resolved,
                        step,
                        graph,
                        steps,
                        outputs_by_node,
                        budget=budget,
                        caller=caller,
                        arm_id=arm_id,
                        base_args=base_args,
                        stop_on_failure=stop_on_failure,
                        actor=actor,
                        planner=planner,
                        max_replans=max_replans,
                        on_step_callback=on_step_callback,
                    )
                    break
                self._fire_step_callback(on_step_callback, step, gi, total_nodes)
            else:
                layer_results = self._run_layer_parallel(
                    layer_nodes,
                    graph,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    actor=actor,
                )
                failed_any = False
                for _gi, node, step in layer_results:
                    steps.append(step)
                    if step.success or node.continue_on_failure:
                        outputs_by_node[node.node_id] = step.result.output
                    elif stop_on_failure:
                        failed_any = True

                if failed_any:
                    resolved_by_gi = {g: r for g, _n, r in layer_nodes}
                    for gi, node, step in layer_results:
                        if not step.success:
                            self._retry_or_replan(
                                gi,
                                node,
                                resolved_by_gi.get(gi, {}),
                                step,
                                graph,
                                steps,
                                outputs_by_node,
                                budget=budget,
                                caller=caller,
                                arm_id=arm_id,
                                base_args=base_args,
                                stop_on_failure=stop_on_failure,
                                actor=actor,
                                planner=planner,
                                max_replans=max_replans,
                                on_step_callback=on_step_callback,
                            )
                    break
                # Fire callback once per layer (layer-internal parallel
                # nodes have no inter-step gap — ThreadPoolExecutor blocks).
                for _gi, _node, step in layer_results:
                    self._fire_step_callback(on_step_callback, step, _gi, total_nodes)

            completed_count = sum(1 for s in steps if s.success)
            if (
                self.checkpoint_every_n_nodes > 0
                and completed_count > 0
                and completed_count % self.checkpoint_every_n_nodes == 0
            ):
                self.journal.write_checkpoint(
                    task_id=graph.task_id,
                    arm_id=arm_id,
                    actor=actor,
                    nodes_completed=completed_count,
                    total_nodes=total_nodes,
                    tokens_spent=budget.tokens_spent,
                    usd_spent=budget.usd_spent,
                )
        return steps, outputs_by_node

    def _run_layer_parallel(
        self,
        layer_nodes: list[tuple[int, Any, dict[str, Any]]],
        graph: TaskGraph,
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        actor: str | None,
    ) -> list[tuple[int, Any, Step]]:
        results: list[tuple[int, Any, Step]] = []
        workers = min(len(layer_nodes), self.max_parallel)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {}
            for gi, node, resolved in layer_nodes:
                fut = pool.submit(
                    self._execute_node,
                    gi,
                    node,
                    resolved,
                    graph,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    actor=actor,
                )
                futures[fut] = (gi, node)
            for fut in as_completed(futures):
                gi, node = futures[fut]
                try:
                    step = fut.result(timeout=300)
                except (TimeoutError, OSError, RuntimeError):
                    step = Step(
                        step_id=gi,
                        node_id=node.node_id,
                        action=ToolCall(
                            caller=caller,
                            sucker_id=node.skill_ref,
                            args={},
                        ),
                        result=ExecutionResult(
                            status="failed",
                            error_type="ParallelExecutionError",
                            output={"error": "node timed out or crashed"},
                        ),
                    )
                results.append((gi, node, step))
        results.sort(key=lambda t: t[0])
        return results

    def _retry_or_replan(
        self,
        i: int,
        node: Any,
        resolved: dict[str, Any],
        failed_step: Step,
        graph: TaskGraph,
        steps: list[Step],
        outputs_by_node: dict[str, Any],
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        base_args: dict[str, Any],
        stop_on_failure: bool,
        actor: str | None,
        planner: LLMPlanner | None,
        max_replans: int,
        on_step_callback: OnStepCallback | None = None,
    ) -> None:
        if not resolved:
            for s in steps:
                if s.node_id == node.node_id and s.action:
                    resolved = s.action.args or {}
                    break
        retry_call = normalize_task_node_tool_call(node, resolved, node_index=i)
        retry_step = self.executor.execute_step(
            step_id=i,
            node_id=node.node_id,
            sucker_id=SkillId(retry_call.name),
            args=retry_call.arguments,
            caller=caller,
            task_id=graph.task_id,
            arm_id=arm_id,
            budget=budget,
            predicted_cost=None,
            actor=actor,
        )
        if retry_step.success:
            for idx, s in enumerate(steps):
                if s.node_id == node.node_id:
                    steps[idx] = retry_step
                    break
            outputs_by_node[node.node_id] = retry_step.result.output
        else:
            self._try_replan(
                i,
                node,
                retry_step,
                graph,
                steps,
                outputs_by_node,
                budget=budget,
                caller=caller,
                arm_id=arm_id,
                base_args=base_args,
                stop_on_failure=stop_on_failure,
                actor=actor,
                planner=planner,
                max_replans=max_replans,
                on_step_callback=on_step_callback,
            )

    def _try_replan(
        self,
        i: int,
        node: Any,
        failed_step: Step,
        graph: TaskGraph,
        steps: list[Step],
        outputs_by_node: dict[str, Any],
        *,
        budget: Budget,
        caller: str,
        arm_id: ArmId,
        base_args: dict[str, Any],
        stop_on_failure: bool,
        actor: str | None,
        planner: LLMPlanner | None,
        max_replans: int,
        on_step_callback: OnStepCallback | None = None,
    ) -> None:
        if planner is None or max_replans <= 0:
            return
        try:
            from runtime.platform.models import ParsedIntent

            failed_info = (
                f"节点 {node.node_id} (skill={node.skill_ref}) "
                f"执行失败: "
                f"{getattr(failed_step.result, 'error_type', 'unknown')}"
            )
            replan_intent = ParsedIntent(
                raw=failed_info,
                intent_type="task",
                normalized_goal=(
                    f"[REPLAN] 原计划第 {i + 1}/{len(graph.nodes)} 步失败。"
                    f"已完成节点: {list(outputs_by_node.keys())}。"
                    f"失败原因: {failed_info}。"
                    "请规划替代方案完成剩余任务。"
                ),
            )
            # Extract user-selected model from base_args if available
            user_model = base_args.get("user_model") if base_args else None
            new_graph = planner.plan(replan_intent, model=user_model)
            if new_graph and hasattr(new_graph, "nodes") and new_graph.nodes:
                replan_traj = self.run(
                    new_graph,
                    budget=budget,
                    caller=caller,
                    arm_id=arm_id,
                    base_args=base_args,
                    stop_on_failure=stop_on_failure,
                    actor=actor,
                    planner=planner,
                    max_replans=max_replans - 1,
                    on_step_callback=on_step_callback,
                )
                steps.extend(replan_traj.steps)
                for s in replan_traj.steps:
                    if s.success and s.result and s.result.output:
                        outputs_by_node[s.node_id] = s.result.output
        except (ConnectionError, TimeoutError, TypeError, ValueError) as exc:
            _logger.warning("replan failed: %s", exc)

    def _make_template_error_step(
        self,
        i: int,
        node: Any,
        merged: dict[str, Any],
        error: TemplateResolutionError,
        caller: str,
    ) -> Step:
        failed_call = ToolCall(
            caller=caller,
            sucker_id=node.skill_ref,
            args=merged,
        )
        failed_result = ExecutionResult(
            call_id=failed_call.call_id,
            status="failed",
            error_type="TemplateResolutionError",
            stderr_tags=["template_resolution"],
            output={"error": str(error)},
        )
        return Step(
            step_id=i,
            node_id=node.node_id,
            action=failed_call,
            result=failed_result,
        )
