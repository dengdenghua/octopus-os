"""Implementation note."""

from __future__ import annotations

import pytest
from runtime.platform import identity_filter


@pytest.fixture(autouse=True)
def _force_lock_on(monkeypatch: pytest.MonkeyPatch):
    """Identity lock default is env-gated · force ON so every test
    exercises the filter code path regardless of the dev machine's
    ``ECHO_IDENTITY_LOCK`` setting."""
    monkeypatch.setenv("ECHO_IDENTITY_LOCK", "1")
    identity_filter.set_runtime_lock(None)
    yield
    identity_filter.set_runtime_lock(None)


class TestVendorCoverage:
    """Every major provider name must be scrubbed in both EN and ZH."""

    @pytest.mark.parametrize(
        "vendor",
        [
            "Anthropic",
            "OpenAI",
            "Moonshot AI",
            "DeepSeek",
            "MiniMax",
            "Google",
            "Microsoft",
            "Meta",
            "Alibaba",
            # The AWS regression set · MUST be scrubbed after this fix.
            "AWS",
            "Amazon",
            "Amazon Web Services",
            "Bedrock",
            # 2025 additions
            "Groq",
            "xAI",
            "Cohere",
            "Mistral",
            "Mistral AI",
        ],
    )
    def test_en_vendor_scrubbed(self, vendor: str):
        text = f"I'm an AI assistant built by {vendor}, helping you."
        out = identity_filter.filter_text(text)
        assert vendor not in out, f"vendor {vendor!r} leaked through filter: {out!r}"

    @pytest.mark.parametrize(
        "vendor",
        [
            "Anthropic",
            "OpenAI",
            "月之暗面",
            "深度求索",
            "DeepSeek",
            "MiniMax",
            "Google",
            "谷歌",
            "智谱",
            "阿里",
            "字节跳动",
            "百度",
            # AWS regression (ZH)
            "AWS",
            "亚马逊",
            "Amazon",
            "Bedrock",
            # 2025 additions
            "Groq",
            "xAI",
            "Cohere",
            "Mistral",
            "百川",
            "零一万物",
        ],
    )
    def test_zh_vendor_scrubbed(self, vendor: str):
        text = f"我是 Echo, 一个由 {vendor} 构建的 AI 助手。"
        out = identity_filter.filter_text(text)
        assert vendor not in out, f"vendor {vendor!r} leaked through filter: {out!r}"


class TestAWSRegression:
    """Exact user-visible string from the UI screenshot."""

    def test_double_vendor_clause_does_not_persist(self):
        """Simulate the compound sentence the user saw: one vendor
        scrubbed, another left behind. After the fix, both are gone."""
        text = (
            "我是 Echo, 一个由 AWS 构建的 "
            "由 Anthropic 构建的 AI 编程助手, "
            "专注于帮助开发者更高效地编写代码。"
        )
        out = identity_filter.filter_text(text)
        # Neither vendor name survives.
        assert "AWS" not in out, out
        assert "Anthropic" not in out, out
        # Brand stays.
        assert "Echo" in out, out

    def test_bedrock_scrubbed(self):
        text = "I'm Claude running on AWS Bedrock."
        out = identity_filter.filter_text(text)
        assert "Bedrock" not in out
        assert "Claude" not in out


class TestModelNames:
    @pytest.mark.parametrize(
        "model",
        [
            "Claude",
            "Kimi",
            "GPT-4",
            "Gemini",
            "GLM-4",
            "Yi-34B",
            "Grok-3",
            "Llama",
            "LLaMA",
            "Mistral",
        ],
    )
    def test_model_name_scrubbed(self, model: str):
        text = f"I'm {model}, ready to help."
        out = identity_filter.filter_text(text)
        assert model not in out, f"model {model!r} leaked: {out!r}"


class TestAgentIdentity:
    def test_non_echo_agent_self_claim_is_rewritten(self):
        class Agent:
            agent_id = "coder"
            display_name = "Coder"

        out = identity_filter.filter_text("我是 Echo, 可以帮你写代码。", agent=Agent())
        assert out.startswith("我是 Coder"), out
        assert "我是 Echo" not in out

    def test_non_echo_agent_english_self_claim_is_rewritten(self):
        class Agent:
            agent_id = "researcher"
            display_name = "Researcher"

        out = identity_filter.filter_text("I'm Echo, ready to research.", agent=Agent())
        assert out.startswith("I'm Researcher"), out
        assert "I'm Echo" not in out


class TestLockingOff:
    def test_no_rewrite_when_lock_off(self, monkeypatch: pytest.MonkeyPatch):
        """Developer mode · ECHO_IDENTITY_LOCK=0 · filter is a no-op."""
        monkeypatch.setenv("ECHO_IDENTITY_LOCK", "0")
        identity_filter.set_runtime_lock(None)
        text = "I'm Claude, built by Anthropic."
        # No rewrite · Claude / Anthropic survive verbatim.
        assert identity_filter.filter_text(text) == text
