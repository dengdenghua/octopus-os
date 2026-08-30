"""Group fan-out stream driver — 蜂群 / 冒泡 cowork dispatch.

Extracted from ``realtime_team_stream.py``. Fans a message out to every
member agent in parallel and emits each persona reply as its own
group-chat bubble. Falls back to single-agent ReAct when the room has
<2 member agents or nobody answers, so the turn never stalls.

Public API (re-exported by ``realtime_team_stream``):

* ``_drive_group_fanout`` — fan the message out to every member agent.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import Any

from runtime.memory.threads.event_log import EventLog
from runtime.platform.models import ParsedIntent
from runtime.protocol import (
    AgentMessageItem,
    ItemStatus,
    McpToolCallItem,
    ReasoningItem,
    ServerMethod,
    SubagentItem,
    Turn,
)
from runtime.sensing.gateway.realtime_approval import GatewayApprovalProvider
from runtime.sensing.gateway.realtime_gateway import EventEmitter

_logger = logging.getLogger(__name__)


def _extract_mention_target(body: str, roster_members: list[dict[str, Any]]) -> str | None:
    """③ 从回复正文里解析 @ 到的成员名，用于气泡"回应 @谁"标注。"""
    if not body or not roster_members:
        return None
    for m in roster_members:
        display = str(m.get("display_name") or m.get("name") or "")
        parts = display.split()
        cands = {
            display,
            parts[0] if parts else display,
            display.replace(" ", ""),
        }
        if any(c and ("@" + c) in body for c in cands):
            return display
    return None


# ── 成员失败的错误净化 ──────────────────────────────────────────────
# 蜂群把成员异常(ConnectError/超时/429/权限)原样打进聊天气泡会让用户看到
# 一堆 SSL/traceback 噪音(thread t0Wn5Zhvh3VUFwoAR2uP4M: "⚠️ 钊审财 · 财报
# 研究员 未能回应 · ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING] …")。
# 用户需要知道"谁没答上、要不要紧",不需要底层异常串。这里把常见异常归类成
# 一句友好话术;原始细节只进日志/审计,不进聊天。
_SANITIZED_ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("ssl", "网络连接中断"),
    ("unexpected_eof", "网络连接中断"),
    ("timeout", "响应超时"),
    ("timed out", "响应超时"),
    ("connection refused", "服务未启动或拒绝连接"),
    ("connection reset", "连接被重置"),
    ("rate limit", "触发限流(稍后自动重试)"),
    ("429", "触发限流(稍后自动重试)"),
    ("quota", "额度不足"),
    ("auth", "鉴权失败"),
    ("permission", "权限不足"),
    ("model not found", "模型不可用"),
    ("model not found or", "模型不可用"),
    ("no model", "模型未配置"),
    ("context length", "上下文超长"),
    ("exceeds", "上下文超长"),
)


def _friendly_member_error(error: Any) -> str:
    raw = str(error or "").strip()
    if not raw:
        return "未能产生回复"
    lower = raw.lower()
    for hint, label in _SANITIZED_ERROR_HINTS:
        if hint in lower:
            return label
    # 兜底:绝不在气泡里展示原始异常堆栈/长串,只给类型名的简短形式。
    name = raw.splitlines()[0].strip()
    if len(name) <= 60:
        return f"异常({name})"
    return "未知异常(详见审计日志)"


def _fanout_member_context(ctx: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Clone the parent turn contract for every fan-out member.

    Group fan-out previously forwarded only the generated chat prompt.  That
    made a member silently fall back to its default mode even when the user had
    selected research/build/audit for this turn.  Keep the structured context
    for runtimes that consume it and a compact prompt addendum for lightweight
    persona lanes that only consume text.
    """

    member_context = dict(ctx)
    # These are group-driver implementation details, not child work policy.
    member_context.pop("agent_roster", None)
    member_context.pop("conversation_messages", None)

    from runtime.core.cerebrum._react_context_code import (
        _build_code_agent_mode_prompt,
        _build_personal_agent_mode_prompt,
        _build_workflow_preset_prompt,
    )
    from runtime.execution.misc.skill_policy import is_audit_read_only_context

    sections: list[str] = []
    workflow_preset = str(member_context.get("workflow_preset") or "").strip()
    if workflow_preset:
        rendered = _build_workflow_preset_prompt(workflow_preset)
        if rendered:
            sections.append(rendered)
    agent_mode = str(member_context.get("agent_mode") or "").strip()
    if agent_mode:
        sections.append(_build_code_agent_mode_prompt(agent_mode))
    personal_mode = str(member_context.get("personal_mode") or "").strip().lower()
    if personal_mode:
        rendered = _build_personal_agent_mode_prompt(personal_mode)
        if rendered:
            sections.append(rendered)
        elif personal_mode == "research":
            sections.append(
                "<personal-mode>当前任务类型: research。先搜索、读取并交叉核对证据;"
                "优先一手来源,不要把未经验证的印象写成结论。</personal-mode>"
            )
    personal_instructions = str(member_context.get("personal_instructions") or "").strip()
    if personal_instructions:
        sections.append(
            "<inherited-personal-instructions>"
            + personal_instructions[:2000]
            + "</inherited-personal-instructions>"
        )
    mode_contract = str(member_context.get("mode_contract") or "").strip()
    if mode_contract:
        sections.append(
            "<inherited-mode-contract>" + mode_contract[:2000] + "</inherited-mode-contract>"
        )

    if is_audit_read_only_context(member_context):
        member_context["tool_allowlist_read_only"] = True
    policy_prompt = "\n".join(sections)
    existing_addendum = str(member_context.get("system_addendum") or "").strip()
    if policy_prompt:
        member_context["system_addendum"] = "\n\n".join(
            part for part in (existing_addendum, policy_prompt) if part
        )
    return member_context, policy_prompt


async def _drive_group_fanout(
    runtime: Any,
    turn: Turn,
    log: EventLog,
    emitter: EventEmitter,
    intent: ParsedIntent,
    *,
    text: str,
) -> None:
    """蜂群 / 冒泡: fan the message out to every member agent in parallel and
    emit each persona reply as its own group-chat bubble — the "boss speaks,
    everyone chimes in" experience. Falls back to single-agent ReAct when the
    room has <2 member agents or nobody answers, so the turn never stalls.
    """
    ctx = getattr(intent, "user_context", None) or {}
    try:
        from runtime.platform.process.session import Session, current_session

        parent_session = current_session()
        if parent_session is None:
            # ``TurnParams.owner_actor_id`` / ``tenant_id`` are server-only
            # fields stamped by RealtimeGateway. This gives worker-thread
            # members a trusted principal without trusting user_context.
            params = getattr(turn, "params", None)
            actor = str(getattr(params, "owner_actor_id", None) or "").strip()
            tenant = str(getattr(params, "tenant_id", None) or "").strip()
            if actor and tenant:
                metadata = dict(ctx)
                metadata["tenant_id"] = tenant
                parent_session = Session(
                    actor=actor,
                    thread_id=turn.thread_id,
                    conversation_id=turn.thread_id,
                    turn_id=turn.id,
                    metadata=metadata,
                )
    except (ImportError, LookupError):
        parent_session = None
    member_context, member_policy_prompt = _fanout_member_context(ctx)
    # Standard Coder members execute on worker threads, but approvals must
    # still round-trip through this parent realtime turn.  This object is
    # server-created and deliberately replaces any similarly named client key.
    group_gateway_provider = GatewayApprovalProvider(
        emitter,
        asyncio.get_running_loop(),
        thread_id=str(ctx.get("thread_id") or turn.thread_id),
        turn_id=turn.id,
        trace_store=runtime._trace_store,
    )
    member_context["_codex_approval_provider"] = runtime._wrap_with_policy(group_gateway_provider)
    roster = ctx.get("agent_roster") or []
    members = [
        {
            "name": str(r.get("agent_id")),
            "display_name": str(r.get("display_name") or r.get("agent_id")),
        }
        for r in roster
        if isinstance(r, dict) and r.get("agent_id")
    ]

    async def _emit(
        body: str,
        *,
        display_name: str | None = None,
        agent_id: str | None = None,
        icon: str | None = None,
        reply_to: str | None = None,
    ) -> None:
        # Tag the bubble with its real author so the UI shows that member's
        # avatar + name instead of the turn leader's. Use the shared resolver so
        # the URL carries ``?v=<mtime>`` — that cache-busts when an agent's
        # avatar file changes (e.g. swapping in a brand logo).
        avatar_url: str | None = None
        if agent_id:
            try:
                from runtime.sensing.gateway.agents_router import _avatar_url_for

                avatar_url = _avatar_url_for(agent_id)
            except Exception:  # noqa: BLE001 — avatar is decoration; never break the turn
                avatar_url = None
            avatar_url = avatar_url or f"/api/agents/{agent_id}/avatar"
        item = AgentMessageItem(
            text=body,
            status=ItemStatus.COMPLETED,
            agent_display_name=display_name,
            agent_avatar_url=avatar_url,
            agent_icon=icon,
            reply_to=reply_to,
        )
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
        gateway_provider = GatewayApprovalProvider(
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

    def _record_fallback_audit(reason: str, exc: BaseException | None = None) -> None:
        payload: dict[str, Any] = {
            "schema": "echo.group_fanout_fallback.v1",
            "reason": reason,
            "fallback": "react",
        }
        if exc is not None:
            payload.update(
                {
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        with contextlib.suppress(Exception):
            audit_item = ReasoningItem(
                summary=["Group fanout fallback"],
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                status=ItemStatus.COMPLETED,
            )
            turn.items.append(audit_item)
            log.item_started(turn.thread_id, turn.id, audit_item)
            log.item_completed(turn.thread_id, turn.id, audit_item)

    async def _notify_started(item: Any) -> None:
        turn.items.append(item)
        with contextlib.suppress(Exception):
            log.item_started(turn.thread_id, turn.id, item)
        payload = {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
        }
        with contextlib.suppress(Exception):
            await emitter.notify(ServerMethod.ITEM_STARTED, payload)

    async def _notify_completed(item: Any) -> None:
        with contextlib.suppress(Exception):
            log.item_completed(turn.thread_id, turn.id, item)
        payload = {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "item": item.model_dump(by_alias=True, mode="json"),
        }
        with contextlib.suppress(Exception):
            await emitter.notify(ServerMethod.ITEM_COMPLETED, payload)

    team_trace_item: McpToolCallItem | None = None
    member_trace_items: dict[str, SubagentItem] = {}
    team_trace_started = 0.0
    planned_group_capacity: dict[str, Any] = {}

    async def _start_group_trace(
        group_members: list[dict[str, str]],
        *,
        max_members: int,
        max_concurrency: int,
        scale_mode: str,
    ) -> None:
        """Expose lightweight cowork fanout as a first-class team run.

        Kimi-style swarm UX depends on the user seeing who was dispatched before
        the replies arrive. The fanout itself is still conversational, but the
        runtime now records a replayable parent ``team_swarm`` item plus one
        ``SubagentItem`` lane per member.
        """
        nonlocal team_trace_item, team_trace_started
        if not group_members:
            return
        nonlocal planned_group_capacity
        team_trace_started = time.monotonic()
        dispatched_members = group_members[:max_members]
        planned_group_capacity = {
            "schema": "echo.group_fanout_capacity.v1",
            "requested_members": len(group_members),
            "dispatched_members": len(dispatched_members),
            "dropped_members": max(0, len(group_members) - len(dispatched_members)),
            "max_members": max_members,
            "max_concurrency": max_concurrency,
            "concurrency": max(1, min(len(dispatched_members), max_concurrency)),
            "scale_mode": scale_mode,
            "capacity_tier": "kimi_scale"
            if len(group_members) >= 300
            else "large"
            if len(dispatched_members) >= 64
            else "team_scale"
            if len(dispatched_members) >= 16
            else "room_scale"
            if len(dispatched_members) >= 2
            else "single",
        }
        specs = [
            {
                "agent_id": member["name"],
                "display_name": member["display_name"],
                "role": "cowork",
                "task": text[:500],
            }
            for member in dispatched_members
        ]
        team_trace_item = McpToolCallItem(
            server="team",
            tool="team_swarm",
            arguments={
                "schema": "echo.group_fanout_run.v1",
                "mode": "cowork_swarm",
                "message": text[:1000],
                "specs": specs,
                "capacity": planned_group_capacity,
            },
            status=ItemStatus.IN_PROGRESS,
        )
        await _notify_started(team_trace_item)
        for member in dispatched_members:
            agent_id = member["name"]
            display = member["display_name"]
            item = SubagentItem(
                subagent_id=agent_id,
                role="cowork",
                name=display,
                codename=display,
                parent_item_id=team_trace_item.id,
                summary="waiting for cowork fanout reply",
                status=ItemStatus.IN_PROGRESS,
            )
            member_trace_items[agent_id] = item
            await _notify_started(item)

    async def _complete_group_trace(result: dict[str, Any]) -> None:
        replies = [reply for reply in result.get("replies", []) if isinstance(reply, dict)]
        by_agent = {str(reply.get("agent_id") or ""): reply for reply in replies}
        for agent_id, item in member_trace_items.items():
            reply = by_agent.get(agent_id, {})
            body = str(reply.get("reply") or "").strip()
            err = str(reply.get("error") or "").strip()
            ok = bool(reply.get("ok")) and bool(body)
            item.status = ItemStatus.COMPLETED if ok else ItemStatus.FAILED
            item.summary = body[:2000] if body else None
            item.error = None if ok else (err or "empty cowork fanout reply")
            item.iteration_count = 1
            await _notify_completed(item)
        if team_trace_item is not None:
            ok = bool(result.get("ok"))
            team_trace_item.status = ItemStatus.COMPLETED if ok else ItemStatus.FAILED
            team_trace_item.result = {
                "schema": "echo.group_fanout_result.v1",
                "count": result.get("count"),
                "spoke": result.get("spoke"),
                "dropped": result.get("dropped", 0),
                "capacity": result.get("capacity") or planned_group_capacity,
                "arbitration": result.get("arbitration"),
                "synthesis": result.get("synthesis"),
                "replies": replies,
            }
            team_trace_item.error = None if ok else str(result.get("error") or "no member replied")
            team_trace_item.duration_ms = max(
                0,
                int((time.monotonic() - team_trace_started) * 1000),
            )
            await _notify_completed(team_trace_item)

    async def _fail_group_trace(exc: BaseException) -> None:
        for item in member_trace_items.values():
            if item.status == ItemStatus.IN_PROGRESS:
                item.status = ItemStatus.FAILED
                item.error = f"{type(exc).__name__}: {exc}"
                await _notify_completed(item)
        if team_trace_item is not None and team_trace_item.status == ItemStatus.IN_PROGRESS:
            team_trace_item.status = ItemStatus.FAILED
            team_trace_item.error = f"{type(exc).__name__}: {exc}"
            team_trace_item.duration_ms = max(
                0,
                int((time.monotonic() - team_trace_started) * 1000),
            )
            await _notify_completed(team_trace_item)

    def _group_summary(result: dict[str, Any]) -> str | None:
        arbitration = result.get("arbitration")
        if not isinstance(arbitration, dict):
            return None
        synthesis = result.get("synthesis")
        answered = arbitration.get("answered_agent_ids")
        failed = arbitration.get("failed_agent_ids")
        empty = arbitration.get("empty_agent_ids")
        if isinstance(synthesis, dict):
            primary = str(synthesis.get("primary_agent_id") or "").strip()
            recommended = str(
                synthesis.get("recommended_next_action") or "",
            ).strip()
        else:
            primary = str(arbitration.get("primary_agent_id") or "").strip()
            recommended = str(arbitration.get("recommended_next_action") or "").strip()
        if not isinstance(answered, list):
            answered = []
        if not isinstance(failed, list):
            failed = []
        if not isinstance(empty, list):
            empty = []
        if len(answered) < 2 and not failed and not empty:
            return None
        # Multi-round debate double-counts the same member across rounds —
        # the summary should report distinct members, not bubble count.
        distinct_answered = list(dict.fromkeys(answered))
        parts = [
            f"协作汇总: {len(distinct_answered)} 位成员已回应",
        ]
        debate = result.get("debate")
        debate_rounds = debate.get("rounds") if isinstance(debate, dict) else None
        rounds = int(debate_rounds or arbitration.get("rounds") or 1)
        if rounds > 1:
            parts.append(f"共 {rounds} 轮成员互见辩论")
        if primary:
            parts.append(f"优先采纳 {primary} 的视角继续")
        if recommended and recommended != "use_primary_response":
            parts.append(f"下一步建议: {_group_next_action_label(recommended)}")
        blocked = [str(x) for x in [*failed, *empty] if x]
        if blocked:
            parts.append(f"{len(blocked)} 位成员需要补看")
        return "；".join(parts) + "。"

    def _group_next_action_label(action: str) -> str:
        labels = {
            "use_primary_response": "采纳主视角继续",
            "use_primary_and_retry_failed_members": "采纳主视角，同时补看失败成员",
            "ask_members_to_expand": "请成员补充展开",
            "retry_or_fallback_to_single_agent": "重试成员或回退单 Agent",
            "fallback_to_single_agent": "回退单 Agent",
        }
        return labels.get(action, action.replace("_", " "))

    if len(members) < 2:
        # Not a real group → one agent answers (the normal single-agent path).
        _record_fallback_audit("insufficient_members")
        await _fallback_to_react()
        return

    try:
        from runtime.execution.agents.group_fanout import run_group_fanout
        from runtime.execution.suckers.delegation_skills import _call_agent

        def _mentioned(display: str) -> bool:
            parts = display.split()
            cands = {display, parts[0] if parts else display, display.replace(" ", "")}
            return any(c and ("@" + c) in text for c in cands)

        # 辩论意图检测：消息含辩论 cue（辩论/反驳/挑战/谁不同意/互怼/打擂台等）
        # 或上下文显式传 swarm_debate_rounds/debate_rounds（>=2 强制多轮）。
        # 用户 @ 了谁 → 这些成员在第二轮被点名优先回应（成员互见 + @反驳）。
        debate_cues = (
            "辩论",
            "反驳",
            "挑战",
            "谁不同意",
            "谁反对",
            "互怼",
            "打擂台",
            "互驳",
            "观点交锋",
            "battle",
            "debate",
            "rebut",
        )

        def _wants_debate() -> int:
            # Explicit context flag wins.
            for key in ("swarm_debate_rounds", "debate_rounds"):
                raw = ctx.get(key)
                if raw is not None:
                    try:
                        val = int(raw)
                    except (TypeError, ValueError):
                        val = 0
                    if val >= 2:
                        return min(val, 3)
            low = text.lower()
            if any(cue.lower() in low for cue in debate_cues):
                return 2
            return 0

        def _mentioned_names() -> list[str]:
            """Display names the boss @-mentioned in the message (dedup)."""
            found: list[str] = []
            for m in members:
                display = str(m.get("display_name") or m.get("name") or "")
                parts = display.split()
                cands = {
                    display,
                    parts[0] if parts else display,
                    display.replace(" ", ""),
                }
                if any(c and ("@" + c) in text for c in cands):
                    found.append(display)
            return found

        chat_members = list(members)
        # @-mentioned chat members first so a small fan-out cap never drops them.
        chat_members.sort(key=lambda m: 0 if _mentioned(m["display_name"]) else 1)

        def _member_caller(agent_id: str, prompt: str, timeout_s: int = 90) -> dict[str, Any]:
            """Run every group member through the in-process agent boundary."""
            effective_prompt = (
                member_policy_prompt + "\n\n" + prompt if member_policy_prompt else prompt
            )
            return _call_agent(
                agent_id=agent_id,
                prompt=effective_prompt,
                timeout_s=timeout_s,
                context=member_context,
                session=parent_session,
            )

        spoke = 0
        if chat_members:
            scale_mode = (
                str(ctx.get("swarm_scale_mode") or ctx.get("fanout_scale_mode") or "safe")
                .strip()
                .lower()
            )
            if scale_mode not in {"safe", "full"}:
                scale_mode = "safe"
            requested_limit = ctx.get("swarm_max_members") or ctx.get("max_members")
            try:
                requested_limit_int = int(requested_limit) if requested_limit is not None else 0
            except (TypeError, ValueError):
                requested_limit_int = 0
            fanout_limit = (
                min(512, max(2, requested_limit_int or len(chat_members)))
                if scale_mode == "full"
                else min(32, max(2, requested_limit_int or len(chat_members)))
            )
            try:
                fanout_concurrency = int(ctx.get("swarm_max_concurrency") or 32)
            except (TypeError, ValueError):
                fanout_concurrency = 32
            fanout_concurrency = max(1, min(64, fanout_concurrency))
            await _start_group_trace(
                chat_members,
                max_members=fanout_limit,
                max_concurrency=fanout_concurrency,
                scale_mode=scale_mode,
            )
            debate_rounds = _wants_debate()
            mentioned = _mentioned_names()
            result = await asyncio.to_thread(
                run_group_fanout,
                text,
                chat_members,
                agent_caller=_member_caller,
                # Cover the whole roster (a small hard cap would silently drop
                # members ordered last).
                max_members=fanout_limit,
                max_concurrency=fanout_concurrency,
                scale_mode=scale_mode,
                turn_id=turn.id,
                debate_rounds=debate_rounds,
                mentioned=mentioned,
            )
            await _complete_group_trace(result)
            arbitration = result.get("arbitration")
            if isinstance(arbitration, dict):
                with contextlib.suppress(Exception):
                    audit_item = ReasoningItem(
                        content=json.dumps(
                            {
                                "schema": "echo.group_fanout_audit.v1",
                                "arbitration": arbitration,
                                "capacity": result.get("capacity") or planned_group_capacity,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        status=ItemStatus.COMPLETED,
                    )
                    turn.items.append(audit_item)
                    log.item_started(turn.thread_id, turn.id, audit_item)
                    log.item_completed(turn.thread_id, turn.id, audit_item)
            last_round_emitted = 0
            for reply in result.get("replies", []):
                body = str(reply.get("reply") or "").strip()
                round_no = int(reply.get("round") or 1)
                if round_no > 1 and round_no != last_round_emitted:
                    last_round_emitted = round_no
                    await _emit(
                        "⚔️ 第 "
                        + str(round_no)
                        + " 轮 · 成员互见辩论 —— 大家看到彼此观点后点名回应：",
                        display_name="主持人",
                        agent_id="swarm-moderator",
                        icon="⚔️",
                    )
                if reply.get("ok") and body:
                    # ③ @因果链：把回复里 @ 到的成员解析出来，作为气泡的
                    # reply_to 附加信息，前端在气泡标题旁显示"回应 @谁"。
                    reply_to = _extract_mention_target(body, chat_members)
                    await _emit(
                        body,
                        display_name=str(reply.get("display_name") or ""),
                        agent_id=str(reply.get("agent_id") or ""),
                        reply_to=reply_to,
                    )
                    spoke += 1
                elif not reply.get("ok"):
                    # ② 蜂群失败可视化：workbuddy 在 inbox 里明确显示
                    # "X failed · 原因"，我们之前是静默跳过——现在打一行。
                    err = str(reply.get("error") or "no reply")
                    await _emit(
                        "⚠️ "
                        + str(reply.get("display_name") or reply.get("agent_id") or "成员")
                        + " 未能回应 · "
                        + _friendly_member_error(err),
                        display_name=str(reply.get("display_name") or ""),
                        agent_id=str(reply.get("agent_id") or ""),
                    )
            summary = _group_summary(result)
            if summary:
                await _emit(summary)

        if spoke == 0:
            _record_fallback_audit("no_member_response")
            await _fallback_to_react()
    except Exception as exc:  # noqa: BLE001 — never break the turn on a fan-out fault
        _logger.warning(
            "group fan-out failed (%s: %s) — falling back to react",
            type(exc).__name__,
            exc,
        )
        await _fail_group_trace(exc)
        _record_fallback_audit("exception", exc)
        await _fallback_to_react()
