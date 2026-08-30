"""Realtime driver for the isolated Codex App Server execution backend.

Echo remains the public control plane: it owns the authenticated outer
thread, durable journal, approvals, interruption, UI items, and final status.
Codex owns only the inner coding loop for a selected ``codex-cli`` local
partner.  The adapter never exposes Codex protocol objects to the frontend and
never lets a failed security check fall through to a weaker executor.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from pathlib import Path
from typing import Any

from runtime.execution.agents.shared_blackboard import (
    blackboard_brief,
    harvest_to_blackboard,
)
from runtime.execution.codex_backend.backend import (
    CodexBackendUnavailable,
    CodexExecutionRequest,
    CodexExecutionSession,
)
from runtime.execution.codex_backend.events import (
    CodexEventState,
    translate_notification,
)
from runtime.execution.codex_backend.role_runner import (
    agent_uses_codex_execution_backend,
    build_codex_role_request,
    codex_app_server_command,
    codex_execution_lifecycle,
    require_codex_backend_enabled,
    resolve_codex_sandbox_mode,
)
from runtime.execution.codex_backend.role_runner import (
    state_root_for_workspace as _shared_state_root_for_workspace,
)
from runtime.execution.codex_backend.security import (
    CodexSandboxMode,
    CodexSecurityError,
)
from runtime.execution.codex_backend.types import RemoteError, RequestTimeoutError
from runtime.platform.process.paths import app_paths
from runtime.platform.process.session import Session, current_session
from runtime.platform.runtime_policy.feature_flags import resolution
from runtime.protocol import AgentMessageItem, ServerMethod, TurnStatus
from runtime.safety.sandboxing.sandbox import (
    effective_process_sandbox_mode,
    resolved_process_backend,
)

_logger = logging.getLogger(__name__)

_PRODUCTION_MODES = frozenset({"commercial", "production", "server", "shared"})
_NOTIFICATION_POLL_S = 0.5
_HEARTBEAT_INTERVAL_S = 5.0
_INTERRUPT_GRACE_S = 5.0
_DEFAULT_TURN_TIMEOUT_S = 30.0 * 60.0
_MAX_TURN_TIMEOUT_S = 4.0 * 60.0 * 60.0
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


def _deployment_mode() -> str:
    return str(os.environ.get("ECHO_DEPLOYMENT_MODE") or "local").strip().lower()


def _explicit_feature_flag() -> bool | None:
    value, source = resolution("execution.codex_app_server")
    if source in (None, "default"):
        return None
    # Production enablement is a security decision, so JSON numbers, objects,
    # and other merely truthy file values are not accepted as an explicit yes.
    return value if type(value) is bool else False


def agent_is_codex_app_server_partner(agent: Any) -> bool:
    """Return whether routing should enter the Codex App Server boundary.

    Local/single-user deployments enable it by default and retain an explicit
    opt-out to the hardened one-shot adapter.  Production-like deployments
    always enter this boundary even while disabled so they fail closed instead
    of silently running the weaker legacy CLI path.
    """

    return agent_uses_codex_execution_backend(agent)


def _require_enabled_for_deployment() -> None:
    require_codex_backend_enabled()


def _turn_timeout_s() -> float:
    raw = str(os.environ.get("ECHO_CODEX_APP_SERVER_TIMEOUT") or "").strip()
    if not raw:
        return _DEFAULT_TURN_TIMEOUT_S
    try:
        return min(_MAX_TURN_TIMEOUT_S, max(30.0, float(raw)))
    except (TypeError, ValueError):
        _logger.warning("invalid ECHO_CODEX_APP_SERVER_TIMEOUT=%r; using default", raw)
        return _DEFAULT_TURN_TIMEOUT_S


def _state_root_for_workspace(workspace: Path) -> Path:
    return _shared_state_root_for_workspace(workspace)


def _source_codex_home() -> Path | None:
    explicit = str(os.environ.get("ECHO_CODEX_SOURCE_HOME") or "").strip()
    if explicit:
        source = Path(explicit).expanduser()
        if not source.is_absolute():
            raise CodexSecurityError("ECHO_CODEX_SOURCE_HOME must be absolute")
        return source.resolve(strict=False)
    if _deployment_mode() in _PRODUCTION_MODES:
        return None
    # Local desktop/CLI use deliberately reuses the current OS user's Codex
    # login, but the security layer copies only a validated private auth.json
    # into a principal/thread-scoped CODEX_HOME.  The rest of ~/.codex is never
    # inherited.
    return (Path.home() / ".codex").resolve(strict=False)


def _sandbox_mode(
    context: dict[str, Any],
    *,
    trusted_parent_metadata: dict[str, Any] | None = None,
) -> CodexSandboxMode:
    # ``danger-full-access`` remains capped by the shared resolver.  Audit and
    # trusted parent read-only declarations additionally force the built-in
    # Codex shell/patch surface into a read-only filesystem profile.
    return resolve_codex_sandbox_mode(
        context,
        trusted_parent_metadata=trusted_parent_metadata,
    )


def _partner_command(agent: Any) -> tuple[str, ...]:
    return codex_app_server_command(agent)


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
            raise CodexSecurityError("Codex App Server driver requires an embedded Coder role")
        command = str(
            capabilities.get("codex_app_server_executable")
            or capabilities.get("codex_executable")
            or "codex"
        ).strip()
        if not command or "\x00" in command:
            raise CodexSecurityError("Codex executable is invalid")
        model = ""
        return CodexExecutionRequest(
            outer_thread_id=str(getattr(turn, "thread_id", "") or ""),
            outer_turn_id=str(getattr(turn, "id", "") or ""),
            workspace=workspace,
            realm_id=str(os.environ.get("ECHO_CODEX_REALM") or app_paths().data_dir.resolve()),
            tenant_id=str(context.get("tenant_id") or "local"),
            principal_id=str(context.get("owner_actor_id") or "local"),
            prompt=prompt,
            command=(command, "app-server", "--strict-config", "--listen", "stdio://"),
            source_codex_home=_source_codex_home(),
            model=model or None,
            effort=str(context.get("reasoning_effort") or "").strip() or None,
            sandbox_mode=_sandbox_mode(
                context,
                trusted_parent_metadata=(
                    trusted_parent.metadata
                    if trusted_parent is not None and isinstance(trusted_parent.metadata, dict)
                    else None
                ),
            ),
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
    """Run one outer turn through Codex and stream it into native UI items.

    The embedded Coder has no persona/policy-equivalent fallback. Every
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
    raw_context = getattr(intent, "user_context", None)
    context = dict(raw_context) if isinstance(raw_context, dict) else {}
    trusted_parent = _trusted_realtime_parent(turn, agent, context, request.workspace)
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
        try:
            await session.start()
        except CodexBackendUnavailable:
            if session.turn_started or _deployment_mode() in _PRODUCTION_MODES:
                raise
            # Keep one execution and security model.  The retired one-shot CLI
            # bridge must not be resurrected as a silent fallback.
            raise

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
                        "reason": turn.interrupt_reason or turn.outcome_reason,
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
                await runtime._apply_react_event(
                    turn,
                    log,
                    emitter,
                    bridge_state,
                    event,
                )
            if notification.method == "turn/completed":
                saw_terminal = True

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
