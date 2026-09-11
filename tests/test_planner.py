"""Implementation note."""

from __future__ import annotations

import pytest

from runtime.core.cerebrum import PlannerError, StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.platform.models import BudgetSpec, ParsedIntent, SkillId


@pytest.fixture
def code_fix_rule():
    return Rule(
        name="code_fix_basic",
        intent_types=["debug"],
        keywords=["test", "pytest", "failing"],
        skill_sequence=[SkillId("read_file"), SkillId("edit_code"), SkillId("run_pytest")],
        priority=10,
    )


@pytest.fixture
def chitchat_rule():
    return Rule(
        name="chitchat",
        intent_types=["chitchat"],
        skill_sequence=[SkillId("reply_casually")],
    )


@pytest.fixture
def planner(code_fix_rule, chitchat_rule):
    return StaticPlanner(
        rules=[code_fix_rule, chitchat_rule],
        default_budget=BudgetSpec(tokens=20_000, usd=0.20),
        fallback_skill=SkillId("clarify"),
    )


class TestMatching:
    def test_matches_by_intent_and_keyword(self, planner):
        intent = ParsedIntent(
            raw="x",
            intent_type="debug",
            normalized_goal="fix failing pytest in foo.py",
        )
        graph = planner.plan(intent)
        assert graph.strategy == "code_fix_basic"
        assert len(graph.nodes) == 3
        assert [n.skill_ref for n in graph.nodes] == ["read_file", "edit_code", "run_pytest"]

    def test_matches_by_intent_only(self, planner):
        intent = ParsedIntent(raw="x", intent_type="chitchat", normalized_goal="hi there")
        graph = planner.plan(intent)
        assert graph.strategy == "chitchat"
        assert len(graph.nodes) == 1

    def test_falls_back_to_fallback_skill(self, planner):
        intent = ParsedIntent(raw="x", intent_type="query", normalized_goal="unrelated topic")
        graph = planner.plan(intent)
        assert graph.strategy == "fallback"
        assert graph.nodes[0].skill_ref == "clarify"

    def test_no_match_no_fallback_raises(self):
        p = StaticPlanner(rules=[], default_budget=BudgetSpec(tokens=100, usd=0.01))
        intent = ParsedIntent(raw="x", intent_type="query", normalized_goal="x")
        with pytest.raises(PlannerError):
            p.plan(intent)


class TestEdgeStructure:
    def test_linear_edges_between_consecutive_nodes(self, planner):
        intent = ParsedIntent(raw="x", intent_type="debug", normalized_goal="fix failing test")
        graph = planner.plan(intent)
        assert len(graph.edges) == 2
        assert graph.edges[0].from_node == "n0"
        assert graph.edges[0].to_node == "n1"


class TestTaskType:
    def test_debug_maps_to_code_fix(self, planner):
        intent = ParsedIntent(raw="x", intent_type="debug", normalized_goal="fix failing test")
        graph = planner.plan(intent)
        assert graph.task_type == "code_fix"

    def test_chitchat_maps_to_chitchat(self, planner):
        intent = ParsedIntent(raw="x", intent_type="chitchat", normalized_goal="hi")
        graph = planner.plan(intent)
        assert graph.task_type == "chitchat"


class TestPriority:
    def test_higher_priority_rule_wins(self):
        high = Rule(name="high", intent_types=["task"], skill_sequence=[SkillId("A")], priority=10)
        low = Rule(name="low", intent_types=["task"], skill_sequence=[SkillId("B")], priority=1)
        p = StaticPlanner(rules=[low, high], default_budget=BudgetSpec(tokens=100, usd=0.01))
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        graph = p.plan(intent)
        assert graph.strategy == "high"


class TestIntentPlaceholders:
    def test_rule_can_pass_goal_to_skill_specific_arg(self):
        p = StaticPlanner(
            rules=[
                Rule(
                    name="research",
                    intent_types=["task"],
                    keywords=["调研"],
                    skill_sequence=[SkillId("web_search")],
                    node_args_templates=[{"query": "{intent_goal}"}],
                )
            ],
            default_budget=BudgetSpec(tokens=100, usd=0.01),
        )
        intent = ParsedIntent(raw="nas调研", intent_type="task", normalized_goal="nas调研")

        graph = p.plan(intent)

        assert graph.strategy == "research"
        assert graph.nodes[0].args_template["query"] == "nas调研"
        assert graph.nodes[0].args_template["intent_goal"] == "nas调研"
