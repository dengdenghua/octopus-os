"""Tests for extended preset and persona system.

Echo Native preset/persona tests.
"""

from __future__ import annotations

import pytest

from runtime.platform.config.presets_extended import (
    apply_persona,
    apply_preset,
    get_persona_description,
    get_persona_details,
    get_preset_description,
    get_preset_details,
    list_personas,
    list_presets,
)
from runtime.platform.config.schema import AgentConfig


class TestTaskPresets:
    """Test task-oriented presets."""

    def test_list_task_presets(self) -> None:
        """List task presets."""
        presets = list_presets(category="task")
        assert "code-reviewer" in presets
        assert "researcher" in presets
        assert "debugger" in presets
        assert "writer" in presets
        assert "ops" in presets

    def test_apply_code_reviewer_preset(self) -> None:
        """Apply code-reviewer preset."""
        config = apply_preset("code-reviewer")
        assert isinstance(config, AgentConfig)
        assert config.budget is not None
        assert config.immunity is not None

    def test_apply_researcher_preset(self) -> None:
        """Apply researcher preset."""
        config = apply_preset("researcher")
        assert isinstance(config, AgentConfig)
        # Researcher has higher budget
        assert config.budget is not None
        assert config.budget.max_tokens == 500000

    def test_apply_debugger_preset(self) -> None:
        """Apply debugger preset."""
        config = apply_preset("debugger")
        assert isinstance(config, AgentConfig)

    def test_apply_writer_preset(self) -> None:
        """Apply writer preset."""
        config = apply_preset("writer")
        assert isinstance(config, AgentConfig)

    def test_apply_ops_preset(self) -> None:
        """Apply ops preset."""
        config = apply_preset("ops")
        assert isinstance(config, AgentConfig)
        # Ops has strict security
        assert config.immunity is not None
        assert config.immunity.unknown_policy == "reject"

    def test_get_preset_description(self) -> None:
        """Get preset descriptions."""
        desc = get_preset_description("code-reviewer")
        assert "代码审查" in desc or "review" in desc.lower()

        desc = get_preset_description("researcher")
        assert "研究" in desc or "research" in desc.lower()

    def test_get_preset_details(self) -> None:
        """Get full preset details."""
        details = get_preset_details("code-reviewer")
        assert "description" in details
        assert "tool_allowlist" in details
        assert "system_prompt_additions" in details

        # Check tool allowlist
        tools = details["tool_allowlist"]
        assert "read_file" in tools
        assert "lint_check" in tools


class TestUsagePresets:
    """Test usage-based presets (existing)."""

    def test_list_usage_presets(self) -> None:
        """List usage presets."""
        presets = list_presets(category="usage")
        assert "personal" in presets
        assert "team" in presets
        assert "enterprise" in presets
        assert "research" in presets

    def test_apply_personal_preset(self) -> None:
        """Apply personal preset."""
        config = apply_preset("personal")
        assert isinstance(config, AgentConfig)

    def test_apply_team_preset(self) -> None:
        """Apply team preset."""
        config = apply_preset("team")
        assert isinstance(config, AgentConfig)

    def test_apply_enterprise_preset(self) -> None:
        """Apply enterprise preset."""
        config = apply_preset("enterprise")
        assert isinstance(config, AgentConfig)


class TestAllPresets:
    """Test combined preset listing."""

    def test_list_all_presets(self) -> None:
        """List all presets (usage + task)."""
        presets = list_presets()
        # Usage presets
        assert "personal" in presets
        assert "team" in presets
        # Task presets
        assert "code-reviewer" in presets
        assert "researcher" in presets

    def test_apply_preset_invalid(self) -> None:
        """Reject invalid preset name."""
        with pytest.raises(ValueError, match="Unknown preset"):
            apply_preset("nonexistent-preset")

    def test_apply_preset_with_base(self) -> None:
        """Apply preset with base config."""
        base = AgentConfig()
        config = apply_preset("code-reviewer", base=base)
        assert isinstance(config, AgentConfig)


class TestPersonas:
    """Test conversation personas."""

    def test_list_personas(self) -> None:
        """List available personas."""
        personas = list_personas()
        assert "senior-engineer" in personas
        assert "beginner-friendly" in personas
        assert "academic" in personas
        assert "casual" in personas
        assert "tutor" in personas

    def test_apply_senior_engineer_persona(self) -> None:
        """Apply senior-engineer persona."""
        persona = apply_persona("senior-engineer")
        assert persona["tone"] == "professional"
        assert persona["verbosity"] == "concise"
        assert "system_prompt_additions" in persona

    def test_apply_beginner_friendly_persona(self) -> None:
        """Apply beginner-friendly persona."""
        persona = apply_persona("beginner-friendly")
        assert persona["tone"] == "friendly"
        assert persona["verbosity"] == "detailed"
        assert len(persona["system_prompt_additions"]) > 0

    def test_apply_academic_persona(self) -> None:
        """Apply academic persona."""
        persona = apply_persona("academic")
        assert persona["tone"] == "formal"
        assert persona["verbosity"] == "detailed"

    def test_apply_casual_persona(self) -> None:
        """Apply casual persona."""
        persona = apply_persona("casual")
        assert persona["tone"] == "casual"

    def test_apply_tutor_persona(self) -> None:
        """Apply tutor persona."""
        persona = apply_persona("tutor")
        assert persona["tone"] == "encouraging"
        assert "system_prompt_additions" in persona

    def test_apply_persona_invalid(self) -> None:
        """Reject invalid persona name."""
        with pytest.raises(ValueError, match="Unknown persona"):
            apply_persona("nonexistent-persona")

    def test_get_persona_description(self) -> None:
        """Get persona descriptions."""
        desc = get_persona_description("senior-engineer")
        assert "资深工程师" in desc or "senior" in desc.lower()

        desc = get_persona_description("beginner-friendly")
        assert "新手" in desc or "beginner" in desc.lower()

    def test_get_persona_details(self) -> None:
        """Get full persona details."""
        details = get_persona_details("senior-engineer")
        assert "description" in details
        assert "tone" in details
        assert "verbosity" in details
        assert "system_prompt_additions" in details


class TestPresetPersonaCombination:
    """Test combining presets and personas."""

    def test_code_reviewer_with_senior_engineer(self) -> None:
        """Combine code-reviewer preset with senior-engineer persona."""
        config = apply_preset("code-reviewer")
        persona = apply_persona("senior-engineer")

        assert config is not None
        assert persona is not None
        # Both should have system_prompt_additions that can be combined
        preset_details = get_preset_details("code-reviewer")
        assert "system_prompt_additions" in preset_details
        assert "system_prompt_additions" in persona

    def test_researcher_with_academic(self) -> None:
        """Combine researcher preset with academic persona."""
        config = apply_preset("researcher")
        persona = apply_persona("academic")

        assert config is not None
        assert persona is not None

    def test_writer_with_beginner_friendly(self) -> None:
        """Combine writer preset with beginner-friendly persona."""
        config = apply_preset("writer")
        persona = apply_persona("beginner-friendly")

        assert config is not None
        assert persona is not None


class TestPresetMetadata:
    """Test preset metadata and details."""

    def test_preset_has_description(self) -> None:
        """All presets have descriptions."""
        for preset in list_presets():
            desc = get_preset_description(preset)
            assert desc, f"Preset {preset} missing description"

    def test_task_preset_has_tools(self) -> None:
        """Task presets have tool allowlists."""
        task_presets = list_presets(category="task")
        for preset in task_presets:
            details = get_preset_details(preset)
            assert "tool_allowlist" in details
            assert len(details["tool_allowlist"]) > 0

    def test_task_preset_has_prompt_additions(self) -> None:
        """Task presets have system prompt additions."""
        task_presets = list_presets(category="task")
        for preset in task_presets:
            details = get_preset_details(preset)
            assert "system_prompt_additions" in details
            assert len(details["system_prompt_additions"]) > 0

    def test_persona_has_description(self) -> None:
        """All personas have descriptions."""
        for persona in list_personas():
            desc = get_persona_description(persona)
            assert desc, f"Persona {persona} missing description"

    def test_persona_has_tone(self) -> None:
        """All personas have tone."""
        for persona in list_personas():
            details = get_persona_details(persona)
            assert "tone" in details
            assert details["tone"]

    def test_persona_has_verbosity(self) -> None:
        """All personas have verbosity."""
        for persona in list_personas():
            details = get_persona_details(persona)
            assert "verbosity" in details
            assert details["verbosity"]


class TestPresetToolAllowlist:
    """Test tool allowlist functionality."""

    def test_code_reviewer_tools(self) -> None:
        """Code reviewer has appropriate tools."""
        details = get_preset_details("code-reviewer")
        tools = details["tool_allowlist"]

        # Should have file operations
        assert "read_file" in tools
        assert "list_files" in tools
        # Should have testing/linting
        assert "run_tests" in tools or "lint_check" in tools
        # Should NOT have web tools
        assert "web_search" not in tools

    def test_researcher_tools(self) -> None:
        """Researcher has web and file tools."""
        details = get_preset_details("researcher")
        tools = details["tool_allowlist"]

        # Should have web tools
        assert "web_search" in tools or "web_fetch" in tools
        # Should have file operations
        assert "read_file" in tools
        assert "write_file" in tools

    def test_ops_tools(self) -> None:
        """Ops has system management tools."""
        details = get_preset_details("ops")
        tools = details["tool_allowlist"]

        # Should have bash
        assert "bash" in tools
        # Should have file operations
        assert "read_file" in tools


class TestSystemPromptAdditions:
    """Test system prompt additions."""

    def test_preset_prompt_additions(self) -> None:
        """Presets have meaningful prompt additions."""
        details = get_preset_details("code-reviewer")
        additions = details["system_prompt_additions"]

        assert len(additions) > 0
        # Check one addition mentions code quality
        combined = " ".join(additions).lower()
        assert "code" in combined or "quality" in combined

    def test_persona_prompt_additions(self) -> None:
        """Personas have meaningful prompt additions."""
        details = get_persona_details("senior-engineer")
        additions = details["system_prompt_additions"]

        assert len(additions) > 0
        # Check one addition mentions professional or concise
        combined = " ".join(additions).lower()
        assert "professional" in combined or "concise" in combined

