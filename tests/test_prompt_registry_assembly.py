"""dsh-style system-prompt assembly tests.

Absorbed from DeepSeek Harness (2026-08-14): ordered ``PromptSection``
registration with a ``complete`` override, dynamic runtime contexts
with ``suppress_runtime_context``, ``{{variable}}`` interpolation, and
per-scope shadowing. The file-template API (``get``/``set``/``list``)
is untouched and must keep working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.platform.prompts.registry import PromptRegistry


@pytest.fixture
def registry(tmp_path: Path) -> PromptRegistry:
    return PromptRegistry(tmp_path / "prompts")


class TestOrderedSections:
    def test_sections_join_in_ascending_order(self, registry: PromptRegistry) -> None:
        registry.register_section("tools", order=100, text="TOOLS")
        registry.register_section("identity", order=-100, text="IDENTITY")
        registry.register_section("persona", order=0, text="PERSONA")

        assert registry.assemble() == "IDENTITY\n\nPERSONA\n\nTOOLS"

    def test_duplicate_name_rejected(self, registry: PromptRegistry) -> None:
        registry.register_section("a", order=0, text="A")
        with pytest.raises(ValueError, match="already registered"):
            registry.register_section("a", order=1, text="B")

    def test_requires_text_or_provider(self, registry: PromptRegistry) -> None:
        with pytest.raises(ValueError, match="needs text or provider"):
            registry.register_section("empty", order=0)

    def test_disposer_removes_section(self, registry: PromptRegistry) -> None:
        dispose = registry.register_section("temp", order=0, text="TEMP")
        assert "TEMP" in registry.assemble()
        dispose()
        assert "TEMP" not in registry.assemble()


class TestCompleteSection:
    def test_complete_replaces_everything(self, registry: PromptRegistry) -> None:
        registry.register_section("identity", order=-100, text="IDENTITY")
        registry.register_section(
            "override",
            order=0,
            text="ONLY THIS",
            complete=True,
        )
        assert registry.assemble() == "ONLY THIS"

    def test_multiple_complete_fails_loud(self, registry: PromptRegistry) -> None:
        registry.register_section("c1", order=0, text="A", complete=True)
        registry.register_section("c2", order=1, text="B", complete=True)
        with pytest.raises(ValueError, match="more than one complete"):
            registry.assemble()

    def test_dynamic_provider_section(self, registry: PromptRegistry) -> None:
        registry.register_section(
            "dynamic",
            order=0,
            provider=lambda scope: f"scope={scope or 'global'}",
        )
        assert registry.assemble() == "scope=global"
        assert registry.assemble(scope="agent-a") == "scope=agent-a"


class TestVariables:
    def test_variable_interpolation(self, registry: PromptRegistry) -> None:
        registry.register_section("greeting", order=0, text="Hello {{name}}!")
        registry.register_variable("name", lambda _scope: "World")
        assert registry.assemble() == "Hello World!"

    def test_unknown_variable_fails_loud(self, registry: PromptRegistry) -> None:
        registry.register_section("bad", order=0, text="Hi {{missing}}")
        with pytest.raises(ValueError, match="not registered"):
            registry.assemble()

    def test_none_variable_fails_loud(self, registry: PromptRegistry) -> None:
        registry.register_section("bad", order=0, text="Hi {{value}}")
        registry.register_variable("value", lambda _scope: None)
        with pytest.raises(ValueError, match="resolved to None"):
            registry.assemble()

    def test_scoped_variable_shadows_global(self, registry: PromptRegistry) -> None:
        registry.register_section("greeting", order=0, text="Hello {{name}}!")
        registry.register_variable("name", lambda _scope: "Global")
        registry.register_variable(
            "name",
            lambda _scope: "Scoped",
            scope="agent-a",
        )
        assert registry.assemble() == "Hello Global!"
        assert registry.assemble(scope="agent-a") == "Hello Scoped!"


class TestRuntimeContextSuppression:
    def test_contexts_join_after_sections(self, registry: PromptRegistry) -> None:
        registry.register_section("persona", order=0, text="PERSONA")
        registry.register_context("cwd", order=0, text="CWD=/tmp")
        assert registry.assemble() == "PERSONA\n\nCWD=/tmp"

    def test_suppress_removes_contexts_keeps_sections(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.register_section("persona", order=0, text="PERSONA")
        registry.register_context("cwd", order=0, text="CWD=/tmp")
        unsuppress = registry.suppress_runtime_context()
        assert registry.assemble() == "PERSONA"
        unsuppress()
        assert registry.assemble() == "PERSONA\n\nCWD=/tmp"

    def test_scoped_suppression_only_hits_that_scope(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.register_context("cwd", order=0, text="CWD=/tmp")
        registry.suppress_runtime_context(scope="agent-a")
        assert registry.assemble(scope="agent-a") == ""
        assert registry.assemble(scope="agent-b") == "CWD=/tmp"
        assert registry.assemble() == "CWD=/tmp"

    def test_global_suppression_hits_every_scope(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.register_context("cwd", order=0, text="CWD=/tmp")
        registry.suppress_runtime_context()
        assert registry.assemble(scope="agent-a") == ""


class TestScopedSections:
    def test_scoped_section_shadows_global(self, registry: PromptRegistry) -> None:
        registry.register_section("persona", order=0, text="GLOBAL PERSONA")
        registry.register_section(
            "persona",
            order=0,
            text="AGENT PERSONA",
            scope="agent-a",
        )
        assert registry.assemble() == "GLOBAL PERSONA"
        assert registry.assemble(scope="agent-a") == "AGENT PERSONA"

    def test_scoped_section_invisible_to_other_scopes(
        self,
        registry: PromptRegistry,
    ) -> None:
        registry.register_section("secret", order=0, text="SECRET", scope="agent-a")
        assert registry.assemble(scope="agent-a") == "SECRET"
        assert registry.assemble(scope="agent-b") == ""
        assert registry.assemble() == ""

    def test_sections_enumerates_effective_set(self, registry: PromptRegistry) -> None:
        registry.register_section("a", order=10, text="A")
        registry.register_section(
            "b",
            order=0,
            text="B",
            provider=lambda _scope: "B",
            scope="agent-a",
        )
        listed = registry.sections(scope="agent-a")
        assert [s["name"] for s in listed] == ["b", "a"]
        assert listed[0]["dynamic"] is True
        assert listed[0]["complete"] is False


class TestFileTemplateCompatibility:
    def test_get_set_unaffected_by_assembly(self, tmp_path: Path) -> None:
        reg = PromptRegistry(tmp_path / "prompts")
        reg.register_section("persona", order=0, text="ASSEMBLED")
        reg.set("system_prompt", "FROM FILE")
        assert reg.get("system_prompt").strip() == "FROM FILE"
        assert reg.assemble() == "ASSEMBLED"

