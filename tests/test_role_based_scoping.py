"""Implementation note."""

from __future__ import annotations

import json

import pytest

from runtime.core.cerebrum import LLMPlanner, StaticPlanner
from runtime.core.cerebrum.planner import PlannerError, Rule
from runtime.core.graph_runtime import GraphRuntime
from runtime.execution.arms.base import ArmPool, Worker
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    BudgetSpec,
    ParsedIntent,
    SkillId,
)
from runtime.sensing.model_router import MockModelRouter

# ═══════════════════════════════════════════════════════════
# Test doubles
# ═══════════════════════════════════════════════════════════


def _mk_skill(name: str) -> Skill:
    return Skill(
        name=name,
        description=f"skill {name}",
        trusted_source=f"skill://public/{name}",
        handler=lambda **_kw: {"ok": True},
    )


def _mk_registry(*names: str) -> SkillRegistry:
    reg = SkillRegistry()
    for n in names:
        reg.register(_mk_skill(n), verify_tests=False)
    return reg


@pytest.fixture
def registry_large() -> SkillRegistry:
    """Implementation note."""
    return _mk_registry(
        "read_file",
        "write_file",
        "list_dir",
        "web_search",
        "rag_lookup",
        "summarize",
        "exec_shell",
        "git_commit",
        "git_log",
        "hash_text",
    )


def _mk_arm(
    arm_id: str,
    *,
    affinity: list[str],
    skills: list[str],
) -> Worker:
    runtime = GraphRuntime(executor=_FakeExecutor(), journal=None)
    return Worker(
        arm_id=ArmId(arm_id),
        affinity=affinity,
        allowed_skills=[SkillId(s) for s in skills],
        runtime=runtime,
    )


class _FakeExecutor:
    """Implementation note."""

    journal = None


# ═══════════════════════════════════════════════════════════
# ArmPool.pick_for_intent
# ═══════════════════════════════════════════════════════════


class TestPickForIntent:
    def test_affinity_keyword_match(self):
        code_arm = _mk_arm(
            "code_arm",
            affinity=["code", "git"],
            skills=["read_file", "git_commit"],
        )
        search_arm = _mk_arm(
            "search_arm",
            affinity=["search", "web"],
            skills=["web_search", "rag_lookup"],
        )
        pool = ArmPool([code_arm, search_arm])

        # Implementation note.
        intent = ParsedIntent(
            raw="commit the changes",
            intent_type="task",
            normalized_goal="make a git commit",
        )
        assert pool.pick_for_intent(intent) is code_arm

        # Implementation note.
        intent2 = ParsedIntent(
            raw="go search the web",
            intent_type="task",
            normalized_goal="search the web for python tutorials",
        )
        assert pool.pick_for_intent(intent2) is search_arm

    def test_highest_score_wins(self):
        a1 = _mk_arm("a1", affinity=["code"], skills=["read_file"])
        a2 = _mk_arm(
            "a2",
            affinity=["code", "git", "test"],
            skills=["read_file", "git_commit", "run_test"],
        )
        pool = ArmPool([a1, a2])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="write code with git test coverage",
        )
        # Implementation note.
        assert pool.pick_for_intent(intent) is a2

    def test_no_match_returns_none(self):
        arm = _mk_arm(
            "code_arm",
            affinity=["code", "git"],
            skills=["read_file"],
        )
        pool = ArmPool([arm])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="make coffee quickly",
        )
        assert pool.pick_for_intent(intent) is None

    def test_empty_pool(self):
        pool = ArmPool([])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="anything",
        )
        assert pool.pick_for_intent(intent) is None

    def test_tie_first_wins(self):
        """Implementation note."""
        a1 = _mk_arm("first", affinity=["text"], skills=["read_file"])
        a2 = _mk_arm("second", affinity=["text"], skills=["write_file"])
        pool = ArmPool([a1, a2])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="process text",
        )
        assert pool.pick_for_intent(intent) is a1

    def test_case_insensitive_match(self):
        arm = _mk_arm("a", affinity=["Code"], skills=["read_file"])
        pool = ArmPool([arm])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="write some CODE in Python",
        )
        assert pool.pick_for_intent(intent) is arm

    def test_substring_match_still_counts(self):
        """Implementation note."""
        arm = _mk_arm("a", affinity=["git"], skills=["git_log"])
        pool = ArmPool([arm])
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="read my github repo",
        )
        assert pool.pick_for_intent(intent) is arm


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLLMPlannerScopedContext:
    def _make_planner(self, registry: SkillRegistry, router: MockModelRouter):
        journal = InMemoryJournal()
        composer = ContextComposer(registry=registry, journal=journal)
        return LLMPlanner(
            router=router,
            registry=registry,
            composer=composer,
        )

    def test_allowed_skills_only_injects_these(self, registry_large):
        """Implementation note."""
        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "x",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        planner = self._make_planner(registry_large, router)
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="read a file",
        )
        planner.plan(
            intent,
            allowed_skills=["read_file", "write_file"],
        )

        req = router.call_log[0]
        all_text = "\n".join(m.content for m in req.messages)
        # Implementation note.
        assert "read_file" in all_text
        # Implementation note.
        sucker_segs = [m.content for m in req.messages if m.role != "system"]
        sucker_text = "\n".join(sucker_segs)
        assert "web_search" not in sucker_text
        assert "git_commit" not in sucker_text
        assert "rag_lookup" not in sucker_text

    def test_default_still_injects_all(self, registry_large):
        """Implementation note."""
        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "x",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        planner = self._make_planner(registry_large, router)
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="anything",
        )
        planner.plan(intent)
        sucker_text = "\n".join(
            m.content for m in router.call_log[0].messages if m.role != "system"
        )
        # Implementation note.
        assert "read_file" in sucker_text
        assert "web_search" in sucker_text
        assert "git_commit" in sucker_text

    def test_token_savings_observable(self, registry_large):
        """Implementation note."""
        router_full = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        router_scoped = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        p_full = self._make_planner(registry_large, router_full)
        p_scoped = self._make_planner(registry_large, router_scoped)
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="do stuff",
        )
        p_full.plan(intent)
        p_scoped.plan(intent, allowed_skills=["read_file"])

        full_len = sum(len(m.content) for m in router_full.call_log[0].messages)
        scoped_len = sum(len(m.content) for m in router_scoped.call_log[0].messages)
        assert scoped_len < full_len, (
            f"scoped ({scoped_len}) should be shorter than full ({full_len})"
        )

    def test_wildcard_allowed_skills_injects_full_catalog(self, registry_large):
        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "",
                    "nodes": [{"skill": "read_file", "args": {}}],
                }
            ),
        )
        planner = self._make_planner(registry_large, router)
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="do stuff",
        )
        planner.plan(intent, allowed_skills=["*"])

        sucker_text = "\n".join(
            m.content for m in router.call_log[0].messages if m.role != "system"
        )
        assert "read_file" in sucker_text
        assert "web_search" in sucker_text
        assert "git_commit" in sucker_text


# ═══════════════════════════════════════════════════════════
# StaticPlanner.plan(allowed_skills=...)
# ═══════════════════════════════════════════════════════════


class TestStaticPlannerScoped:
    def test_rule_skipped_when_skill_not_allowed(self):
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="code_rule",
                    intent_types=["task"],
                    skill_sequence=[SkillId("git_commit")],
                ),
                Rule(
                    name="read_rule",
                    intent_types=["task"],
                    skill_sequence=[SkillId("read_file")],
                ),
            ],
            default_budget=BudgetSpec(tokens=1000, usd=0.1),
            fallback_skill=SkillId("hash_text"),
        )
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="x",
        )
        # Implementation note.
        graph = planner.plan(intent, allowed_skills=["read_file"])
        assert graph.strategy == "read_rule"

    def test_fallback_blocked_if_not_in_allowlist(self):
        """Implementation note."""
        planner = StaticPlanner(
            rules=[],
            default_budget=BudgetSpec(tokens=1000, usd=0.1),
            fallback_skill=SkillId("hash_text"),
        )
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="x",
        )
        with pytest.raises(PlannerError):
            planner.plan(intent, allowed_skills=["read_file"])

    def test_no_allowed_skills_behaves_as_before(self):
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="any_rule",
                    intent_types=["task"],
                    skill_sequence=[SkillId("git_commit")],
                ),
            ],
            default_budget=BudgetSpec(tokens=1000, usd=0.1),
            fallback_skill=SkillId("hash_text"),
        )
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="x",
        )
        # Implementation note.
        graph = planner.plan(intent)
        assert graph.strategy == "any_rule"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEnd:
    def test_full_per_role_flow(self, registry_large):
        """Implementation note."""
        code_arm = _mk_arm(
            "code_arm",
            affinity=["code", "git"],
            skills=["read_file", "git_commit", "git_log"],
        )
        search_arm = _mk_arm(
            "search_arm",
            affinity=["search", "web"],
            skills=["web_search", "rag_lookup"],
        )
        pool = ArmPool([code_arm, search_arm])

        router = MockModelRouter(
            response=json.dumps(
                {
                    "reasoning": "x",
                    "nodes": [{"skill": "git_commit", "args": {}}],
                }
            ),
        )
        journal = InMemoryJournal()
        composer = ContextComposer(registry=registry_large, journal=journal)
        planner = LLMPlanner(
            router=router,
            registry=registry_large,
            composer=composer,
        )

        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="commit my git changes",
        )

        # Implementation note.
        arm = pool.pick_for_intent(intent)
        assert arm is code_arm

        # Implementation note.
        planner.plan(
            intent,
            allowed_skills=[str(s) for s in arm.allowed_skills],
        )

        req = router.call_log[0]
        sucker_text = "\n".join(m.content for m in req.messages if m.role != "system")
        # Implementation note.
        assert "git_commit" in sucker_text
        # Implementation note.
        assert "web_search" not in sucker_text
        assert "rag_lookup" not in sucker_text
