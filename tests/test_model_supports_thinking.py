"""Coverage tests for ``model_supports_thinking``.

The reasoning surface (collapsible thinking block) only fills when the
kernel asks the provider for a thinking channel, and the ask is gated on
this predicate. False negatives leave the block empty even though the
stream layer would happily pass ``reasoning_content`` through; false
positives are worse (a non-thinking model may reject the thinking
params), so the negative cases below pin the exclusions just as firmly.
"""

from __future__ import annotations

import pytest

from runtime.platform.models.llm import model_supports_thinking


@pytest.mark.parametrize(
    "model",
    [
        "o1-preview",
        "o3-mini",
        "o4-mini",
        "gpt-5.5",
        "gpt-5-codex",
        "gpt-oss-120b",
        "deepseek-v4-pro",
        "deepseek-reasoner",
        # Newly covered reasoning families:
        "kimi-thinking-preview",
        "kimi-k2-thinking",
        "moonshot/kimi-k2-thinking",
        "qwen3-235b-a22b",
        "qwen3-32b",
        "glm-4.5",
        "glm-4.6",
        "glm-4.5-air",
        "gemini-2.5-pro",
        "gemini-3-flash",
        "claude-sonnet-4-5",
        "claude-opus-4-1",
    ],
)
def test_thinking_capable_models(model: str) -> None:
    assert model_supports_thinking(model) is True


@pytest.mark.parametrize(
    "model",
    [
        "",
        "gpt-4o",
        "gpt-4.1",
        "claude-3-7-sonnet",
        "deepseek-chat",
        # Plain K2 / K2-instruct do not expose a thinking channel.
        "kimi-k2",
        "kimi-k2-instruct",
        "moonshot-v1-128k",
        # The Qwen3 instruct refresh dropped the thinking mode.
        "qwen3-30b-a3b-instruct-2507",
        "qwen3-235b-a22b-instruct-2507",
        # Earlier GLM generations have no thinking mode.
        "glm-4",
        "glm-4-flash",
        # Gemini 2.0 and earlier do not think.
        "gemini-2.0-flash",
    ],
)
def test_non_thinking_models(model: str) -> None:
    assert model_supports_thinking(model) is False

