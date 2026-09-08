"""Realtime adapters for the engine-neutral execution supervisor.

Only this host adapter knows about websocket emitters, Turn items and role
drivers. The supervisor can be embedded by other hosts without importing the
gateway. Permissions and subprocess isolation remain inside the existing
execution paths.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.execution.artifact_contracts import HandoffRecorder
from runtime.execution.codex_backend.readiness import CodexReadiness
from runtime.execution.engines import (
    EngineId,
    EngineSelectionError,
    ExecutionPhase,
    ExecutionRoute,
    ExecutionSupervisor,
    select_execution_route,
)
from runtime.platform.models import ParsedIntent
from runtime.protocol import ServerMethod, Turn
from runtime.protocol.items import ExecutionSnapshot
from runtime.sensing.gateway.realtime_execution_context import RealtimeExecutionContext

_CODING_PURPOSE = re.compile(
    r"代码|编程|前端|后端|源码|单元测试|接口实现|修复.*(?:报错|错误|漏洞)|"
    r"(?:开发|实现|搭建|构建).*(?:网站|网页|应用|程序|脚本|接口)|"
    r"\b(?:code|coding|debug|refactor|typescript|javascript|python|pytest|"
    r"frontend|backend|bug|repository|unit\s+tests?)\b|"
    r"\b(?:build|implement|develop)\b.*\b(?:app|website|api|script)\b",
    re.IGNORECASE,
)


def is_coding_task(intent: ParsedIntent) -> bool:
    """Use explicit work modes and parsed intent, not an extra model hop."""
    context = intent.user_context or {}
    if intent.intent_type in {"refactor", "debug"}:
        return True
    # The unified General/Design selector describes a work surface. It no
    # longer implies a coding task, even when that surface grants code tools.
    if context.get("agent_mode") in {"develop", "uxui"}:
        return bool(_CODING_PURPOSE.search(intent.raw or intent.normalized_goal or ""))
    # Personal space grants code tools to general/office work too. That
    # capability is not evidence that the user asked for a coding task.
    if context.get("personal_mode") in {"general", "research"}:
        return False
    return (
        context.get("mode") == "code"
        or context.get("capability_mode") == "code"
        or context.get("personal_mode") == "build"
    )


def codex_readiness_for_turn(runtime: Any, turn: Turn, agent: Any) -> CodexReadiness:
    from runtime.execution.codex_backend.account import resolve_codex_execution_auth_home
    from runtime.execution.codex_backend.model_profile import CodexModelPreferenceStore
    from runtime.execution.codex_backend.readiness import inspect_codex_readiness
    from runtime.execution.codex_backend.role_runner import (
        _execution_profile,
        deployment_mode,
        source_codex_home,
        state_root_for_workspace,
    )
    from runtime.execution.codex_backend.security import CodexSecurityError
    from runtime.execution.codex_backend.types import ConfigurationError
    from runtime.safety.auth.scope import TenantScope

    capabilities = getattr(agent, "capabilities", None)
    if isinstance(capabilities, dict) and capabilities.get("codex_app_server") is False:
        return CodexReadiness(False, "disabled")
    if not turn.execution_workspace_path:
        return CodexReadiness(False, "workspace_required")
    try:
        state_root = state_root_for_workspace(Path(turn.execution_workspace_path))
        actor = getattr(turn.params, "owner_actor_id", None)
        tenant = getattr(turn.params, "tenant_id", None)
        if bool(actor) != bool(tenant):
            return CodexReadiness(False, "principal_unavailable")
        scope = TenantScope(tenant_id=tenant, actor_id=actor) if actor and tenant else None
        preference = CodexModelPreferenceStore(state_root / "model_profile.json").read(scope)
        profile = _execution_profile(runtime._stack, agent, {}, preference=preference)
        return inspect_codex_readiness(
            profile,
            tools_available=getattr(getattr(runtime._stack, "executor", None), "registry", None)
            is not None,
            auth_source=lambda: resolve_codex_execution_auth_home(
                state_root=state_root,
                scope=scope,
                deployment_mode=deployment_mode(),
                legacy_source_home=source_codex_home(),
                allow_local_principal_inheritance=deployment_mode() == "local",
            ),
        )
    except ConfigurationError:
        return CodexReadiness(False, "model_incompatible")
    except (CodexSecurityError, OSError, ValueError):
        return CodexReadiness(False, "configuration_unavailable")


async def select_turn_execution(
    runtime: Any,
    turn: Turn,
    agent: Any,
    intent: ParsedIntent,
    *,
    project_command: bool,
    group_fanout: bool,
    topology_id: str | None,
    codex_partner: bool,
    reflection_fast_path: bool,
    coordinated: bool = False,
) -> ExecutionRoute:
    requested = str(getattr(turn.params, "execution_engine", "auto") or "auto").strip().lower()
    # ``native`` was the public spelling before the unified supervisor. Keep
    # old clients and persisted drafts working while durable evidence uses the
    # canonical ``octopus`` engine id.
    if requested == "native":
        requested = "octopus"
    config = getattr(getattr(runtime, "_stack", None), "config", None)
    default_member = getattr(getattr(config, "execution", None), "member_engine", "octopus")
    route = select_execution_route(
        project_command=project_command,
        group_fanout=group_fanout,
        topology_id=topology_id,
        codex_partner=codex_partner,
        reflection_fast_path=reflection_fast_path,
        requested_engine=None if requested == "auto" else EngineId(requested),
        coding_task=is_coding_task(intent),
        coordinated=coordinated,
        coordinator_engine=EngineId.OPENCODE if default_member == "opencode" else None,
        default_engine=EngineId.OPENCODE if default_member == "opencode" else None,
    )
    if route.engine is EngineId.OCTOPUS:
        return route
    if route.engine is EngineId.OPENCODE:
        from runtime.execution.opencode_backend import inspect_readiness
        from runtime.sensing.gateway.realtime_opencode_backend import scope_for_turn

        # Readiness must validate the exact host-selected Zen model. Passing
        # no selection would always reject the explicit OpenCode route, while
        # accepting a catalog row different from the request would make the
        # durable execution evidence lie about model ownership.
        selection = getattr(turn.params, "model", None)
        status = await asyncio.to_thread(
            inspect_readiness,
            scope_for_turn(turn),
            selection,
        )
        if status["available"]:
            return route
        raise EngineSelectionError(status["reason"], engine=EngineId.OPENCODE, reason="unavailable")
    readiness = await asyncio.to_thread(codex_readiness_for_turn, runtime, turn, agent)
    if readiness.available:
        return route
    if requested == "auto" and route.reason == "coding_task":
        # This is selection before any effects, not recovery from a failed
        # engine. Explicit Codex choices and operator-selected Codex roles
        # fail closed instead of acquiring a weaker execution path.
        return ExecutionRoute(EngineId.OCTOPUS, "react", f"codex_unavailable:{readiness.reason}")
    raise EngineSelectionError(
        f"Codex is unavailable ({readiness.reason}). Configure the Codex engine or select Octopus.",
        engine=EngineId.CODEX,
        reason=readiness.reason or "unavailable",
    )


@dataclass(frozen=True, slots=True)
class TurnExecutionRequest:
    intent: ParsedIntent
    text: str
    model: str | None


@dataclass(frozen=True, slots=True)
class _TurnHost:
    runtime: Any
    turn: Turn
    log: Any
    emitter: Any
    provider: Any
    agent: Any
    route: ExecutionRoute
    topology_id: str | None
    context: RealtimeExecutionContext = field(default_factory=RealtimeExecutionContext)


async def _dispatch_host_driver(
    host: _TurnHost, request: TurnExecutionRequest, phase: ExecutionPhase
) -> bool:
    """Dispatch host orchestration separately from model continuations."""
    args = (host.turn, host.log, host.emitter, request.intent)
    driver = host.route.driver_for(phase)
    if driver == "project_os":
        await host.runtime._drive_project_os(
            *args, thread_id=host.turn.thread_id, text=request.text
        )
    elif driver == "group_fanout":
        await host.runtime._drive_group_fanout(*args, text=request.text)
    elif driver == "swarm_mesh":
        await host.runtime._drive_swarm_mesh(*args, text=request.text, topology_id=host.topology_id)
    else:
        return False
    return True


@dataclass(frozen=True, slots=True)
class NativeExecutionAdapter:
    host: _TurnHost
    engine = EngineId.OCTOPUS

    async def execute(self, request: TurnExecutionRequest, phase: ExecutionPhase) -> None:
        h = self.host
        async with h.context.activate(h.runtime, h.turn, h.agent, request.intent, request.text):
            await self._execute(request, phase)

    async def _execute(self, request: TurnExecutionRequest, phase: ExecutionPhase) -> None:
        h = self.host
        args = (h.turn, h.log, h.emitter, request.intent)
        driver = h.route.driver_for(phase)
        if await _dispatch_host_driver(h, request, phase):
            return
        if driver == "reflection_fast_path":
            await h.runtime._drive_reflection_fast_path(*args, h.agent, model=request.model)
        elif driver == "react":
            await h.runtime._drive_react(*args, h.provider, h.agent, model=request.model)
        else:
            raise ValueError(f"unsupported native driver: {driver}")


@dataclass(frozen=True, slots=True)
class CodexExecutionAdapter:
    host: _TurnHost
    engine = EngineId.CODEX

    async def execute(self, request: TurnExecutionRequest, phase: ExecutionPhase) -> None:
        h = self.host
        # Every continuation resumes the same durable Codex thread. Never
        # use the native planner as an implicit verification/failure fallback.
        async with h.context.activate(h.runtime, h.turn, h.agent, request.intent, request.text):
            if await _dispatch_host_driver(h, request, phase):
                return
            await h.runtime._drive_codex_app_server(
                h.turn, h.log, h.emitter, request.intent, h.agent, h.provider, text=request.text
            )


@dataclass(frozen=True, slots=True)
class OpenCodeExecutionAdapter:
    host: _TurnHost
    engine = EngineId.OPENCODE

    async def execute(self, request: TurnExecutionRequest, phase: ExecutionPhase) -> None:
        from runtime.sensing.gateway.realtime_opencode_backend import drive_opencode

        h = self.host
        async with h.context.activate(h.runtime, h.turn, h.agent, request.intent, request.text):
            if await _dispatch_host_driver(h, request, phase):
                return
            await drive_opencode(
                h.runtime,
                h.turn,
                h.log,
                h.emitter,
                request.intent,
                h.agent,
                h.provider,
                text=request.text,
            )


def bind_turn_execution(
    runtime: Any,
    turn: Turn,
    log: Any,
    emitter: Any,
    provider: Any,
    agent: Any,
    route: ExecutionRoute,
    *,
    topology_id: str | None = None,
) -> ExecutionSupervisor[TurnExecutionRequest]:
    """Bind an authenticated turn and persist every invocation before dispatch."""

    def read_handoffs() -> tuple[dict[str, Any], ...]:
        return tuple(
            dict(event.payload)
            for event in log.iter_events()
            if event.event == "execution_handoff" and event.thread_id == turn.thread_id
        )

    context = RealtimeExecutionContext(
        approval_provider=provider,
        handoff_recorder=HandoffRecorder(
            lambda receipt: log.execution_handoff(turn.thread_id, turn.id, receipt),
            read_handoffs,
        ),
        trusted_workspace_mode=("code" if route.driver == "project_os" else None),
    )
    if route.engine is EngineId.CODEX:
        from runtime.sensing.gateway.realtime_codex_backend import _turn_timeout_s

        # Preserve Codex's existing operator limit, but start it only once for
        # this host turn instead of granting every continuation a new window.
        context.maximum_duration_s = _turn_timeout_s()
    host = _TurnHost(runtime, turn, log, emitter, provider, agent, route, topology_id, context)
    adapter = {
        EngineId.CODEX: CodexExecutionAdapter,
        EngineId.OPENCODE: OpenCodeExecutionAdapter,
        EngineId.OCTOPUS: NativeExecutionAdapter,
    }[route.engine](host)

    async def before_invoke(phase: ExecutionPhase, invocation: int) -> None:
        snapshot = ExecutionSnapshot(
            engine=route.engine.value,
            driver=route.driver_for(phase),
            reason=route.reason,
            phase=phase.value,
            invocation=invocation,
        )
        # A failed journal write must prevent engine start; an observable
        # action must not outrun its durable engine/phase coordinate.
        log.turn_updated(
            turn.thread_id,
            turn.id,
            execution=snapshot.model_dump(mode="json"),
            execution_model={
                "engine": route.engine.value,
                "invocation": invocation,
                "model": turn.params.model or "auto",
            }
            if route.engine is not EngineId.OCTOPUS and turn.params is not None
            else None,
            durable=True,
        )
        turn.execution = snapshot
        turn.execution_engine = route.engine.value
        await emitter.notify(
            ServerMethod.TURN_EXECUTION_UPDATED,
            {
                "threadId": turn.thread_id,
                "turnId": turn.id,
                "execution": snapshot.model_dump(mode="json"),
            },
        )

    return ExecutionSupervisor(
        route,
        adapter,
        is_interrupted=lambda: emitter.is_turn_interrupted(turn.id),
        before_invoke=before_invoke,
    )
