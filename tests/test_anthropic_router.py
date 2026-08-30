"""Implementation note."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.sensing.model_router import Message, ModelRequest
from runtime.sensing.model_router.anthropic_router import (
    ANTHROPIC_AVAILABLE,
    AnthropicModelRouter,
    _estimate_usd,
    _split_system,
)
from runtime.sensing.model_router.models import ModelStreamEvent, ToolSpec

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPricing:
    def test_haiku_price(self):
        # haiku: $0.80/M in, $4/M out
        usd = _estimate_usd("claude-haiku-4-5-20251001", in_tok=1_000_000, out_tok=0)
        assert usd == pytest.approx(0.80)

    def test_sonnet_price(self):
        usd = _estimate_usd("claude-sonnet-4-6", in_tok=0, out_tok=1_000_000)
        assert usd == pytest.approx(15.00)

    def test_opus_price(self):
        usd = _estimate_usd("claude-opus-4-7", in_tok=1_000_000, out_tok=1_000_000)
        assert usd == pytest.approx(15.00 + 75.00)

    def test_unknown_model_falls_back_to_sonnet(self):
        usd = _estimate_usd("unknown-model", in_tok=1_000_000, out_tok=0)
        assert usd == pytest.approx(3.00)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSplitSystem:
    def test_no_system(self):
        sys_text, rest = _split_system([Message(role="user", content="hi")])
        assert sys_text == ""
        assert rest == [{"role": "user", "content": "hi"}]

    def test_single_system(self):
        sys_text, rest = _split_system(
            [
                Message(role="system", content="You are X"),
                Message(role="user", content="hi"),
            ]
        )
        assert sys_text == "You are X"
        assert len(rest) == 1
        assert rest[0]["role"] == "user"

    def test_multiple_systems_concatenated(self):
        sys_text, rest = _split_system(
            [
                Message(role="system", content="A"),
                Message(role="system", content="B"),
                Message(role="user", content="hi"),
            ]
        )
        assert sys_text == "A\n\nB"
        assert len(rest) == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _MockAnthropicClient:
    """duck-typed anthropic client for testing."""

    def __init__(self, response_text: str = "hello from mock"):
        self.response_text = response_text
        self.last_call: dict | None = None
        self.messages = self  # provide `.messages.create`

    def create(self, **kwargs):
        self.last_call = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(text=self.response_text)],
            usage=SimpleNamespace(input_tokens=123, output_tokens=45),
            stop_reason="end_turn",
            model=kwargs.get("model", "claude-mock"),
        )


class _MockAnthropicStream:
    def __init__(self, final_msg: SimpleNamespace):
        self._final_msg = final_msg

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self._final_msg


class _MockAnthropicStreamClient:
    def __init__(self):
        self.last_call: dict | None = None
        self.messages = self
        self.final_msg = SimpleNamespace(
            content=[SimpleNamespace(type="text", text="streamed")],
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                cache_read_input_tokens=700,
                cache_creation_input_tokens=300,
            ),
            stop_reason="end_turn",
        )

    def stream(self, **kwargs):
        self.last_call = kwargs
        return _MockAnthropicStream(self.final_msg)


class TestCallFlow:
    def test_basic_call(self):
        client = _MockAnthropicClient(response_text="planned")
        router = AnthropicModelRouter(client=client)
        resp = router.call(
            ModelRequest(
                model="claude-haiku-4-5-20251001",
                messages=[
                    Message(role="system", content="be helpful"),
                    Message(role="user", content="do a thing"),
                ],
                max_tokens=500,
            )
        )
        assert resp.text == "planned"
        assert resp.input_tokens == 123
        assert resp.output_tokens == 45
        assert resp.provider == "anthropic"
        # Implementation note.
        expected = (123 * 0.80 + 45 * 4.00) / 1_000_000
        assert resp.cost.usd == pytest.approx(expected)

    def test_system_passed_as_top_level_param(self):
        client = _MockAnthropicClient()
        router = AnthropicModelRouter(client=client)
        router.call(
            ModelRequest(
                model="claude-haiku-4-5",
                messages=[
                    Message(role="system", content="sysprompt"),
                    Message(role="user", content="hi"),
                ],
            )
        )
        # Implementation note.
        assert client.last_call["system"] == "sysprompt"
        # Implementation note.
        assert all(m["role"] != "system" for m in client.last_call["messages"])

    def test_long_system_prompt_is_cache_marked(self):
        client = _MockAnthropicClient()
        router = AnthropicModelRouter(client=client)
        router.call(
            ModelRequest(
                model="claude-sonnet-4-6",
                messages=[
                    Message(role="system", content="cache me\n" * 500),
                    Message(role="user", content="hi"),
                ],
            )
        )

        system = client.last_call["system"]
        assert isinstance(system, list)
        assert system[0]["cache_control"] == {"type": "ephemeral"}

    def test_tool_catalog_is_cache_marked(self):
        client = _MockAnthropicClient()
        router = AnthropicModelRouter(client=client)
        router.call(
            ModelRequest(
                model="claude-sonnet-4-6",
                messages=[Message(role="user", content="hi")],
                tools=[
                    ToolSpec(
                        name="huge_tool",
                        description="tool docs " * 500,
                        input_schema={
                            "type": "object",
                            "properties": {"path": {"type": "string", "description": "p" * 2000}},
                        },
                    )
                ],
            )
        )

        assert client.last_call["tools"][-1]["cache_control"] == {"type": "ephemeral"}

    def test_default_model_when_empty(self):
        client = _MockAnthropicClient()
        router = AnthropicModelRouter(client=client, default_model="claude-opus-4-7")
        # Implementation note.
        router.call(
            ModelRequest(
                model="",
                messages=[Message(role="user", content="x")],
            )
        )
        assert client.last_call["model"] == "claude-opus-4-7"

    def test_no_api_key_no_client_raises(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        with pytest.raises(
            RuntimeError,
            match="ANTHROPIC_API_KEY|ANTHROPIC_AUTH_TOKEN|no.*api.*key|anthropic SDK not installed",
        ):
            AnthropicModelRouter(api_key=None)

    def test_stream_final_includes_prompt_cache_telemetry(self):
        client = _MockAnthropicStreamClient()
        router = AnthropicModelRouter(client=client)

        events = list(
            router.call_stream(
                ModelRequest(
                    model="claude-sonnet-4-6",
                    messages=[Message(role="user", content="hello")],
                )
            )
        )

        done = next(e for e in events if e.type == "done")
        assert isinstance(done, ModelStreamEvent)
        assert done.final is not None
        assert done.final.cache_read_tokens == 700
        assert done.final.cache_creation_tokens == 300

    def test_stream_request_uses_prompt_cache_hints(self):
        client = _MockAnthropicStreamClient()
        router = AnthropicModelRouter(client=client)

        list(
            router.call_stream(
                ModelRequest(
                    model="claude-sonnet-4-6",
                    messages=[
                        Message(role="system", content="stream cache\n" * 500),
                        Message(role="user", content="hello"),
                    ],
                    tools=[
                        ToolSpec(
                            name="huge_tool",
                            description="stream tool docs " * 500,
                            input_schema={"type": "object", "properties": {}},
                        )
                    ],
                )
            )
        )

        assert client.last_call["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert client.last_call["tools"][-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.skipif(not ANTHROPIC_AVAILABLE, reason="anthropic SDK not installed")
class TestImportAvailable:
    def test_module_imports_cleanly(self):
        # Implementation note.
        from runtime.sensing.model_router.anthropic_router import AnthropicModelRouter  # noqa: F401

