"""Transport and safety tests for the Codex App Server stdio client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from runtime.execution.codex_backend import (
    ApprovalRequest,
    BackpressureError,
    CodexAppServerClient,
    CodexAppServerConfig,
    ConfigurationError,
    MessageTooLargeError,
    ProcessFactory,
    ProcessLaunch,
    ProtocolError,
    RemoteError,
    RequestTimeoutError,
)
from runtime.execution.codex_backend._transport import decode_message

_CODEX_0_149_FIXTURES = Path(__file__).with_name("fixtures") / "codex_app_server_0_149"


def _codex_0_149_fixture(name: str) -> dict[str, Any]:
    # Fixed from OpenAI Codex rust-v0.149.0 (a4e15bf): protocol/common.rs's
    # serialization test and app-server's strict codex_apps elicitation test.
    value = json.loads((_CODEX_0_149_FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class _FakeReader:
    def __init__(self) -> None:
        self._chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._closed = False

    async def readline(self) -> bytes:
        chunk = await self._chunks.get()
        return b"" if chunk is None else chunk

    async def read(self, _n: int = -1) -> bytes:
        chunk = await self._chunks.get()
        return b"" if chunk is None else chunk

    def feed_message(self, payload: Mapping[str, Any]) -> None:
        self.feed_raw(json.dumps(payload, separators=(",", ":")).encode() + b"\n")

    def feed_raw(self, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("reader is closed")
        self._chunks.put_nowait(payload)

    def feed_eof(self) -> None:
        if not self._closed:
            self._closed = True
            self._chunks.put_nowait(None)


class _FakeWriter:
    def __init__(self, on_close: Callable[[], None]) -> None:
        self.messages: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._on_close = on_close
        self.closed = False

    def write(self, data: bytes) -> None:
        assert data.endswith(b"\n")
        self.messages.put_nowait(json.loads(data))

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._on_close()

    async def wait_closed(self) -> None:
        await asyncio.sleep(0)


class _FakeProcess:
    def __init__(self, *, exit_on_stdin_close: bool = True, exit_on_terminate: bool = True) -> None:
        self.pid = 987_654
        self.stdout = _FakeReader()
        self.stderr = None
        self._returncode: int | None = None
        self._exited = asyncio.Event()
        self.exit_on_stdin_close = exit_on_stdin_close
        self.exit_on_terminate = exit_on_terminate
        self.terminate_calls = 0
        self.kill_calls = 0
        self.stdin = _FakeWriter(self._stdin_closed)

    @property
    def returncode(self) -> int | None:
        return self._returncode

    async def wait(self) -> int:
        await self._exited.wait()
        assert self._returncode is not None
        return self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.exit_on_terminate:
            self.exit(-15)

    def kill(self) -> None:
        self.kill_calls += 1
        self.exit(-9)

    def exit(self, code: int) -> None:
        if self._returncode is None:
            self._returncode = code
            self.stdout.feed_eof()
            self._exited.set()

    def _stdin_closed(self) -> None:
        if self.exit_on_stdin_close:
            self.exit(0)

    async def receive(self) -> dict[str, Any]:
        return await asyncio.wait_for(self.stdin.messages.get(), timeout=1)


class _Factory:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.launches: list[ProcessLaunch] = []

    async def __call__(self, launch: ProcessLaunch) -> _FakeProcess:
        self.launches.append(launch)
        return self.process


async def _start_client(
    *,
    config: CodexAppServerConfig | None = None,
    process: _FakeProcess | None = None,
    approval_handler: Callable[[ApprovalRequest], Any] | None = None,
    dynamic_tool_handler: Callable[[ApprovalRequest], Any] | None = None,
) -> tuple[CodexAppServerClient, _FakeProcess, _Factory]:
    fake = process or _FakeProcess()
    factory = _Factory(fake)
    client = CodexAppServerClient(
        config,
        approval_handler=approval_handler,
        dynamic_tool_handler=dynamic_tool_handler,
        process_factory=cast(ProcessFactory, factory),
    )
    start_task = asyncio.create_task(client.start())
    initialize = await fake.receive()
    assert initialize["method"] == "initialize"
    fake.stdout.feed_message(
        {
            "id": initialize["id"],
            "result": {
                "userAgent": "echo-test",
                "codexHome": "/tmp/codex-home",
                "platformFamily": "unix",
                "platformOs": "macos",
            },
        }
    )
    result = await asyncio.wait_for(start_task, timeout=1)
    assert result["codexHome"] == "/tmp/codex-home"
    initialized = await fake.receive()
    assert initialized == {"method": "initialized"}
    return client, fake, factory


async def _answer_request(
    fake: _FakeProcess,
    operation: Awaitable[Any],
    result: Mapping[str, Any],
) -> tuple[dict[str, Any], Any]:
    task = asyncio.ensure_future(operation)
    request = await fake.receive()
    fake.stdout.feed_message({"id": request["id"], "result": result})
    return request, await asyncio.wait_for(task, timeout=1)


@pytest.mark.asyncio
async def test_handshake_safe_thread_turn_resume_interrupt_and_stream() -> None:
    config = CodexAppServerConfig(
        source_environment={
            "PATH": "/usr/bin",
            "HOME": "/safe/home",
            "OPENAI_API_KEY": "must-not-leak",
        }
    )
    client, fake, factory = await _start_client(config=config)
    try:
        launch = factory.launches[0]
        assert launch.argv == ("codex", "app-server", "--listen", "stdio://")
        assert launch.env == {"PATH": "/usr/bin", "HOME": "/safe/home"}
        assert launch.stream_limit == config.max_message_bytes + 1

        thread_operation = client.start_thread(
            cwd="/workspace", extra_params={"serviceName": "echo"}
        )
        thread_task = asyncio.ensure_future(thread_operation)
        thread_request = await fake.receive()
        assert thread_request["method"] == "thread/start"
        assert thread_request["params"] == {
            "serviceName": "echo",
            "cwd": "/workspace",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
            "sandbox": "workspace-write",
            "ephemeral": False,
        }
        fake.stdout.feed_message(
            {
                "method": "thread/started",
                "params": {"thread": {"id": "thr-1", "status": {"type": "idle"}}},
            }
        )
        fake.stdout.feed_message(
            {"id": thread_request["id"], "result": {"thread": {"id": "thr-1"}}}
        )
        thread_response = await thread_task
        assert thread_response["thread"] == {"id": "thr-1"}
        notification = await client.next_notification(timeout_s=1)
        assert notification.method == "thread/started"

        resume_request, resume_response = await _answer_request(
            fake,
            client.resume_thread("thr-1", cwd="/workspace", exclude_turns=True),
            {"thread": {"id": "thr-1", "turns": []}},
        )
        assert resume_request["method"] == "thread/resume"
        assert resume_request["params"]["approvalPolicy"] == "on-request"
        assert resume_request["params"]["sandbox"] == "workspace-write"
        assert resume_response["thread"]["id"] == "thr-1"

        turn_operation = client.start_turn("thr-1", "implement the change")
        turn_task = asyncio.ensure_future(turn_operation)
        turn_request = await fake.receive()
        assert turn_request["method"] == "turn/start"
        assert turn_request["params"] == {
            "threadId": "thr-1",
            "input": [{"type": "text", "text": "implement the change"}],
        }
        fake.stdout.feed_message(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "msg-1",
                    "delta": "done",
                },
            }
        )
        fake.stdout.feed_message({"id": turn_request["id"], "result": {"turn": {"id": "turn-1"}}})
        turn_response = await turn_task
        turn = turn_response["turn"]
        assert isinstance(turn, dict)
        assert turn["id"] == "turn-1"
        assert (await client.next_notification(timeout_s=1)).params["delta"] == "done"

        interrupt_request, _ = await _answer_request(
            fake,
            client.interrupt("thr-1", "turn-1"),
            {},
        )
        assert interrupt_request == {
            "id": interrupt_request["id"],
            "method": "turn/interrupt",
            "params": {"threadId": "thr-1", "turnId": "turn-1"},
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_plugin_marketplace_list_install_and_uninstall_wire_contract() -> None:
    client, fake, _ = await _start_client()
    try:
        list_request, listed = await _answer_request(
            fake,
            client.list_plugins(
                cwds=["/workspace"],
                force_refetch=True,
                marketplace_kinds=["local", "workspace-directory"],
            ),
            {"marketplaces": [], "featuredPluginIds": []},
        )
        assert list_request == {
            "id": list_request["id"],
            "method": "plugin/list",
            "params": {
                "cwds": ["/workspace"],
                "forceRefetch": True,
                "marketplaceKinds": ["local", "workspace-directory"],
            },
        }
        assert listed["marketplaces"] == []

        install_request, installed = await _answer_request(
            fake,
            client.install_plugin(
                "linear",
                marketplace_path="/safe/marketplace.json",
                install_attempt_id="attempt-1",
            ),
            {"authPolicy": "ON_USE", "appsNeedingAuth": []},
        )
        assert install_request["method"] == "plugin/install"
        assert install_request["params"] == {
            "pluginName": "linear",
            "marketplacePath": "/safe/marketplace.json",
            "installAttemptId": "attempt-1",
        }
        assert installed["authPolicy"] == "ON_USE"

        uninstall_request, _ = await _answer_request(
            fake,
            client.uninstall_plugin("linear@openai-curated"),
            {},
        )
        assert uninstall_request["method"] == "plugin/uninstall"
        assert uninstall_request["params"] == {"pluginId": "linear@openai-curated"}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_custom_permissions_profile_omits_legacy_sandbox_fields() -> None:
    config = CodexAppServerConfig(experimental_api=True)
    client, fake, _ = await _start_client(config=config)
    try:
        start_operation = client.start_thread(
            cwd="/workspace",
            sandbox=None,
            permissions="echo-sidecar",
        )
        start_task = asyncio.create_task(start_operation)
        start_request = await fake.receive()
        assert start_request["params"]["permissions"] == "echo-sidecar"
        assert "sandbox" not in start_request["params"]
        fake.stdout.feed_message(
            {"id": start_request["id"], "result": {"thread": {"id": "thr-permissions"}}}
        )
        await start_task

        resume_operation = client.resume_thread(
            "thr-permissions",
            cwd="/workspace",
            sandbox=None,
            permissions="echo-sidecar",
        )
        resume_task = asyncio.create_task(resume_operation)
        resume_request = await fake.receive()
        assert resume_request["params"]["permissions"] == "echo-sidecar"
        assert "sandbox" not in resume_request["params"]
        fake.stdout.feed_message(
            {"id": resume_request["id"], "result": {"thread": {"id": "thr-permissions"}}}
        )
        await resume_task

        with pytest.raises(ConfigurationError, match="exactly one"):
            await client.start_thread(
                cwd="/workspace",
                permissions="echo-sidecar",
            )
        with pytest.raises(ConfigurationError, match="exactly one"):
            await client.start_thread(cwd="/workspace", sandbox=None)
    finally:
        await client.close()

    client, _fake, _ = await _start_client()
    try:
        with pytest.raises(ConfigurationError, match="experimental_api"):
            await client.start_thread(
                cwd="/workspace",
                sandbox=None,
                permissions="echo-sidecar",
            )
    finally:
        await client.close()


def test_emitted_at_ms_is_allowed_only_as_non_negative_integer() -> None:
    config = CodexAppServerConfig()
    valid = decode_message(
        '{"method":"thread/started","params":{},"emittedAtMs":0}\n',
        config,
    )
    assert valid["emittedAtMs"] == 0

    for invalid in (-1, True, 1.5, "1", None):
        payload = json.dumps({"method": "thread/started", "params": {}, "emittedAtMs": invalid})
        with pytest.raises(ProtocolError, match="emittedAtMs"):
            decode_message(payload, config)


@pytest.mark.asyncio
async def test_server_approval_callback_and_default_fail_closed_responses() -> None:
    seen: list[ApprovalRequest] = []

    async def approve(request: ApprovalRequest) -> Mapping[str, Any]:
        seen.append(request)
        return {"decision": "accept"}

    client, fake, _ = await _start_client(approval_handler=approve)
    try:
        fake.stdout.feed_message(
            {
                "id": "approval-1",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "cmd-1",
                    "startedAtMs": 1,
                    "command": "pytest -q",
                    "cwd": "/workspace",
                },
            }
        )
        assert await fake.receive() == {"id": "approval-1", "result": {"decision": "accept"}}
        assert seen[0].params["command"] == "pytest -q"
    finally:
        await client.close()

    client, fake, _ = await _start_client()
    try:
        fake.stdout.feed_message(
            {
                "id": 40,
                "method": "item/fileChange/requestApproval",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "patch-1",
                    "startedAtMs": 1,
                },
            }
        )
        assert await fake.receive() == {"id": 40, "result": {"decision": "decline"}}
        fake.stdout.feed_message(
            {
                "id": 41,
                "method": "item/permissions/requestApproval",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "perm-1",
                    "startedAtMs": 1,
                    "cwd": "/workspace",
                    "permissions": {"network": {"enabled": True}},
                },
            }
        )
        assert await fake.receive() == {
            "id": 41,
            "result": {"permissions": {}, "scope": "turn"},
        }
        fake.stdout.feed_message(
            {
                "id": 42,
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "itemId": "app-1",
                    "isBlocking": True,
                    "questions": [],
                },
            }
        )
        assert await fake.receive() == {"id": 42, "result": {"answers": {}}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_codex_0_149_apps_elicitation_approval_round_trip() -> None:
    seen: list[ApprovalRequest] = []

    async def approve(request: ApprovalRequest) -> Mapping[str, Any]:
        seen.append(request)
        return {"action": "accept", "content": {}}

    client, fake, _ = await _start_client(approval_handler=approve)
    try:
        request = _codex_0_149_fixture("mcp_apps_approval_request.json")
        fake.stdout.feed_message(request)

        assert await fake.receive() == {
            "id": "mcp-approval-149",
            "result": {"action": "accept", "content": {}},
        }
        assert len(seen) == 1
        assert seen[0].method == "mcpServer/elicitation/request"
        assert seen[0].params["serverName"] == "codex_apps"
        assert seen[0].params["requestedSchema"] == {
            "type": "object",
            "properties": {},
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_request_user_input_is_not_routed_to_approval_provider() -> None:
    seen: list[ApprovalRequest] = []

    async def unsafe_questionnaire_bridge(request: ApprovalRequest) -> Mapping[str, Any]:
        seen.append(request)
        return {"answers": {"approval": {"answers": ["Accept"]}}}

    client, fake, _ = await _start_client(approval_handler=unsafe_questionnaire_bridge)
    try:
        fake.stdout.feed_message(
            {
                "id": "questionnaire-1",
                "method": "item/tool/requestUserInput",
                "params": {
                    "threadId": "inner-thread",
                    "turnId": "inner-turn",
                    "itemId": "app-call-1",
                    "isBlocking": True,
                    "questions": [
                        {
                            "id": "approval",
                            "header": "App",
                            "question": "Allow this?",
                            "options": [
                                {"label": "Accept", "description": "Run it"},
                                {"label": "Decline", "description": "Do not run it"},
                            ],
                        }
                    ],
                },
            }
        )

        assert await fake.receive() == {
            "id": "questionnaire-1",
            "result": {"answers": {}},
        }
        assert seen == []
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_codex_0_149_arbitrary_mcp_form_declines_before_callback() -> None:
    seen: list[ApprovalRequest] = []

    async def must_not_run(request: ApprovalRequest) -> Mapping[str, Any]:
        seen.append(request)
        return {"action": "accept", "content": {}}

    client, fake, _ = await _start_client(approval_handler=must_not_run)
    try:
        request = _codex_0_149_fixture("mcp_form_request.json")
        fake.stdout.feed_message(request)

        assert await fake.receive() == {
            "id": 9,
            "result": {"action": "decline", "content": None},
        }
        assert seen == []
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_response",
    [
        {"action": "accept", "content": {"confirmed": True}},
        {"action": "accept", "content": {}, "_meta": {}},
        {"action": "acceptForSession", "content": {}},
        {"action": "decline", "content": {}},
    ],
    ids=("form-content", "response-meta", "persistent", "decline-content"),
)
async def test_mcp_elicitation_invalid_handler_response_fails_closed(
    unsafe_response: Mapping[str, Any],
) -> None:
    async def unsafe(_request: ApprovalRequest) -> Mapping[str, Any]:
        return unsafe_response

    client, fake, _ = await _start_client(approval_handler=unsafe)
    try:
        fake.stdout.feed_message(_codex_0_149_fixture("mcp_apps_approval_request.json"))
        assert await fake.receive() == {
            "id": "mcp-approval-149",
            "result": {"action": "decline", "content": None},
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_approval_timeout_and_invalid_response_fail_closed() -> None:
    async def too_slow(_request: ApprovalRequest) -> Mapping[str, Any]:
        await asyncio.sleep(1)
        return {"decision": "accept"}

    config = CodexAppServerConfig(approval_timeout_s=0.01)
    client, fake, _ = await _start_client(config=config, approval_handler=too_slow)
    try:
        fake.stdout.feed_message(
            {
                "id": 50,
                "method": "item/commandExecution/requestApproval",
                "params": {},
            }
        )
        assert await fake.receive() == {"id": 50, "result": {"decision": "decline"}}
    finally:
        await client.close()

    def invalid(_request: ApprovalRequest) -> Mapping[str, Any]:
        return {"decision": "run-everything"}

    client, fake, _ = await _start_client(approval_handler=invalid)
    try:
        fake.stdout.feed_message(
            {
                "id": 51,
                "method": "item/fileChange/requestApproval",
                "params": {},
            }
        )
        assert await fake.receive() == {"id": 51, "result": {"decision": "decline"}}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_request_timeout_remote_error_and_late_response_do_not_poison_connection() -> None:
    client, fake, _ = await _start_client()
    try:
        timed_out = asyncio.create_task(client.request("test/slow", {}, timeout_s=0.01))
        slow_request = await fake.receive()
        with pytest.raises(RequestTimeoutError):
            await timed_out

        fake.stdout.feed_message({"id": slow_request["id"], "result": {"late": True}})
        error_task = asyncio.create_task(client.request("test/error", {}))
        error_request = await fake.receive()
        fake.stdout.feed_message(
            {
                "id": error_request["id"],
                "error": {"code": -32600, "message": "bad request", "data": {"field": "x"}},
            }
        )
        with pytest.raises(RemoteError) as exc_info:
            await error_task
        assert exc_info.value.code == -32600
        assert exc_info.value.data == {"field": "x"}

        _, result = await _answer_request(fake, client.request("test/healthy", {}), {"ok": True})
        assert result == {"ok": True}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_pending_request_and_notification_queues_are_bounded() -> None:
    config = CodexAppServerConfig(max_pending_requests=1, notification_queue_size=1)
    client, fake, _ = await _start_client(config=config)
    try:
        first = asyncio.create_task(client.request("test/first", {}))
        first_request = await fake.receive()
        with pytest.raises(BackpressureError):
            await client.request("test/second", {})
        fake.stdout.feed_message({"id": first_request["id"], "result": {}})
        assert await first == {}

        fake.stdout.feed_message({"method": "event/one", "params": {}})
        fake.stdout.feed_message({"method": "event/two", "params": {}})
        with pytest.raises(BackpressureError):
            await client.next_notification(timeout_s=1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_strict_json_duplicate_keys_and_size_limit_fail_connection() -> None:
    client, fake, _ = await _start_client()
    try:
        fake.stdout.feed_raw(b'{"method":"event/one","method":"event/two","params":{}}\n')
        with pytest.raises(ProtocolError, match="duplicate"):
            await client.next_notification(timeout_s=1)
    finally:
        await client.close()

    config = CodexAppServerConfig(max_message_bytes=512)
    client, fake, _ = await _start_client(config=config)
    try:
        with pytest.raises(MessageTooLargeError, match="outbound"):
            await client.request("test/huge", {"blob": "x" * 600})
        assert fake.stdin.messages.empty()
        with pytest.raises(ConfigurationError, match="timeout"):
            await client.request("test/invalid-timeout", {}, timeout_s=0)
        assert fake.stdin.messages.empty()

        fake.stdout.feed_raw(b"{" + b"x" * 511 + b"}\n")
        with pytest.raises(MessageTooLargeError):
            await client.next_notification(timeout_s=1)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_unknown_server_request_gets_method_not_found_without_callback() -> None:
    client, fake, _ = await _start_client()
    try:
        fake.stdout.feed_message({"id": "server-1", "method": "danger/newPrompt", "params": {}})
        assert await fake.receive() == {
            "id": "server-1",
            "error": {"code": -32601, "message": "unsupported server request: danger/newPrompt"},
        }
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_dynamic_tool_server_request_uses_bounded_fail_closed_handler() -> None:
    seen: list[ApprovalRequest] = []

    async def dynamic_tool(request: ApprovalRequest) -> Mapping[str, Any]:
        seen.append(request)
        return {
            "contentItems": [{"type": "inputText", "text": "registry result"}],
            "success": True,
        }

    client, fake, _ = await _start_client(dynamic_tool_handler=dynamic_tool)
    try:
        fake.stdout.feed_message(
            {
                "id": "tool-1",
                "method": "item/tool/call",
                "params": {
                    "threadId": "thr-1",
                    "turnId": "turn-1",
                    "callId": "call-1",
                    "tool": "read_file",
                    "arguments": {"path": "README.md"},
                },
            }
        )
        assert await fake.receive() == {
            "id": "tool-1",
            "result": {
                "contentItems": [{"type": "inputText", "text": "registry result"}],
                "success": True,
            },
        }
        assert seen[0].params["callId"] == "call-1"
    finally:
        await client.close()

    client, fake, _ = await _start_client()
    try:
        fake.stdout.feed_message({"id": "tool-2", "method": "item/tool/call", "params": {}})
        response = await fake.receive()
        assert response["id"] == "tool-2"
        assert response["result"]["success"] is False
        assert len(response["result"]["contentItems"][0]["text"]) < 8_000
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_dynamic_tool_handler_exception_is_redacted_from_response_and_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(_request: ApprovalRequest) -> Mapping[str, Any]:
        raise RuntimeError("token=super-secret /private/host/path")

    client, fake, _ = await _start_client(dynamic_tool_handler=broken)
    try:
        fake.stdout.feed_message({"id": "tool-secret", "method": "item/tool/call", "params": {}})
        response = await fake.receive()
        assert response["id"] == "tool-secret"
        assert response["result"]["success"] is False
        assert "super-secret" not in response["result"]["contentItems"][0]["text"]
        assert "super-secret" not in caplog.text
        assert "/private/host/path" not in caplog.text
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_close_escalates_from_graceful_eof_to_terminate_then_hard_kill() -> None:
    fake = _FakeProcess(exit_on_stdin_close=False, exit_on_terminate=False)
    config = CodexAppServerConfig(
        close_grace_s=0.01,
        terminate_grace_s=0.01,
        kill_wait_s=0.1,
    )
    client, fake, _ = await _start_client(config=config, process=fake)

    await client.close()

    assert fake.stdin.closed is True
    assert fake.terminate_calls == 1
    assert fake.kill_calls == 1
    assert fake.returncode == -9
    assert client.ready is False


def test_environment_overrides_require_explicit_allowlist() -> None:
    with pytest.raises(ConfigurationError, match="not allowlisted"):
        CodexAppServerConfig(env_overrides={"OPENAI_API_KEY": "secret"})

    config = CodexAppServerConfig(
        env_allowlist=frozenset({"PATH", "OPENAI_API_KEY"}),
        env_overrides={"OPENAI_API_KEY": "explicit-secret"},
        source_environment={"PATH": "/bin", "UNRELATED_SECRET": "never"},
    )
    assert config.env_overrides["OPENAI_API_KEY"] == "explicit-secret"

