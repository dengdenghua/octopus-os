"""Realtime Codex App Server routing and driver tests (fakes only)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from runtime.execution.codex_backend.backend import (
    CodexBackendUnavailable,
    CodexExecutionRequest,
)
from runtime.execution.codex_backend.responses_proxy import ScopedResponsesProxy
from runtime.execution.codex_backend.security import CodexSecurityError
from runtime.execution.codex_backend.types import (
    Notification,
    RemoteError,
    RequestTimeoutError,
)
from runtime.platform.models.llm import ModelRequest, ModelResponse
from runtime.platform.process.session import current_session
from runtime.platform.runtime_policy import feature_flags
from runtime.protocol import TurnStatus
from runtime.safety.auth.scope import TenantScope
from runtime.safety.sandboxing.sandbox import (
    BackendChoice,
    DirectBackend,
    SandboxViolation,
)
from runtime.sensing.gateway import realtime_codex_backend as mod


def _agent(
    *,
    command: str = "/opt/echo/bin/codex",
    app_server: bool | None = None,
) -> SimpleNamespace:
    capabilities: dict[str, object] = {
        "execution_backend": "codex_app_server",
        "codex_app_server_executable": command,
    }
    if app_server is not None:
        capabilities["codex_app_server"] = app_server
    return SimpleNamespace(
        agent_id="agent-codex",
        display_name="Codex",
        capabilities=capabilities,
    )


@pytest.fixture
def set_gate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., None]]:
    """Set the legacy env source without leaking the cached flag snapshot."""

    previous_file = feature_flags._FILE_PATH
    feature_flags.configure(None)

    def _set(*, mode: str, enabled: str | None) -> None:
        monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", mode)
        if enabled is None:
            monkeypatch.delenv("ECHO_CODEX_APP_SERVER_ENABLED", raising=False)
        else:
            monkeypatch.setenv("ECHO_CODEX_APP_SERVER_ENABLED", enabled)
        feature_flags.reload()

    yield _set
    monkeypatch.undo()
    feature_flags.configure(previous_file)


def test_agent_feature_gate_defaults_opt_out_and_production_fails_closed(
    set_gate_env: Callable[..., None],
) -> None:
    set_gate_env(mode="local", enabled=None)
    assert mod.agent_is_codex_app_server_partner(_agent()) is True
    assert mod.agent_is_codex_app_server_partner(_agent(app_server=False)) is False

    set_gate_env(mode="local", enabled="false")
    assert mod.agent_is_codex_app_server_partner(_agent()) is False

    # Production still enters this boundary when disabled.  The driver then
    # rejects execution here instead of falling through to the legacy CLI.
    set_gate_env(mode="production", enabled="false")
    assert mod.agent_is_codex_app_server_partner(_agent(app_server=False)) is True
    with pytest.raises(CodexSecurityError, match="disabled.*production-like"):
        mod._require_enabled_for_deployment()

    set_gate_env(mode="production", enabled="true")
    assert mod.agent_is_codex_app_server_partner(_agent()) is True
    mod._require_enabled_for_deployment()


def test_production_gate_rejects_truthy_non_boolean_file_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous_file = feature_flags._FILE_PATH
    path = tmp_path / "feature_flags.json"
    path.write_text('{"execution.codex_app_server":{"enabled":true}}', encoding="utf-8")
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    monkeypatch.delenv("ECHO_FF_EXECUTION_CODEX_APP_SERVER", raising=False)
    monkeypatch.delenv("ECHO_CODEX_APP_SERVER_ENABLED", raising=False)
    try:
        feature_flags.configure(path)
        assert mod._explicit_feature_flag() is False
        with pytest.raises(CodexSecurityError, match="disabled.*production-like"):
            mod._require_enabled_for_deployment()

        path.write_text('{"execution.codex_app_server":true}', encoding="utf-8")
        feature_flags.reload()
        assert mod._explicit_feature_flag() is True
        mod._require_enabled_for_deployment()
    finally:
        feature_flags.configure(previous_file)


@pytest.mark.parametrize(
    "message",
    [
        "no active turn to steer",
        "expected active turn id `turn-a` but found `turn-b`",
        "cannot steer a review turn",
        "cannot steer a compact turn",
        "active turn uses a different output schema",
    ],
)
def test_steer_not_submitted_errors_are_recognized(message: str) -> None:
    assert mod._steer_was_not_submitted(RemoteError(-32600, message)) is True


def test_ambiguous_steer_error_is_not_safe_to_retry() -> None:
    assert mod._steer_was_not_submitted(RemoteError(-32603, "internal failure")) is False


def test_request_uses_only_authoritative_cwd_and_caps_full_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "trusted-workspace"
    attacker_workspace = tmp_path / "client-selected-workspace"
    workspace.mkdir()
    attacker_workspace.mkdir()
    monkeypatch.setattr(mod, "blackboard_brief", lambda _turn_id: "")
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "local")

    turn = SimpleNamespace(id="outer-turn", thread_id="outer-thread")
    intent = SimpleNamespace(
        user_context={
            "cwd": str(workspace),
            # This value may originate in client metadata and must not become
            # an execution coordinate.
            "workspace_path": str(attacker_workspace),
            "owner_actor_id": "person-7",
            "tenant_id": "tenant-3",
            "reasoning_effort": "xhigh",
            "sandbox_policy": {"type": "danger-full-access"},
        }
    )

    request = mod._request_for_turn(
        object(),
        turn,
        intent,
        _agent(command="/trusted/bin/codex"),
        text="repair the tests",
    )

    assert request.workspace == workspace.resolve()
    assert request.workspace != attacker_workspace.resolve()
    assert request.outer_thread_id == "outer-thread"
    assert request.outer_turn_id == "outer-turn"
    assert request.tenant_id == "tenant-3"
    assert request.principal_id == "person-7"
    assert request.model is None
    assert request.effort == "xhigh"
    assert request.command == (
        "/trusted/bin/codex",
        "app-server",
        "--strict-config",
        "--listen",
        "stdio://",
    )
    assert request.sandbox_mode == "workspace-write"


class _FakeBridgeState:
    def __init__(self) -> None:
        self.flush_calls: list[object] = []

    async def flush(self, _turn, _log, _emitter, *, status: object) -> None:
        self.flush_calls.append(status)

    @staticmethod
    def prose_status_for_turn(status: object) -> object:
        return status


class _FakeRuntime:
    def __init__(self) -> None:
        self._stack: Any = None
        self.events: list[dict[str, Any]] = []
        self.bridge = _FakeBridgeState()
        self.steering_polls = 0
        self.steering_batches: list[list[str]] = []
        self.restored_steering: list[str] = []

    def _make_bridge_state(self, thread_id: str, turn_id: str, *, agent: Any):
        assert (thread_id, turn_id, agent.agent_id) == (
            "outer-thread",
            "outer-turn",
            "agent-codex",
        )
        return self.bridge

    async def _apply_react_event(
        self,
        _turn,
        _log,
        _emitter,
        _bridge,
        event: dict[str, Any],
    ) -> None:
        self.events.append(dict(event))

    async def _publish_discovered_steering(self, _turn, _emitter) -> None:
        self.steering_polls += 1

    def _drain_turn_steering(self, _turn_id: str) -> list[str]:
        return self.steering_batches.pop(0) if self.steering_batches else []

    def _restore_turn_steering(self, _turn_id: str, messages: list[str]) -> None:
        self.restored_steering.extend(messages)


class _CoderRuntime(_FakeRuntime):
    def _make_bridge_state(self, thread_id: str, turn_id: str, *, agent: Any):
        assert (thread_id, turn_id, agent.agent_id) == (
            "outer-thread",
            "outer-turn",
            "coder",
        )
        return self.bridge


class _FakeEmitter:
    def __init__(self, *, interrupted: bool = False, reason: str | None = None) -> None:
        self.interrupted = interrupted
        self.reason = reason
        self.notifications: list[tuple[object, dict[str, Any]]] = []

    def is_turn_interrupted(self, _turn_id: str) -> bool:
        return self.interrupted

    def get_interrupt_reason(self, _turn_id: str) -> str | None:
        return self.reason

    async def notify(self, method: object, params: dict[str, Any]) -> None:
        self.notifications.append((method, params))


class _FakeSession:
    def __init__(
        self,
        request,
        *,
        security,
        approval_provider,
        is_interrupted,
        process_backend,
        events: list[Notification],
        operations: list[str],
        start_error: BaseException | None = None,
        crossed_turn_boundary: bool = False,
        steer_error: BaseException | None = None,
    ) -> None:
        self.request = request
        self.security = security
        self.approval_provider = approval_provider
        self.is_interrupted = is_interrupted
        self.process_backend = process_backend
        self.events = list(events)
        self.operations = operations
        self.start_error = start_error
        self.crossed_turn_boundary = crossed_turn_boundary
        self.steer_error = steer_error
        self.turn_started = False

    async def start(self) -> None:
        self.operations.append("start")
        if self.start_error is not None:
            self.turn_started = self.crossed_turn_boundary
            raise self.start_error
        self.turn_started = True

    async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
        self.operations.append(f"next:{timeout_s}")
        if not self.events:
            raise RequestTimeoutError("no fake notification")
        return self.events.pop(0)

    async def interrupt(self, *, timeout_s: float | None = None) -> None:
        self.operations.append(f"interrupt:{timeout_s}")

    async def steer(self, text: str, *, timeout_s: float | None = None) -> None:
        self.operations.append(f"steer:{text}:{timeout_s}")
        if self.steer_error is not None:
            raise self.steer_error

    async def close(self) -> None:
        self.operations.append("close")


def _turn() -> SimpleNamespace:
    return SimpleNamespace(
        id="outer-turn",
        thread_id="outer-thread",
        items=[],
        status=TurnStatus.IN_PROGRESS,
        outcome_reason=None,
        interrupt_reason=None,
    )


def _intent(workspace: Path) -> SimpleNamespace:
    return SimpleNamespace(
        user_context={
            "cwd": str(workspace),
            "workspace_path": "/must/not/be/used",
            "owner_actor_id": "person",
            "tenant_id": "tenant",
        }
    )


def _install_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[Notification] | None = None,
    start_error: BaseException | None = None,
    crossed_turn_boundary: bool = False,
    steer_error: BaseException | None = None,
) -> tuple[list[_FakeSession], list[str]]:
    instances: list[_FakeSession] = []
    operations: list[str] = []

    def _factory(
        request,
        *,
        security,
        approval_provider,
        is_interrupted,
        process_backend,
    ):
        session = _FakeSession(
            request,
            security=security,
            approval_provider=approval_provider,
            is_interrupted=is_interrupted,
            process_backend=process_backend,
            events=list(events or []),
            operations=operations,
            start_error=start_error,
            crossed_turn_boundary=crossed_turn_boundary,
            steer_error=steer_error,
        )
        instances.append(session)
        return session

    monkeypatch.setattr(mod, "CodexExecutionSession", _factory)
    return instances, operations


def _prepare_driver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, SimpleNamespace, SimpleNamespace]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_root = tmp_path / "state"
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "local")
    monkeypatch.setattr(mod, "_state_root_for_workspace", lambda _workspace: state_root)
    monkeypatch.setattr(mod, "_source_codex_home", lambda: None)
    monkeypatch.setattr(mod, "blackboard_brief", lambda _turn_id: "")
    monkeypatch.setattr(
        mod,
        "resolved_process_backend",
        lambda _mode: BackendChoice(DirectBackend(), "direct", hard=False),
    )
    monkeypatch.setattr(mod, "effective_process_sandbox_mode", lambda: "auto")
    return workspace, _turn(), _intent(workspace)


def _follow_system_request(workspace: Path) -> CodexExecutionRequest:
    return CodexExecutionRequest(
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=workspace.resolve(),
        realm_id="realm",
        tenant_id="tenant-a",
        principal_id="alice",
        prompt="use the system model",
        command=("codex", "app-server", "--listen", "stdio://"),
        source_codex_home=workspace.parent / "must-be-cleared",
        model="deepseek-system",
        use_system_model_proxy=True,
    )


def _standard_coder() -> SimpleNamespace:
    return SimpleNamespace(
        agent_id="coder",
        display_name="Kane",
        capabilities={"execution_backend": "codex_app_server"},
    )


def _install_tracking_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> list[Any]:
    from runtime.execution.codex_backend import role_runner

    instances: list[Any] = []

    class _TrackingProxy(ScopedResponsesProxy):
        close_calls = 0

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            instances.append(self)

        async def close(self) -> None:
            self.close_calls += 1
            await super().close()

    monkeypatch.setattr(role_runner, "ScopedResponsesProxy", _TrackingProxy)
    return instances


@pytest.mark.asyncio
async def test_first_start_streams_native_text_and_terminal_then_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    instances, operations = _install_fake_session(
        monkeypatch,
        events=[
            Notification(
                "item/agentMessage/delta",
                {"threadId": "inner-thread", "turnId": "inner-turn", "delta": "你好"},
            ),
            Notification(
                "turn/completed",
                {
                    "threadId": "inner-thread",
                    "turn": {"id": "inner-turn", "status": "completed"},
                },
            ),
        ],
    )
    runtime = _FakeRuntime()

    result = await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _agent(),
        object(),
        text="完成这个任务",
    )

    assert result is True
    assert len(instances) == 1
    assert instances[0].request.workspace == workspace.resolve()
    assert operations[0] == "start"
    assert operations[-1] == "close"
    assert operations.count("close") == 1
    assert runtime.events == [
        {"type": "text_delta", "delta": "你好"},
        {
            "type": "react_completed",
            "success": True,
            "terminated_reason": "completed",
            "completion_receipt": {"message": "", "codex_status": "completed"},
        },
    ]
    assert len(runtime.bridge.flush_calls) == 1


@pytest.mark.asyncio
async def test_unavailable_before_turn_start_fails_closed_without_cli_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(
        monkeypatch,
        start_error=CodexBackendUnavailable("unsupported API"),
    )
    with pytest.raises(CodexBackendUnavailable, match="unsupported API"):
        await mod.drive_codex_app_server(
            _FakeRuntime(), turn, object(), _FakeEmitter(), intent, _agent(), object(), text="do it"
        )
    assert operations == ["start", "close"]


@pytest.mark.asyncio
async def test_unavailable_after_turn_start_never_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(
        monkeypatch,
        start_error=CodexBackendUnavailable("turn/start response was lost"),
        crossed_turn_boundary=True,
    )
    with pytest.raises(CodexBackendUnavailable, match="response was lost"):
        await mod.drive_codex_app_server(
            _FakeRuntime(),
            turn,
            object(),
            _FakeEmitter(),
            intent,
            _agent(),
            object(),
            text="do it",
        )

    assert operations == ["start", "close"]


@pytest.mark.asyncio
async def test_production_never_falls_back_to_legacy_exec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    monkeypatch.setattr(mod, "_require_enabled_for_deployment", lambda: None)
    _instances, operations = _install_fake_session(
        monkeypatch,
        start_error=CodexBackendUnavailable("unsupported API"),
    )
    with pytest.raises(CodexBackendUnavailable, match="unsupported API"):
        await mod.drive_codex_app_server(
            _FakeRuntime(),
            turn,
            object(),
            _FakeEmitter(),
            intent,
            _agent(),
            object(),
            text="do it",
        )

    assert operations == ["start", "close"]


@pytest.mark.asyncio
async def test_production_rejects_unavailable_outer_backend_before_session_or_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    monkeypatch.setenv("ECHO_DEPLOYMENT_MODE", "production")
    monkeypatch.setattr(mod, "_require_enabled_for_deployment", lambda: None)
    monkeypatch.setattr(mod, "effective_process_sandbox_mode", lambda: "strict")

    def _unavailable(_mode: str) -> BackendChoice:
        raise SandboxViolation("no verified hard backend")

    monkeypatch.setattr(mod, "resolved_process_backend", _unavailable)
    _instances, operations = _install_fake_session(monkeypatch)
    with pytest.raises(SandboxViolation, match="no verified hard backend"):
        await mod.drive_codex_app_server(
            _FakeRuntime(),
            turn,
            object(),
            _FakeEmitter(),
            intent,
            _agent(),
            object(),
            text="do it",
        )

    assert operations == []


@pytest.mark.asyncio
async def test_live_steering_is_forwarded_once_to_the_active_inner_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(
        monkeypatch,
        events=[
            Notification(
                "turn/completed",
                {
                    "threadId": "inner-thread",
                    "turn": {"id": "inner-turn", "status": "completed"},
                },
            )
        ],
    )
    runtime = _FakeRuntime()
    runtime.steering_batches = [["先修复竞态", "再跑回归"]]

    assert await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _agent(),
        object(),
        text="do it",
    )

    assert operations.count("steer:先修复竞态\n\n再跑回归:2.0") == 1
    assert runtime.restored_steering == []


@pytest.mark.asyncio
async def test_terminal_race_restores_unsubmitted_steering_for_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(
        monkeypatch,
        events=[
            Notification(
                "turn/completed",
                {
                    "threadId": "inner-thread",
                    "turn": {"id": "inner-turn", "status": "completed"},
                },
            )
        ],
        steer_error=RemoteError(-32600, "no active turn to steer"),
    )
    runtime = _FakeRuntime()
    runtime.steering_batches = [["保留这条修正"]]

    assert await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _agent(),
        object(),
        text="do it",
    )

    assert operations.count("steer:保留这条修正:2.0") == 1
    assert runtime.restored_steering == ["保留这条修正"]


@pytest.mark.asyncio
async def test_interrupt_requests_inner_interrupt_then_hard_closes_as_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(monkeypatch)
    monkeypatch.setattr(mod, "_INTERRUPT_GRACE_S", 0.0)
    runtime = _FakeRuntime()

    result = await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(interrupted=True, reason="stop now"),
        intent,
        _agent(),
        object(),
        text="long operation",
    )

    assert result is True
    assert operations == ["start", "interrupt:2.0", "close"]
    assert turn.status == TurnStatus.CANCELLED
    assert turn.outcome_reason == "user_cancelled"
    assert turn.interrupt_reason == "stop now"
    assert runtime.events == [{"type": "react_cancelled", "reason": "stop now"}]


@pytest.mark.asyncio
async def test_turn_deadline_interrupts_then_hard_closes_as_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    _instances, operations = _install_fake_session(monkeypatch)
    monkeypatch.setattr(mod, "_turn_timeout_s", lambda: 0.0)
    monkeypatch.setattr(mod, "_INTERRUPT_GRACE_S", 0.0)
    runtime = _FakeRuntime()

    result = await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _agent(),
        object(),
        text="long operation",
    )

    assert result is True
    assert operations == ["start", "interrupt:2.0", "close"]
    assert turn.status == TurnStatus.CANCELLED
    assert turn.outcome_reason == "codex_timeout"
    assert turn.interrupt_reason == "Codex 代码任务超过运行时限"
    assert runtime.events == [{"type": "react_cancelled", "reason": "Codex 代码任务超过运行时限"}]


@pytest.mark.asyncio
async def test_direct_follow_system_materializes_scoped_proxy_for_entire_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.test_codex_responses_proxy import _post

    workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    turn.params = SimpleNamespace(owner_actor_id="alice", tenant_id="tenant-a")
    request = _follow_system_request(workspace)
    monkeypatch.setattr(mod, "_request_for_turn", lambda *_args, **_kwargs: request)
    proxies = _install_tracking_proxy(monkeypatch)
    sessions: list[Any] = []

    class _Router:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []
            self.sessions: list[Any] = []

        def call(self, model_request: ModelRequest) -> ModelResponse:
            self.requests.append(model_request)
            self.sessions.append(current_session())
            return ModelResponse(text="direct proxy response", model=model_request.model)

    router = _Router()

    class _ProxyCallingSession:
        def __init__(self, materialized: CodexExecutionRequest, **_kwargs: Any) -> None:
            self.request = materialized
            self.turn_started = False
            self.closed = False
            sessions.append(self)

        async def start(self) -> None:
            profile = self.request.provider_profile
            assert profile is not None
            status, _headers, _body = await _post(
                profile,
                {
                    "model": self.request.model,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "direct turn"}],
                        }
                    ],
                    "stream": True,
                },
            )
            assert status == 200
            self.turn_started = True

        async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
            return Notification(
                "turn/completed",
                {
                    "threadId": "inner-thread",
                    "turn": {"id": "inner-turn", "status": "completed"},
                },
            )

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(mod, "CodexExecutionSession", _ProxyCallingSession)
    runtime = _CoderRuntime()
    runtime._stack = SimpleNamespace(planner=SimpleNamespace(router=router))

    assert await mod.drive_codex_app_server(
        runtime,
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _standard_coder(),
        object(),
        text="follow the system model",
    )

    assert len(sessions) == 1
    materialized = sessions[0].request
    assert materialized.use_system_model_proxy is False
    assert materialized.source_codex_home is None
    assert materialized.provider_profile is not None
    assert materialized.provider_profile.base_url.startswith("http://127.0.0.1:")
    assert materialized.provider_profile.base_url.endswith("/v1")
    assert sessions[0].closed is True
    assert len(router.requests) == 1
    assert router.requests[0].model == "deepseek-system"
    assert len(router.sessions) == 1
    assert router.sessions[0].actor == "alice"
    assert router.sessions[0].metadata["tenant_id"] == "tenant-a"
    assert len(proxies) == 1
    assert proxies[0].close_calls == 1
    assert proxies[0]._token is None
    assert proxies[0]._active is False


@pytest.mark.asyncio
async def test_direct_follow_system_revokes_proxy_after_session_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    turn.params = SimpleNamespace(owner_actor_id="alice", tenant_id="tenant-a")
    request = _follow_system_request(workspace)
    monkeypatch.setattr(mod, "_request_for_turn", lambda *_args, **_kwargs: request)
    proxies = _install_tracking_proxy(monkeypatch)
    _instances, operations = _install_fake_session(
        monkeypatch,
        start_error=RuntimeError("synthetic App Server failure"),
    )
    runtime = _CoderRuntime()
    runtime._stack = SimpleNamespace(
        planner=SimpleNamespace(router=SimpleNamespace(call=lambda _request: None))
    )

    with pytest.raises(RuntimeError, match="synthetic App Server failure"):
        await mod.drive_codex_app_server(
            runtime,
            turn,
            object(),
            _FakeEmitter(),
            intent,
            _standard_coder(),
            object(),
            text="fail after proxy start",
        )

    assert operations == ["start", "close"]
    assert len(proxies) == 1
    assert proxies[0].close_calls == 1
    assert proxies[0]._token is None
    assert proxies[0]._active is False


@pytest.mark.asyncio
async def test_direct_follow_system_revokes_proxy_when_driver_task_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    turn.params = SimpleNamespace(owner_actor_id="alice", tenant_id="tenant-a")
    request = _follow_system_request(workspace)
    monkeypatch.setattr(mod, "_request_for_turn", lambda *_args, **_kwargs: request)
    proxies = _install_tracking_proxy(monkeypatch)
    waiting = asyncio.Event()
    sessions: list[Any] = []

    class _BlockingSession:
        def __init__(self, materialized: CodexExecutionRequest, **_kwargs: Any) -> None:
            self.request = materialized
            self.turn_started = False
            self.closed = False
            sessions.append(self)

        async def start(self) -> None:
            self.turn_started = True

        async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
            waiting.set()
            await asyncio.Future()
            raise AssertionError("unreachable")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(mod, "CodexExecutionSession", _BlockingSession)
    runtime = _CoderRuntime()
    runtime._stack = SimpleNamespace(
        planner=SimpleNamespace(router=SimpleNamespace(call=lambda _request: None))
    )
    task = asyncio.create_task(
        mod.drive_codex_app_server(
            runtime,
            turn,
            object(),
            _FakeEmitter(),
            intent,
            _standard_coder(),
            object(),
            text="cancel while active",
        )
    )
    await asyncio.wait_for(waiting.wait(), timeout=2.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(sessions) == 1
    assert sessions[0].closed is True
    assert len(proxies) == 1
    assert proxies[0].close_calls == 1
    assert proxies[0]._token is None
    assert proxies[0]._active is False


@pytest.mark.asyncio
async def test_direct_chatgpt_turn_refreshes_principal_auth_before_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from runtime.execution.codex_backend import role_runner
    from runtime.execution.codex_backend.account import codex_account_home

    workspace, turn, intent = _prepare_driver(tmp_path, monkeypatch)
    turn.params = SimpleNamespace(owner_actor_id="alice", tenant_id="tenant-a")
    state_root = tmp_path / "state"
    scope = TenantScope(tenant_id="tenant-a", actor_id="alice")
    managed_home = codex_account_home(state_root, scope)
    refreshed_home = tmp_path / "refreshed-account-home"
    request = CodexExecutionRequest(
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=workspace.resolve(),
        realm_id="realm",
        tenant_id="tenant-a",
        principal_id="alice",
        prompt="use ChatGPT",
        command=("codex", "app-server", "--listen", "stdio://"),
        source_codex_home=managed_home,
        model="gpt-codex-account",
    )
    refresh_calls: list[tuple[Path, TenantScope | None]] = []

    async def _refresh(*, state_root: Path, scope: TenantScope | None) -> Path:
        refresh_calls.append((state_root, scope))
        return refreshed_home

    monkeypatch.setattr(role_runner, "refresh_codex_execution_auth_home", _refresh)
    monkeypatch.setattr(mod, "_request_for_turn", lambda *_args, **_kwargs: request)
    instances, _operations = _install_fake_session(
        monkeypatch,
        events=[
            Notification(
                "turn/completed",
                {
                    "threadId": "inner-thread",
                    "turn": {"id": "inner-turn", "status": "completed"},
                },
            )
        ],
    )

    assert await mod.drive_codex_app_server(
        _CoderRuntime(),
        turn,
        object(),
        _FakeEmitter(),
        intent,
        _standard_coder(),
        object(),
        text="refresh before execution",
    )

    assert refresh_calls == [(state_root, scope)]
    assert len(instances) == 1
    assert instances[0].request.source_codex_home == refreshed_home

