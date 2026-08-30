from __future__ import annotations

import os

import pytest

from runtime.sensing.model_router import Message, ModelRequest, OpenAIModelRouter
from runtime.sensing.model_router.models import ToolSpec
from runtime.sensing.model_router.openai_compat_smoke_matrix import (
    OpenAICompatSmokeProvider,
    openai_compat_smoke_providers,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ECHO_LIVE_MODEL_SMOKE") != "1",
    reason="set ECHO_LIVE_MODEL_SMOKE=1 and provider API keys to run live smoke tests",
)


PROVIDERS = openai_compat_smoke_providers()


@pytest.mark.parametrize("provider", PROVIDERS, ids=[item.id for item in PROVIDERS])
def test_openai_compat_live_chat_smoke(provider: OpenAICompatSmokeProvider) -> None:
    api_key = _first_env(provider.api_key_env)
    if not api_key:
        pytest.skip(f"missing API key env for {provider.id}")

    model = os.environ.get(
        provider.model_env,
        provider.default_model,
    )
    router = OpenAIModelRouter(
        base_url=provider.base_url,
        api_key=api_key,
        default_model=model,
        timeout_seconds=30.0,
    )

    response = router.call(
        ModelRequest(
            model=model,
            messages=[Message(role="user", content="Reply with exactly: pong")],
            max_tokens=12,
            temperature=0.0,
        ),
    )

    assert response.text.strip()
    assert response.provider == "openai_compat"


@pytest.mark.skipif(
    os.environ.get("ECHO_LIVE_MODEL_TOOL_SMOKE") != "1",
    reason="set ECHO_LIVE_MODEL_TOOL_SMOKE=1 to run live tool-call probes",
)
@pytest.mark.parametrize("provider", PROVIDERS, ids=[item.id for item in PROVIDERS])
def test_openai_compat_live_tool_shape_smoke(provider: OpenAICompatSmokeProvider) -> None:
    api_key = _first_env(provider.api_key_env)
    if not api_key:
        pytest.skip(f"missing API key env for {provider.id}")

    model = os.environ.get(
        provider.model_env,
        provider.default_model,
    )
    router = OpenAIModelRouter(
        base_url=provider.base_url,
        api_key=api_key,
        default_model=model,
        timeout_seconds=30.0,
    )

    response = router.call(
        ModelRequest(
            model=model,
            messages=[
                Message(
                    role="user",
                    content=(
                        "Use the diagnostic_echo tool exactly once with "
                        'value "pong". Do not answer directly.'
                    ),
                ),
            ],
            tools=[
                ToolSpec(
                    name="diagnostic_echo",
                    description="Echo a short value for compatibility probing.",
                    input_schema={
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                ),
            ],
            max_tokens=48,
            temperature=0.0,
        ),
    )

    assert response.provider == "openai_compat"
    assert response.text.strip() or response.tool_calls


def _first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""

