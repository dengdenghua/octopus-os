"""Translate an official OpenCode session into Echo's ordinary realtime items."""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace
from typing import Any

import httpx

from runtime.execution.opencode_backend import (
    OpenCodeError,
    resolve_zen_model,
    zen_catalog,
)
from runtime.execution.opencode_roles import stream_role
from runtime.execution.request import current_execution_request
from runtime.platform.models.custom_model_selection import custom_model_selection_id
from runtime.platform.process.session import current_session
from runtime.protocol import ServerMethod, TurnStatus
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway.realtime_engine_history import engine_history_for_turn


def scope_for_turn(turn: Any) -> TenantScope | None:
    actor, tenant = turn.params.owner_actor_id, turn.params.tenant_id
    if bool(actor) != bool(tenant):
        raise OpenCodeError("当前会话身份不完整，请重新登录。")
    return TenantScope(tenant_id=tenant, actor_id=actor) if actor and tenant else None


async def drive_opencode(
    runtime: Any,
    turn: Any,
    log: Any,
    emitter: Any,
    intent: Any,
    agent: Any,
    provider: Any = None,
    *,
    text: str,
) -> None:
    scope_for_turn(turn)
    request = current_execution_request()
    if request is None:
        raise OpenCodeError("OpenCode 缺少宿主执行上下文。")
    session = current_session()
    if session is None:
        raise OpenCodeError("OpenCode 缺少当前角色的宿主会话。")
    if (
        request.task.actor_id != turn.params.owner_actor_id
        or request.task.tenant_id != turn.params.tenant_id
        or request.task.thread_id != turn.thread_id
        or request.task.task_id != turn.id
    ):
        raise OpenCodeError("OpenCode 回合与宿主任务不一致。")
    if provider is not None and provider is not request.task.approval_provider:
        request = replace(request, task=replace(request.task, approval_provider=provider))
    model = resolve_zen_model(turn.params.model, zen_catalog())
    turn.params = turn.params.model_copy(
        update={"model": custom_model_selection_id("opencode-zen", model)}
    )
    if turn.execution is not None:
        log.turn_updated(
            turn.thread_id,
            turn.id,
            execution_model={
                "engine": "opencode",
                "invocation": turn.execution.invocation,
                "model": turn.params.model,
            },
            durable=True,
        )
    turn.execution_engine = "opencode"
    turn.execution_agent_id = getattr(agent, "agent_id", None)
    history = engine_history_for_turn(log, turn, "opencode")
    bridge = runtime._make_bridge_state(turn.thread_id, turn.id, agent=agent)
    started = time.monotonic()

    async def heartbeat() -> None:
        while True:
            await emitter.notify(
                ServerMethod.TURN_HEARTBEAT,
                {
                    "threadId": turn.thread_id,
                    "turnId": turn.id,
                    "role": "opencode-server",
                    "elapsedMs": int((time.monotonic() - started) * 1000),
                },
            )
            await asyncio.sleep(5)

    def interrupted() -> bool:
        return bool(emitter.is_turn_interrupted(turn.id))

    pulse = asyncio.create_task(heartbeat())
    try:
        async with contextlib.aclosing(
            stream_role(
                runtime._stack,
                agent,
                request=request,
                session=session,
                context=intent.user_context or {},
                model=model,
                text=history.prompt(text, resumed=True),
                fresh_text=history.prompt(text, resumed=False),
                interrupted=interrupted,
            )
        ) as events:
            async for event in events:
                if event.get("type") == "react_completed":
                    history.mark_delivered()
                await runtime._apply_react_event(turn, log, emitter, bridge, event)
    except asyncio.CancelledError:
        turn.status = TurnStatus.CANCELLED
        turn.outcome_reason = "user_cancelled" if interrupted() else "execution_cancelled"
        await runtime._apply_react_event(
            turn,
            log,
            emitter,
            bridge,
            {
                "type": "react_cancelled",
                "reason": "用户停止了任务" if interrupted() else "任务已停止",
            },
        )
        raise
    except (OpenCodeError, httpx.HTTPError) as exc:
        message = (
            str(exc) if isinstance(exc, OpenCodeError) else "OpenCode 本地引擎连接中断，请重试。"
        )
        await runtime._apply_react_event(
            turn,
            log,
            emitter,
            bridge,
            {
                "type": "react_error",
                "kind": "opencode_engine_error",
                "message": message,
            },
        )
    finally:
        pulse.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pulse
        await bridge.flush(turn, log, emitter, status=bridge.prose_status_for_turn(turn.status))


