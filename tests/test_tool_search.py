"""Tests for tool search: goal-relevance selection in build_anthropic_tool_specs."""

from __future__ import annotations

from typing import Any

from runtime.execution.tool_spec_builder import (
    _goal_tokens,
    _relevance_score,
    build_anthropic_tool_specs,
)


class _FakeSkill:
    def __init__(self, name: str, description: str, handler: Any = None) -> None:
        self.name = name
        self.description = description
        self.handler = handler
        self.trusted_source = f"skill://public/{name}"
        self.capabilities = ()


class _FakeRegistry:
    def __init__(self, skills: dict[str, _FakeSkill]) -> None:
        self._skills = skills

    def all_names(self) -> list[str]:
        return list(self._skills)

    def is_enabled(self, name: str) -> bool:
        return True

    def has(self, name: str) -> bool:
        return name in self._skills

    def get(self, name: str) -> _FakeSkill | None:
        return self._skills.get(name)


def _registry() -> _FakeRegistry:
    skills = {
        "web_search": _FakeSkill("web_search", "Search the web for current information"),
        "read_file": _FakeSkill("read_file", "Read a file from the workspace"),
        "write_text_file": _FakeSkill("write_text_file", "Write text to a file"),
        "list_cwd": _FakeSkill("list_cwd", "List the current working directory"),
        "mcp_payments_list": _FakeSkill(
            "mcp_payments_list", "List payment transactions from the payments API"
        ),
        "mcp_payments_refund": _FakeSkill(
            "mcp_payments_refund", "Refund a payment via the payments API"
        ),
        "mcp_analytics_events": _FakeSkill("mcp_analytics_events", "Query analytics event stream"),
        "mcp_inventory_stock": _FakeSkill(
            "mcp_inventory_stock", "Check warehouse inventory levels"
        ),
    }
    return _FakeRegistry(skills)


def test_goal_tokens_filter_stopwords() -> None:
    tokens = _goal_tokens("Please help me search the web for pricing")
    assert "pricing" in tokens
    assert "search" in tokens
    assert "the" not in tokens  # stopword
    assert "please" not in tokens  # stopword


def test_relevance_score_ranks_mcp_tool_by_name_and_description() -> None:
    tokens = _goal_tokens("refund payment records")
    assert _relevance_score("mcp_payments_refund", "Refund a payment", tokens) > _relevance_score(
        "mcp_inventory_stock", "Check warehouse inventory", tokens
    )


def test_tool_search_selects_relevant_skills_over_arbitrary_order() -> None:
    reg = _registry()
    # Budget tight enough that only a handful survive.
    specs = build_anthropic_tool_specs(
        reg, max_skills=3, goal="refund a failed payment transaction"
    )
    names = {spec.name for spec in specs}
    # The payment-refund tool is the most relevant — it must survive.
    assert "mcp_payments_refund" in names
    # The inventory tool is irrelevant to a payment goal.
    assert "mcp_inventory_stock" not in names


def test_no_goal_falls_back_to_registry_order() -> None:
    reg = _registry()
    specs = build_anthropic_tool_specs(reg, max_skills=3, goal="")
    names = [spec.name for spec in specs]
    # No goal → registry order (deterministic), not relevance.
    assert names == list(reg.all_names())[:3]

