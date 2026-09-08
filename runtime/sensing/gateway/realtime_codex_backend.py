"""Realtime driver for the isolated Codex App Server execution backend.

Echo remains the public control plane: it owns the authenticated outer
thread, durable journal, approvals, interruption, UI items, and final status.
Codex owns the inner execution loop for roles configured with the
``codex_app_server`` backend. The adapter never exposes Codex protocol objects
to the frontend and never lets a failed security check fall through to a
weaker executor.
"""

from __future__ import annotations

import contextlib
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from runtime.execution.agents.shared_blackboard import (
    blackboard_brief,
    harvest_to_blackboard,
)
from runtime.execution.codex_backend.backend import (
    CodexExecutionRequest,
    CodexExecutionSession,
)
from runtime.execution.codex_backend.command import codex_app_server_argv
from runtime.execution.codex_backend.events import (
    CodexEventState,
    translate_notification,
)
from runtime.execution.codex_backend.role_runner import (
    _explicit_feature_flag as _explicit_feature_flag,
)
from runtime.execution.codex_backend.role_runner import (
    agent_uses_codex_execution_backend,
    build_codex_role_request,
    codex_execution_lifecycle,
    codex_request_policy,
    configured_codex_executable,
    require_codex_backend_enabled,
)
from runtime.execution.codex_backend.role_runner import (
    deployment_mode as _deployment_mode,
)
from runtime.execution.codex_backend.role_runner import (
    source_codex_home as _source_codex_home,
)
from runtime.execution.codex_backend.role_runner import (
    state_root_for_workspace as _shared_state_root_for_workspace,
)
from runtime.execution.codex_backend.security import (
    CodexSecurityError,
)
from runtime.execution.codex_backend.timeouts import execution_timeout_s
from runtime.execution.codex_backend.types import RemoteError, RequestTimeoutError
from runtime.execution.host_boundary import bind_session_execution_request
from runtime.platform.process.session import Session, current_session, session_scope
from runtime.platform.process.task_execution import TaskExecutionGuard
from runtime.protocol import AgentMessageItem, ServerMethod, TurnStatus
from runtime.safety.sandboxing.sandbox import (
    effective_process_sandbox_mode,
    resolved_process_backend,
)

_logger = logging.getLogger(__name__)

_NOTIFICATION_POLL_S = 0.5
_HEARTBEAT_INTERVAL_S = 5.0
_INTERRUPT_GRACE_S = 5.0
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_NOT_SUBMITTED_STEER_MESSAGES = frozenset(
    {
        "active turn uses a different output schema",
        "cannot steer a compact turn",
        "cannot steer a review turn",
        "no active turn to steer",
    }
)


def agent_is_codex_app_server_partner(agent: Any) -> bool:
    """Return whether routing should enter the Codex App Server boundary.

    Local/single-user deployments enable explicitly configured roles by
    default and honor an explicit opt-out. Production-like deployments always
    enter this boundary even while disabled so the execution gate rejects the
    request instead of silently selecting another engine.
    """

    return agent_uses_codex_execution_backend(agent)


def _require_enabled_for_deployment() -> None:
    require_codex_backend_enabled()


def _turn_timeout_s() -> float:
    return execution_timeout_s()


def _state_root_for_workspace(workspace: Path) -> Path:
    return _shared_state_root_for_workspace(workspace)


def _trusted_realtime_parent(
    turn: Any,
    agent: Any,
    context: dict[str, Any],
    workspace: Path,
) -> Session | None:
    trusted_parent = current_session()
    if trusted_parent is not None:
        return trusted_parent
    # These TurnParams fields are excluded from client serialization and
    # stamped by RealtimeGateway after its thread/tenant authorization checks.
    # Never synthesize a principal Session from user_context identity fields.
    params = getattr(turn, "params", None)
    actor = str(getattr(params, "owner_actor_id", None) or "").strip()
    tenant = str(getattr(params, "tenant_id", None) or "").strip()
    if bool(actor) != bool(tenant):
        raise CodexSecurityError("authenticated realtime principal is incomplete")
    if not actor:
        return None
    trusted_metadata = dict(context)
    trusted_metadata["tenant_id"] = tenant
    trusted_metadata["workspace_path"] = str(workspace)
    return Session(
        actor=actor,
        agent=agent,
        thread_id=str(getattr(turn, "thread_id", "") or ""),
        conversation_id=str(getattr(turn, "thread_id", "") or ""),
        turn_id=str(getattr(turn, "id", "") or ""),
        metadata=trusted_metadata,
    )


def _resolved_codex_workspace(context: dict[str, Any]) -> Path:
    """Resolve the server-selected Codex workspace once at the boundary."""

    raw_cwd = context.get("cwd")
    if not isinstance(raw_cwd, str) or not raw_cwd.strip():
        raise CodexSecurityError("Codex execution requires a server-resolved workspace")
    workspace = Path(raw_cwd.strip()).expanduser()
    if not workspace.is_absolute():
        raise CodexSecurityError("server-resolved Codex workspace must be absolute")
    try:
        workspace = workspace.resolve(strict=True)
    except OSError as exc:
        raise CodexSecurityError("server-resolved Codex workspace does not exist") from exc
    if not workspace.is_dir():
        raise CodexSecurityError("server-resolved Codex workspace is not a directory")
    return workspace


def _host_codex_session(
    runtime: Any,
    turn: Any,
    intent: Any,
    agent: Any,
    *,
    text: str,
    workspace: Path,
) -> tuple[Session | None, TaskExecutionGuard | None]:
    """Admit Codex to the same host lease and request used by native turns.

    The Codex sidecar already has its own workspace security.  This helper
    adds the Echo-side identity/lease ceiling before the dynamic-tool broker
    is constructed, so a stale Codex continuation cannot keep invoking Echo
    tools after its parent task lost ownership.  Legacy embedders without a
    ``TaskSupervisor`` retain the old no-guard path.
    """

    supervisor = getattr(runtime, "_task_supervisor", None)
    if supervisor is None:
        return None, None

    guards = getattr(runtime, "_task_execution_guards", None)
    if not isinstance(guards, dict):
        guards = {}
        runtime._task_execution_guards = guards

    parent = current_session()
    if parent is None:
        parent = _trusted_realtime_parent(
            turn,
            agent,
            dict(getattr(intent, "user_context", None) or {}),
            workspace,
        )

    existing_request = getattr(parent, "execution_request", None) if parent is not None else None
    existing_lease = getattr(parent, "execution_lease", None) if parent is not None else None
    if existing_request is not None:
        # Nested Codex execution already belongs to an outer host task. Never
        # replace that immutable request with a new task identity.
        return parent, existing_lease if isinstance(existing_lease, TaskExecutionGuard) else None

    previous = guards.get(turn.id)
    guard = previous.fork() if previous is not None else TaskExecutionGuard(supervisor)
    # Install the pending guard before admission. If another holder rejects
    # the start, finalization sees a pending guard and cannot transition that
    # holder's task record on our behalf.
    guards[turn.id] = guard

    context = dict(getattr(intent, "user_context", None) or {})
    params = getattr(turn, "params", None)
    owner_id = str(getattr(parent, "actor", None) or "").strip() or None
    if owner_id is None:
        owner_id = str(getattr(params, "owner_actor_id", None) or "").strip() or None
    tenant_id = None
    if parent is not None and isinstance(parent.metadata, dict):
        tenant_id = str(parent.metadata.get("tenant_id") or "").strip() or None
    if tenant_id is None:
        tenant_id = str(getattr(params, "tenant_id", None) or "").strip() or None

    task_id = str(
        getattr(turn, "task_id", None)
        or getattr(turn, "objective_id", None)
        or getattr(turn, "id", "")
    ).strip()
    guard.admit(
        task_id,
        kind="realtime_codex",
        owner_id=owner_id,
        thread_id=str(getattr(turn, "thread_id", "") or "").strip(),
        title=str(text or "").strip()[:80],
        goal=str(text or "").strip(),
        mode="codex",
        workspace_path=str(workspace),
        origin_task_id=str(getattr(turn, "id", "") or "").strip(),
        metadata={
            "turn_id": str(getattr(turn, "id", "") or ""),
            "objective_id": task_id,
            "execution_engine": "codex",
            "model_name": str(getattr(params, "model", None) or "").strip() or None,
        },
    )

    if parent is None:
        parent = Session(
            actor=owner_id,
            agent=agent,
            thread_id=str(getattr(turn, "thread_id", "") or "").strip(),
            conversation_id=str(getattr(turn, "thread_id", "") or "").strip(),
            turn_id=str(getattr(turn, "id", "") or "").strip(),
            metadata={},
            execution_lease=guard,
        )
    else:
        # The trusted parent from TurnParams may contain transport context.
        # Build a narrow host projection so client privilege fields cannot
        # expand the request's scope while the provider still gets the
        # server-selected workspace and principal.
        parent = Session(
            actor=parent.actor,
            agent=parent.agent or agent,
            thread_id=parent.thread_id or str(getattr(turn, "thread_id", "") or "").strip(),
            conversation_id=parent.conversation_id
            or str(getattr(turn, "thread_id", "") or "").strip(),
            turn_id=parent.turn_id or str(getattr(turn, "id", "") or "").strip(),
            started_at=parent.started_at,
            metadata={},
            execution_lease=guard,
        )
    parent.metadata.update(
        {
            "tenant_id": tenant_id,
            "owner_actor_id": owner_id,
            "workspace_path": str(workspace),
            "extra_workspaces": [str(workspace)],
            "mode": "code",
            "permission_mode": "default",
            "approval_policy": "on-request",
            "sandbox_mode": "full",
            "execution_environment": "sandbox",
        }
    )
    budget = getattr(getattr(getattr(runtime, "_stack", None), "config", None), "budget", None)
    try:
        bind_session_execution_request(
            parent,
            task_id=task_id,
            goal=str(text or "").strip(),
            timeout_s=_turn_timeout_s(),
            parent_task_id=(
                str(context.get("parent_task_id") or "").strip() or None
            ),
            budget=budget,
            execution_engine="codex",
        )
    except BaseException:
        # Admission succeeded, but the host request could not be built. Stop
        # this provider scope immediately; outer turn finalization may still
        # settle the exact durable lease with ``require_open=False``.
        guard.close()
        raise
    with contextlib.suppress(AttributeError, TypeError, ValueError):
        turn.task_id = task_id
    return parent, guard


def _request_for_turn(
    runtime: Any,
    turn: Any,
    intent: Any,
    agent: Any,
    *,
    text: str,
    approval_provider: Any = None,
    is_interrupted: Any = None,
) -> CodexExecutionRequest:
    context = getattr(intent, "user_context", None)
    context = dict(context) if isinstance(context, dict) else {}
    # The realtime gateway sanitizes approvalPolicy and auto_approve. Require
    # the server-owned operator switch again here before relaxing Codex's own
    # approval policy; client metadata alone can never enable full access.
    server_auto_approve = bool(
        getattr(runtime, "_allow_client_auto_approve", False)
        and context.get("approval_policy") == "never"
        and context.get("auto_approve") is True
    )
    workspace = _resolved_codex_workspace(context)
    # Realtime turn validation designates ``cwd`` as the execution
    # coordinate. A browser-supplied workspace_path must not override it.
    context["workspace_path"] = str(workspace)
    trusted_parent = _trusted_realtime_parent(turn, agent, context, workspace)
    if trusted_parent is not None:
        context["caller_session"] = trusted_parent
    prompt = text
    brief = blackboard_brief(str(getattr(turn, "id", "") or ""))
    if brief:
        prompt = f"{brief}\n\n---\n\n{text}"

    stack = getattr(runtime, "_stack", None)
    if stack is None:
        # Keep the small adapter independently testable and compatible with
        # older embeddings that have not wired an Echo execution stack.
        # Such embeddings receive no Echo dynamic tools; production app
        # construction always supplies ``_stack``.
        capabilities = getattr(agent, "capabilities", None)
        if (
            not isinstance(capabilities, dict)
            or str(capabilities.get("execution_backend") or "").casefold() != "codex_app_server"
        ):
            raise CodexSecurityError(
                "Codex App Server driver requires an embedded standard role"
            )
        from runtime.safety.approval.permission_modes import approval_reviewer_for_mode

        policy = codex_request_policy(
            context,
            server_auto_approve=server_auto_approve,
            trusted_parent_metadata=(
                trusted_parent.metadata
                if trusted_parent is not None and isinstance(trusted_parent.metadata, dict)
                else None
            ),
        )

        return CodexExecutionRequest(
            outer_thread_id=str(getattr(turn, "thread_id", "") or ""),
            outer_turn_id=str(getattr(turn, "id", "") or ""),
            workspace=workspace,
            **policy,
            tenant_id=str(context.get("tenant_id") or "local"),
            principal_id=str(context.get("owner_actor_id") or "local"),
            prompt=prompt,
            command=codex_app_server_argv(configured_codex_executable(agent)),
            source_codex_home=_source_codex_home(),
            effort=str(context.get("reasoning_effort") or "").strip() or None,
            approval_reviewer=approval_reviewer_for_mode(context.get("permission_mode")),
        )

    request, _broker, _provider = build_codex_role_request(
        stack,
        agent,
        prompt,
        context=context,
        outer_thread_id=str(getattr(turn, "thread_id", "") or ""),
        outer_turn_id=str(getattr(turn, "id", "") or ""),
        approval_provider=approval_provider,
        is_interrupted=is_interrupted,
        server_auto_approve=server_auto_approve,
    )
    return request


async def _heartbeat(emitter: Any, turn: Any, *, started_at: float) -> None:
    await emitter.notify(
        ServerMethod.TURN_HEARTBEAT,
        {
            "threadId": turn.thread_id,
            "turnId": turn.id,
            "role": "codex-app-server",
            "elapsedMs": max(0, int((time.monotonic() - started_at) * 1000)),
        },
    )


def _final_agent_text(turn: Any) -> str:
    for item in reversed(getattr(turn, "items", ())):
        if (
            isinstance(item, AgentMessageItem)
            and getattr(item, "message_kind", "answer") == "answer"
        ):
            text = str(item.text or "").strip()
            if text:
                return text
    return ""


def _steer_was_not_submitted(error: RemoteError) -> bool:
    """Recognize responses that prove an optimistic steer had no effect."""

    if error.code == _METHOD_NOT_FOUND:
        return True
    if error.code != _INVALID_REQUEST:
        return False
    return error.message in _NOT_SUBMITTED_STEER_MESSAGES or error.message.startswith(
        "expected active turn id `"
    )


async def drive_codex_app_server(
    runtime: Any,
    turn: Any,
    log: Any,
    emitter: Any,
    intent: Any,
    agent: Any,
    provider: Any,
    *,
    text: str,
) -> bool:
    """Run Codex inside Echo's host-owned request/lease context.

    The App Server remains an isolated provider, but its Echo dynamic tools
    must observe the same task identity and permission ceiling as Native
    ReAct.  The wrapper is a no-op for legacy runtimes that do not expose a
    ``TaskSupervisor``.
    """

    context = getattr(intent, "user_context", None)
    context = dict(context) if isinstance(context, dict) else {}
    workspace = _resolved_codex_workspace(context)
    host_session, execution_guard = _host_codex_session(
        runtime,
        turn,
        intent,
        agent,
        text=text,
        workspace=workspace,
    )
    if host_session is None:
        return await _drive_codex_app_server_inner(
            runtime,
            turn,
            log,
            emitter,
            intent,
            agent,
            provider,
            text=text,
        )

    try:
        with session_scope(host_session):
            return await _drive_codex_app_server_inner(
                runtime,
                turn,
                log,
                emitter,
                intent,
                agent,
                provider,
                text=text,
            )
    finally:
        # The outer lifecycle owns final transition. Closing this provider
        # scope only blocks late dynamic-tool calls; a subsequent steering or
        # repair invocation can fork the still-current durable lease.
        guards = getattr(runtime, "_task_execution_guards", None)
        if (
            execution_guard is not None
            and isinstance(guards, dict)
            and guards.get(getattr(turn, "id", "")) is execution_guard
        ):
            execution_guard.close()


async def _drive_codex_app_server_inner(
    runtime: Any,
    turn: Any,
    log: Any,
    emitter: Any,
    intent: Any,
    agent: Any,
    provider: Any,
    *,
    text: str,
) -> bool:
    """Run one outer turn through Codex and stream it into native UI items.

    The embedded standard role has no persona/policy-equivalent fallback. Every
    security failure and every App Server failure is terminal.
    """

    _require_enabled_for_deployment()

    def interrupted() -> bool:
        return bool(emitter.is_turn_interrupted(turn.id))

    request = _request_for_turn(
        runtime,
        turn,
        intent,
        agent,
        text=text,
        approval_provider=provider,
        is_interrupted=interrupted,
    )
    from runtime.sensing.gateway.realtime_execution_evidence import record_execution

    record_execution(log, turn, engine="codex", driver="codex_app_server", model=request.model)
    from runtime.sensing.gateway.realtime_engine_history import engine_history_for_turn

    # Resolve the current role/tool authority before adding quoted history.
    history = engine_history_for_turn(log, turn, "codex")
    request = replace(
        request,
        prompt=history.prompt(request.prompt, resumed=True),
        fresh_thread_prompt=history.prompt(request.prompt, resumed=False),
    )
    # Persist the engine-owned effective model, not an outer smart-routing
    # candidate. This is the authoritative coordinate used by history,
    # outcome/evolution records and any UI that inspects the completed turn.
    if request.model and getattr(turn, "params", None) is not None:
        copy_with_update = getattr(turn.params, "model_copy", None)
        if callable(copy_with_update):
            turn.params = copy_with_update(update={"model": request.model})
        else:
            # Lightweight test/dynamic adapters may expose a mutable params
            # object rather than the production Pydantic model.
            turn.params.model = request.model
    turn.execution_engine = "codex"
    # The resolver may replace an outer routing candidate with the provider's
    # effective model. Refresh the same durable task record while it runs.
    execution_guard = getattr(runtime, "_task_execution_guards", {}).get(turn.id)
    if isinstance(execution_guard, TaskExecutionGuard) and request.model:
        with contextlib.suppress(Exception):
            execution_guard.transition(
                "running",
                metadata_patch={
                    "execution_engine": "codex",
                    "model_name": request.model,
                },
            )
    raw_context = getattr(intent, "user_context", None)
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    trusted_parent = _trusted_realtime_parent(turn, agent, context, request.workspace)
    execution_lease = getattr(trusted_parent, "execution_lease", None)
    if isinstance(execution_lease, TaskExecutionGuard):
        execution_lease.assert_allowed()
    async with codex_execution_lifecycle(
        getattr(runtime, "_stack", None),
        request,
        trusted_session=trusted_parent,
        approval_provider=provider,
        is_interrupted=interrupted,
        timeout_s=_turn_timeout_s(),
        state_root=_state_root_for_workspace(request.workspace),
        deployment_mode_value=_deployment_mode(),
        process_backend=resolved_process_backend(effective_process_sandbox_mode()),
        session_factory=CodexExecutionSession,
    ) as prepared:
        session = prepared.session
        # Startup failures propagate through the shared cleanup boundary;
        # never retry an ambiguous start through a different executor.
        await session.start()
        history.mark_delivered()

        bridge_state = runtime._make_bridge_state(turn.thread_id, turn.id, agent=agent)
        event_state = CodexEventState()
        started_at = time.monotonic()
        last_heartbeat = started_at
        interrupt_started: float | None = None
        interrupt_requested = False
        saw_terminal = False
        live_steering_supported = True
        timeout_s = _turn_timeout_s()

        while not saw_terminal:
            now = time.monotonic()
            if now - started_at >= timeout_s and not interrupt_requested:
                interrupt_requested = True
                interrupt_started = now
                turn.status = TurnStatus.CANCELLED
                turn.outcome_reason = "codex_timeout"
                turn.interrupt_reason = "Codex 代码任务超过运行时限"
                with contextlib.suppress(Exception):
                    await session.interrupt(timeout_s=2.0)
            elif emitter.is_turn_interrupted(turn.id) and not interrupt_requested:
                interrupt_requested = True
                interrupt_started = now
                turn.status = TurnStatus.CANCELLED
                turn.outcome_reason = "user_cancelled"
                turn.interrupt_reason = emitter.get_interrupt_reason(turn.id) or "用户停止了任务"
                with contextlib.suppress(Exception):
                    await session.interrupt(timeout_s=2.0)

            if interrupt_started is not None and now - interrupt_started >= _INTERRUPT_GRACE_S:
                await runtime._apply_react_event(
                    turn,
                    log,
                    emitter,
                    bridge_state,
                    {
                        "type": "react_cancelled",
                        "reason": turn.outcome_reason or turn.interrupt_reason,
                    },
                )
                break

            if not interrupt_requested:
                await runtime._publish_discovered_steering(turn, emitter)
                if live_steering_supported:
                    corrections = runtime._drain_turn_steering(turn.id)
                    if corrections:
                        try:
                            await session.steer("\n\n".join(corrections), timeout_s=2.0)
                        except RemoteError as exc:
                            if not _steer_was_not_submitted(exc):
                                raise
                            # The peer proved it did not accept the steer. Put
                            # the payload back so the outer lifecycle continues
                            # it on the same durable Codex thread after terminal.
                            runtime._restore_turn_steering(turn.id, corrections)
                            live_steering_supported = False

            if now - last_heartbeat >= _HEARTBEAT_INTERVAL_S:
                last_heartbeat = now
                await _heartbeat(emitter, turn, started_at=started_at)

            try:
                notification = await session.next_notification(timeout_s=_NOTIFICATION_POLL_S)
            except RequestTimeoutError:
                continue

            for event in translate_notification(notification, event_state):
                terminal = event.get("type") in {
                    "react_completed", "react_cancelled", "react_error",
                }
                if terminal and interrupt_requested:
                    # A queued completion/error may arrive after we accepted
                    # a stop. It must not replace the authoritative stop reason
                    # or flush the task's output as successfully completed.
                    event = {
                        "type": "react_cancelled",
                        "reason": turn.outcome_reason or "user_cancelled",
                    }
                if isinstance(execution_lease, TaskExecutionGuard) and event.get("type") in {
                    "tool_start",
                    "react_completed",
                }:
                    if event.get("type") == "tool_start":
                        execution_lease.assert_allowed()
                    else:
                        execution_lease.assert_held()
                await runtime._apply_react_event(
                    turn,
                    log,
                    emitter,
                    bridge_state,
                    event,
                )
                if terminal:
                    saw_terminal = True
                    break

        if not saw_terminal and turn.status == TurnStatus.IN_PROGRESS:
            await runtime._apply_react_event(
                turn,
                log,
                emitter,
                bridge_state,
                {
                    "type": "react_error",
                    "kind": "codex_missing_terminal",
                    "message": "Codex App Server ended without a terminal turn event",
                },
            )
        with contextlib.suppress(Exception):
            await bridge_state.flush(
                turn,
                log,
                emitter,
                status=bridge_state.prose_status_for_turn(turn.status),
            )
        answer = _final_agent_text(turn)
        if answer:
            harvest_to_blackboard(
                str(getattr(turn, "id", "") or ""),
                str(getattr(agent, "agent_id", "") or "codex-cli"),
                answer,
            )
        return True


__all__ = ["agent_is_codex_app_server_partner", "drive_codex_app_server"]
