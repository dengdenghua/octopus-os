"""Mesh swarm stream driver — auto-selecting swarm (mesh vs team) + fallback.

Extracted from ``realtime_team_stream.py``. Plans the graph, then routes it
to the boids/SignalBus MESH swarm (``SwarmRuntime``) when it is a parallel
graph, or to the sequential ``TeamRunner`` otherwise. Any mesh fault falls
back to react, so a swarm problem never takes down the turn.

Public API (re-exported by ``realtime_team_stream``):

* ``_graph_favors_mesh`` — decide whether a parallel mesh is worth it.
* ``_drive_swarm_mesh`` — run a swarm-mode turn on the best engine.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import (
    AgentMessageItem,
    ItemStatus,
    ServerMethod,
    Turn,
)
from runtime.sensing.gateway.realtime_gateway import EventEmitter

_logger = logging.getLogger(__name__)


def _budget_for_graph(graph: Any) -> tuple[int, float]:
    """④ 预算随任务规模动态伸缩：节点越多给越大，同时保留硬上限。

    固定 200k/2.0 对单节点任务浪费、对大图又不够。按节点数线性扩展，
    token 上限 800k、USD 上限 8.0，防止失控。
    """
    node_count = max(1, len(getattr(graph, "nodes", None) or []) or 1)
    tokens = min(800_000, 100_000 + 40_000 * node_count)
    usd = min(8.0, 1.0 + 0.5 * node_count)
    return int(tokens), usd


def _graph_favors_mesh(graph: Any) -> bool:
    """Decide whether the parallel mesh swarm is worth it for this graph.

    Mesh only beats the sequential team when there is real parallelism to
    exploit — several nodes AND a topo-layer with independent siblings. A small
    or strictly-sequential graph runs no faster on the mesh (and the mesh skips
    the team's curated topologies), so those go to the team. This is what lets
    the engine be auto-selected instead of toggled by hand.
    """
    nodes = getattr(graph, "nodes", None) or []
    if len(nodes) < 3:
        return False
    try:
        from runtime.execution.swarm.runtime import _split_topo_layers

        layers = _split_topo_layers(graph)
    except Exception:  # noqa: BLE001 — undecidable → let the team handle it
        return False
    widest = max((len(layer) for layer in layers), default=1)
    return widest >= 2


async def _drive_swarm_mesh(
    runtime: Any,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    *,
    text: str,
    topology_id: str = "",
) -> None:
    """Run a swarm-mode turn on the best engine for the task — auto-selected.

    Plans the graph, then: a parallel graph (>=3 nodes, a layer with
    independent siblings) runs on the boids/SignalBus MESH swarm
    (``SwarmRuntime`` — parallel arms over a registry-derived pool with live
    Arm-to-Arm coordination); a small or sequential graph runs on the
    sequential ``TeamRunner`` (``topology_id``). ``ECHO_SERVE_MESH=1``/``0``
    forces mesh/team; unset = auto. Any mesh fault falls back to react, so a
    swarm problem never takes down the turn.
    """
    import asyncio

    # Resolve the parent module lazily so ``_drive_team_topology`` and
    # ``GatewayApprovalProvider`` stay monkeypatchable on
    # ``realtime_team_stream`` (tests swap them out) without a module-level
    # circular import.
    from runtime.sensing.gateway import realtime_team_stream as _parent

    async def _emit(body: str) -> None:
        item = AgentMessageItem(text=body, status=ItemStatus.COMPLETED)
        turn.items.append(item)
        with contextlib.suppress(Exception):
            log.item_started(turn.thread_id, turn.id, item)
            log.item_completed(turn.thread_id, turn.id, item)
        payload = {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
        }
        with contextlib.suppress(Exception):
            await emitter.notify(ServerMethod.ITEM_STARTED, payload)
            await emitter.notify(ServerMethod.ITEM_COMPLETED, payload)

    async def _fallback_to_react() -> None:
        loop = asyncio.get_running_loop()
        gateway_provider = _parent.GatewayApprovalProvider(
            emitter,
            loop,
            thread_id=intent.user_context.get("thread_id", turn.thread_id),
            turn_id=turn.id,
            trace_store=runtime._trace_store,
        )
        provider = runtime._wrap_with_policy(gateway_provider)
        from runtime.protocol.items import TurnParams

        agent = None
        with contextlib.suppress(Exception):
            agent = runtime._resolve_agent(
                TurnParams(threadId=turn.thread_id, input=[]),  # type: ignore[call-arg]
            )
        await runtime._drive_react(turn, log, emitter, intent, provider, agent)

    def _session():
        from runtime.memory.journal.journal_context import journal_context
        from runtime.platform.process.session import Session, session_scope

        session_metadata = dict(intent.user_context or {})
        params = getattr(turn, "params", None)
        actor = str(getattr(params, "owner_actor_id", None) or "").strip() or None
        tenant = str(getattr(params, "tenant_id", None) or "").strip()
        if tenant:
            session_metadata["tenant_id"] = tenant
        turn_session = Session(
            actor=actor,
            agent=None,
            thread_id=turn.thread_id,
            conversation_id=turn.thread_id,
            turn_id=turn.id,
            metadata=session_metadata,
        )
        return session_scope(turn_session), journal_context(
            conversation_id=turn.thread_id,
        )

    def _plan() -> Any:
        scope, jctx = _session()
        with scope, jctx:
            # Extract user-selected model from intent.user_context
            user_model = None
            if isinstance(intent.user_context, dict):
                user_model = intent.user_context.get("model_name")
            return runtime._stack.planner.plan(intent, model=user_model)

    def _run(graph: Any) -> Any:
        from runtime.core.graph_runtime import GraphRuntime
        from runtime.execution.swarm.drive import (
            build_arm_pool_from_registry,
            run_swarm,
        )
        from runtime.platform.models import Budget, BudgetLimits
        from runtime.safety.chromatophores import SignalBus

        stack = runtime._stack
        scope, jctx = _session()
        with scope, jctx:
            grt = GraphRuntime(executor=stack.executor, journal=stack.journal)
            sb = SignalBus()
            pool = build_arm_pool_from_registry(stack.registry, grt, signal_bus=sb)
            _budget_tokens, _budget_usd = _budget_for_graph(graph)
            budget = Budget(
                task_id=graph.task_id,
                limits=BudgetLimits(tokens=int(_budget_tokens), usd=_budget_usd),
            )
            strategy = "topo_layers" if getattr(graph, "edges", None) else "per_node"
            signals: list[Any] = []
            result = run_swarm(
                graph,
                budget,
                arm_pool=pool,
                signal_bus=sb,
                journal=stack.journal,
                split_strategy=strategy,
                on_signal=signals.append,
                registry=stack.registry,  # ADR-010 Phase 2 · skill-declared exclusivity
            )
            return result, len(signals)

    import os

    # Engine choice precedence: the per-turn UI pick (集群 vs 蜂群) wins, then
    # the ECHO_SERVE_MESH env, then auto (_graph_favors_mesh). The UI sends
    # serve_mesh="1" for 蜂群 (mesh) and "0" for 集群 (sequential TeamRunner);
    # absent = auto, which keeps the "smart default, overridable" (B) behavior.
    ctx = getattr(intent, "user_context", None) or {}
    force = str(ctx.get("serve_mesh") or "").strip().lower()
    if not force:
        force = os.environ.get("ECHO_SERVE_MESH", "").strip().lower()
    forced_off = force in {"0", "false", "no", "off"}
    forced_on = force in {"1", "true", "yes", "on"}

    graph: Any = None
    with contextlib.suppress(Exception):
        graph = await asyncio.to_thread(_plan)

    use_mesh = graph is not None and not forced_off and (forced_on or _graph_favors_mesh(graph))
    if not use_mesh:
        # small / sequential / planning failed / forced off → sequential team
        await _parent._drive_team_topology(
            runtime,
            turn,
            log,
            emitter,
            intent,
            text=text,
            topology_id=topology_id,
        )
        return

    try:
        result, signal_count = await asyncio.to_thread(_run, graph)
        arms = list(getattr(result, "arm_results", []) or [])
        # Plain language for the chat: no "swarm / arm / coordination signal"
        # jargon. Only surface the agents that hit a problem, then one summary.
        failed = [a for a in arms if str(getattr(a, "status", "")) != "success"]
        for arm in failed:
            reason = str(getattr(arm, "reason", "") or "").strip()[:2000]
            await _emit(f"One agent couldn't finish: {reason or 'unknown error'}")
        done = len(arms) - len(failed)
        tail = f", {len(failed)} need a look" if failed else ""
        # Surface the SignalBus chatter — observable proof the agents actually
        # shared progress with each other, not just ran in isolation.
        shared = f" · shared {signal_count} live updates between them" if signal_count else ""
        await _emit(f"Ran {len(arms)} agents in parallel — {done} done{tail}{shared}")
    except Exception as exc:  # noqa: BLE001 — never break the turn on a mesh fault
        _logger.warning(
            "mesh swarm failed (%s: %s) — falling back to react",
            type(exc).__name__,
            exc,
        )
        await _fallback_to_react()
