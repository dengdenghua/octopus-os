"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal, JSONLJournal
from runtime.platform.models import (
    ArmId,
    CostEntry,
    ParsedIntent,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.sensing.model_router import MockModelRouter

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    for name in ["read_file", "hash_text"]:
        r.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
    return r


@pytest.fixture
def composer(registry) -> ContextComposer:
    return ContextComposer(registry=registry, journal=InMemoryJournal())


@pytest.fixture
def planner(registry, composer) -> LLMPlanner:
    router = MockModelRouter(
        response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
    )
    return LLMPlanner(router=router, registry=registry, composer=composer)


def _mk_traj(
    recipe_id: str | None,
    *,
    success: bool = True,
    cost_usd: float = 0.01,
    tokens: int = 100,
) -> Trajectory:
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        recipe_id=recipe_id,
        steps=[],
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=tokens // 2, tokens_out=tokens // 2, usd=cost_usd),
        ),
    )


def _seed_journal_with(recipe_id: str, *, successes: int, failures: int) -> InMemoryJournal:
    j = InMemoryJournal()
    for _ in range(successes):
        j.write_trajectory(_mk_traj(recipe_id, success=True))
    for _ in range(failures):
        j.write_trajectory(_mk_traj(recipe_id, success=False))
    return j


# ═══════════════════════════════════════════════════════════
# assess_recipe_from_journal
# ═══════════════════════════════════════════════════════════


class TestAssessRecipe:
    def test_default_no_verdict(self, planner):
        assert planner.current_recipe_verdict is None
        assert planner._recipe_assessed_count == 0

    def test_losing_verdict_captured(self, planner):
        # Implementation note.
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=1, failures=9)

        match = planner.assess_recipe_from_journal(journal)
        assert match is not None
        assert match.recipe_id == my_hash
        assert match.verdict == "losing"
        assert planner.current_recipe_verdict is match
        assert planner._recipe_assessed_count == 1

    def test_winning_verdict_captured(self, planner):
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=9, failures=1)
        match = planner.assess_recipe_from_journal(journal)
        assert match is not None
        assert match.verdict == "winning"

    def test_insufficient_when_few_uses(self, planner):
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=1, failures=0)  # Implementation note.
        match = planner.assess_recipe_from_journal(journal)
        assert match is not None
        assert match.verdict == "insufficient_data"

    def test_no_match_when_hash_differs(self, planner):
        # Implementation note.
        journal = _seed_journal_with("llm@wrong", successes=5, failures=0)
        match = planner.assess_recipe_from_journal(journal)
        assert match is None
        assert planner.current_recipe_verdict is None
        assert planner._recipe_assessed_count == 1  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRecipeSelfAssessmentPromptInjection:
    def _plan_and_get_system(self, planner) -> str:
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        return next(m.content for m in planner.router.call_log[-1].messages if m.role == "system")

    def test_losing_injects_warning(self, planner):
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=1, failures=9)
        planner.assess_recipe_from_journal(journal)
        sys_msg = self._plan_and_get_system(planner)
        assert "RECIPE SELF-ASSESSMENT" in sys_msg
        assert my_hash in sys_msg

    def test_winning_no_warning(self, planner):
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=9, failures=1)
        planner.assess_recipe_from_journal(journal)
        sys_msg = self._plan_and_get_system(planner)
        assert "RECIPE SELF-ASSESSMENT" not in sys_msg

    def test_neutral_no_warning(self, planner):
        my_hash = planner.recipe_hash()
        # Implementation note.
        journal = _seed_journal_with(my_hash, successes=5, failures=5)
        planner.assess_recipe_from_journal(journal)
        v = planner.current_recipe_verdict
        assert v is not None and v.verdict == "neutral"
        sys_msg = self._plan_and_get_system(planner)
        assert "RECIPE SELF-ASSESSMENT" not in sys_msg

    def test_no_verdict_no_warning(self, planner):
        # Implementation note.
        sys_msg = self._plan_and_get_system(planner)
        assert "RECIPE SELF-ASSESSMENT" not in sys_msg

    def test_insufficient_no_warning(self, planner):
        my_hash = planner.recipe_hash()
        journal = _seed_journal_with(my_hash, successes=1, failures=0)
        planner.assess_recipe_from_journal(journal)
        sys_msg = self._plan_and_get_system(planner)
        assert "RECIPE SELF-ASSESSMENT" not in sys_msg


# ═══════════════════════════════════════════════════════════
# config-driven
# ═══════════════════════════════════════════════════════════


class TestConfigDrivenRecipeAssess:
    def test_build_from_config_triggers_assessment(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        # Implementation note.
        # Implementation note.
        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(),
        )
        peek = build_from_config(cfg)
        expected_hash = peek.planner.recipe_hash()

        # Implementation note.
        path = tmp_path / "recipes.jsonl"
        fj = JSONLJournal(path)
        for _ in range(8):
            fj.write_trajectory(_mk_traj(expected_hash, success=False))
        for _ in range(1):
            fj.write_trajectory(_mk_traj(expected_hash, success=True))

        # Implementation note.
        cfg2 = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(assess_recipe_from_journal=str(path)),
        )
        stack = build_from_config(cfg2)
        assert stack.planner._recipe_assessed_count == 1
        v = stack.planner.current_recipe_verdict
        assert v is not None
        assert v.verdict == "losing"

    def test_missing_assess_file_no_crash(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(assess_recipe_from_journal=str(tmp_path / "nope.jsonl")),
        )
        stack = build_from_config(cfg)
        # Implementation note.
        assert stack.planner.current_recipe_verdict is None
        assert stack.planner._recipe_assessed_count == 0
