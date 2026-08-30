"""Test runtime/platform/config/presets.py"""

from __future__ import annotations

import pytest

from runtime.platform.config.presets import (
    apply_preset,
    get_preset_description,
    list_presets,
)
from runtime.platform.config.schema import AgentConfig


class TestPresets:
    """Test configuration presets."""

    def test_list_presets_returns_all(self) -> None:
        """Test that list_presets returns all preset names."""
        presets = list_presets()
        assert "personal" in presets
        assert "team" in presets
        assert "enterprise" in presets
        assert "research" in presets
        assert len(presets) == 4

    def test_apply_preset_personal(self) -> None:
        """Test applying personal preset."""
        config = apply_preset("personal")
        assert config.planner.type == "llm"
        assert config.planner.model == "gpt-4o-mini"
        assert config.immunity.unknown_policy == "quarantine"

    def test_apply_preset_team(self) -> None:
        """Test applying team preset."""
        config = apply_preset("team")
        assert config.planner.type == "llm"
        assert config.budget.max_usd == 2.00
        assert config.learn.min_hits == 5
        assert config.learn.max_rules == 60

    def test_apply_preset_enterprise(self) -> None:
        """Test applying enterprise preset."""
        config = apply_preset("enterprise")
        assert config.budget.max_tokens == 200000
        assert config.budget.max_usd == 10.00
        assert config.immunity.unknown_policy == "reject"
        assert config.learn.max_rules == 100

    def test_apply_preset_research(self) -> None:
        """Test applying research preset."""
        config = apply_preset("research")
        assert config.budget.max_tokens == 500000
        assert config.budget.max_usd == 50.00
        assert config.immunity.unknown_policy == "allow"
        assert config.learn.min_hits == 2
        assert config.learn.max_rules == 200

    def test_apply_preset_unknown_raises(self) -> None:
        """Test that unknown preset raises ValueError."""
        with pytest.raises(ValueError, match="unknown preset"):
            apply_preset("nonexistent")

    def test_apply_preset_with_base_config(self) -> None:
        """Test that apply_preset merges with base config."""
        base = AgentConfig(
            name="my-config",
            version_compat="0.2",
            preset="personal",
        )
        result = apply_preset("team", base)

        # Should keep base values
        assert result.name == "my-config"
        assert result.version_compat == "0.2"

        # Should apply preset values
        assert result.budget.max_usd == 2.00  # from team preset

    def test_get_preset_description(self) -> None:
        """Test that preset descriptions are defined."""
        assert "个人" in get_preset_description("personal")
        assert "团队" in get_preset_description("team")
        assert "企业" in get_preset_description("enterprise")
        assert "研究" in get_preset_description("research")

    def test_get_preset_description_unknown_returns_empty(self) -> None:
        """Test that unknown preset description returns empty string."""
        assert get_preset_description("nonexistent") == ""

    def test_presets_are_valid_configs(self) -> None:
        """Test that all presets produce valid configs."""
        for preset_name in list_presets():
            config = apply_preset(preset_name)
            # Should be a valid AgentConfig
            assert isinstance(config, AgentConfig)
            assert config.name is not None
            assert config.version_compat is not None

