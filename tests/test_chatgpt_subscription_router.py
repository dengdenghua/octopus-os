from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from runtime.sensing.model_router.chatgpt_subscription_router import (
    ChatGPTSubscriptionModelRouter,
    ChatGPTSubscriptionRouterError,
    _ChatGPTCredentials,
    _read_credentials,
    _safe_http_error,
)
from runtime.sensing.model_router.dispatch_router import ModelDispatchRouter
from runtime.sensing.model_router.models import (
    Message,
    MockModelRouter,
    ModelRequest,
    ToolSpec,
)


class _Broker:
    def __init__(self) -> None:
        self.refreshes: list[bool] = []

    def load(self, *, force_refresh: bool = False) -> _ChatGPTCredentials:
        self.refreshes.append(force_refresh)
        suffix = "fresh" if force_refresh else "initial"
        return _ChatGPTCredentials(f"secret-{suffix}", "account-123")


def _sse(*events: dict[str, object]) -> bytes:
    return b"".join(
        f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )


def _completed_response(*, tool: bool = False) -> dict[str, object]:
    output: list[dict[str, object]] = [
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "你好"}],
        }
    ]
    if tool:
        output.append(
            {
                "id": "fc_1",
                "type": "function_call",
                "status": "completed",
                "call_id": "call_1",
                "name": "read_file",
                "arguments": '{"path":"README.md"}',
            }
        )
    return {
        "id": "resp_1",
        "object": "response",
        "status": "completed",
        "model": "gpt-5.6-sol",
        "output": output,
        "usage": {"input_tokens": 12, "output_tokens": 3},
    }


def test_native_router_streams_subscription_model_without_putting_token_in_body() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["payload"] = json.loads(request.content)
        completed = _completed_response(tool=True)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse(
                {"type": "response.output_text.delta", "delta": "你"},
                {"type": "response.output_text.delta", "delta": "好"},
                {
                    "type": "response.output_item.done",
                    "item": completed["output"][1],  # type: ignore[index]
                },
                {"type": "response.completed", "response": completed},
            ),
        )

    broker = _Broker()
    client = httpx.Client(transport=httpx.MockTransport(handler))
    router = ChatGPTSubscriptionModelRouter(credential_broker=broker, client=client)
    request = ModelRequest(
        model="chatgpt/gpt-5.6-sol",
        messages=[
            Message(role="system", content="只回答中文"),
            Message(role="assistant", content="上一轮回答"),
            Message(role="user", content="你好"),
        ],
        tools=[ToolSpec(name="read_file", description="读取文件")],
        reasoning_effort="xhigh",
    )

    events = list(router.call_stream(request))

    assert [event.delta for event in events if event.type == "text_delta"] == ["你", "好"]
    final = events[-1].final
    assert final is not None
    assert final.text == "你好"
    assert final.model == "gpt-5.6-sol"
    assert final.provider == "chatgpt_subscription"
    assert final.cost.usd == 0.0
    assert final.tool_calls[0].input == {"path": "README.md"}
    assert broker.refreshes == [False]
    assert seen["url"] == "https://chatgpt.com/backend-api/codex/responses"
    headers = seen["headers"]
    assert isinstance(headers, dict)
    assert headers["authorization"] == "Bearer secret-initial"
    assert headers["chatgpt-account-id"] == "account-123"
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gpt-5.6-sol"
    assert payload["reasoning"] == {"effort": "xhigh", "summary": "auto"}
    assert payload["input"][0]["content"] == [{"type": "output_text", "text": "上一轮回答"}]
    assert payload["input"][1]["content"] == [{"type": "input_text", "text": "你好"}]
    assert "secret-initial" not in json.dumps(payload)


def test_router_refreshes_managed_login_once_after_unauthorized() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(401, json={"error": {"message": "expired"}})
        assert request.headers["Authorization"] == "Bearer secret-fresh"
        completed = _completed_response()
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_sse({"type": "response.completed", "response": completed}),
        )

    broker = _Broker()
    router = ChatGPTSubscriptionModelRouter(
        credential_broker=broker,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    response = router.call(
        ModelRequest(
            model="chatgpt/gpt-5.6-sol",
            messages=[Message(role="user", content="hello")],
        )
    )

    assert response.text == "你好"
    assert calls == 2
    assert broker.refreshes == [False, True]


def test_dispatch_prefix_keeps_echo_kernel_and_selects_chatgpt_transport() -> None:
    fallback = MockModelRouter(response="system")
    chatgpt = MockModelRouter(response="subscription")
    dispatch = ModelDispatchRouter(fallback=fallback, routes={"chatgpt": chatgpt})

    response = dispatch.call(
        ModelRequest(
            model="chatgpt/gpt-5.6-sol",
            messages=[Message(role="user", content="hello")],
        )
    )

    assert response.text == "subscription"
    assert chatgpt.call_log[0].model == "chatgpt/gpt-5.6-sol"
    assert fallback.call_log == []


def test_http_400_classifies_context_and_tool_protocol_errors_without_leaking_body() -> None:
    context_error = _safe_http_error(
        400,
        '{"error":{"code":"context_length_exceeded","message":"secret detail"}}',
    )
    tool_error = _safe_http_error(
        400,
        '{"error":{"message":"Missing function_call_output for tool call"}}',
    )

    assert "上下文超过" in context_error
    assert "工具调用链" in tool_error
    assert "secret detail" not in context_error


def test_credentials_must_be_private_and_are_never_returned_as_public_metadata(
    tmp_path: Path,
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": "top-secret",
                    "account_id": "account-123",
                },
            }
        ),
        encoding="utf-8",
    )
    auth.chmod(0o600)
    credentials = _read_credentials(auth)
    assert credentials.account_id == "account-123"
    assert "top-secret" not in repr(credentials)

    auth.chmod(0o644)
    with pytest.raises(ChatGPTSubscriptionRouterError, match="权限不安全"):
        _read_credentials(auth)


