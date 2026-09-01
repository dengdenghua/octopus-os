from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from runtime.sensing.model_router.models import (
    ModelRequest,
    ModelResponse,
    ModelRouter,
    ModelStreamEvent,
)
from runtime.sensing.model_router.provider import Provider, ProviderCapabilities


class StreamingCompatRouter(Provider, ModelRouter):
    provider_name = "streaming_compat"
    capabilities = ProviderCapabilities(default_model="streaming/v1")

    def call(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(text='{"ok": true}', model=request.model, provider=self.provider_name)

    def call_stream(self, request: ModelRequest) -> Iterator[ModelStreamEvent]:
        yield ModelStreamEvent(type="text_delta", delta="o")
        yield ModelStreamEvent(
            type="done",
            final=ModelResponse(text="ok", model=request.model, provider=self.provider_name),
        )


class LimitedDomesticRouter(Provider, ModelRouter):
    provider_name = "kimi_coding"
    capabilities = ProviderCapabilities(
        supports_vision=True,
        default_model="kimi-k2.7-code",
        extra={"profile": "builtin-domestic"},
    )

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if request.tools:
            raise NotImplementedError("tool_use unsupported")
        if any(message.role == "system" for message in request.messages):
            raise ValueError("system prompt unsupported")
        if request.reasoning_effort:
            raise ValueError("reasoning_effort unsupported")
        if any(isinstance(message.content, list) for message in request.messages):
            return ModelResponse(
                text='{"ok": true}',
                model=request.model,
                provider=self.provider_name,
            )
        if _looks_like_structured_probe(request):
            return ModelResponse(text="OK", model=request.model, provider=self.provider_name)
        return ModelResponse(text="ok", model=request.model, provider=self.provider_name)


def _looks_like_structured_probe(request: ModelRequest) -> bool:
    return any(
        message.role == "user"
        and isinstance(message.content, str)
        and "Return exactly one minified JSON object" in message.content
        for message in request.messages
    )


@pytest.fixture(autouse=True)
def isolated_capability_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from runtime.sensing.model_router import capability_probe

    monkeypatch.setattr(
        capability_probe,
        "_disk_cache_path",
        lambda: tmp_path / "provider_caps.json",
    )
    capability_probe.clear_capability_cache()
    yield
    capability_probe.clear_capability_cache()


def test_probe_matrix_detects_real_call_stream_override() -> None:
    from runtime.sensing.model_router.capability_probe import probe_provider
    from runtime.sensing.model_router.models import MockModelRouter

    streaming = probe_provider(StreamingCompatRouter(), model="streaming/v1", force=True)
    fallback = probe_provider(MockModelRouter(response='{"ok": true}'), model="mock/v1", force=True)

    assert streaming.supports_streaming is True
    assert fallback.supports_streaming is False
    assert streaming.extra["capability_probe"]["streaming"] is True


def test_probe_matrix_records_domestic_provider_unsupported_fields() -> None:
    from runtime.sensing.model_router.capability_probe import probe_provider

    caps = probe_provider(LimitedDomesticRouter(), model="kimi-k2.7-code", force=True)
    probe = caps.extra["capability_probe"]

    assert probe["schema"] == "echo.provider_capability_probe.v1"
    assert probe["model"] == "kimi-k2.7-code"
    assert probe["vision"] is True
    assert probe["tool_use"] is False
    assert probe["json_schema"] is False
    assert probe["system_prompt"] is False
    assert probe["reasoning_effort"] is False
    assert probe["unsupported_fields"] == [
        "json_schema",
        "reasoning_effort",
        "streaming",
        "system_prompt",
        "tool_use",
    ]
    assert caps.supports_structured_output is False
    assert caps.extra["profile"] == "builtin-domestic"

