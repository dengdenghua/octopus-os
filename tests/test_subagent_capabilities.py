"""dsh-style subagent capability declarations + fail-loud checks.

Absorbed from DeepSeek Harness (2026-08-14): a subagent definition
declares the capabilities it supports; a request that needs one the
definition lacks is rejected loudly BEFORE any runner work starts —
never accepted-then-ignored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.execution.subagents import bridge
from runtime.execution.subagents.registry import (
    SubagentDefinition,
    SubagentRegistry,
    load_subagent_file,
)


def _definition(*, capabilities: tuple[str, ...] = ()) -> SubagentDefinition:
    return SubagentDefinition(
        name="code-reviewer",
        description="Reviews risky changes.",
        system_prompt="You are a reviewer.",
        tools=("Read", "Grep"),
        capabilities=capabilities,
    )


@pytest.fixture
def clean_bridge() -> None:
    bridge.set_subagent_registry(None)
    bridge.set_sub_agent_runner(None)
    yield
    bridge.set_subagent_registry(None)
    bridge.set_sub_agent_runner(None)


class TestFrontmatterParsing:
    def test_capabilities_parsed_and_normalized(self, tmp_path: Path) -> None:
        path = tmp_path / "reviewer.md"
        path.write_text(
            "---\n"
            "name: reviewer\n"
            "description: Reviews.\n"
            "capabilities: [output_schema, Tool_Filter, output_schema]\n"
            "---\n"
            "Review everything.\n",
            encoding="utf-8",
        )
        definition = load_subagent_file(path, scope="project")
        assert definition.capabilities == ("output_schema", "tool_filter")

    def test_comma_separated_capabilities(self, tmp_path: Path) -> None:
        path = tmp_path / "comma.md"
        path.write_text(
            "---\n"
            "name: comma\n"
            "description: Comma list.\n"
            "capabilities: depth_limit, persona\n"
            "---\n"
            "Body.\n",
            encoding="utf-8",
        )
        definition = load_subagent_file(path, scope="project")
        assert definition.capabilities == ("depth_limit", "persona")

    def test_missing_capabilities_defaults_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "plain.md"
        path.write_text(
            "---\nname: plain\ndescription: No caps.\n---\nBody.\n",
            encoding="utf-8",
        )
        assert load_subagent_file(path, scope="project").capabilities == ()


class TestRegistryQueries:
    def test_supports_and_capabilities_of(self) -> None:
        registry = SubagentRegistry([_definition(capabilities=("output_schema",))])
        assert registry.supports("code-reviewer", "output_schema") is True
        assert registry.supports("code-reviewer", "continuable") is False
        assert registry.supports("missing", "output_schema") is False
        assert registry.capabilities_of("code-reviewer") == ("output_schema",)
        assert registry.capabilities_of("missing") == ()

    def test_wire_serialization_includes_capabilities(self) -> None:
        definition = _definition(capabilities=("output_schema", "tool_filter"))
        wire = definition.to_wire()
        assert wire["capabilities"] == ["output_schema", "tool_filter"]


class TestFailLoudDispatch:
    def test_missing_capability_fails_before_runner(
        self,
        clean_bridge: None,
    ) -> None:
        runner_calls: list[tuple[str, str]] = []

        def runner(agent_id: str, prompt: str) -> str:
            runner_calls.append((agent_id, prompt))
            return "ran"

        bridge.set_subagent_registry(SubagentRegistry([_definition(capabilities=("tool_filter",))]))
        bridge.set_sub_agent_runner(runner)

        result = bridge.call_subagent(
            "code-reviewer",
            "review this",
            requires_capabilities=["output_schema"],
        )
        assert result["success"] is False
        assert result.get("capability_error") == "missing_required_capability"
        assert "lacks required capability" in result["error"]
        assert "output_schema" in result["error"]
        assert runner_calls == []

    def test_declared_capability_passes_the_gate(
        self,
        clean_bridge: None,
    ) -> None:
        bridge.set_subagent_registry(
            SubagentRegistry([_definition(capabilities=("output_schema", "continuable"))])
        )

        result = bridge.call_subagent(
            "code-reviewer",
            "review this",
            requires_capabilities=["output_schema", "continuable"],
        )
        # The gate passed: the error (if any) is no longer about
        # missing capabilities — the dispatch proceeded downstream.
        assert result.get("capability_error") is None
        assert "lacks required capability" not in (result.get("error") or "")

    def test_unregistered_agent_skips_capability_gate(
        self,
        clean_bridge: None,
    ) -> None:
        bridge.set_subagent_registry(SubagentRegistry([_definition()]))
        result = bridge.call_subagent(
            "not-registered",
            "hello",
            requires_capabilities=["output_schema"],
        )
        assert result.get("capability_error") is None

    def test_no_requirements_is_noop(self, clean_bridge: None) -> None:
        bridge.set_subagent_registry(SubagentRegistry([_definition()]))
        result = bridge.call_subagent("code-reviewer", "hello")
        assert result.get("capability_error") is None

