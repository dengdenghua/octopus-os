"""Multi-agent team-topology stream driver — topology resolution + bridge.

Extracted from ``realtime_team_stream.py`` to keep the swarm router lean.
This module owns the producer-thread → asyncio-queue bridge that streams
``TeamRunner`` live events onto ``item/*`` notifications, plus the topology
resolution + fallback-to-react logic.

Public API (re-exported by ``realtime_team_stream``):

* ``_drive_team_topology`` — run a turn through a multi-agent ``TeamTopology``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import TYPE_CHECKING, Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import (
    AgentMessageItem,
    ErrorItem,
    ItemStatus,
    ServerMethod,
    SubagentItem,
    Turn,
    TurnStatus,
)
from runtime.sensing.gateway.realtime_approval import GatewayApprovalProvider
from runtime.sensing.gateway.realtime_gateway import EventEmitter

if TYPE_CHECKING:
    from runtime.sensing.gateway.realtime_cerebrum import CerebrumRuntime

_logger = logging.getLogger(__name__)


async def _drive_team_topology(
    runtime: CerebrumRuntime,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    *,
    text: str,
    topology_id: str,
) -> None:
    """Run the turn through a multi-agent ``TeamTopology``.

    ╔═══════════════════════════════════════════════════════════════╗
    ║ _drive_team_topology · navigation (523 lines, async bridge).  ║
    ║                                                               ║
    ║   PHASE 1 · topology resolution + fallback       ~L2182      ║
    ║   PHASE 2 · queue bridge setup                   ~L2230      ║
    ║   PHASE 3 · producer thread definition           ~L2281      ║
    ║   PHASE 4 · interrupt watcher + helpers          ~L2360      ║
    ║   PHASE 5 · consumer loop (event dispatch)       ~L2480      ║
    ║   PHASE 6 · finalization + perf log              ~L2635      ║
    ║                                                               ║
    ║ Why one big async method: producer thread + asyncio queue     ║
    ║ bridge + nested async closures sharing ~10 state vars.        ║
    ╚═══════════════════════════════════════════════════════════════╝

    The topology id resolves through the organization registry by
    fingerprint *or* by name. On miss we fall back to single-agent
    ReAct so a stale ``topology_id`` never aborts the turn.

    Each role's output emits as a separate ``AgentMessageItem`` so
    the client sees the team's reasoning trace; the team's final
    output becomes the trailing AgentMessageItem and the run gets
    recorded into ``data/topology_performance.jsonl`` for the
    evolver to score next tick.
    """
    # ── PHASE 1 · topology resolution + fallback ────────────────
    from runtime.safety.evolution.governance_audit import (
        append_governance_audit_event,
    )
    from runtime.safety.evolution.subagent_policy import evaluate_agent_policy
    from runtime.safety.organization import TeamTopology
    from runtime.safety.organization.forge import load_registry
    from runtime.safety.organization.performance_log import record_run
    from runtime.safety.organization.team_runner import (
        TeamRunner,
        TeamRunResult,
    )

    async def _fallback_to_react() -> None:
        loop = asyncio.get_running_loop()
        gateway_provider = GatewayApprovalProvider(
            emitter,
            loop,
            thread_id=intent.user_context.get("thread_id", turn.thread_id),
            turn_id=turn.id,
            trace_store=runtime._trace_store,
        )
        provider = runtime._wrap_with_policy(gateway_provider)
        from runtime.protocol.items import TurnParams  # local

        agent = None
        try:
            agent = runtime._resolve_agent(
                TurnParams(threadId=turn.thread_id, input=[]),  # type: ignore[call-arg]
            )
        except Exception:  # noqa: BLE001
            _logger.debug("agent resolution failed, using default", exc_info=True)
            agent = None
        await runtime._drive_react(turn, log, emitter, intent, provider, agent)

    thread_id = turn.thread_id
    registry = load_registry()
    topology: TeamTopology | None = registry.get(topology_id)
    if topology is None:
        # Allow name lookup as a convenience (UI users will refer
        # to topologies by their human-readable name, not the
        # fingerprint).
        for t in registry.values():
            if t.name == topology_id:
                topology = t
                break
    if topology is None:
        _logger.warning(
            "topology_id %r not in registry · falling back to react",
            topology_id,
        )
        await _fallback_to_react()
        return
    policy_report = evaluate_agent_policy(
        {str(role): spec.agent_id for role, spec in topology.agents.items()}
    )
    if policy_report.get("blocked"):
        _logger.warning(
            "topology_id %r is blocked by operator subagent policy · falling back to react",
            topology_id,
        )
        with contextlib.suppress(Exception):
            append_governance_audit_event(
                event_type="topology_policy_block",
                target="topology_policy",
                status="blocked",
                agent_id=str(intent.user_context.get("agent_id") or ""),
                artifact={
                    "topology_id": topology_id,
                    "topology_name": topology.name,
                    "topology_fingerprint": topology.fingerprint,
                    "subagent_policy": policy_report,
                },
                decision_context={
                    "thread_id": thread_id,
                    "turn_id": turn.id,
                    "source": "realtime_team_topology",
                },
            )
        await _fallback_to_react()
        return

    # ── PHASE 2 · queue bridge setup ────────────────────────────
    runner_timeout = int(runtime._max_iterations * 30)

    # Live-event bridge: TeamRunner -> emitter (this coroutine).
    #
    # Why this exists: ``TeamRunner.run`` is synchronous and used to
    # be invoked through ``asyncio.to_thread(...)`` followed by a
    # batch flush of every role's output. For a 3-role research swarm
    # that meant the user saw nothing for 60-120 seconds, then a
    # wall of text — and during the silent window the WS often
    # closed because the frontend treated "no events for N seconds"
    # as an interrupted stream ("本次回复已中断").
    #
    # Now: producer thread runs ``runner.run`` with an emitter that
    # marshals every progress event onto the asyncio queue; this
    # coroutine drains the queue and translates events into
    # ``item/*`` notifications using the same ``_ReactBridgeState``
    # the ReAct path uses, so subagents in a swarm appear in the
    # UI's tool timeline alongside regular tool calls.
    from runtime.safety.approval.cancellation import (
        CancellationSource,
        scoped_cancellation,
    )

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)
    loop = asyncio.get_running_loop()
    cancel_source = CancellationSource()

    def _push(event: dict[str, Any]) -> None:
        # Producer side: marshal events back to the asyncio loop.
        # Use ``run_coroutine_threadsafe(...).result()`` so the
        # producer thread blocks if the consumer can't keep up
        # (instead of fire-and-forget which silently drops events
        # when the queue is full). Bounded blocking is safe here
        # because the consumer drains continuously; the only way
        # we'd block forever is consumer dead, which would surface
        # as a hung turn anyway.
        try:
            asyncio.run_coroutine_threadsafe(
                queue.put(event),
                loop,
            ).result(timeout=10.0)
        except (RuntimeError, TimeoutError):
            # RuntimeError: loop closed mid-call.
            # TimeoutError: consumer stuck — drop this event rather
            # than block the producer indefinitely. Telemetry only;
            # the run keeps going.
            _logger.debug(
                "team_runner emitter push failed/timed out",
            )

    # ── PHASE 3 · producer thread definition ────────────────────
    def producer() -> TeamRunResult:
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
            thread_id=thread_id,
            conversation_id=thread_id,
            turn_id=turn.id,
            metadata=session_metadata,
        )
        # Install the cancellation scope on the worker thread so
        # ``call_subagent`` inside the runner sees the same token
        # as react_loop does — every long-running subprocess /
        # network call inside a role checks
        # ``current_cancellation_token()`` and bails out fast.
        # journal_context feeds the journal's conversation_id
        # contextvar (separate from session_scope) so trace rows
        # carry thread_id instead of None.
        with (
            session_scope(turn_session),
            journal_context(conversation_id=thread_id),
            scoped_cancellation(cancel_source.token),
        ):
            try:
                runner = TeamRunner(
                    timeout_seconds=runner_timeout,
                    event_emitter=_push,
                )
                return runner.run(
                    topology,
                    text,
                    context={
                        **session_metadata,
                        "thread_id": thread_id,
                        "turn_id": turn.id,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - surface as event
                with contextlib.suppress(RuntimeError, TimeoutError):
                    asyncio.run_coroutine_threadsafe(
                        queue.put(
                            {
                                "type": "team_runner_error",
                                "kind": exc.__class__.__name__,
                                "message": str(exc),
                            }
                        ),
                        loop,
                    ).result(timeout=5.0)
                return TeamRunResult(
                    topology_name=topology.name,
                    topology_fingerprint=topology.fingerprint,
                    task_bucket=topology.task_bucket,
                    success=False,
                    final_output="",
                    error=f"{type(exc).__name__}: {exc}",
                )
            finally:
                # Always send the sentinel even if the consumer has
                # already gone away — suppress so we don't deadlock
                # the worker thread when the loop is torn down.
                with contextlib.suppress(RuntimeError, TimeoutError):
                    asyncio.run_coroutine_threadsafe(
                        queue.put(None),
                        loop,
                    ).result(timeout=5.0)

    worker = asyncio.create_task(asyncio.to_thread(producer))
    state = runtime._make_bridge_state(turn.thread_id)
    # Track how many characters of role text streamed in via
    # ``sub_text_delta`` for the currently-open role. ``team_role_end``
    # uses this to decide whether to dump the role's full output as a
    # one-shot bubble (zero streamed → we never got live text, fall
    # back to the post-hoc dump) or skip it (already streamed).
    streamed_chars: dict[str, int] = {"count": 0}
    subagent_items: dict[str, SubagentItem] = {}
    subagent_seq = 0

    # ── PHASE 4 · interrupt watcher + helpers ───────────────────
    async def _interrupt_watcher() -> None:
        # Trip cancellation the instant the gateway records a
        # ``turn/interrupt`` for this turn id. Without this the
        # swarm runs to natural completion (or to the
        # ``runner_timeout`` of ``max_iterations * 30`` seconds)
        # even after the user clicks "stop".
        try:
            while not cancel_source.is_cancelled:
                if emitter.is_turn_interrupted(turn.id):
                    cancel_source.cancel(reason="user interrupted turn")
                    return
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_interrupt_watcher())

    async def _safe_notify(method: Any, params: dict[str, Any]) -> None:
        # WS may close while we're mid-stream. Notify is best-effort
        # at the consumer layer — we don't want a single ws.send
        # failure to abort processing of the rest of the queue
        # (and the run_result we're waiting on).
        try:
            await emitter.notify(method, params)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("emitter.notify failed: %s", exc, exc_info=True)

    async def _safe_emit_started(turn: Turn, log: EventLog, item: Any) -> None:
        log.item_started(turn.thread_id, turn.id, item)
        await _safe_notify(
            ServerMethod.ITEM_STARTED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
            },
        )

    async def _safe_emit_completed(turn: Turn, log: EventLog, item: Any) -> None:
        log.item_completed(turn.thread_id, turn.id, item)
        await _safe_notify(
            ServerMethod.ITEM_COMPLETED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "item": item.model_dump(by_alias=True, mode="json"),
            },
        )

    async def _emit_marker(
        turn: Turn,
        log: EventLog,
        text: str,
        *,
        icon: str = "💬",
        agent_display_name: str = "主持人",
    ) -> None:
        """Emit a one-shot phase-marker bubble (parallel start, cross-check,
        critic revision, blocked role, ...) so the four cluster optimizations
        are visible in the stream instead of silently happening inside the
        runner."""
        await state.flush(turn, log, emitter)
        item = AgentMessageItem(
            text=text,
            status=ItemStatus.COMPLETED,
            agent_display_name=agent_display_name,
            agent_icon=icon,
        )
        turn.items.append(item)
        log.item_started(turn.thread_id, turn.id, item)
        log.item_completed(turn.thread_id, turn.id, item)
        await _safe_emit_started(turn, log, item)
        await _safe_emit_completed(turn, log, item)

    def _coerce_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value if item is not None]

    def _subagent_key(evt: dict[str, Any]) -> str:
        raw = str(evt.get("agent_id") or evt.get("role") or "subagent")
        return raw or "subagent"

    async def _emit_subagent_lifecycle(evt: dict[str, Any]) -> None:
        nonlocal subagent_seq
        ekind = str(evt.get("type") or "")
        agent_key = _subagent_key(evt)
        role = str(evt.get("role") or "") or None
        codename = str(evt.get("codename") or "") or None
        avatar = str(evt.get("avatar") or "") or None
        status = str(evt.get("status") or "") or None
        existing = subagent_items.get(agent_key)
        if existing is None:
            subagent_seq += 1
            safe_agent = re.sub(r"[^A-Za-z0-9_.:-]+", "_", agent_key).strip("_")
            item_id = f"sub_{safe_agent or 'agent'}_{subagent_seq}"[:80]
            existing = SubagentItem(
                id=item_id,
                subagent_id=agent_key,
                role=role,
                name=codename,
                codename=codename,
                avatar=avatar,
                status=ItemStatus.IN_PROGRESS,
            )
            subagent_items[agent_key] = existing
            turn.items.append(existing)
            await _safe_emit_started(turn, log, existing)
        elif ekind == "subagent_spawned":
            existing = existing.model_copy(
                update={
                    "role": role or existing.role,
                    "name": codename or existing.name,
                    "codename": codename or existing.codename,
                    "avatar": avatar or existing.avatar,
                }
            )
            subagent_items[agent_key] = existing
            turn.items = [existing if item.id == existing.id else item for item in turn.items]
            await _safe_emit_started(turn, log, existing)
        if ekind != "subagent_finished":
            return

        ok = bool(evt.get("ok", True)) and not evt.get("error")
        iteration_count: int | None
        try:
            iteration_count = int(evt.get("iteration_count") or 0)
        except (TypeError, ValueError):
            iteration_count = existing.iteration_count
        completed = existing.model_copy(
            update={
                "status": ItemStatus.COMPLETED if ok else ItemStatus.FAILED,
                "role": role or existing.role,
                "name": codename or existing.name,
                "codename": codename or existing.codename,
                "avatar": avatar or existing.avatar,
                "summary": status or existing.summary,
                "error": str(evt.get("error")) if evt.get("error") else None,
                "iteration_count": iteration_count,
                "files_touched": _coerce_str_list(evt.get("files_touched")),
            }
        )
        subagent_items[agent_key] = completed
        turn.items = [completed if item.id == completed.id else item for item in turn.items]
        await _safe_emit_completed(turn, log, completed)

    # ── PHASE 5 · consumer loop (event dispatch) ────────────────
    run_result: TeamRunResult | None = None
    try:
        while True:
            evt = await queue.get()
            if evt is None:
                break
            if emitter.is_turn_interrupted(turn.id):
                if not cancel_source.is_cancelled:
                    cancel_source.cancel(reason="user interrupted turn")
                turn.status = TurnStatus.CANCELLED
                turn.outcome_reason = "user_cancelled"
                # Keep draining so the producer can finish cleanly
                # (and emit its final None sentinel) — don't break
                # mid-queue or the worker hangs on its put().
                continue
            ekind = evt.get("type")
            if ekind == "team_role_start":
                # Close any running role bubble first.
                await state.flush(turn, log, emitter)
                role_label = str(evt.get("role") or "role")
                agent_id = str(evt.get("agent_id") or "")
                header = f"[{role_label}] starting · agent={agent_id}\n"
                await state.append_agent_message(
                    turn,
                    log,
                    emitter,
                    header,
                )
                # Reset the streamed-chars counter for this role so
                # the team_role_end completion check below can tell
                # whether the role's text already streamed in vs.
                # needs a one-shot dump.
                streamed_chars["count"] = 0
            elif ekind == "team_parallel_start":
                # ② 并行副本阶段标记：研究员池以 N 个副本并行跑。
                replicas = int(evt.get("replicas") or 1)
                role_label = str(evt.get("role") or "role")
                await _emit_marker(
                    turn,
                    log,
                    f"🔀 并行 {role_label} 池 · {replicas} 个副本同时开工 · agent={evt.get('agent_id') or ''}",
                    icon="🔀",
                )
            elif ekind == "team_cross_check_start":
                # ② 并行副本交叉验证：critic 对 N 个副本产出做去重/冲突/缺口核查。
                replicas = int(evt.get("replicas") or 1)
                await _emit_marker(
                    turn,
                    log,
                    f"🔍 critic 交叉核查 · 对 {replicas} 个副本去重/冲突/缺口 · agent={evt.get('agent_id') or ''}",
                    icon="🔍",
                )
            elif ekind == "team_revision_start":
                # ③ critic 反驳 → generator 重写：标出本轮修订。
                round_no = int(evt.get("round_no") or 1)
                await _emit_marker(
                    turn,
                    log,
                    f"✍️ critic 反驳 → generator 修订 第 {round_no} 轮 · agent={evt.get('agent_id') or ''}",
                    icon="✍️",
                )
            elif ekind == "team_role_blocked":
                # ① 失败隔离/路由阻断：被策略拦下的角色明确亮出来。
                role_label = str(evt.get("role") or "role")
                reason = str(evt.get("error") or "subagent blocked by routing policy")
                await _emit_marker(
                    turn,
                    log,
                    f"⛔ {role_label} 被路由策略阻断 · {reason}",
                    icon="⛔",
                )
            elif ekind == "sub_text_delta":
                # Live role text. Each chunk lands on the currently-
                # open AgentMessageItem (opened by team_role_start).
                # Without this, role text only appeared after the
                # role finished — the user saw a 30s gap between
                # "role starting" and the role's verdict.
                chunk = str(evt.get("delta") or "")
                if chunk:
                    await state.append_agent_message(
                        turn,
                        log,
                        emitter,
                        chunk,
                    )
                    streamed_chars["count"] += len(chunk)
            elif ekind == "team_role_end":
                if evt.get("status") == "error":
                    await state.flush(
                        turn,
                        log,
                        emitter,
                        status=ItemStatus.FAILED,
                    )
                    err_text = str(evt.get("error") or "role failed")
                    body = f"[{evt.get('role')}] FAILED · {err_text}"
                    item = AgentMessageItem(
                        text=body,
                        status=ItemStatus.COMPLETED,
                    )
                    turn.items.append(item)
                    await _safe_emit_started(turn, log, item)
                    await _safe_emit_completed(turn, log, item)
                else:
                    await state.flush(turn, log, emitter)
                    # Two completion paths:
                    #
                    # 1. ``streamed_chars["count"] > 0``: text already
                    #    landed via ``sub_text_delta`` chunks. Just
                    #    flush the open AgentMessageItem so the UI
                    #    marks it complete; the body is already there.
                    #
                    # 2. ``streamed_chars["count"] == 0``: the
                    #    underlying router didn't stream (synthetic
                    #    fallback, or this role used ``call`` not
                    #    ``call_stream``). Fall back to a one-shot
                    #    AgentMessageItem with the role's full output
                    #    so the user still sees the verdict instead
                    #    of just the header.
                    await state.flush(turn, log, emitter)
                    if streamed_chars["count"] == 0:
                        out_text = str(evt.get("output") or "").strip()
                        if out_text:
                            body = f"[{evt.get('role')}] {out_text[:8000]}"
                            item = AgentMessageItem(
                                text=body,
                                status=ItemStatus.COMPLETED,
                            )
                            turn.items.append(item)
                            await _safe_emit_started(turn, log, item)
                            await _safe_emit_completed(turn, log, item)
                    # Reset for the next role.
                    streamed_chars["count"] = 0
            elif ekind == "sub_tool_start":
                # Translate to the react_loop tool_start shape so
                # ``state.start_tool`` can render this in the UI's
                # live tool timeline alongside react-mode tools.
                # Prefer the upstream ``tool_call_id`` (the LLM's
                # ToolCall.id, guaranteed unique within a round)
                # and fall back to a synthetic key only when the
                # producer didn't supply one (older path / mocks).
                call_id = str(evt.get("tool_call_id") or "") or (
                    f"{evt.get('agent_id', 'role')}-r"
                    f"{evt.get('round', 0)}-{evt.get('skill', 'tool')}"
                )
                await state.start_tool(
                    turn,
                    log,
                    emitter,
                    {
                        "tool_call_id": call_id,
                        "tool_name": str(evt.get("skill") or "tool"),
                        "input_preview": evt.get("args_preview"),
                        "iteration": evt.get("round"),
                    },
                )
            elif ekind == "sub_tool_end":
                call_id = str(evt.get("tool_call_id") or "") or (
                    f"{evt.get('agent_id', 'role')}-r"
                    f"{evt.get('round', 0)}-{evt.get('skill', 'tool')}"
                )
                await state.complete_tool(
                    turn,
                    log,
                    emitter,
                    {
                        "tool_call_id": call_id,
                        "status": evt.get("status", "success"),
                        "duration_ms": evt.get("duration_ms"),
                        "output_preview": evt.get("output_preview"),
                    },
                )
            elif ekind in {"subagent_spawned", "subagent_finished"}:
                await _emit_subagent_lifecycle(evt)
            elif ekind == "team_heartbeat":
                # Lightweight keepalive: prevents the frontend's
                # pong-timeout (70s) from killing the WS during
                # long-running roles that don't produce text deltas
                # (e.g. a researcher doing multi-step web_search).
                # Emit an empty agent message delta so the frontend
                # sees activity without polluting the message body.
                await _safe_notify(
                    ServerMethod.TURN_HEARTBEAT,
                    {
                        "threadId": thread_id,
                        "turnId": turn.id,
                        "role": str(evt.get("role") or ""),
                        "agentId": str(evt.get("agent_id") or ""),
                        "elapsedS": evt.get("elapsed_s", 0),
                    },
                )
            elif ekind == "team_runner_error":
                await state.flush(
                    turn,
                    log,
                    emitter,
                    status=ItemStatus.FAILED,
                )
                err = ErrorItem(
                    message=str(evt.get("message") or "team runner error"),
                    will_retry=False,
                )
                turn.status = TurnStatus.FAILED
                turn.items.append(err)
                await _safe_emit_started(turn, log, err)
                await _safe_emit_completed(turn, log, err)
    finally:
        # ── PHASE 6 · finalization + perf log ───────────────────
        # Trip cancellation so the producer THREAD bails fast (task
        # cancellation can't reach an asyncio.to_thread worker). On a
        # ws-disconnect teardown this is what stops the runner from
        # looping against a dead queue and orphaning the thread.
        cancel_source.cancel(reason="consumer teardown")
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
        with contextlib.suppress(Exception):
            await state.flush(
                turn,
                log,
                emitter,
                status=state.prose_status_for_turn(turn.status),
            )
        # Always reap the worker so the thread can't outlive the turn.
        with contextlib.suppress(Exception):
            run_result = await worker
    if run_result is None:
        # Consumer was torn down before the worker produced a result
        # (e.g. ws disconnect). Nothing more to finalize.
        return

    # If the watcher tripped cancellation, prefer that over the
    # natural success/fail outcome — the user explicitly stopped.
    if cancel_source.is_cancelled:
        turn.status = TurnStatus.CANCELLED
        turn.outcome_reason = "user_cancelled"
    elif run_result.success:
        turn.status = TurnStatus.COMPLETED
    else:
        turn.status = TurnStatus.FAILED

    # ② 集群失败可视化：失败隔离后角色失败不再整体失败，但要在最终交付上
    # 标注"降级"——学 workbuddy 的 "X failed" 可见性。
    degraded = list(getattr(run_result, "degraded_roles", None) or [])
    if degraded:
        _names = "、".join(degraded)
        _rev = getattr(run_result, "revision_rounds", 0) or 0
        _note = f"⚠️ 降级交付：{_names} 未能完成，已保留部分产出继续；"
        if _rev:
            _note += f"critic 反驳后修订 {_rev} 轮。"
        else:
            _note += "其余角色已接力完成。"
        with contextlib.suppress(Exception):
            _item = AgentMessageItem(
                text=_note,
                status=ItemStatus.COMPLETED,
                agent_display_name="主持人",
                agent_avatar_url="/api/agents/swarm-moderator/avatar",
                agent_icon="⚠️",
            )
            turn.items.append(_item)
            log.item_started(turn.thread_id, turn.id, _item)
            log.item_completed(turn.thread_id, turn.id, _item)
            await _safe_emit_started(turn, log, _item)
            await _safe_emit_completed(turn, log, _item)

    with contextlib.suppress(Exception):
        await state.finalize_workbench(
            turn,
            log,
            emitter,
            terminal_status=turn.status,
        )

    # Record into the topology performance log so the evolver has
    # something to score. Best-effort: a write failure must not
    # take down the turn that just succeeded.
    with contextlib.suppress(Exception):
        record_run(
            run_result,
            extra={
                "thread_id": thread_id,
                "turn_id": turn.id,
            },
        )
