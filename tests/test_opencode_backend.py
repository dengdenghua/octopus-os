"""OpenCode boundary: credentials, session isolation, terminal errors and abort."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from runtime.execution.host_mcp_connection import HostMCPConnection
from runtime.execution.opencode_backend import (
    MessageEvents,
    OpenCodeError,
    child_environment,
    resolve_zen_model,
    state_directory,
    stream_prompt,
    validate_catalog_model,
)
from runtime.platform.models.custom_model_selection import custom_model_selection_id
from runtime.safety.auth.scope import TenantScope


@pytest.mark.asyncio
async def test_privacy_blocks_engine_before_process_or_http_access(tmp_path, monkeypatch):
    from runtime.execution import opencode_backend as backend
    from runtime.safety.privacy import PrivacyViolation

    monkeypatch.setattr("runtime.safety.privacy.privacy_enabled", lambda: True)
    assert backend.inspect_readiness(None, "big-pickle")["available"] is False
    with pytest.raises(PrivacyViolation):
        async with backend.managed_server("unused", tmp_path / "engine", None, "big-pickle", True):
            pytest.fail("privacy started a cloud engine")
    assert not (tmp_path / "engine").exists()
    with pytest.raises(PrivacyViolation):
        async for _ in stream_prompt(
            None,
            "ses_test",
            text="private",
            system="role",
            model="big-pickle",
            interrupted=lambda: False,
        ):
            pytest.fail("privacy sent a prompt")


def test_native_web_is_never_granted_outside_host_tools(tmp_path):
    env = child_environment(tmp_path, None, "password", "big-pickle", True)
    assert json.loads(env["OPENCODE_CONFIG_CONTENT"])["permission"] == {"*": "deny"}


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:123/mcp",
        "http://evil.example:123/mcp",
        "http://127.0.0.1:123/mcp?token=x",
        "http://user@127.0.0.1:123/mcp",
    ],
)
def test_host_connection_rejects_non_loopback_or_credential_urls(url):
    with pytest.raises(ValueError):
        HostMCPConnection(url, "a" * 43)


def test_executable_requires_absolute_operator_path(tmp_path, monkeypatch):
    from runtime.execution import opencode_backend as backend

    (tmp_path / "opencode.exe").write_bytes(b"test fixture, not executable")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ECHO_OPENCODE_BIN", "opencode.exe")
    assert backend.executable() is None
    monkeypatch.setenv("ECHO_OPENCODE_BIN", str(tmp_path / "opencode.exe"))
    assert Path(backend.executable()) == tmp_path / "opencode.exe"


@pytest.mark.asyncio
async def test_managed_server_uses_isolated_process_and_cleans_tree_after_failure(
    tmp_path, monkeypatch
):
    from runtime.execution import opencode_backend as backend

    command = str(tmp_path / "opencode.exe")
    monkeypatch.setattr(backend, "executable", lambda: command)
    process = SimpleNamespace(
        pid=4242, returncode=None, wait=AsyncMock(return_value=0), kill=Mock()
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(backend.asyncio, "create_subprocess_exec", launch)
    killed = []

    def terminate(pid, **kwargs):
        killed.append(pid)
        process.returncode = 0
        return True

    monkeypatch.setattr("runtime.platform.process.tree.terminate_pid_tree", terminate)
    original_client = httpx.AsyncClient

    def respond(request):
        if request.url.path == "/global/health":
            return httpx.Response(200, json={"healthy": True})
        return httpx.Response(
            200,
            json={
                "all": [
                    {"id": "opencode", "models": {"selected": {"cost": {"input": 0, "output": 0}}}}
                ]
            },
        )

    monkeypatch.setattr(
        backend.httpx,
        "AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(ValueError, match="consumer failure"):
        async with backend.managed_server(command, tmp_path / "engine", None, "selected", False):
            raise ValueError("consumer failure")
    assert launch.call_args.args[:4] == (command, "serve", "--hostname", "127.0.0.1")
    assert launch.call_args.kwargs["cwd"] == tmp_path / "engine" / "workspace"
    assert json.loads(launch.call_args.kwargs["env"]["OPENCODE_CONFIG_CONTENT"])["permission"] == {
        "*": "deny"
    }
    assert killed == [4242]
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("identifier", ["../escape", "ses_x?other=1", "", None])
async def test_invalid_engine_session_never_gets_persisted(tmp_path, identifier):
    from runtime.execution.opencode_backend import session_for_thread

    async with httpx.AsyncClient(
        base_url="http://localhost",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"id": identifier})),
    ) as client:
        with pytest.raises(OpenCodeError, match="会话记录"):
            await session_for_thread(client, tmp_path)
    assert not (tmp_path / "session.json").exists()


def test_model_selection_keeps_provider_identity():
    catalog = {
        "opencode-zen": {
            "id": "opencode-zen",
            "models": ["big-pickle"],
            "managed_by_plugin": "opencode-zen",
        },
        "other": {"id": "other", "models": ["big-pickle"]},
    }
    with pytest.raises(OpenCodeError):
        resolve_zen_model(None, catalog)
    assert (
        resolve_zen_model(custom_model_selection_id("opencode-zen", "big-pickle"), catalog)
        == "big-pickle"
    )
    for model in [
        "gpt-5",
        custom_model_selection_id("other", "big-pickle"),
        custom_model_selection_id("opencode-zen", "big-pickle", "1m"),
    ]:
        with pytest.raises(OpenCodeError):
            resolve_zen_model(model, catalog)


def test_auto_and_unconfigured_cloud_model_fail_closed(tmp_path, monkeypatch):
    from runtime.execution import opencode_backend as backend

    monkeypatch.setattr(backend, "executable", lambda: "opencode")
    monkeypatch.setattr(backend, "zen_catalog", lambda: {})
    monkeypatch.setattr(backend.CredentialStore, "get_secret", lambda *args: None)
    assert backend.inspect_readiness(None)["available"] is False
    for selection in (None, "auto", "big-pickle"):
        with pytest.raises(OpenCodeError):
            resolve_zen_model(selection, {})
    monkeypatch.setenv("OPENCODE_API_KEY", "must-not-inherit")
    env = child_environment(tmp_path, None, "password", "big-pickle", False)
    assert "OPENCODE_API_KEY" not in env
    assert "provider" not in json.loads(env["OPENCODE_CONFIG_CONTENT"])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cost,allowed",
    [
        ({"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}, True),
        ({"input": 0, "output": 1}, False),
        ({"input": 1, "output": 0}, False),
        ({"input": 0, "output": 0, "cache": {"read": 1}}, False),
        ({"input": 0}, False),
        ({"input": False, "output": 0}, False),
        ({"input": "0", "output": 0}, False),
        (None, False),
    ],
)
async def test_anonymous_execution_requires_live_zero_pricing(cost, allowed):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"all": [{"id": "opencode", "models": {"selected": {"cost": cost}}}]}
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        if allowed:
            await validate_catalog_model(client, "selected", free_only=True)
        else:
            with pytest.raises(OpenCodeError, match="零费用"):
                await validate_catalog_model(client, "selected", free_only=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("available", [False, True])
async def test_selected_model_must_exist_in_the_official_zen_catalog(available):
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(
            200,
            json={
                "all": [
                    {"id": "other", "models": {"deepseek-v4-flash-free": {}}},
                    {"id": "opencode", "models": {"big-pickle": {}}},
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        if available:
            await validate_catalog_model(client, "big-pickle")
        else:
            with pytest.raises(OpenCodeError, match="未提供所选 Zen 模型"):
                await validate_catalog_model(client, "deepseek-v4-flash-free")
    assert calls == [("GET", "/provider")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [(200, {}), (200, []), (503, {"error": "private"})])
async def test_unreadable_catalog_is_not_reported_as_an_unavailable_model(status, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload)),
        base_url="http://localhost",
    ) as client:
        with pytest.raises(OpenCodeError, match="无法读取 OpenCode 模型列表"):
            await validate_catalog_model(client, "big-pickle")


@pytest.mark.parametrize("web", [True, False])
def test_child_environment_isolates_credentials_and_denies_local_tools(tmp_path, monkeypatch, web):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")
    monkeypatch.setenv("OPENCODE_CLIENT", "untrusted-override")
    monkeypatch.setenv("OPENCODE_CONFIG", "untrusted-config")
    env = child_environment(tmp_path, "zen-test-key", "test-password", "big-pickle", web)
    assert "OPENAI_API_KEY" not in env
    assert "OPENCODE_CLIENT" not in env
    assert "OPENCODE_CONFIG" not in env
    assert env["OPENCODE_API_KEY"] == "zen-test-key"
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert "zen-test-key" not in env["OPENCODE_CONFIG_CONTENT"]
    assert config["permission"]["*"] == "deny"
    assert "websearch" not in config["permission"]
    assert config["small_model"] == config["model"]
    assert config["share"] == "disabled"


def test_state_is_scoped_to_actor_tenant_and_thread():
    scopes = [
        TenantScope(tenant_id="one", actor_id="a"),
        TenantScope(tenant_id="one", actor_id="b"),
        TenantScope(tenant_id="two", actor_id="a"),
    ]
    paths = {state_directory(scope, thread) for scope in scopes for thread in ["x", "../../y"]}
    assert len(paths) == 6
    assert all(len(path.name) == 64 for path in paths)


def test_host_mcp_is_explicit_and_credentials_stay_out_of_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ECHO_HOST_MCP_TOKEN", "ambient-token")
    isolated = child_environment(tmp_path, "zen", "password", "big-pickle", False)
    assert "ECHO_HOST_MCP_TOKEN" not in isolated
    connection = HostMCPConnection("http://127.0.0.1:12345/mcp", "turn-token" * 4)
    env = child_environment(tmp_path, "zen", "password", "big-pickle", False, host_mcp=connection)
    config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
    assert config["mcp"]["echo"]["oauth"] is False
    assert "turn-token" not in env["OPENCODE_CONFIG_CONTENT"]
    assert "turn-token" not in repr(connection)
    assert env["ECHO_HOST_MCP_TOKEN"] == "turn-token" * 4
    assert config["permission"] == {"*": "deny", "echo_*": "allow"}


def test_host_tool_events_use_the_native_skill_identity():
    reducer = MessageEvents(set(), tool_names={"echo_read_file": "read_file"})
    event = {
        "info": {"id": "a", "role": "assistant"},
        "parts": [
            {
                "id": "p",
                "type": "tool",
                "tool": "echo_read_file",
                "callID": "c",
                "state": {"status": "completed", "input": {"path": "note.txt"}, "output": "note"},
            }
        ],
    }
    assert {e["tool_name"] for e in reducer.consume([event])} == {"read_file"}


def message(text="OK", error=None):
    return {
        "info": {"id": "assistant-1", "role": "assistant", "finish": "stop", "error": error},
        "parts": [{"id": "text-1", "type": "text", "text": text}],
    }


def test_growing_messages_and_tool_snapshots_are_not_replayed():
    reducer = MessageEvents({"old"})
    assert reducer.consume([{**message(), "info": {"id": "old", "role": "assistant"}}]) == []
    assert reducer.consume([message("O")])[0]["delta"] == "O"
    assert reducer.consume([message("OK")])[0]["delta"] == "K"
    assert reducer.consume([message("OK")]) == []
    tool_message = {
        "info": {"id": "a", "role": "assistant"},
        "parts": [
            {
                "id": "p",
                "callID": "call-1",
                "type": "tool",
                "tool": "websearch",
                "state": {"status": "completed", "input": {"query": "NAS"}, "output": "result"},
            }
        ],
    }
    events = reducer.consume([tool_message])
    assert [e["type"] for e in events] == ["tool_start", "tool_end"]
    assert events[1]["success"]
    assert reducer.consume([tool_message]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [None, {"name": "APIError", "data": {"statusCode": 429, "message": "private detail"}}]
)
async def test_http_200_is_not_sufficient_for_success(error):
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[] if len(calls) == 1 else [message(error=error)])
        return httpx.Response(200, json=message(error=error))

    events = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:

        async def run():
            async for event in stream_prompt(
                client,
                "ses_test",
                text="hello",
                system="role",
                model="big-pickle",
                interrupted=lambda: False,
                poll_s=0.001,
            ):
                events.append(event)

        if error:
            with pytest.raises(OpenCodeError, match="额度"):
                await run()
            assert not any(e["type"] == "react_completed" for e in events)
        else:
            await run()
            assert events[-1]["type"] == "react_completed"
            assert "".join(e["delta"] for e in events if e["type"] == "text_delta") == "OK"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,detail,expected",
    [
        (400, "Model is unavailable", "模型不可用"),
        (401, "invalid credentials", "连接已失效"),
        (500, "internal provider failure", "调用 Zen 失败"),
    ],
)
async def test_http_provider_failure_is_not_a_local_connection_error(status, detail, expected):
    def handle(request):
        if request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(status, json={"error": detail, "private": "secret-test-token"})

    events = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        with pytest.raises(OpenCodeError, match=expected) as failure:
            async for event in stream_prompt(
                client,
                "ses_test",
                text="hello",
                system="role",
                model="big-pickle",
                interrupted=lambda: False,
                poll_s=0.001,
            ):
                events.append(event)
    assert "secret-test-token" not in str(failure.value)
    assert not any(event["type"] == "react_completed" for event in events)


@pytest.mark.asyncio
async def test_stop_aborts_the_official_session():
    submitted = asyncio.Event()
    aborted = asyncio.Event()

    async def handle(request):
        if request.url.path.endswith("/abort"):
            aborted.set()
            return httpx.Response(200, json=True)
        if request.method == "POST":
            submitted.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        events = [
            event
            async for event in stream_prompt(
                client,
                "ses_test",
                text="hello",
                system="role",
                model="big-pickle",
                interrupted=submitted.is_set,
                poll_s=0.001,
            )
        ]
    assert aborted.is_set()
    assert events == [{"type": "react_cancelled", "reason": "用户停止了任务"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed", [False, True])
async def test_engine_prompt_bootstraps_fresh_history_and_only_sends_resume_delta(resumed):
    posted = []
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        if request.method == "POST":
            posted.append(json.loads(request.content))
            return httpx.Response(200, json=message())
        if calls == 1:
            return httpx.Response(200, json=[{"info": {"id": "old"}}] if resumed else [])
        return httpx.Response(200, json=[message()])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="http://localhost"
    ) as client:
        events = [
            event
            async for event in stream_prompt(
                client,
                "ses_test",
                text="MISSING_HISTORY\nLATEST",
                fresh_thread_text="FULL_HISTORY\nLATEST",
                system="current role",
                model="big-pickle",
                interrupted=lambda: False,
                poll_s=0.001,
            )
        ]
    assert events[-1]["type"] == "react_completed"
    assert posted[0]["parts"] == [
        {
            "type": "text",
            "text": ("MISSING_HISTORY" if resumed else "FULL_HISTORY") + "\nLATEST",
        }
    ]
    assert posted[0]["system"] == "current role"
