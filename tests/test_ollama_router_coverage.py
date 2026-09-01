"""Dense coverage for the Ollama model router (audit Q-05)."""

from __future__ import annotations

import json

import pytest

import runtime.sensing.model_router.ollama_router as or_mod
from runtime.platform.models.llm import Message, ModelRequest, ToolSpec
from runtime.sensing.model_router.ollama_router import (
    OllamaModelInfo,
    OllamaModelRouter,
    OllamaRouterError,
)


class _Client:
    def __init__(self, tags=None, chat=None):
        self._tags = tags
        self._chat = chat
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        if callable(self._tags):
            return self._tags()
        return _Resp(200, self._tags or {})

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if callable(self._chat):
            return self._chat()
        return _Resp(200, self._chat or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Resp:
    def __init__(self, status, data=None, text=""):
        self.status_code = status
        self._data = data
        self.text = text

    def json(self):
        return self._data


def _router(client, **kw):
    return OllamaModelRouter(base_url="http://ollama:11434", client=client, auto_detect=False, **kw)


def _req(**kw):
    base = dict(
        model="llama3.2",
        messages=[Message(role="user", content="hi")],
    )
    base.update(kw)
    return ModelRequest(**base)


def test_model_info_repr() -> None:
    info = OllamaModelInfo("llama3.2", size=10, family="llama", quant="q4")
    assert info.name == "llama3.2"
    assert "llama3.2" in repr(info)


def test_init_requires_httpx(monkeypatch) -> None:
    monkeypatch.setattr(or_mod, "HTTPX_AVAILABLE", False)
    with pytest.raises(OllamaRouterError):
        OllamaModelRouter(client=_Client())


def test_init_auto_detect(monkeypatch) -> None:
    client = _Client(tags={"models": [{"name": "qwen2:7b"}]})
    monkeypatch.setattr(or_mod, "httpx", None)
    r = OllamaModelRouter(client=client, base_url="http://x", auto_detect=True)
    # auto_select_model prefers llama family but falls back to first model
    assert r.default_model == "qwen2:7b"


def test_is_available_and_list(monkeypatch) -> None:
    client = _Client(
        tags={
            "models": [
                {
                    "name": "llama3.2:3b",
                    "size": 100,
                    "details": {"family": "llama", "quantization_level": "q4"},
                }
            ]
        }
    )
    monkeypatch.setattr(or_mod, "httpx", None)
    r = _router(client)
    assert r.is_available() is True
    assert r._available is True
    models = r.list_models()
    assert models[0].name == "llama3.2:3b"
    assert models[0].family == "llama"
    assert models[0].quant == "q4"
    # cached path returns the same list
    assert r.list_models() is models


def test_is_available_failure() -> None:
    client = _Client(tags=lambda: (_ for _ in ()).throw(OSError("refused")))
    r = _router(client)
    assert r.is_available() is False
    assert r.list_models() == []


def test_call_text_and_tool_calls(monkeypatch) -> None:
    chat = {
        "choices": [
            {
                "message": {
                    "content": "hello",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {
                                "name": "list_cwd",
                                "arguments": '{"path": "."}',
                            },
                        },
                        {
                            "id": "t2",
                            "function": {
                                "name": "bad_json",
                                "arguments": "{not json",
                            },
                        },
                        "not-a-dict",
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7},
        "model": "llama3.2",
    }
    client = _Client(chat=chat)
    monkeypatch.setattr(or_mod, "httpx", None)
    r = _router(client)
    resp = r.call(
        _req(
            max_tokens=100,
            temperature=0.5,
            tools=[ToolSpec(name="list_cwd", description="list")],
            require_tool_use=False,
        )
    )
    assert resp.text == "hello"
    assert resp.finish_reason == "tool_calls"
    assert resp.input_tokens == 5
    assert resp.output_tokens == 7
    assert resp.provider == "ollama"
    assert resp.tool_calls[0].name == "list_cwd"
    assert resp.tool_calls[0].input == {"path": "."}
    assert resp.tool_calls[1].input == {}  # malformed json -> {}
    assert len(resp.tool_calls) == 2
    payload = client.calls[0][2]["json"]
    assert "tools" in payload
    assert payload["tool_choice"] == "auto"
    assert payload["stream"] is False


def test_call_errors(monkeypatch) -> None:
    def _http_error():
        return _Resp(429, text="rate limited")

    client = _Client(chat=_http_error)
    monkeypatch.setattr(or_mod, "httpx", None)
    r = _router(client)
    with pytest.raises(OllamaRouterError) as ei:
        r.call(_req())
    assert "http_429" in str(ei.value)

    def _network_error():
        raise OSError("conn refused")

    client2 = _Client(chat=_network_error)
    r2 = _router(client2)
    with pytest.raises(OllamaRouterError) as ei:
        r2.call(_req())
    assert "http_error" in str(ei.value)

    class _BadJson(_Resp):
        def json(self):
            raise json.JSONDecodeError("bad", "doc", 0)

    client3 = _Client(chat=lambda: _BadJson(200, {}))
    r3 = _router(client3)
    with pytest.raises(OllamaRouterError) as ei:
        r3.call(_req())
    assert "invalid_json" in str(ei.value)

    # Empty message content -> empty text
    client4 = _Client(chat={"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]})
    r4 = _router(client4)
    resp = r4.call(_req())
    assert resp.text == ""
    assert resp.tool_calls == []

