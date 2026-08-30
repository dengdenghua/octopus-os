from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from runtime.execution.codex_backend.backend import CodexExecutionRequest, CodexExecutionSession
from runtime.execution.codex_backend.command import resolve_codex_app_server_command
from runtime.execution.codex_backend.responses_proxy import (
    CodexResponsesScope,
    ScopedResponsesProxy,
    load_or_create_compaction_key,
)
from runtime.execution.codex_backend.security import CodexSecurityPolicy, CodexSidecarSecurity
from runtime.execution.codex_backend.types import (
    ApprovalRequest,
    CodexAppServerConfig,
    ConfigurationError,
)
from runtime.platform.models.llm import ModelRequest, ModelResponse, ToolCall
from runtime.platform.process.session import Session, current_session
from runtime.safety.approval.approval_gate import AutoDenyProvider


class _RecordingRouter:
    def __init__(self, *, tools: bool = True) -> None:
        self.requests: list[ModelRequest] = []
        self.sessions: list[Session | None] = []
        self._tools = tools

    def call(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        self.sessions.append(current_session())
        calls = (
            [
                ToolCall(id="call-function", name="lookup", input={"query": "echo"}),
                ToolCall(
                    id="call-custom",
                    name="apply_patch",
                    input={"input": "*** Begin Patch\n*** End Patch"},
                ),
                ToolCall(
                    id="call-shell",
                    name="local_shell",
                    input={"action": {"type": "exec", "command": ["pwd"]}},
                ),
            ]
            if self._tools
            else []
        )
        return ModelResponse(
            text="proxy response",
            tool_calls=calls,
            input_tokens=17,
            output_tokens=9,
            cache_read_tokens=3,
            model=request.model,
            provider="test",
        )


class _RealToolLoopRouter:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            names = {tool.name for tool in request.tools}
            if "probe" not in names:
                raise AssertionError(f"real Codex request omitted dynamic tool: {sorted(names)}")
            return ModelResponse(
                text="",
                tool_calls=[ToolCall(id="call-probe", name="probe", input={"value": "ping"})],
                input_tokens=11,
                output_tokens=4,
            )
        return ModelResponse(
            text="real proxy tool loop complete",
            input_tokens=13,
            output_tokens=6,
        )


def _scope(*, turn: str = "turn-a", model: str = "deepseek-chat") -> CodexResponsesScope:
    return CodexResponsesScope(
        tenant_id="tenant-a",
        principal_id="alice",
        thread_id="thread-a",
        turn_id=turn,
        model=model,
    )


def _tool_history_payload(*, model: str = "deepseek-chat", suffix: str = "") -> dict[str, Any]:
    return {
        "model": model,
        "instructions": "Act as the Coder role.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"fix it{suffix}"}],
            },
            {
                "type": "function_call",
                "call_id": "prior-function",
                "name": "lookup",
                "arguments": '{"query":"before"}',
            },
            {
                "type": "function_call_output",
                "call_id": "prior-function",
                "output": "lookup result",
            },
            {
                "type": "custom_tool_call",
                "call_id": "prior-patch",
                "name": "apply_patch",
                "input": "*** Begin Patch\n*** End Patch",
            },
            {
                "type": "custom_tool_call_output",
                "call_id": "prior-patch",
                "output": [{"type": "input_text", "text": "patch applied"}],
            },
            {
                "type": "local_shell_call",
                "call_id": "prior-shell",
                "action": {"type": "exec", "command": ["pwd"]},
            },
            {
                "type": "function_call_output",
                "call_id": "prior-shell",
                "output": "/workspace",
            },
            {
                "type": "reasoning",
                "encrypted_content": "ignored-but-not-rejected",
                "summary": [],
            },
            {"type": "response_metadata", "safe_future_field": True},
        ],
        "tools": [
            {
                "type": "function",
                "name": "lookup",
                "description": "Look up a value.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "type": "custom",
                "name": "apply_patch",
                "description": "Apply a patch.",
                "format": {"type": "text"},
            },
            {"type": "local_shell"},
        ],
        "reasoning": {"effort": "high"},
        "stream": True,
    }


async def _post(
    profile: Any,
    payload: dict[str, Any],
    *,
    token: str | None = None,
    endpoint: str = "responses",
) -> tuple[int, dict[str, str], bytes]:
    parsed = urlsplit(profile.base_url)
    reader, writer = await asyncio.open_connection(parsed.hostname, parsed.port)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    bearer = token if token is not None else profile.scoped_bearer_token
    request = (
        f"POST {parsed.path}/{endpoint} HTTP/1.1\r\n"
        f"Host: {parsed.hostname}:{parsed.port}\r\n"
        f"Authorization: Bearer {bearer}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode() + body
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    head, response_body = response.split(b"\r\n\r\n", 1)
    lines = head.decode("ascii").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers = {
        name.strip().lower(): value.strip()
        for name, value in (line.split(":", 1) for line in lines[1:])
    }
    return status, headers, response_body


@pytest.mark.asyncio
async def test_proxy_normalizes_real_tool_history_and_emits_codex_sse() -> None:
    router = _RecordingRouter()
    trusted = Session(
        actor="alice",
        thread_id="thread-a",
        turn_id="turn-a",
        metadata={"tenant_id": "tenant-a"},
    )
    async with ScopedResponsesProxy(
        router,
        scope=_scope(),
        trusted_session=trusted,
    ) as proxy:
        status, headers, body = await _post(proxy.provider_profile, _tool_history_payload())

    assert status == 200
    assert headers["content-type"] == "text/event-stream"
    rendered = body.decode()
    assert "response.output_item.done" in rendered
    assert '"type":"message"' in rendered
    assert '"phase":"commentary"' in rendered
    assert '"type":"function_call"' in rendered
    assert '"type":"custom_tool_call"' in rendered
    assert '"type":"local_shell_call"' in rendered
    assert '"total_tokens":26' in rendered
    request = router.requests[0]
    assert request.model == "deepseek-chat"
    assert request.reasoning_effort == "high"
    assert [tool.name for tool in request.tools] == ["lookup", "apply_patch", "local_shell"]
    structured = [
        message.content for message in request.messages if isinstance(message.content, list)
    ]
    assert any(blocks[0].get("name") == "apply_patch" for blocks in structured)
    assert any(blocks[0].get("name") == "local_shell" for blocks in structured)
    assert sum(blocks[0].get("type") == "tool_result" for blocks in structured) == 3
    assert router.sessions == [trusted]


def test_responses_translation_preserves_assistant_phase_metadata() -> None:
    from runtime.execution.codex_backend.responses_proxy import (
        model_response_to_responses,
        responses_payload_to_model_request,
    )

    payload = {
        "model": "deepseek-chat",
        "input": [
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "working"}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "continue"}],
            },
        ],
        "stream": False,
    }

    request, projections = responses_payload_to_model_request(
        payload,
        expected_model="deepseek-chat",
    )
    assert request.messages[0].phase == "commentary"
    assert request.messages[1].phase is None

    rendered = model_response_to_responses(
        ModelResponse(text="done"),
        model="deepseek-chat",
        projections=projections,
    )
    assert rendered["output"][0]["phase"] == "final_answer"


def test_responses_translation_keeps_plaintext_compaction_state() -> None:
    from runtime.execution.codex_backend.responses_proxy import (
        responses_payload_to_model_request,
    )

    request, _ = responses_payload_to_model_request(
        {
            "model": "deepseek-chat",
            "input": [
                {
                    "type": "compaction",
                    "summary": "Goal fixed; runtime/a.py edited; tests still pending.",
                },
                {"type": "message", "role": "user", "content": "continue"},
            ],
        },
        expected_model="deepseek-chat",
    )

    rendered = "\n".join(str(message.content) for message in request.messages)
    assert "<provider-compaction>" in rendered
    assert "runtime/a.py edited" in rendered


def test_responses_translation_marks_opaque_compaction_for_regrounding() -> None:
    from runtime.execution.codex_backend.responses_proxy import (
        responses_payload_to_model_request,
    )

    request, _ = responses_payload_to_model_request(
        {
            "model": "deepseek-chat",
            "input": [
                {"type": "compaction", "encrypted_content": "opaque"},
                {"type": "message", "role": "user", "content": "continue"},
            ],
        },
        expected_model="deepseek-chat",
    )

    assert "provider-compaction-unavailable" in str(request.messages[0].content)


@pytest.mark.asyncio
async def test_proxy_compact_endpoint_roundtrips_encrypted_continuation_state() -> None:
    router = _RecordingRouter(tools=False)
    compact_payload = {
        "model": "deepseek-chat",
        "instructions": "Keep verified file receipts and unfinished work.",
        "input": [
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "fix the long task runtime"}],
            },
            {
                "type": "message",
                "role": "assistant",
                "phase": "commentary",
                "content": [{"type": "output_text", "text": "runtime/a.py was inspected"}],
            },
            {
                "type": "function_call_output",
                "call_id": "call-check",
                "output": "12 tests passed",
            },
        ],
    }

    async with ScopedResponsesProxy(
        router,
        scope=_scope(),
        trusted_session=None,
    ) as proxy:
        status, headers, body = await _post(
            proxy.provider_profile,
            compact_payload,
            endpoint="responses/compact",
        )
        assert status == 200
        assert headers["content-type"] == "application/json"
        compacted = json.loads(body)
        assert compacted["object"] == "response.compaction"
        assert compacted["output"][0]["role"] == "user"
        item = compacted["output"][-1]
        assert item["type"] == "compaction"
        assert item["encrypted_content"].startswith("echo.compaction.v1.")
        assert "proxy response" not in item["encrypted_content"]

        status, _, _ = await _post(
            proxy.provider_profile,
            {
                "model": "deepseek-chat",
                "input": [
                    *compacted["output"],
                    {"type": "message", "role": "user", "content": "continue"},
                ],
                "stream": False,
            },
        )

    assert status == 200
    compact_request, resumed_request = router.requests
    assert compact_request.tools == []
    assert "historical evidence" in str(compact_request.messages[0].content)
    resumed = "\n".join(str(message.content) for message in resumed_request.messages)
    assert "<provider-compaction>" in resumed
    assert "proxy response" in resumed
    assert "provider-compaction-unavailable" not in resumed


@pytest.mark.asyncio
async def test_proxy_tampered_local_compaction_is_never_trusted() -> None:
    router = _RecordingRouter(tools=False)
    async with ScopedResponsesProxy(
        router,
        scope=_scope(),
        trusted_session=None,
    ) as proxy:
        status, _, body = await _post(
            proxy.provider_profile,
            {
                "model": "deepseek-chat",
                "input": [{"type": "message", "role": "user", "content": "compact me"}],
            },
            endpoint="responses/compact",
        )
        assert status == 200
        item = json.loads(body)["output"][-1]
        item["encrypted_content"] = item["encrypted_content"][:-1] + "A"
        status, _, _ = await _post(
            proxy.provider_profile,
            {
                "model": "deepseek-chat",
                "input": [item, {"type": "message", "role": "user", "content": "continue"}],
                "stream": False,
            },
        )

    assert status == 200
    resumed = "\n".join(str(message.content) for message in router.requests[-1].messages)
    assert "provider-compaction-unavailable" in resumed
    assert "proxy response" not in resumed


@pytest.mark.asyncio
async def test_compaction_survives_turn_proxy_rotation_and_backend_key_reload(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    key_a = load_or_create_compaction_key(
        state_root,
        tenant_id="tenant-a",
        principal_id="alice",
        thread_id="thread-a",
    )
    key_b = load_or_create_compaction_key(
        state_root,
        tenant_id="tenant-a",
        principal_id="alice",
        thread_id="thread-a",
    )
    other_thread_key = load_or_create_compaction_key(
        state_root,
        tenant_id="tenant-a",
        principal_id="alice",
        thread_id="thread-b",
    )
    assert key_a == key_b
    assert key_a != other_thread_key
    master_path = state_root / "responses-compaction" / "master.key"
    assert master_path.read_bytes() != key_a
    if os.name == "posix":
        assert master_path.stat().st_mode & 0o777 == 0o600

    first_router = _RecordingRouter(tools=False)
    async with ScopedResponsesProxy(
        first_router,
        scope=_scope(turn="turn-a"),
        trusted_session=None,
        compaction_key=key_a,
    ) as first:
        status, _, body = await _post(
            first.provider_profile,
            {
                "model": "deepseek-chat",
                "input": [{"type": "message", "role": "user", "content": "durable goal"}],
            },
            endpoint="responses/compact",
        )
    assert status == 200
    compacted = json.loads(body)

    second_router = _RecordingRouter(tools=False)
    async with ScopedResponsesProxy(
        second_router,
        scope=_scope(turn="turn-b"),
        trusted_session=None,
        compaction_key=key_b,
    ) as second:
        status, _, _ = await _post(
            second.provider_profile,
            {
                "model": "deepseek-chat",
                "input": [
                    *compacted["output"],
                    {"type": "message", "role": "user", "content": "continue next turn"},
                ],
                "stream": False,
            },
        )

    assert status == 200
    resumed = "\n".join(str(message.content) for message in second_router.requests[0].messages)
    assert "<provider-compaction>" in resumed
    assert "proxy response" in resumed


@pytest.mark.asyncio
async def test_proxy_token_allows_multi_round_idempotent_retry_cross_scope_and_expiry() -> None:
    first_router = _RecordingRouter(tools=False)
    first = ScopedResponsesProxy(
        first_router,
        scope=_scope(turn="turn-a"),
        trusted_session=None,
    )
    second = ScopedResponsesProxy(
        _RecordingRouter(tools=False),
        scope=_scope(turn="turn-b"),
        trusted_session=None,
    )
    await first.start()
    await second.start()
    try:
        first_profile = first.provider_profile
        second_profile = second.provider_profile
        assert (await _post(first_profile, _tool_history_payload()))[0] == 200
        assert (await _post(first_profile, _tool_history_payload()))[0] == 200
        assert len(first_router.requests) == 1
        assert (await _post(first_profile, _tool_history_payload(suffix=" second model round")))[
            0
        ] == 200
        assert (await _post(first_profile, _tool_history_payload(model="other-model")))[0] == 403
        assert (await _post(first_profile, _tool_history_payload(), token="x" * 64))[0] == 401
        assert (
            await _post(
                second_profile,
                _tool_history_payload(),
                token=first_profile.scoped_bearer_token,
            )
        )[0] == 401
        first._expires_at = time.monotonic() - 1.0
        assert (await _post(first_profile, _tool_history_payload(suffix=" after expiry")))[0] == 401
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_proxy_token_only_enters_app_server_environment(tmp_path: Path) -> None:
    router = _RecordingRouter(tools=False)
    async with ScopedResponsesProxy(
        router,
        scope=_scope(),
        trusted_session=None,
    ) as proxy:
        profile = proxy.provider_profile
        token = profile.scoped_bearer_token
        assert token is not None
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        security = CodexSidecarSecurity(
            CodexSecurityPolicy(
                state_root=tmp_path / "state",
                allowed_workspace_roots=(tmp_path,),
            )
        )
        context = security.prepare(
            realm_id="realm",
            tenant_id="tenant-a:alice",
            thread_id="thread-a",
            task_id="turn-a",
            workspace=workspace,
            provider_profile=profile,
            host_env={"UPSTREAM_API_KEY": "must-not-enter", "PATH": "/usr/bin:/bin"},
        )
        launch_env = context.launch_env()
        config_text = context.config_path.read_text(encoding="utf-8")
        parsed = tomllib.loads(config_text)

        assert launch_env["ECHO_CODEX_PROXY_TOKEN"] == token
        assert launch_env["NO_PROXY"] == "127.0.0.1,localhost,::1"
        assert launch_env["no_proxy"] == "127.0.0.1,localhost,::1"
        assert "UPSTREAM_API_KEY" not in launch_env
        assert parsed["model_providers"]["echo_proxy"]["env_key"] == (
            "ECHO_CODEX_PROXY_TOKEN"
        )
        assert "ECHO_CODEX_PROXY_TOKEN" not in parsed["shell_environment_policy"]["set"]
        assert token not in config_text
        assert token not in repr(profile)
        assert token not in repr(context)
        app_server_config = CodexAppServerConfig(
            env_allowlist=frozenset({"ECHO_CODEX_PROXY_TOKEN"}),
            env_overrides={"ECHO_CODEX_PROXY_TOKEN": token},
            source_environment={},
        )
        assert token not in repr(app_server_config)
        context.validate_effective_config({"config": parsed})


@pytest.mark.asyncio
async def test_proxy_rejects_remote_image_urls() -> None:
    async with ScopedResponsesProxy(
        _RecordingRouter(tools=False),
        scope=_scope(),
        trusted_session=None,
    ) as proxy:
        payload = {
            "model": "deepseek-chat",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "inspect"},
                        {"type": "input_image", "image_url": "https://example.test/secret.png"},
                    ],
                }
            ],
            "stream": True,
        }
        status, _headers, body = await _post(proxy.provider_profile, payload)
    assert status == 400
    assert b"Remote image URLs are not allowed" in body


def _real_codex_command() -> tuple[str, ...] | None:
    try:
        command = resolve_codex_app_server_command()
    except ConfigurationError:
        return None
    executable = command[0]
    if Path(executable).is_file() or shutil.which(executable):
        return command
    return None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_codex_app_server_completes_scoped_text_and_tool_loop(
    tmp_path: Path,
) -> None:
    """Exercise the locally installed App Server, skipping hosts without Codex."""

    command = _real_codex_command()
    if command is None:
        pytest.skip("real Codex App Server executable is unavailable")
    assert command is not None
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    router = _RealToolLoopRouter()
    tool_calls: list[ApprovalRequest] = []

    async def _dynamic_tool(request: ApprovalRequest) -> dict[str, Any]:
        tool_calls.append(request)
        return {
            "contentItems": [{"type": "inputText", "text": "probe succeeded"}],
            "success": True,
        }

    async with ScopedResponsesProxy(
        router,
        scope=CodexResponsesScope(
            tenant_id="tenant-e2e",
            principal_id="alice",
            thread_id="thread-e2e",
            turn_id="turn-e2e",
            model="echo-e2e-model",
        ),
        trusted_session=None,
        ttl_s=120.0,
    ) as proxy:
        request = CodexExecutionRequest(
            outer_thread_id="thread-e2e",
            outer_turn_id="turn-e2e",
            workspace=workspace,
            realm_id="realm-e2e",
            tenant_id="tenant-e2e",
            principal_id="alice",
            prompt="Call probe exactly once, then report completion.",
            command=command,
            model="echo-e2e-model",
            provider_profile=proxy.provider_profile,
            developer_instructions="Use the advertised probe tool before answering.",
            dynamic_tools=(
                {
                    "type": "function",
                    "name": "probe",
                    "description": "Return a deterministic probe result.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                        "additionalProperties": False,
                    },
                },
            ),
            dynamic_tool_handler=_dynamic_tool,
        )
        security = CodexSidecarSecurity(
            CodexSecurityPolicy(
                state_root=tmp_path / "state",
                allowed_workspace_roots=(tmp_path,),
            )
        )
        execution = CodexExecutionSession(
            request,
            security=security,
            approval_provider=AutoDenyProvider(),
            is_interrupted=lambda: False,
        )
        notifications = []
        try:
            async with asyncio.timeout(60.0):
                await execution.start()
                while True:
                    notification = await execution.next_notification(timeout_s=10.0)
                    notifications.append(notification)
                    if notification.method == "turn/completed":
                        break
                compact_result = await execution._require_client().request(
                    "thread/compact/start",
                    {"threadId": execution.inner_thread_id},
                    timeout_s=20.0,
                )
        finally:
            await execution.close()

    assert isinstance(compact_result, dict)
    assert len(router.requests) == 3
    compact_prompt = "\n".join(
        str(message.content) for message in router.requests[-1].messages
    ).casefold()
    assert "compaction" in compact_prompt
    assert len(tool_calls) == 1
    assert tool_calls[0].params["tool"] == "probe"
    agent_texts: list[str] = []
    for notification in notifications:
        item = notification.params.get("item")
        if (
            notification.method == "item/completed"
            and isinstance(item, dict)
            and item.get("type") == "agentMessage"
        ):
            agent_texts.append(str(item.get("text") or ""))
    assert any("real proxy tool loop complete" in text for text in agent_texts)


