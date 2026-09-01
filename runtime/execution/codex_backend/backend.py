"""High-level, fail-closed execution session for Codex App Server.

The transport client deliberately exposes the versioned App Server protocol.
This module owns the Echo execution boundary above it: tenant isolation,
durable thread binding, approval scope, one inner turn, and notification
filtering.  A session never falls back to another executor itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Self, cast

from runtime.safety.approval.approval_gate import ApprovalProvider
from runtime.safety.sandboxing.sandbox import (
    BackendChoice,
    SandboxPolicy,
    SandboxViolation,
    SeatbeltBackend,
)

from .approvals import CodexApprovalBroker
from .client import CodexAppServerClient
from .security import (
    CodexSandboxMode,
    CodexSecurityError,
    CodexSidecarContext,
    CodexSidecarSecurity,
    CodexThreadBinding,
)
from .types import (
    ApprovalHandler,
    CodexAppServerConfig,
    CodexProviderProfile,
    JsonValue,
    Notification,
    ProtocolError,
    RemoteError,
    RequestTimeoutError,
    TransportClosedError,
)

_SERVER_AUTHORITY = "server"
_METHOD_NOT_FOUND = -32601
_INVALID_REQUEST = -32600
_NESTED_SEATBELT_BYPASS = "none_due_to_nested_incompatibility"

_logger = logging.getLogger(__name__)


class CodexBackendUnavailable(RuntimeError):
    """The App Server executable or required pre-turn API is unavailable.

    Gateways may use this exception for a safe, pre-turn compatibility
    fallback.  Security/config/auth/binding errors are intentionally never
    wrapped in this type.  Once ``turn_started`` is true, this exception is
    never raised by the session.
    """


class CodexBackendStateError(RuntimeError):
    """A session operation was requested in the wrong lifecycle state."""


@dataclass(frozen=True, slots=True)
class CodexExecutionRequest:
    """Authenticated outer-turn inputs for one isolated Codex execution."""

    outer_thread_id: str
    outer_turn_id: str
    workspace: Path
    realm_id: str
    tenant_id: str
    principal_id: str
    prompt: str
    command: tuple[str, ...]
    source_codex_home: Path | None = None
    model: str | None = None
    effort: str | None = None
    sandbox_mode: CodexSandboxMode = "workspace-write"
    host_env: Mapping[str, str] | None = field(default=None, repr=False)
    provider_profile: CodexProviderProfile | None = None
    use_system_model_proxy: bool = False
    developer_instructions: str | None = None
    dynamic_tools: tuple[Mapping[str, Any], ...] = ()
    dynamic_tool_handler: Any | None = None
    selected_app_ids: tuple[str, ...] = ()
    app_mentions: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "outer_thread_id",
            "outer_turn_id",
            "realm_id",
            "tenant_id",
            "principal_id",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if not isinstance(self.command, tuple) or not self.command:
            raise ValueError("command must be an explicit non-empty tuple")
        if any(not isinstance(part, str) or not part or "\x00" in part for part in self.command):
            raise ValueError("command entries must be non-empty, NUL-free strings")
        workspace = Path(self.workspace).expanduser()
        if not workspace.is_absolute():
            raise ValueError("workspace must be absolute")
        object.__setattr__(self, "workspace", workspace)
        if self.source_codex_home is not None:
            source_home = Path(self.source_codex_home).expanduser()
            if not source_home.is_absolute():
                raise ValueError("source_codex_home must be absolute")
            object.__setattr__(self, "source_codex_home", source_home)
        for field_name in ("model", "effort"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ValueError(f"{field_name} must be None or a non-empty string")
        if len(self.selected_app_ids) > 32 or any(
            not isinstance(app_id, str)
            or not app_id.strip()
            or len(app_id) > 256
            or any(char in app_id for char in "\x00\r\n")
            for app_id in self.selected_app_ids
        ):
            raise ValueError("selected_app_ids must contain bounded safe identifiers")
        if len(self.app_mentions) > 8 or any(
            not isinstance(mention, tuple)
            or len(mention) != 2
            or any(
                not isinstance(value, str)
                or not value.strip()
                or len(value) > 256
                or any(char in value for char in "\x00\r\n")
                for value in mention
            )
            for mention in self.app_mentions
        ):
            raise ValueError("app_mentions must contain bounded (id, name) pairs")
        if any(app_id not in self.selected_app_ids for app_id, _name in self.app_mentions):
            raise ValueError("app_mentions must refer to selected apps")
        if self.sandbox_mode not in {"read-only", "workspace-write"}:
            raise ValueError("sandbox_mode must be 'read-only' or 'workspace-write'")
        if self.provider_profile is not None and not isinstance(
            self.provider_profile, CodexProviderProfile
        ):
            raise ValueError("provider_profile must be server-resolved")
        if self.provider_profile is not None:
            if self.model is None:
                object.__setattr__(self, "model", self.provider_profile.model)
            elif self.model != self.provider_profile.model:
                raise ValueError("model must match the server-resolved provider profile")
        if type(self.use_system_model_proxy) is not bool:
            raise ValueError("use_system_model_proxy must be a server-resolved boolean")
        if self.use_system_model_proxy and (
            self.model is None or self.provider_profile is not None
        ):
            raise ValueError("system model proxy requests require an unresolved explicit model")
        if self.developer_instructions is not None and (
            not isinstance(self.developer_instructions, str)
            or not self.developer_instructions.strip()
            or "\x00" in self.developer_instructions
            or len(self.developer_instructions) > 200_000
        ):
            raise ValueError("developer_instructions must be a non-empty, bounded, NUL-free string")
        if not isinstance(self.dynamic_tools, tuple) or len(self.dynamic_tools) > 256:
            raise ValueError("dynamic_tools must be a tuple of at most 256 tool specs")
        seen_dynamic_names: set[str] = set()
        for spec in self.dynamic_tools:
            if not isinstance(spec, Mapping) or spec.get("type") != "function":
                raise ValueError("dynamic tool specs must be function mappings")
            name = spec.get("name")
            schema = spec.get("inputSchema")
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 128
                or name in seen_dynamic_names
                or not isinstance(schema, Mapping)
            ):
                raise ValueError("dynamic tool specs require unique names and object schemas")
            seen_dynamic_names.add(name)
        if self.dynamic_tools and not callable(self.dynamic_tool_handler):
            raise ValueError("advertised dynamic tools require a server-owned handler")


class ClientFactory(Protocol):
    """Constructor contract needed by the execution boundary."""

    def __call__(
        self,
        config: CodexAppServerConfig,
        *,
        approval_handler: ApprovalHandler | None = None,
        dynamic_tool_handler: ApprovalHandler | None = None,
    ) -> CodexAppServerClient: ...


class CodexExecutionSession:
    """One process, one authenticated outer turn, and one inner Codex turn."""

    def __init__(
        self,
        request: CodexExecutionRequest,
        *,
        security: CodexSidecarSecurity,
        approval_provider: ApprovalProvider,
        is_interrupted: Callable[[], bool],
        client_factory: ClientFactory = CodexAppServerClient,
        process_backend: BackendChoice | None = None,
    ) -> None:
        self.request = request
        self._security = security
        self._approval_provider = approval_provider
        self._is_interrupted = is_interrupted
        self._client_factory = client_factory
        self._process_backend = process_backend
        self._context: CodexSidecarContext | None = None
        self._client: CodexAppServerClient | None = None
        self._approval_broker: CodexApprovalBroker | None = None
        self._inner_thread_id: str | None = None
        self._inner_turn_id: str | None = None
        self._turn_started = False
        self._start_attempted = False
        self._closed = False
        self._auth_seeded = False
        self._resumed = False
        self._outer_sandbox = "unresolved"
        self._close_lock = asyncio.Lock()

    async def __aenter__(self) -> Self:
        return await self.start()

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.close()

    @property
    def context(self) -> CodexSidecarContext | None:
        return self._context

    @property
    def inner_thread_id(self) -> str | None:
        return self._inner_thread_id

    @property
    def inner_turn_id(self) -> str | None:
        return self._inner_turn_id

    @property
    def turn_started(self) -> bool:
        """Whether ``turn/start`` crossed the no-fallback boundary."""

        return self._turn_started

    @property
    def resumed(self) -> bool:
        return self._resumed

    @property
    def auth_seeded(self) -> bool:
        return self._auth_seeded

    @property
    def outer_sandbox(self) -> str:
        """Auditable outer launch posture actually selected for this session."""

        return self._outer_sandbox

    async def start(self) -> Self:
        """Prepare isolation, restore/create a thread, and start one turn."""

        if self._start_attempted:
            raise CodexBackendStateError("Codex execution session can only be started once")
        self._start_attempted = True
        try:
            process_backend = self._effective_process_backend()
            context = self._security.prepare(
                realm_id=self.request.realm_id,
                tenant_id=_principal_scoped_tenant(
                    self.request.tenant_id,
                    self.request.principal_id,
                ),
                thread_id=self.request.outer_thread_id,
                task_id=self.request.outer_turn_id,
                workspace=self.request.workspace,
                sandbox_mode=self.request.sandbox_mode,
                provider_profile=self.request.provider_profile,
                selected_app_ids=self.request.selected_app_ids,
                # This attestation is derived from the effective BackendChoice
                # that will actually wrap the process, never from a
                # caller-owned bool or a bypassed nested-incompatible backend.
                # Full enforcement and a successful transform are checked
                # below before the process can start.
                outer_hard_sandbox_active=bool(
                    process_backend is not None and process_backend.hard
                ),
                host_env=self.request.host_env,
            )
            self._context = context
            if self.request.source_codex_home is not None:
                self._auth_seeded = self._security.seed_auth_from_codex_home(
                    context,
                    source_codex_home=self.request.source_codex_home,
                    authority=_SERVER_AUTHORITY,
                )

            broker = CodexApprovalBroker(
                self._approval_provider,
                outer_thread_id=self.request.outer_thread_id,
                outer_turn_id=self.request.outer_turn_id,
                workspace=context.workspace,
                selected_app_ids=self.request.selected_app_ids,
                is_interrupted=self._is_interrupted,
            )
            self._approval_broker = broker
            launch_env = context.launch_env()
            launch_command, launch_env, launch_cwd = self._transform_process_launch(
                context,
                launch_env,
                process_backend=process_backend,
            )
            config = CodexAppServerConfig(
                command=launch_command,
                cwd=str(launch_cwd),
                env_allowlist=frozenset(launch_env),
                env_overrides=launch_env,
                source_environment={},
                experimental_api=True,
            )
            client_kwargs: dict[str, Any] = {"approval_handler": broker}
            if self.request.dynamic_tool_handler is not None:
                client_kwargs["dynamic_tool_handler"] = self.request.dynamic_tool_handler
            self._client = self._client_factory(config, **client_kwargs)
            await self._start_client()

            config_response = await self._request_pre_turn(
                "config/read",
                {"cwd": str(context.workspace), "includeLayers": False},
            )
            if not isinstance(config_response, Mapping):
                raise ProtocolError("config/read response must be a JSON object")
            context.validate_effective_config(cast(Mapping[str, object], config_response))

            binding = self._security.read_server_binding(
                context,
                authority=_SERVER_AUTHORITY,
            )
            inner_thread_id = await self._restore_or_create_thread(context, binding)
            self._inner_thread_id = inner_thread_id

            turn_params = _turn_extra_params(context.turn_start_security_overrides())
            if self.request.model is not None:
                turn_params["model"] = self.request.model
            if self.request.effort is not None:
                turn_params["effort"] = self.request.effort

            # Setting this immediately before invoking turn/start is
            # deliberate: after this point a lost/malformed response cannot
            # prove that the model did not run or tools did not execute.
            self._turn_started = True
            input_items: str | list[dict[str, str]] = self.request.prompt
            if self.request.app_mentions:
                input_items = [{"type": "text", "text": self.request.prompt}]
                input_items.extend(
                    {"type": "mention", "name": name, "path": f"app://{app_id}"}
                    for app_id, name in self.request.app_mentions
                )
            turn = await self._require_client().start_turn(
                inner_thread_id,
                input_items,
                extra_params=turn_params,
            )
            inner_turn_id = _entity_id(turn, entity="turn", operation="turn/start")
            self._inner_turn_id = inner_turn_id
            broker.bind_inner_scope(thread_id=inner_thread_id, turn_id=inner_turn_id)
            bind_dynamic_scope = getattr(
                self.request.dynamic_tool_handler,
                "bind_inner_scope",
                None,
            )
            if callable(bind_dynamic_scope):
                bind_dynamic_scope(thread_id=inner_thread_id, turn_id=inner_turn_id)
            return self
        except BaseException:
            await self._release(suppress_errors=True)
            raise

    async def notifications(self) -> AsyncIterator[Notification]:
        """Yield only notifications scoped to this exact inner turn."""

        thread_id, turn_id = self._require_inner_scope()
        async for notification in self._require_client().notifications():
            if _notification_matches(notification, thread_id=thread_id, turn_id=turn_id):
                yield notification

    async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
        """Read the next notification belonging to this exact inner turn."""

        thread_id, turn_id = self._require_inner_scope()
        loop = asyncio.get_running_loop()
        deadline = None if timeout_s is None else loop.time() + timeout_s
        while True:
            remaining = None if deadline is None else deadline - loop.time()
            if remaining is not None and remaining <= 0:
                raise RequestTimeoutError(
                    "timed out while filtering Codex notifications for the active turn"
                )
            notification = await self._require_client().next_notification(timeout_s=remaining)
            if _notification_matches(notification, thread_id=thread_id, turn_id=turn_id):
                return notification

    async def interrupt(self, *, timeout_s: float | None = None) -> None:
        """Interrupt this inner turn; process termination remains ``close``'s job."""

        thread_id, turn_id = self._require_inner_scope()
        await self._require_client().interrupt(thread_id, turn_id, timeout_s=timeout_s)

    async def steer(self, text: str, *, timeout_s: float | None = None) -> None:
        """Add input to the active turn with an exact-turn precondition."""

        if not isinstance(text, str) or not text.strip():
            raise ValueError("steering text must be non-empty")
        thread_id, turn_id = self._require_inner_scope()
        result = await self._require_client().request(
            "turn/steer",
            {
                "threadId": thread_id,
                "expectedTurnId": turn_id,
                "input": [{"type": "text", "text": text}],
            },
            timeout_s=timeout_s,
        )
        if not isinstance(result, Mapping) or result.get("turnId") != turn_id:
            raise ProtocolError("turn/steer response did not confirm the active turn")

    async def close(self) -> None:
        """Kill the App Server process tree, then remove task-scoped scratch."""

        await self._release(suppress_errors=False)

    async def _start_client(self) -> None:
        try:
            await self._require_client().start()
        except RemoteError as exc:
            if _unsupported_api(exc):
                raise CodexBackendUnavailable(
                    "Codex App Server initialize API is unavailable"
                ) from exc
            raise
        except (OSError, RequestTimeoutError, TransportClosedError) as exc:
            raise CodexBackendUnavailable("Codex App Server could not be started") from exc

    async def _request_pre_turn(
        self,
        method: str,
        params: Mapping[str, Any],
    ) -> JsonValue:
        try:
            return await self._require_client().request(method, params)
        except RemoteError as exc:
            if _unsupported_api(exc):
                raise CodexBackendUnavailable(
                    f"required Codex App Server API {method!r} is unavailable"
                ) from exc
            raise

    async def _restore_or_create_thread(
        self,
        context: CodexSidecarContext,
        binding: CodexThreadBinding | None,
    ) -> str:
        if binding is not None:
            try:
                thread = await self._resume_thread(context, binding.inner_thread_id)
            except RemoteError as exc:
                if _thread_not_found(exc, binding.inner_thread_id):
                    return await self._create_thread(context, replace_binding=True)
                if _unsupported_api(exc):
                    raise CodexBackendUnavailable(
                        "required Codex App Server thread/resume API is unavailable"
                    ) from exc
                raise
            inner_thread_id = _entity_id(
                thread,
                entity="thread",
                operation="thread/resume",
            )
            if inner_thread_id != binding.inner_thread_id:
                raise ProtocolError("thread/resume returned a different thread id")
            self._resumed = True
            return inner_thread_id
        return await self._create_thread(context, replace_binding=False)

    async def _resume_thread(
        self,
        context: CodexSidecarContext,
        inner_thread_id: str,
    ) -> dict[str, JsonValue]:
        overrides = context.thread_start_security_overrides()
        permissions = _permission_profile(overrides)
        params = _thread_extra_params(overrides, resume=True)
        # ``thread/resume`` can otherwise restore a stale App Server catalog.
        # Reassert the exact current-turn catalog, including an explicit empty
        # list when Echo revoked every tool since the previous turn.
        params["dynamicTools"] = [dict(spec) for spec in self.request.dynamic_tools]
        if self.request.developer_instructions is not None:
            params["developerInstructions"] = self.request.developer_instructions
        return await self._require_client().resume_thread(
            inner_thread_id,
            cwd=str(context.workspace),
            model=self.request.model,
            approval_policy="on-request",
            sandbox=None,
            permissions=permissions,
            exclude_turns=True,
            extra_params=params,
        )

    async def _create_thread(
        self,
        context: CodexSidecarContext,
        *,
        replace_binding: bool,
    ) -> str:
        overrides = context.thread_start_security_overrides()
        permissions = _permission_profile(overrides)
        params = _thread_extra_params(overrides, resume=False)
        params["dynamicTools"] = [dict(spec) for spec in self.request.dynamic_tools]
        if self.request.developer_instructions is not None:
            params["developerInstructions"] = self.request.developer_instructions
        try:
            thread = await self._require_client().start_thread(
                cwd=str(context.workspace),
                model=self.request.model,
                approval_policy="on-request",
                sandbox=None,
                permissions=permissions,
                ephemeral=False,
                extra_params=params,
            )
        except RemoteError as exc:
            if _unsupported_api(exc):
                raise CodexBackendUnavailable(
                    "required Codex App Server thread/start API is unavailable"
                ) from exc
            raise
        inner_thread_id = _entity_id(thread, entity="thread", operation="thread/start")
        self._security.write_server_binding(
            context,
            inner_thread_id=inner_thread_id,
            authority=_SERVER_AUTHORITY,
            replace=replace_binding,
        )
        return inner_thread_id

    def _require_client(self) -> CodexAppServerClient:
        if self._client is None:
            raise CodexBackendStateError("Codex App Server client is not initialized")
        return self._client

    def _require_inner_scope(self) -> tuple[str, str]:
        if self._closed or self._inner_thread_id is None or self._inner_turn_id is None:
            raise CodexBackendStateError("Codex inner turn is not active")
        return self._inner_thread_id, self._inner_turn_id

    async def _release(self, *, suppress_errors: bool) -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closed = True
            first_error: BaseException | None = None
            if self._client is not None:
                try:
                    await self._client.close()
                except BaseException as exc:  # noqa: BLE001 - cleanup must continue
                    first_error = exc
            if self._context is not None:
                try:
                    self._context.cleanup()
                except BaseException as exc:  # noqa: BLE001 - preserve first cleanup failure
                    if first_error is None:
                        first_error = exc
            if first_error is not None and not suppress_errors:
                raise first_error

    def _transform_process_launch(
        self,
        context: CodexSidecarContext,
        launch_env: dict[str, str],
        *,
        process_backend: BackendChoice | None,
    ) -> tuple[tuple[str, ...], dict[str, str], Path]:
        """Apply the resolved outer process sandbox before spawning Codex.

        The App Server itself needs network access for model inference and
        private writable state outside the checked-out workspace. Generated
        tools remain network-denied by the independently validated inner
        permission profile.
        """

        try:
            policy = SandboxPolicy(
                workspace=context.workspace,
                allow_network=True,
                mode=context.sandbox_mode,
                additional_write_roots=(
                    context.codex_home,
                    context.task_root,
                    context.scratch_root,
                ),
            )
        except SandboxViolation as exc:
            raise CodexSecurityError(f"invalid Codex outer sandbox policy: {exc}") from exc

        choice = process_backend
        requires_hard = self._security.policy.outer_hard_sandbox_required
        if choice is None:
            if requires_hard:
                raise CodexSecurityError(
                    "production Codex sidecar requires an actual outer sandbox backend"
                )
            return self.request.command, launch_env, context.workspace

        enforcement = choice.backend.enforcement(policy)
        if requires_hard and (not choice.hard or enforcement != "full"):
            raise CodexSecurityError(
                "production Codex sidecar requires a full-enforcement hard sandbox backend"
            )
        try:
            argv, transformed_env, transformed_cwd = choice.backend.transform(
                list(self.request.command),
                dict(launch_env),
                context.workspace,
                policy,
            )
        except (OSError, SandboxViolation) as exc:
            raise CodexSecurityError(f"Codex outer sandbox transform failed: {exc}") from exc
        if not argv or any(
            not isinstance(part, str) or not part or "\x00" in part for part in argv
        ):
            raise CodexSecurityError("Codex outer sandbox produced an invalid command")
        if not isinstance(transformed_cwd, Path):
            transformed_cwd = Path(transformed_cwd)
        if not transformed_cwd.is_absolute() or not transformed_cwd.is_dir():
            raise CodexSecurityError("Codex outer sandbox produced an invalid cwd")
        return tuple(argv), dict(transformed_env), transformed_cwd

    def _effective_process_backend(self) -> BackendChoice | None:
        """Resolve the launch wrapper without disabling Codex's inner sandbox.

        macOS Seatbelt policies cannot be applied recursively: wrapping App
        Server in Echo Seatbelt makes every built-in Codex shell/patch
        launch fail at its own ``sandbox_apply`` call.  Local deployments that
        do not require a hard outer boundary therefore leave App Server
        unwrapped and rely on the validated Codex named permission profile for
        tool filesystem/network confinement.  A production/shared or explicit
        strict posture is rejected before any sidecar process (or state) is
        created; it never silently takes this compatibility path.
        """

        choice = self._process_backend
        if choice is None:
            self._outer_sandbox = "none_configured"
            return None
        if not isinstance(choice.backend, SeatbeltBackend):
            self._outer_sandbox = choice.name
            return choice

        requires_hard = self._security.policy.outer_hard_sandbox_required
        if requires_hard or choice.strict:
            self._outer_sandbox = "rejected_due_to_nested_incompatibility"
            _logger.error(
                "Codex outer_sandbox=%s backend=%s: Seatbelt cannot safely wrap "
                "App Server because built-in tools require a nested sandbox",
                self._outer_sandbox,
                choice.name,
            )
            raise CodexSecurityError(
                "required Codex outer Seatbelt is nested-incompatible with built-in "
                "tool sandboxing; refusing to start the App Server"
            )

        self._outer_sandbox = _NESTED_SEATBELT_BYPASS
        _logger.warning(
            "Codex outer_sandbox=%s backend=%s; local App Server is unwrapped so "
            "the validated inner named permission profile can sandbox built-in tools",
            self._outer_sandbox,
            choice.name,
        )
        return None


def _principal_scoped_tenant(tenant_id: str, principal_id: str) -> str:
    """Prevent two principals in one tenant from sharing a Codex home."""

    return f"tenant:{len(tenant_id)}:{tenant_id}\x1fprincipal:{len(principal_id)}:{principal_id}"


def _thread_extra_params(
    overrides: Mapping[str, object],
    *,
    resume: bool,
) -> dict[str, object]:
    """Remove fields owned by the client's typed thread convenience API."""

    reserved = {
        "cwd",
        "model",
        "approvalPolicy",
        "approvalsReviewer",
        "sandbox",
        "permissions",
        "ephemeral",
    }
    # Resume must reassert every mutable capability selector.  Otherwise the
    # App Server may retain the previous turn's dynamic tool allowlist.
    supported_extras = {
        "runtimeWorkspaceRoots",
        "dynamicTools",
        "selectedCapabilityRoots",
        "developerInstructions",
    }
    return {
        key: value
        for key, value in overrides.items()
        if key not in reserved and key in supported_extras
    }


def _turn_extra_params(overrides: Mapping[str, object]) -> dict[str, object]:
    """Allow only local named-profile fields on ``turn/start``.

    In particular, never forward ``environments``. App Server treats an empty
    list as disabling the local execution environment and a non-empty list as
    an explicit environment selection; neither is caller-controlled here.
    """

    supported = {
        "cwd",
        "runtimeWorkspaceRoots",
        "approvalPolicy",
        "approvalsReviewer",
        "permissions",
    }
    return {key: value for key, value in overrides.items() if key in supported}


def _permission_profile(overrides: Mapping[str, object]) -> str:
    value = overrides.get("permissions")
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("security context did not provide a named permissions profile")
    return value


def _entity_id(
    payload: Mapping[str, JsonValue],
    *,
    entity: str,
    operation: str,
) -> str:
    nested = payload.get(entity)
    value = nested.get("id") if isinstance(nested, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError(f"{operation} response {entity} is missing a non-empty id")
    return value


def _unsupported_api(error: RemoteError) -> bool:
    return error.code == _METHOD_NOT_FOUND


def _thread_not_found(error: RemoteError, thread_id: str) -> bool:
    return error.code == _INVALID_REQUEST and error.message == f"thread not found: {thread_id}"


def _notification_matches(
    notification: Notification,
    *,
    thread_id: str,
    turn_id: str,
) -> bool:
    params = notification.params
    event_thread_id = params.get("threadId")
    if notification.method == "thread/tokenUsage/updated":
        return event_thread_id == thread_id
    event_turn_id = params.get("turnId")
    if event_turn_id is None:
        raw_turn = params.get("turn")
        if isinstance(raw_turn, Mapping):
            event_turn_id = raw_turn.get("id")
    return event_thread_id == thread_id and event_turn_id == turn_id


__all__ = [
    "CodexBackendStateError",
    "CodexBackendUnavailable",
    "CodexExecutionRequest",
    "CodexExecutionSession",
]
