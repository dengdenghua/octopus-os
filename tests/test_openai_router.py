"""Implementation note."""

from __future__ import annotations

from typing import Any

import pytest

httpx = pytest.importorskip("httpx")

from runtime.sensing.model_router import (  # noqa: E402
    Message,
    ModelRequest,
    MultiModelRouter,
    OpenAIModelRouter,
    OpenAIRouterError,
)

# ═══════════════════════════════════════════════════════════
# fake httpx client
# ═══════════════════════════════════════════════════════════


class _FakeResponse:
    def __init__(self, status_code: int, payload: Any = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = (
            text if text else (__import__("json").dumps(payload) if payload is not None else "")
        )

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """Implementation note."""

    def __init__(self, response: _FakeResponse | None = None, raise_exc: Exception | None = None):
        self._response = response
        self._raise = raise_exc
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if self._raise:
            raise self._raise
        return self._response

    def close(self):
        pass


def _req(model: str = "gpt-4o-mini", content: str = "hello") -> ModelRequest:
    return ModelRequest(
        model=model,
        messages=[Message(role="user", content=content)],
        max_tokens=128,
        temperature=0.0,
    )


def _openai_response(text: str = "hi back", *, prompt_tokens: int = 10, completion_tokens: int = 5):
    return {
        "id": "chatcmpl-test",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRequestShape:
    def test_url_and_headers(self):
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(
            base_url="http://localhost:11434/v1",
            api_key="sk-xxx",
            client=fake,
        )
        r.call(_req())
        call = fake.calls[0]
        assert call["url"] == "http://localhost:11434/v1/chat/completions"
        assert call["headers"]["Authorization"] == "Bearer sk-xxx"
        assert call["headers"]["Content-Type"] == "application/json"

    def test_payload_contains_messages_and_params(self):
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        r.call(_req(content="ping"))
        payload = fake.calls[0]["json"]
        assert payload["model"] == "gpt-4o-mini"
        assert payload["messages"] == [{"role": "user", "content": "ping"}]
        assert payload["max_tokens"] == 128
        assert payload["temperature"] == 0.0

    def test_reasoning_effort_is_sent_when_thinking_enabled(self):
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        r.call(
            _req(content="hard problem").model_copy(
                update={
                    "enable_thinking": True,
                    "reasoning_effort": "xhigh",
                },
            ),
        )

        payload = fake.calls[0]["json"]
        # Chat Completions accepts up to ``high``; Echo's xhigh tier is
        # normalized at the provider boundary instead of sending an invalid value.
        assert payload["reasoning_effort"] == "high"
        assert payload["thinking"] == {"type": "enabled"}

    def test_extra_headers_merged(self):
        """Implementation note."""
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(
            base_url="https://openrouter.ai/api/v1",
            client=fake,
            extra_headers={"HTTP-Referer": "https://my-app.com", "X-Title": "my-app"},
        )
        r.call(_req())
        hdrs = fake.calls[0]["headers"]
        assert hdrs["HTTP-Referer"] == "https://my-app.com"
        assert hdrs["X-Title"] == "my-app"

    def test_no_auth_header_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        r.call(_req())
        hdrs = fake.calls[0]["headers"]
        assert "Authorization" not in hdrs


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestResponseParsing:
    def test_extracts_content_and_cost(self):
        fake = _FakeClient(
            response=_FakeResponse(
                200,
                _openai_response(text="hi back", prompt_tokens=100, completion_tokens=50),
            )
        )
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        resp = r.call(_req())

        assert resp.text == "hi back"
        assert resp.input_tokens == 100
        assert resp.output_tokens == 50
        assert resp.finish_reason == "stop"
        assert resp.provider == "openai_compat"
        assert resp.model == "gpt-4o-mini"
        assert resp.cost.tokens_in == 100
        assert resp.cost.tokens_out == 50
        assert resp.cost.usd > 0

    def test_list_content_merged(self):
        """Implementation note."""
        payload = _openai_response()
        payload["choices"][0]["message"]["content"] = [
            {"type": "text", "text": "part1 "},
            {"type": "text", "text": "part2"},
            {"type": "image_url", "url": "..."},  # Implementation note.
        ]
        fake = _FakeClient(response=_FakeResponse(200, payload))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        resp = r.call(_req())
        assert resp.text == "part1 part2"

    def test_null_content_with_tool_calls_is_empty_text(self):
        payload = _openai_response(text="")
        payload["choices"][0]["message"]["content"] = None
        payload["choices"][0]["message"]["tool_calls"] = [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "bb_read", "arguments": '{"key":"plan"}'},
            }
        ]
        fake = _FakeClient(response=_FakeResponse(200, payload))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)

        resp = r.call(_req())

        assert resp.text == ""
        assert [call.name for call in resp.tool_calls] == ["bb_read"]

    def test_null_text_block_is_ignored(self):
        payload = _openai_response()
        payload["choices"][0]["message"]["content"] = [
            {"type": "text", "text": None},
            {"type": "text", "text": "usable"},
        ]
        fake = _FakeClient(response=_FakeResponse(200, payload))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)

        resp = r.call(_req())

        assert resp.text == "usable"

    def test_missing_usage_defaults_to_zero(self):
        payload = _openai_response()
        del payload["usage"]
        fake = _FakeClient(response=_FakeResponse(200, payload))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        resp = r.call(_req())
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.cost.usd == 0.0  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPricing:
    def test_per_model_pricing_overrides_default(self):
        fake = _FakeClient(
            response=_FakeResponse(
                200,
                _openai_response(prompt_tokens=1000, completion_tokens=500),
            )
        )
        r = OpenAIModelRouter(
            base_url="http://x/v1",
            client=fake,
            pricing_per_1k={"gpt-4o-mini": (0.15, 0.60)},  # USD / 1k tokens
        )
        resp = r.call(_req())
        # 1000/1000 * 0.15 + 500/1000 * 0.60 = 0.15 + 0.30 = 0.45
        assert abs(resp.cost.usd - 0.45) < 1e-9

    def test_unknown_model_falls_to_default_rate(self):
        fake = _FakeClient(
            response=_FakeResponse(
                200,
                _openai_response(prompt_tokens=100, completion_tokens=50),
            )
        )
        r = OpenAIModelRouter(
            base_url="http://x/v1",
            client=fake,
            pricing_per_1k={"other-model": (1.0, 2.0)},  # Implementation note.
        )
        resp = r.call(_req())
        # Implementation note.
        assert resp.cost.usd > 0
        assert resp.cost.usd < 0.01  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestErrors:
    def test_http_error_raises(self):
        fake = _FakeClient(response=_FakeResponse(500, text="Internal Server Error"))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError, match="http_500"):
            r.call(_req())

    def test_http_4xx_raises_with_body(self):
        fake = _FakeClient(response=_FakeResponse(401, text="Invalid API key"))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError, match="http_401.*Invalid"):
            r.call(_req())

    def test_http_402_balance_error_is_user_readable(self):
        fake = _FakeClient(
            response=_FakeResponse(
                402,
                {
                    "error": {
                        "code": "402",
                        "message": "Insufficient account balance",
                        "type": "insufficient_balance",
                    }
                },
            )
        )
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError) as exc:
            r.call(_req())
        message = str(exc.value)
        assert "http_402" in message
        assert "模型账户余额不足" in message
        assert "Insufficient account balance" not in message

    def test_network_error_wrapped(self):
        fake = _FakeClient(raise_exc=ConnectionError("refused"))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError, match="http_error.*ConnectionError"):
            r.call(_req())

    def test_invalid_json_raises(self):
        resp = _FakeResponse(200, payload=None, text="not json at all")
        fake = _FakeClient(response=resp)
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError, match="invalid_json"):
            r.call(_req())

    def test_no_choices_raises(self):
        fake = _FakeClient(response=_FakeResponse(200, {"id": "x", "choices": []}))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        with pytest.raises(OpenAIRouterError, match="no choices"):
            r.call(_req())


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAuthFromEnv:
    def test_reads_from_default_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(base_url="http://x/v1", client=fake)
        r.call(_req())
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-from-env"

    def test_custom_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-123")
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(
            base_url="https://openrouter.ai/api/v1",
            env_var_name="OPENROUTER_API_KEY",
            client=fake,
        )
        r.call(_req())
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer sk-or-123"

    def test_explicit_api_key_wins_over_env(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        r = OpenAIModelRouter(
            base_url="http://x/v1",
            api_key="explicit-key",
            client=fake,
        )
        r.call(_req())
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer explicit-key"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMultiRouterIntegration:
    def test_default_model_rewrites_via_multi(self):
        """Implementation note."""
        fake = _FakeClient(response=_FakeResponse(200, _openai_response()))
        primary = OpenAIModelRouter(
            base_url="http://ollama/v1",
            default_model="llama3.2:3b",
            client=fake,
        )
        mm = MultiModelRouter(primary=primary)
        # Implementation note.
        mm.call(
            ModelRequest(
                model="caller-specified",
                messages=[Message(role="user", content="hi")],
            )
        )
        assert fake.calls[0]["json"]["model"] == "llama3.2:3b"

    def test_as_fallback_in_multi(self):
        """Implementation note."""
        from runtime.sensing.model_router import ModelRouter

        class _Bad(ModelRouter):
            def call(self, r):
                raise RuntimeError("primary dead")

        fake = _FakeClient(response=_FakeResponse(200, _openai_response("via-fallback")))
        openai = OpenAIModelRouter(
            base_url="http://x/v1",
            default_model="gpt-4o-mini",
            client=fake,
        )
        mm = MultiModelRouter(primary=_Bad(), fallbacks=[openai])
        resp = mm.call(
            ModelRequest(
                model="any",
                messages=[Message(role="user", content="hi")],
            )
        )
        assert resp.text == "via-fallback"
        assert mm.dispatch_log[-1].final_role == "fallback[0]"
