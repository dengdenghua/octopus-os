"""OpenCode role execution shared by realtime hosts and delegated workers.

The authenticated task and Session are Python host objects. Caller metadata
can narrow this run to a conversational reply, but cannot supply authority.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.execution import opencode_backend as backend
from runtime.execution.request import (
    ExecutionDeadlineExceeded,
    ExecutionRequest,
    current_execution_request,
    execution_request_scope,
)
from runtime.platform.process.session import Session, current_session, session_scope
from runtime.safety.auth.scope import TenantScope


@dataclass
class _StateLock:
    lock: Any = field(default_factory=threading.Lock)
    references: int = 0


_states: dict[str, _StateLock] = {}
_guard = threading.Lock()


@contextlib.asynccontextmanager
async def _task_deadline(request: ExecutionRequest) -> AsyncIterator[None]:
    timeout = asyncio.timeout(request.task.resources.remaining_seconds())
    try:
        async with timeout:
            yield
    except TimeoutError as exc:
        if timeout.expired():
            raise ExecutionDeadlineExceeded("task execution deadline exceeded") from exc
        raise


@contextlib.asynccontextmanager
async def _hold_state(key: str, interrupted: Callable[[], bool]) -> AsyncIterator[None]:
    # Workers have independent event loops. asyncio locks cannot serialize
    # those loops; never block one while waiting for another worker either.
    with _guard:
        entry = _states.setdefault(key, _StateLock())
        entry.references += 1
    acquired = False
    try:
        while not acquired:
            if interrupted():
                raise asyncio.CancelledError()
            acquired = entry.lock.acquire(blocking=False)
            if not acquired:
                await asyncio.sleep(0.05)
        yield
    finally:
        if acquired:
            entry.lock.release()
        with _guard:
            entry.references -= 1
            if entry.references == 0:
                del _states[key]


async def stream_role(
    stack: Any,
    agent: Any,
    *,
    request: ExecutionRequest,
    session: Session,
    context: dict[str, Any],
    model: str,
    text: str,
    fresh_text: str | None = None,
    tool_free: bool = False,
    tool_ceiling: frozenset[str] | None = None,
    interrupted: Callable[[], bool],
) -> AsyncIterator[dict[str, Any]]:
    from runtime.execution.tool_engine.role_instructions import compose_role_instructions

    task = request.task
    if (
        task.execution_engine != "opencode"
        or task.actor_id != session.actor
        or task.tenant_id != session.metadata.get("tenant_id")
        or task.thread_id != session.thread_id
        or task.task_id != session.turn_id
    ):
        raise backend.OpenCodeError("OpenCode 角色任务与宿主身份不一致。")
    workspace = Path(str(session.metadata.get("workspace_path") or ""))
    if not workspace.is_absolute() or not task.permissions.allows_read(workspace):
        raise backend.OpenCodeError("当前工作区不在任务授权范围内，请重新选择工作区。")
    task.resources.remaining_seconds()
    if interrupted():
        raise asyncio.CancelledError()
    # Host-resolved model ownership follows tools and nested text helpers.
    session = replace(
        session, metadata={**session.metadata, "model_name": model, "_execution_task": task}
    )
    scope = (
        TenantScope(tenant_id=task.tenant_id, actor_id=task.actor_id) if task.tenant_id else None
    )
    ready = backend.inspect_readiness(scope)
    if not ready["available"]:
        raise backend.OpenCodeError(ready["reason"])
    # Host metadata wins over all serializable caller inputs.
    effective_context = {k: v for k, v in context.items() if k != "metadata"}
    effective_context.update(session.metadata)
    effective_context["caller_session"] = session
    system = compose_role_instructions(
        agent,
        context=effective_context,
        goal=request.instruction,
        registry=stack.executor.registry,
    )
    system += (
        "\n你是 Echo 当前选定的角色，由官方 OpenCode 引擎执行。"
        "保持角色、记忆和明确选中的技能指令；不要使用 OpenCode 内置子代理或额外插件。"
        "需要团队委派时，仅使用宿主实际提供的委派工具，并以工具返回的成员结果为依据。"
    )
    broker = None
    host_tools = None
    if tool_free:
        system += "\n仅给出当前任务要求的回复，遵循指定的格式、语言和长度，不调用任何工具。"
    else:
        from runtime.execution.tool_engine.host_mcp import HostMCPBridge
        from runtime.execution.tool_engine.host_tool_broker import HostToolBroker
        from runtime.safety.approval.guardian_review import (
            AutoReviewApprovalProvider,
            approval_router_for_stack,
        )
        from runtime.safety.approval.permission_modes import approval_reviewer_for_mode

        auto_approve = bool(
            task.server_auto_approve
            and task.permissions.approval_policy == "never"
            and task.permissions.permission_mode == "bypassPermissions"
        )
        provider = task.approval_provider
        if (
            not auto_approve
            and approval_reviewer_for_mode(task.permissions.permission_mode) == "auto_review"
        ):
            router = approval_router_for_stack(stack)
            if router is None:
                raise backend.OpenCodeError("自动审批缺少审核模型，请选择其他权限模式。")
            provider = AutoReviewApprovalProvider(
                router,
                user_intent=task.authorization_intent or task.goal,
            )
        broker = HostToolBroker(
            stack,
            agent,
            context=effective_context,
            goal=request.instruction,
            outer_thread_id=task.thread_id,
            outer_turn_id=task.task_id,
            workspace=str(workspace),
            tenant_id=task.tenant_id or "local",
            principal_id=task.actor_id or "local",
            approval_provider=provider,
            is_interrupted=interrupted,
            server_auto_approve=auto_approve,
            max_tools=64,
            tool_ceiling=tool_ceiling,
        )
        host_tools = HostMCPBridge(broker, request, session)
        system += (
            "\necho_ 开头的工具来自当前角色的授权目录。文件、终端、浏览器和桌面操作"
            "只能通过实际公布的工具完成。宿主权限、拒绝和审批结论具有最终效力。"
            "调研时使用网页工具并附真实来源；无法验证的内容须标明。直接完成需求，不要只给计划。"
        )
        system += (
            "\n本轮可执行宿主已授权的写入工具，以执行器校验为准。这不是 OpenCode Plan Mode。"
            if task.permissions.writable_roots
            else "\n本轮只读，不得写入。"
        )
    system += f"\n当前工作区：{workspace}。历史权限说明不代表当前状态。"
    if output_root := session.metadata.get("_artifact_output_root"):
        system += f"\n本轮文件产物输出目录：{output_root}。"
    root = backend.state_directory(scope, task.thread_id)
    with execution_request_scope(request), session_scope(session):
        async with (
            _task_deadline(request),
            _hold_state(str(root), interrupted),
        ):
            async with contextlib.AsyncExitStack() as lifetime:
                if interrupted():
                    raise asyncio.CancelledError()
                connection = (
                    await lifetime.enter_async_context(host_tools.serve()) if host_tools else None
                )
                client = await lifetime.enter_async_context(
                    backend.managed_server(
                        backend.executable(),
                        root,
                        backend.zen_key(scope),
                        model,
                        not tool_free and task.permissions.network_policy == "allow",
                        host_mcp=connection,
                    )
                )
                if connection is not None:
                    status = await client.get("/mcp", timeout=30)
                    status.raise_for_status()
                    if status.json().get("echo", {}).get("status") != "connected":
                        raise backend.OpenCodeError("OpenCode 未能连接 Echo 工具，请重试。")
                session_id = await backend.session_for_thread(client, root)
                async with contextlib.aclosing(
                    backend.stream_prompt(
                        client,
                        session_id,
                        text=text,
                        fresh_thread_text=fresh_text,
                        system=system,
                        model=model,
                        interrupted=interrupted,
                        tool_names={
                            f"echo_{name}": broker.skill_name(name) or name
                            for name in broker.catalog.names
                        }
                        if broker
                        else {},
                    )
                ) as events:
                    async for event in events:
                        if event.get("type") == "react_cancelled":
                            raise asyncio.CancelledError()
                        yield event


def run_role_sync(
    stack: Any,
    agent: Any,
    goal: str,
    *,
    context: dict[str, Any],
    interrupted: Callable[[], bool],
    tool_ceiling: frozenset[str] | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Run a delegated role inside the existing authenticated task ceiling."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("OpenCode synchronous roles must be dispatched with asyncio.to_thread")
    parent = current_execution_request()
    session = current_session()
    if parent is None or session is None:
        raise backend.OpenCodeError("OpenCode 成员任务缺少宿主执行上下文。")
    if parent.task.actor_id != session.actor or parent.task.tenant_id != session.metadata.get(
        "tenant_id"
    ):
        raise backend.OpenCodeError("OpenCode 成员任务的宿主身份不一致。")
    # A bridge may already have allocated the child; direct team fanout has
    # not. Never reuse a parent's engine session as a member conversation.
    task = parent.task
    if task.parent_task_id is None or task.execution_engine is not None:
        child_id = f"role_{uuid4().hex}"
        task = replace(task, task_id=child_id, thread_id=child_id, parent_task_id=task.task_id)
    task = replace(task, execution_engine="opencode", goal=goal)
    child = replace(
        session,
        thread_id=task.thread_id,
        turn_id=task.task_id,
        agent=agent,
        metadata={**session.metadata, "_execution_task": task},
    )
    request = ExecutionRequest(task, goal)
    capabilities = getattr(agent, "capabilities", None)
    role_model = (
        capabilities.get("opencode_model") if isinstance(capabilities, dict) else None
    ) or getattr(agent, "model", None)
    if role_model == "octopus-agent":
        role_model = None
    model = backend.resolve_zen_model(
        context.get("model_name") or role_model, backend.zen_catalog()
    )

    async def run() -> str:
        output: list[str] = []
        completed = False
        async with contextlib.aclosing(
            stream_role(
                stack,
                agent,
                request=request,
                session=child,
                context=context,
                model=model,
                text=goal,
                tool_free=bool(context.get("direct_conversation_reply"))
                or tool_ceiling == frozenset(),
                tool_ceiling=tool_ceiling,
                interrupted=interrupted,
            )
        ) as events:
            async for event in events:
                if on_event is not None:
                    on_event(event)
                if event.get("type") == "text_delta":
                    if event.get("start_new_segment"):
                        output.append("\n\n")
                    output.append(str(event.get("delta") or ""))
                elif event.get("type") == "react_completed":
                    completed = event.get("success") is True
                elif event.get("type") in {"tool_start", "tool_end"}:
                    notify = context.get("emit_tool_event")
                    if callable(notify):
                        notify(
                            tool_name=event.get("tool_name", "tool"),
                            status="started"
                            if event["type"] == "tool_start"
                            else ("completed" if event.get("success") else "failed"),
                            input_preview=event.get("input_preview"),
                            output_preview=event.get("output_preview"),
                            payload={
                                "execution_engine": "opencode",
                                "tool_call_id": event.get("tool_call_id"),
                            },
                        )
        if not completed or not "".join(output).strip():
            raise backend.OpenCodeError("OpenCode 成员任务没有完成回答。")
        return "".join(output).strip()

    try:
        return asyncio.run(run())
    except asyncio.CancelledError as exc:
        # The worker interface uses ordinary host exceptions; leaking an
        # asyncio BaseException through Future.result would bypass its
        # structured failure/cleanup handlers and cancel a sibling collector.
        from runtime.safety.approval.cancellation import OperationCancelled

        raise OperationCancelled("OpenCode member execution cancelled") from exc


