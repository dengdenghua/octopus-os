"""High-level lifecycle tests for the isolated Codex execution backend."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal, cast

import pytest

from runtime.execution.codex_backend import (
    CodexBackendUnavailable,
    CodexExecutionRequest,
    CodexExecutionSession,
)
from runtime.execution.codex_backend.security import (
    CodexSecurityError,
    CodexThreadBinding,
)
from runtime.execution.codex_backend.types import (
    ApprovalHandler,
    CodexAppServerConfig,
    JsonValue,
    Notification,
    RemoteError,
)
from runtime.safety.approval.approval_gate import AutoDenyProvider
from runtime.safety.sandboxing.sandbox import (
    BackendChoice,
    DirectBackend,
    SandboxPolicy,
    SandboxViolation,
    SeatbeltBackend,
)


@dataclass
class _FakeContext:
    workspace: Path
    thread_root: Path
    codex_home: Path
    task_root: Path
    scratch_root: Path
    sandbox_mode: str = "workspace-write"
    cleaned: bool = False
    config_validated: bool = False

    def launch_env(self) -> dict[str, str]:
        return {
            "PATH": "/safe/bin",
            "CODEX_HOME": "/isolated/codex-home",
            "HOME": "/isolated/app-home",
        }

    def thread_start_security_overrides(self) -> dict[str, object]:
        return {
            "cwd": str(self.workspace),
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "permissions": "echo-sidecar",
            "runtimeWorkspaceRoots": [str(self.workspace)],
            "dynamicTools": [],
            "selectedCapabilityRoots": [],
        }

    def turn_start_security_overrides(self) -> dict[str, object]:
        return {
            "cwd": str(self.workspace),
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "permissions": "echo-sidecar",
            "runtimeWorkspaceRoots": [str(self.workspace)],
        }

    def validate_effective_config(self, response: Mapping[str, object]) -> None:
        assert response == {"config": {"safe": True}}
        self.config_validated = True

    def cleanup(self) -> None:
        self.cleaned = True


@dataclass
class _FakeSecurity:
    context: _FakeContext
    require_hard: bool = False
    binding: CodexThreadBinding | None = None
    read_error: BaseException | None = None
    prepare_kwargs: dict[str, Any] | None = None
    seed_calls: list[tuple[Path, str]] = field(default_factory=list)
    writes: list[tuple[str, str, bool]] = field(default_factory=list)

    @property
    def policy(self) -> SimpleNamespace:
        return SimpleNamespace(outer_hard_sandbox_required=self.require_hard)

    def prepare(self, **kwargs: Any) -> _FakeContext:
        self.prepare_kwargs = kwargs
        return self.context

    def seed_auth_from_codex_home(
        self,
        _context: _FakeContext,
        *,
        source_codex_home: Path,
        authority: str,
    ) -> bool:
        self.seed_calls.append((source_codex_home, authority))
        return True

    def read_server_binding(
        self,
        _context: _FakeContext,
        *,
        authority: str,
    ) -> CodexThreadBinding | None:
        assert authority == "server"
        if self.read_error is not None:
            raise self.read_error
        return self.binding

    def write_server_binding(
        self,
        _context: _FakeContext,
        *,
        inner_thread_id: str,
        authority: str,
        replace: bool = False,
    ) -> CodexThreadBinding:
        self.writes.append((inner_thread_id, authority, replace))
        return _binding(inner_thread_id)


class _FakeClient:
    def __init__(
        self,
        config: CodexAppServerConfig,
        approval_handler: ApprovalHandler | None,
    ) -> None:
        self.config = config
        self.approval_handler = approval_handler
        self.start_error: BaseException | None = None
        self.config_error: BaseException | None = None
        self.resume_error: BaseException | None = None
        self.thread_start_error: BaseException | None = None
        self.turn_start_error: BaseException | None = None
        self.resume_result: dict[str, JsonValue] = {"thread": {"id": "inner-existing"}}
        self.thread_start_result: dict[str, JsonValue] = {"thread": {"id": "inner-new"}}
        self.turn_start_result: dict[str, JsonValue] = {"turn": {"id": "turn-new"}}
        self.events: list[Notification] = []
        self.calls: list[tuple[str, Any]] = []
        self.interrupt_calls: list[tuple[str, str, float | None]] = []
        self.closed = False
        self._notification_index = 0

    async def start(self) -> dict[str, JsonValue]:
        self.calls.append(("initialize", None))
        if self.start_error is not None:
            raise self.start_error
        return {"userAgent": "fake"}

    async def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> JsonValue:
        self.calls.append((method, (dict(params or {}), timeout_s)))
        if method == "config/read":
            if self.config_error is not None:
                raise self.config_error
            return {"config": {"safe": True}}
        if method == "turn/steer":
            return {"turnId": "turn-new"}
        raise AssertionError(f"unexpected request: {method}")

    async def resume_thread(self, thread_id: str, **kwargs: Any) -> dict[str, JsonValue]:
        self.calls.append(("thread/resume", (thread_id, kwargs)))
        if self.resume_error is not None:
            raise self.resume_error
        return dict(self.resume_result)

    async def start_thread(self, **kwargs: Any) -> dict[str, JsonValue]:
        self.calls.append(("thread/start", kwargs))
        if self.thread_start_error is not None:
            raise self.thread_start_error
        return dict(self.thread_start_result)

    async def start_turn(
        self,
        thread_id: str,
        prompt: str,
        **kwargs: Any,
    ) -> dict[str, JsonValue]:
        self.calls.append(("turn/start", (thread_id, prompt, kwargs)))
        if self.turn_start_error is not None:
            raise self.turn_start_error
        return dict(self.turn_start_result)

    async def notifications(self) -> AsyncIterator[Notification]:
        for event in self.events:
            yield event

    async def next_notification(self, *, timeout_s: float | None = None) -> Notification:
        self.calls.append(("next_notification", timeout_s))
        event = self.events[self._notification_index]
        self._notification_index += 1
        return event

    async def interrupt(
        self,
        thread_id: str,
        turn_id: str,
        *,
        timeout_s: float | None = None,
    ) -> None:
        self.interrupt_calls.append((thread_id, turn_id, timeout_s))

    async def close(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.client = _FakeClient(CodexAppServerConfig(), None)

    def __call__(
        self,
        config: CodexAppServerConfig,
        *,
        approval_handler: ApprovalHandler | None = None,
        dynamic_tool_handler: ApprovalHandler | None = None,
    ) -> _FakeClient:
        self.client.config = config
        self.client.approval_handler = approval_handler
        self.client.dynamic_tool_handler = dynamic_tool_handler
        return self.client


@dataclass
class _RecordingBackend:
    enforcement_level: Literal["full", "partial", "none"] = "full"
    fail_transform: bool = False
    calls: list[tuple[list[str], dict[str, str], Path, SandboxPolicy]] = field(default_factory=list)

    def enforcement(self, _policy: SandboxPolicy) -> Literal["full", "partial", "none"]:
        return self.enforcement_level

    def transform(
        self,
        argv: list[str],
        env: dict[str, str],
        cwd: Path,
        policy: SandboxPolicy,
    ) -> tuple[list[str], dict[str, str], Path]:
        self.calls.append((list(argv), dict(env), cwd, policy))
        if self.fail_transform:
            raise SandboxViolation("fake backend refused launch")
        return (
            ["/sandbox-wrapper", "--", *argv],
            {**env, "ECHO_OUTER_SANDBOX": "1"},
            cwd,
        )


def _binding(inner_thread_id: str = "inner-existing") -> CodexThreadBinding:
    return CodexThreadBinding(
        inner_thread_id=inner_thread_id,
        cwd_hash="a" * 64,
        realm_hash="b" * 64,
        tenant_hash="c" * 64,
        thread_hash="d" * 64,
    )


def _make_session(
    tmp_path: Path,
    *,
    binding: CodexThreadBinding | None = None,
    source_auth: bool = False,
    require_hard: bool = False,
    process_backend: BackendChoice | None = None,
    selected_app_ids: tuple[str, ...] = (),
    app_mentions: tuple[tuple[str, str], ...] = (),
) -> tuple[CodexExecutionSession, _FakeSecurity, _FakeContext, _Factory, _FakeClient]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    source_home = tmp_path / "source-codex" if source_auth else None
    if source_home is not None:
        source_home.mkdir(exist_ok=True)
    thread_root = tmp_path / "state" / "thread"
    task_root = thread_root / "tasks" / "outer-turn"
    scratch_root = tmp_path / "state" / "scratch" / "outer-turn"
    for directory in (thread_root, task_root, scratch_root):
        directory.mkdir(parents=True, exist_ok=True)
    context = _FakeContext(
        workspace=workspace,
        thread_root=thread_root,
        codex_home=thread_root / "codex-home",
        task_root=task_root,
        scratch_root=scratch_root,
    )
    context.codex_home.mkdir(parents=True, exist_ok=True)
    security = _FakeSecurity(
        context=context,
        binding=binding,
        require_hard=require_hard,
    )
    factory = _Factory()
    request = CodexExecutionRequest(
        outer_thread_id="outer-thread",
        outer_turn_id="outer-turn",
        workspace=workspace,
        realm_id="realm-a",
        tenant_id="tenant-a",
        principal_id="user-a",
        prompt="Inspect the project",
        command=("/opt/codex", "app-server", "--listen", "stdio://"),
        source_codex_home=source_home,
        model="gpt-test",
        effort="high",
        host_env={"PATH": "/host/bin", "SECRET": "must-not-pass"},
        selected_app_ids=selected_app_ids,
        app_mentions=app_mentions,
    )
    session = CodexExecutionSession(
        request,
        security=cast(Any, security),
        approval_provider=AutoDenyProvider(),
        is_interrupted=lambda: False,
        client_factory=cast(Any, factory),
        process_backend=process_backend,
    )
    return session, security, context, factory, factory.client


@pytest.mark.asyncio
async def test_app_mention_is_sent_as_typed_turn_input(tmp_path: Path) -> None:
    session, _security, _context, _factory, client = _make_session(
        tmp_path,
        selected_app_ids=("google_drive",),
        app_mentions=(("google_drive", "Google Drive"),),
    )

    await session.start()

    turn_call = next(value for name, value in client.calls if name == "turn/start")
    assert turn_call[1] == [
        {"type": "text", "text": "Inspect the project"},
        {
            "type": "mention",
            "name": "Google Drive",
            "path": "app://google_drive",
        },
    ]


@pytest.mark.asyncio
async def test_first_start_is_isolated_durable_and_binds_approval_scope(tmp_path: Path) -> None:
    session, security, context, _factory, client = _make_session(tmp_path, source_auth=True)

    await session.start()

    assert session.inner_thread_id == "inner-new"
    assert session.inner_turn_id == "turn-new"
    assert session.turn_started is True
    assert session.resumed is False
    assert session.auth_seeded is True
    assert context.config_validated is True
    assert security.seed_calls == [(tmp_path / "source-codex", "server")]
    assert security.writes == [("inner-new", "server", False)]
    assert security.prepare_kwargs is not None
    assert security.prepare_kwargs["tenant_id"] != "tenant-a"
    assert "user-a" in security.prepare_kwargs["tenant_id"]

    thread_call = next(value for name, value in client.calls if name == "thread/start")
    assert thread_call["ephemeral"] is False
    assert thread_call["approval_policy"] == "on-request"
    assert thread_call["sandbox"] is None
    assert thread_call["permissions"] == "echo-sidecar"
    assert thread_call["extra_params"] == {
        "runtimeWorkspaceRoots": [str(tmp_path / "workspace")],
        "dynamicTools": [],
        "selectedCapabilityRoots": [],
    }
    turn_call = next(value for name, value in client.calls if name == "turn/start")
    assert turn_call[0:2] == ("inner-new", "Inspect the project")
    assert turn_call[2]["extra_params"]["effort"] == "high"
    assert turn_call[2]["extra_params"]["model"] == "gpt-test"
    assert set(turn_call[2]["extra_params"]) == {
        "cwd",
        "runtimeWorkspaceRoots",
        "approvalPolicy",
        "approvalsReviewer",
        "permissions",
        "model",
        "effort",
    }
    assert client.config.source_environment == {}
    assert client.config.env_overrides == context.launch_env()
    assert client.config.env_allowlist == frozenset(context.launch_env())
    assert client.approval_handler is not None

    await session.close()
    assert client.closed is True
    assert context.cleaned is True


@pytest.mark.asyncio
async def test_environment_ids_are_never_forwarded_even_if_context_regresses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, _security, context, _factory, client = _make_session(tmp_path)
    original_thread = context.thread_start_security_overrides
    original_turn = context.turn_start_security_overrides
    monkeypatch.setattr(
        context,
        "thread_start_security_overrides",
        lambda: {**original_thread(), "environments": [{"environmentId": "remote"}]},
    )
    monkeypatch.setattr(
        context,
        "turn_start_security_overrides",
        lambda: {**original_turn(), "environments": [{"environmentId": "remote"}]},
    )

    await session.start()

    thread_call = next(value for name, value in client.calls if name == "thread/start")
    turn_call = next(value for name, value in client.calls if name == "turn/start")
    assert "environments" not in thread_call["extra_params"]
    assert "environments" not in turn_call[2]["extra_params"]
    await session.close()


@pytest.mark.asyncio
async def test_local_seatbelt_is_bypassed_for_nested_codex_tool_sandbox(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        process_backend=BackendChoice(
            backend=SeatbeltBackend(),
            name="seatbelt",
            hard=True,
            strict=False,
        ),
    )

    with caplog.at_level("WARNING", logger="runtime.execution.codex_backend.backend"):
        await session.start()

    assert security.prepare_kwargs is not None
    assert security.prepare_kwargs["outer_hard_sandbox_active"] is False
    assert client.config.command == (
        "/opt/codex",
        "app-server",
        "--listen",
        "stdio://",
    )
    assert session.outer_sandbox == "none_due_to_nested_incompatibility"
    assert "outer_sandbox=none_due_to_nested_incompatibility" in caplog.text
    await session.close()


@pytest.mark.asyncio
async def test_local_direct_backend_remains_compatible(tmp_path: Path) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        process_backend=BackendChoice(
            backend=DirectBackend(),
            name="direct",
            hard=False,
        ),
    )

    await session.start()

    assert security.prepare_kwargs is not None
    assert security.prepare_kwargs["outer_hard_sandbox_active"] is False
    assert client.config.command == (
        "/opt/codex",
        "app-server",
        "--listen",
        "stdio://",
    )
    assert session.outer_sandbox == "direct"
    await session.close()


@pytest.mark.asyncio
async def test_production_seatbelt_rejects_before_prepare_or_process_start(
    tmp_path: Path,
) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        require_hard=True,
        process_backend=BackendChoice(
            backend=SeatbeltBackend(),
            name="seatbelt",
            hard=True,
            strict=False,
        ),
    )

    with pytest.raises(CodexSecurityError, match="nested-incompatible"):
        await session.start()

    assert security.prepare_kwargs is None
    assert session.context is None
    assert session.outer_sandbox == "rejected_due_to_nested_incompatibility"
    assert client.calls == []


@pytest.mark.asyncio
async def test_explicit_strict_seatbelt_does_not_take_local_bypass(tmp_path: Path) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        process_backend=BackendChoice(
            backend=SeatbeltBackend(),
            name="seatbelt",
            hard=True,
            strict=True,
        ),
    )

    with pytest.raises(CodexSecurityError, match="nested-incompatible"):
        await session.start()

    assert security.prepare_kwargs is None
    assert session.outer_sandbox == "rejected_due_to_nested_incompatibility"
    assert client.calls == []


@pytest.mark.asyncio
async def test_production_launch_is_transformed_by_full_backend(tmp_path: Path) -> None:
    backend = _RecordingBackend()
    choice = BackendChoice(backend=backend, name="fake-full", hard=True, strict=True)
    session, security, context, _factory, client = _make_session(
        tmp_path,
        require_hard=True,
        process_backend=choice,
    )

    await session.start()

    assert security.prepare_kwargs is not None
    assert security.prepare_kwargs["outer_hard_sandbox_active"] is True
    assert len(backend.calls) == 1
    raw_argv, raw_env, raw_cwd, policy = backend.calls[0]
    assert raw_argv == ["/opt/codex", "app-server", "--listen", "stdio://"]
    assert raw_env == context.launch_env()
    assert raw_cwd == context.workspace
    assert policy.allow_network is True
    assert policy.mode == "workspace-write"
    assert policy.additional_write_roots == (
        context.codex_home.resolve(),
        context.task_root.resolve(),
        context.scratch_root.resolve(),
    )
    assert client.config.command[:2] == ("/sandbox-wrapper", "--")
    assert client.config.command[2:] == tuple(raw_argv)
    assert client.config.env_overrides["ECHO_OUTER_SANDBOX"] == "1"
    await session.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "choice, message",
    [
        (None, "actual outer sandbox backend"),
        (
            BackendChoice(
                backend=DirectBackend(),
                name="direct",
                hard=False,
            ),
            "full-enforcement hard sandbox",
        ),
        (
            BackendChoice(
                backend=_RecordingBackend(enforcement_level="partial"),
                name="fake-partial",
                hard=True,
                strict=True,
            ),
            "full-enforcement hard sandbox",
        ),
    ],
)
async def test_production_rejects_missing_or_partial_backend_before_client_start(
    tmp_path: Path,
    choice: BackendChoice | None,
    message: str,
) -> None:
    session, _security, context, _factory, client = _make_session(
        tmp_path,
        require_hard=True,
        process_backend=choice,
    )

    with pytest.raises(CodexSecurityError, match=message):
        await session.start()

    assert client.calls == []
    assert context.cleaned is True


@pytest.mark.asyncio
async def test_production_transform_failure_is_fail_closed(tmp_path: Path) -> None:
    backend = _RecordingBackend(fail_transform=True)
    session, _security, context, _factory, client = _make_session(
        tmp_path,
        require_hard=True,
        process_backend=BackendChoice(
            backend=backend,
            name="fake-full",
            hard=True,
            strict=True,
        ),
    )

    with pytest.raises(CodexSecurityError, match="outer sandbox transform failed"):
        await session.start()

    assert client.calls == []
    assert context.cleaned is True


@pytest.mark.asyncio
async def test_existing_binding_resumes_without_rewriting(tmp_path: Path) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        binding=_binding(),
    )

    await session.start()

    assert session.resumed is True
    assert security.writes == []
    resume = next(value for name, value in client.calls if name == "thread/resume")
    assert resume[0] == "inner-existing"
    assert resume[1]["exclude_turns"] is True
    assert resume[1]["sandbox"] is None
    assert resume[1]["permissions"] == "echo-sidecar"
    assert resume[1]["extra_params"] == {
        "runtimeWorkspaceRoots": [str(tmp_path / "workspace")],
        "dynamicTools": [],
        "selectedCapabilityRoots": [],
    }
    assert not any(name == "thread/start" for name, _value in client.calls)
    await session.close()


@pytest.mark.asyncio
async def test_resume_reasserts_the_exact_current_dynamic_tool_catalog(tmp_path: Path) -> None:
    session, _security, _context, _factory, client = _make_session(
        tmp_path,
        binding=_binding(),
    )
    spec = {
        "type": "function",
        "name": "read_file",
        "description": "Read one allowed file.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    }
    session.request = replace(
        session.request,
        dynamic_tools=(spec,),
        dynamic_tool_handler=lambda _request: {
            "contentItems": [{"type": "inputText", "text": "ok"}],
            "success": True,
        },
    )

    await session.start()

    resume = next(value for name, value in client.calls if name == "thread/resume")
    assert resume[1]["extra_params"]["dynamicTools"] == [spec]
    await session.close()


@pytest.mark.asyncio
async def test_exact_thread_not_found_replaces_binding_but_other_error_does_not(
    tmp_path: Path,
) -> None:
    session, security, _context, _factory, client = _make_session(
        tmp_path,
        binding=_binding(),
    )
    client.resume_error = RemoteError(-32600, "thread not found: inner-existing")

    await session.start()

    assert security.writes == [("inner-new", "server", True)]
    assert session.inner_thread_id == "inner-new"
    await session.close()

    second_path = tmp_path / "other-error"
    second_path.mkdir()
    failed, failed_security, failed_context, _factory, failed_client = _make_session(
        second_path,
        binding=_binding(),
    )
    error = RemoteError(-32600, "thread inner-existing is closing")
    failed_client.resume_error = error

    with pytest.raises(RemoteError) as exc_info:
        await failed.start()
    assert exc_info.value is error
    assert failed_security.writes == []
    assert not any(name == "thread/start" for name, _value in failed_client.calls)
    assert failed_client.closed is True
    assert failed_context.cleaned is True


@pytest.mark.asyncio
async def test_binding_mismatch_is_security_failure_not_unavailable(tmp_path: Path) -> None:
    session, security, context, _factory, client = _make_session(tmp_path)
    security.read_error = CodexSecurityError("binding identity mismatch")

    with pytest.raises(CodexSecurityError, match="binding identity mismatch"):
        await session.start()

    assert session.turn_started is False
    assert client.closed is True
    assert context.cleaned is True


@pytest.mark.asyncio
async def test_pre_turn_missing_api_is_unavailable_but_turn_start_error_never_is(
    tmp_path: Path,
) -> None:
    session, _security, context, _factory, client = _make_session(tmp_path)
    client.config_error = RemoteError(-32601, "Method not found")

    with pytest.raises(CodexBackendUnavailable):
        await session.start()
    assert session.turn_started is False
    assert context.cleaned is True

    startup_path = tmp_path / "missing-executable"
    startup_path.mkdir()
    unavailable, _security, unavailable_context, _factory, unavailable_client = _make_session(
        startup_path
    )
    unavailable_client.start_error = FileNotFoundError("codex")

    with pytest.raises(CodexBackendUnavailable):
        await unavailable.start()
    assert unavailable.turn_started is False
    assert unavailable_client.closed is True
    assert unavailable_context.cleaned is True

    second_path = tmp_path / "turn-error"
    second_path.mkdir()
    failed, _security, failed_context, _factory, failed_client = _make_session(second_path)
    turn_error = RemoteError(-32601, "Method not found")
    failed_client.turn_start_error = turn_error

    with pytest.raises(RemoteError) as exc_info:
        await failed.start()
    assert exc_info.value is turn_error
    assert failed.turn_started is True
    assert failed.inner_thread_id == "inner-new"
    assert failed.inner_turn_id is None
    assert failed_client.closed is True
    assert failed_context.cleaned is True


@pytest.mark.asyncio
async def test_interrupt_steer_and_notification_scope(tmp_path: Path) -> None:
    session, _security, _context, _factory, client = _make_session(tmp_path)
    client.events = [
        Notification(
            method="item/agentMessage/delta",
            params={"threadId": "other", "turnId": "turn-new", "delta": "wrong"},
        ),
        Notification(
            method="item/reasoning/textDelta",
            params={"threadId": "inner-new", "turnId": "other", "delta": "wrong"},
        ),
        Notification(
            method="item/agentMessage/delta",
            params={"threadId": "inner-new", "turnId": "turn-new", "delta": "right"},
        ),
        Notification(
            method="thread/tokenUsage/updated",
            params={"threadId": "inner-new", "tokenUsage": {}},
        ),
        Notification(
            method="turn/completed",
            params={"threadId": "inner-new", "turn": {"id": "turn-new"}},
        ),
    ]
    await session.start()

    events = [event async for event in session.notifications()]
    assert [event.method for event in events] == [
        "item/agentMessage/delta",
        "thread/tokenUsage/updated",
        "turn/completed",
    ]
    next_event = await session.next_notification(timeout_s=1.0)
    assert next_event.params["delta"] == "right"

    await session.interrupt(timeout_s=2.5)
    assert client.interrupt_calls == [("inner-new", "turn-new", 2.5)]
    await session.steer("Use the smaller fix", timeout_s=3.0)
    steer = next(value for name, value in client.calls if name == "turn/steer")
    assert steer == (
        {
            "threadId": "inner-new",
            "expectedTurnId": "turn-new",
            "input": [{"type": "text", "text": "Use the smaller fix"}],
        },
        3.0,
    )
    await session.close()

