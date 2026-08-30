"""Per-provider default reasoning effort (dsh-style) + ``off`` passthrough."""

from __future__ import annotations

from runtime.platform.models import custom_model_flags
from runtime.platform.models.custom_model_flags import (
    custom_model_default_reasoning_effort,
)
from runtime.platform.models.llm import default_reasoning_effort
from runtime.sensing.gateway._openai_gateway_router_helpers import (
    _reasoning_effort_from_body,
)


class TestBuiltinDefaults:
    def test_deepseek_v4_defaults_to_high(self) -> None:
        assert default_reasoning_effort("deepseek-v4-flash") == "high"
        assert default_reasoning_effort("deepseek-v4-pro") == "high"

    def test_deepseek_reasoner_defaults_to_high(self) -> None:
        assert default_reasoning_effort("deepseek-reasoner") == "high"

    def test_other_models_have_no_default(self) -> None:
        assert default_reasoning_effort("gpt-5") is None
        assert default_reasoning_effort("claude-sonnet-4-5") is None
        assert default_reasoning_effort("qwen3-max") is None


class TestCustomModelOverrides:
    def test_entry_can_pin_off(self, monkeypatch) -> None:
        monkeypatch.setattr(
            custom_model_flags,
            "read_custom_models",
            lambda: {"m": {"id": "my-deepseek", "default_reasoning_effort": "off"}},
        )
        assert default_reasoning_effort("my-deepseek") == "off"

    def test_entry_can_pin_max(self, monkeypatch) -> None:
        monkeypatch.setattr(
            custom_model_flags,
            "read_custom_models",
            lambda: {"m": {"id": "my-deepseek", "default_reasoning_effort": "max"}},
        )
        assert default_reasoning_effort("my-deepseek") == "max"

    def test_entry_none_disables_even_builtin_default(self, monkeypatch) -> None:
        monkeypatch.setattr(
            custom_model_flags,
            "read_custom_models",
            lambda: {"m": {"id": "deepseek-v4-flash", "default_reasoning_effort": "none"}},
        )
        assert default_reasoning_effort("deepseek-v4-flash") is None

    def test_entry_malformed_falls_back_to_builtin(self, monkeypatch) -> None:
        monkeypatch.setattr(
            custom_model_flags,
            "read_custom_models",
            lambda: {"m": {"id": "deepseek-v4-flash", "default_reasoning_effort": "turbo"}},
        )
        assert custom_model_default_reasoning_effort("deepseek-v4-flash") is None
        assert default_reasoning_effort("deepseek-v4-flash") == "high"


class TestBodyEffortPassthrough:
    def test_deepseek_off_passes_through_gateway_helper(self) -> None:
        assert _reasoning_effort_from_body({"reasoning_effort": "off"}) == "off"

    def test_disabled_alias_passes_through(self) -> None:
        assert _reasoning_effort_from_body({"reasoning_effort": "disabled"}) == "off"

    def test_openai_tiers_still_normalized(self) -> None:
        assert _reasoning_effort_from_body({"reasoning_effort": "xhigh"}) == "xhigh"
        assert _reasoning_effort_from_body({"reasoning_effort": "extra_high"}) == "xhigh"

    def test_absent_returns_none(self) -> None:
        assert _reasoning_effort_from_body({"model": "deepseek-v4-flash"}) is None

