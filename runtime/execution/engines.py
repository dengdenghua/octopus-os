"""Engine-neutral execution admission and binding.

The host retains task state, permissions, persistence and finalization. This
module binds one execution adapter for the lifetime of a turn and prevents a
continuation from selecting a different engine. It does not plan, retry or
interpret model output.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar


class EngineId(StrEnum):
    OCTOPUS = "octopus"
    # Backward-compatible name used by the pre-unified host tests and older
    # callers.  It intentionally resolves to the same durable engine id.
    NATIVE = "octopus"
    CODEX = "codex"
    OPENCODE = "opencode"


class ExecutionPhase(StrEnum):
    PRIMARY = "primary"
    STEERING = "steering"
    VERIFICATION = "verification"
    REPAIR = "repair"


class EngineSelectionError(RuntimeError):
    """A requested backend cannot satisfy this task before execution starts."""

    def __init__(self, message: str, *, engine: EngineId, reason: str) -> None:
        super().__init__(message)
        self.engine = engine
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ExecutionRoute:
    engine: EngineId
    driver: str
    reason: str

    def driver_for(self, phase: ExecutionPhase) -> str:
        if phase is ExecutionPhase.PRIMARY and self.driver in {
            "project_os",
            "group_fanout",
            "swarm_mesh",
        }:
            return self.driver
        if self.engine is EngineId.CODEX:
            return "codex_app_server"
        if self.engine is EngineId.OPENCODE:
            return "opencode_server"
        return self.driver if phase is ExecutionPhase.PRIMARY else "react"


def select_execution_route(
    *,
    project_command: bool = False,
    group_fanout: bool = False,
    topology_id: str | None = None,
    codex_partner: bool = False,
    reflection_fast_path: bool = False,
    requested_engine: EngineId | None = None,
    coding_task: bool = False,
    coordinated: bool = False,
    coordinator_engine: EngineId | None = None,
    default_engine: EngineId | None = None,
) -> ExecutionRoute:
    """Resolve host-validated signals without an additional model call.

    Project/team orchestration takes precedence over a roster member's engine.
    Individual members retain their own engine binding when dispatched.
    """
    if requested_engine is EngineId.CODEX and (
        project_command or group_fanout or topology_id or coordinated
    ):
        raise EngineSelectionError(
            "Codex cannot own project or team orchestration. Select Auto or Octopus; "
            "individual coding tasks can use Codex.",
            engine=EngineId.CODEX,
            reason="orchestration_required",
        )
    if requested_engine is EngineId.CODEX:
        # Privacy is a host policy, so explicit external-engine requests fail
        # before any readiness probe, credential lookup or process launch.
        from runtime.safety.privacy import PrivacyViolation, deny_private_operation

        try:
            deny_private_operation("codex_model_selection")
        except PrivacyViolation as exc:
            raise EngineSelectionError(
                str(exc), engine=EngineId.CODEX, reason="privacy"
            ) from exc
    # These schedulers run on the host regardless of the selected model
    # engine. External adapters dispatch them before model continuations.
    orchestration_engine = requested_engine or coordinator_engine or EngineId.OCTOPUS
    if project_command:
        return ExecutionRoute(orchestration_engine, "project_os", "explicit_project")
    if group_fanout:
        return ExecutionRoute(orchestration_engine, "group_fanout", "explicit_group")
    if topology_id:
        return ExecutionRoute(orchestration_engine, "swarm_mesh", "explicit_topology")
    if coordinated:
        selected = requested_engine or coordinator_engine
        if selected is EngineId.OPENCODE:
            return ExecutionRoute(EngineId.OPENCODE, "opencode_server", "team_coordinator")
        if selected is EngineId.CODEX:
            return ExecutionRoute(EngineId.CODEX, "codex_app_server", "team_coordinator")
        return ExecutionRoute(EngineId.OCTOPUS, "react", "team_coordinator")
    if requested_engine is EngineId.CODEX:
        return ExecutionRoute(EngineId.CODEX, "codex_app_server", "explicit_engine")
    if requested_engine is EngineId.OPENCODE:
        return ExecutionRoute(EngineId.OPENCODE, "opencode_server", "explicit_engine")
    if requested_engine is EngineId.OCTOPUS:
        driver = "reflection_fast_path" if reflection_fast_path and not coding_task else "react"
        return ExecutionRoute(EngineId.OCTOPUS, driver, "explicit_engine")
    if codex_partner:
        from runtime.safety.privacy import privacy_enabled

        if privacy_enabled():
            return ExecutionRoute(EngineId.OCTOPUS, "react", "privacy_local_only")
        return ExecutionRoute(EngineId.CODEX, "codex_app_server", "role_backend")
    if default_engine is EngineId.OPENCODE:
        return ExecutionRoute(EngineId.OPENCODE, "opencode_server", "host_default")
    if default_engine is EngineId.CODEX:
        return ExecutionRoute(EngineId.CODEX, "codex_app_server", "host_default")
    if coding_task:
        return ExecutionRoute(EngineId.CODEX, "codex_app_server", "coding_task")
    if reflection_fast_path:
        return ExecutionRoute(EngineId.OCTOPUS, "reflection_fast_path", "reflection")
    return ExecutionRoute(EngineId.OCTOPUS, "react", "native_default")


RequestT = TypeVar("RequestT")
RequestContra = TypeVar("RequestContra", contravariant=True)


class ExecutionAdapter(Protocol[RequestContra]):
    @property
    def engine(self) -> EngineId: ...

    async def execute(self, request: RequestContra, phase: ExecutionPhase) -> None: ...


class ExecutionAdmissionError(RuntimeError):
    """A duplicate, overlapping or failed invocation cannot be resumed implicitly."""


class ExecutionSupervisor(Generic[RequestT]):
    """One bound adapter; the host's cancellation and journal stay authoritative.

    Admission flags describe invocations only, never task completion. Any
    exception propagates to the host and seals this instance: it cannot silently
    retry an engine that may already have performed effects.
    """

    def __init__(
        self,
        route: ExecutionRoute,
        adapter: ExecutionAdapter[RequestT],
        *,
        is_interrupted: Callable[[], bool],
        before_invoke: Callable[[ExecutionPhase, int], Awaitable[None]],
    ) -> None:
        if adapter.engine != route.engine:
            raise ValueError("adapter does not match the selected execution engine")
        self._route = route
        self._adapter = adapter
        self._is_interrupted = is_interrupted
        self._before_invoke = before_invoke
        self._invocations = 0
        self._in_flight = False
        self._sealed = False

    @property
    def route(self) -> ExecutionRoute:
        return self._route

    async def execute(
        self, request: RequestT, *, phase: ExecutionPhase = ExecutionPhase.PRIMARY
    ) -> None:
        if self._sealed or self._in_flight:
            raise ExecutionAdmissionError("execution is sealed or already in flight")
        if (phase is ExecutionPhase.PRIMARY) != (self._invocations == 0):
            raise ExecutionAdmissionError("execute the primary once before any continuation")
        if self._is_interrupted():
            self._sealed = True
            raise asyncio.CancelledError("execution interrupted before admission")
        self._in_flight = True
        self._invocations += 1
        try:
            await self._before_invoke(phase, self._invocations)
            # Persistence/notification may yield while a user presses Stop.
            if self._is_interrupted():
                raise asyncio.CancelledError("execution interrupted before engine start")
            await self._adapter.execute(request, phase)
        except BaseException:
            self._sealed = True
            raise
        finally:
            self._in_flight = False
