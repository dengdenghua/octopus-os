"""Tests for model-aware guard routing policy."""

from runtime.core.cerebrum.guard_model_policy import (
    classify_model_tier,
    explain_guard_policy,
    guard_categories_for_model,
    should_apply_code_smell_guards,
)


class TestClassifyModelTier:
    """Test model tier classification."""

    def test_opus_is_premium(self):
        assert classify_model_tier("claude-opus-5") == "premium"
        assert classify_model_tier("claude-3-opus-20240229") == "premium"

    def test_sonnet_is_premium(self):
        assert classify_model_tier("claude-sonnet-5") == "premium"
        assert classify_model_tier("claude-3-5-sonnet-20241022") == "premium"

    def test_o1_is_premium(self):
        assert classify_model_tier("o1-preview") == "premium"
        assert classify_model_tier("o1") == "premium"
        # o3-mini has "mini" marker so it's cheap (correct behavior)
        assert classify_model_tier("o3-mini") == "cheap"

    def test_gpt4_turbo_is_premium(self):
        assert classify_model_tier("gpt-4-turbo") == "premium"
        assert classify_model_tier("gpt-4o") == "premium"

    def test_haiku_is_cheap(self):
        assert classify_model_tier("claude-haiku-4-5-20251001") == "cheap"
        assert classify_model_tier("claude-3-haiku-20240307") == "cheap"

    def test_flash_is_cheap(self):
        assert classify_model_tier("gemini-1.5-flash") == "cheap"
        assert classify_model_tier("glm-4-flash") == "cheap"

    def test_mini_variants_are_cheap(self):
        assert classify_model_tier("gpt-4o-mini") == "cheap"
        assert classify_model_tier("gpt-3.5-turbo-mini") == "cheap"

    def test_chinese_cheap_models(self):
        assert classify_model_tier("glm-4-flash") == "cheap"
        assert classify_model_tier("qwen-turbo") == "cheap"
        assert classify_model_tier("qwen3.5-flash") == "cheap"

    def test_small_oss_models_are_cheap(self):
        assert classify_model_tier("llama-3.1-8b-instruct") == "cheap"
        assert classify_model_tier("mistral-7b-instruct") == "cheap"
        assert classify_model_tier("gemma-7b") == "cheap"
        assert classify_model_tier("phi-3-mini") == "cheap"

    def test_unknown_model(self):
        assert classify_model_tier("unknown-model-2024") == "unknown"
        assert classify_model_tier("custom-fine-tune") == "unknown"

    def test_none_is_unknown(self):
        assert classify_model_tier(None) == "unknown"

    def test_empty_string_is_unknown(self):
        assert classify_model_tier("") == "unknown"


class TestShouldApplyCodeSmellGuards:
    """Test code-smell guard enablement logic."""

    def test_premium_models_skip_code_smell(self):
        assert should_apply_code_smell_guards("claude-opus-5") is False
        assert should_apply_code_smell_guards("claude-sonnet-5") is False
        assert should_apply_code_smell_guards("o1-preview") is False
        assert should_apply_code_smell_guards("gpt-4-turbo") is False

    def test_cheap_models_need_code_smell(self):
        assert should_apply_code_smell_guards("claude-haiku-4-5-20251001") is True
        assert should_apply_code_smell_guards("gpt-4o-mini") is True
        assert should_apply_code_smell_guards("glm-4-flash") is True

    def test_unknown_models_get_guards_conservative(self):
        assert should_apply_code_smell_guards("unknown-model") is True
        assert should_apply_code_smell_guards(None) is True
        assert should_apply_code_smell_guards("") is True


class TestGuardCategoriesForModel:
    """Test guard category resolution."""

    def test_premium_model_no_code_smell(self):
        categories = guard_categories_for_model(
            "claude-opus-5",
            base_categories={"security", "protocol"},
        )
        assert "security" in categories
        assert "protocol" in categories
        assert "code-smell" not in categories

    def test_cheap_model_includes_code_smell(self):
        categories = guard_categories_for_model(
            "claude-haiku-4-5-20251001",
            base_categories={"security", "protocol"},
        )
        assert "security" in categories
        assert "protocol" in categories
        assert "code-smell" in categories

    def test_no_base_categories_only_adds_code_smell(self):
        # Premium
        categories = guard_categories_for_model("claude-opus-5")
        assert categories == frozenset()

        # Cheap
        categories = guard_categories_for_model("claude-haiku-4-5-20251001")
        assert categories == frozenset({"code-smell"})

    def test_unknown_model_conservative(self):
        categories = guard_categories_for_model(
            "unknown-model",
            base_categories={"security"},
        )
        assert "security" in categories
        assert "code-smell" in categories  # Conservative: apply guards


class TestExplainGuardPolicy:
    """Test human-readable policy explanations."""

    def test_premium_explanation(self):
        explanation = explain_guard_policy("claude-opus-5")
        assert "PREMIUM" in explanation
        assert "DISABLED" in explanation
        assert "claude-opus-5" in explanation

    def test_cheap_explanation(self):
        explanation = explain_guard_policy("claude-haiku-4-5-20251001")
        assert "CHEAP" in explanation
        assert "ENABLED" in explanation
        assert "claude-haiku-4-5-20251001" in explanation

    def test_unknown_explanation(self):
        explanation = explain_guard_policy("custom-model")
        assert "UNKNOWN" in explanation
        assert "conservative" in explanation


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_case_insensitive(self):
        assert classify_model_tier("CLAUDE-OPUS-5") == "premium"
        assert classify_model_tier("GPT-4O-MINI") == "cheap"

    def test_mixed_case(self):
        assert classify_model_tier("Claude-Haiku-4-5") == "cheap"

    def test_whitespace_handled(self):
        # Model names shouldn't have whitespace but handle gracefully
        assert classify_model_tier("  claude-opus-5  ") == "premium"

    def test_cheap_marker_takes_precedence(self):
        # If a model name somehow has both markers, cheap wins
        # (This shouldn't happen in practice but good to define behavior)
        assert classify_model_tier("opus-mini") == "cheap"
        assert classify_model_tier("haiku-opus") == "cheap"


class TestRealWorldModels:
    """Test against actual model names used in production."""

    def test_anthropic_current_models(self):
        # Current Anthropic lineup (2026)
        assert classify_model_tier("claude-opus-5") == "premium"
        assert classify_model_tier("claude-sonnet-5") == "premium"
        assert classify_model_tier("claude-haiku-4-5-20251001") == "cheap"

    def test_openai_current_models(self):
        assert classify_model_tier("gpt-4o") == "premium"
        assert classify_model_tier("gpt-4o-mini") == "cheap"
        assert classify_model_tier("o1-preview") == "premium"

    def test_chinese_models(self):
        # Zhipu AI
        assert classify_model_tier("glm-4-flash") == "cheap"

        # Alibaba
        assert classify_model_tier("qwen-turbo") == "cheap"
        assert classify_model_tier("qwen3.5-flash") == "cheap"

    def test_deepseek(self):
        assert classify_model_tier("deepseek-r1") == "premium"

