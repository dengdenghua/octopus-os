"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    CostEntry,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import (
    RecipeEvaluator,
    RecipeEvaluatorConfig,
    format_recipe_report,
)


def _mk_traj(
    recipe_id: str | None,
    *,
    success: bool = True,
    cost_usd: float = 0.01,
    tokens: int = 100,
    steps: int = 2,
    arm: str = "code_arm",
) -> Trajectory:
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId(arm),
        strategy_id="s",
        recipe_id=recipe_id,
        steps=[],
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=tokens // 2, tokens_out=tokens // 2, usd=cost_usd),
        ),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicEvaluate:
    def test_empty_journal(self):
        j = InMemoryJournal()
        report = RecipeEvaluator(j).evaluate()
        assert report.recipes_found == 0
        assert report.best is None
        assert report.worst is None

    def test_trajectories_without_recipe_ignored(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj(recipe_id=None))
        report = RecipeEvaluator(j).evaluate()
        assert report.recipes_found == 0

    def test_group_by_recipe_id(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(_mk_traj(recipe_id="llm@aaa"))
        for _ in range(3):
            j.write_trajectory(_mk_traj(recipe_id="llm@bbb"))
        report = RecipeEvaluator(j).evaluate()
        assert report.recipes_found == 2
        ids = {s.recipe_id for s in report.scores}
        assert ids == {"llm@aaa", "llm@bbb"}

    def test_swarm_aggregate_deduplicates_same_recipe_task(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())
        for strategy in ("s", "swarm"):
            j.write_trajectory(
                Trajectory(
                    task_id=task_id,
                    arm_id=ArmId("swarm" if strategy == "swarm" else "code_arm"),
                    strategy_id=strategy,
                    recipe_id="llm@dup",
                    steps=[],
                    outcome=TrajectoryOutcome(
                        success=True,
                        cost=CostEntry(tokens_in=50, tokens_out=50, usd=0.01),
                    ),
                )
            )

        report = RecipeEvaluator(j, config=RecipeEvaluatorConfig(min_uses_to_score=1)).evaluate()
        [score] = [s for s in report.scores if s.recipe_id == "llm@dup"]
        assert score.uses == 1
        assert score.successes == 1


# ═══════════════════════════════════════════════════════════
# Verdict
# ═══════════════════════════════════════════════════════════


class TestVerdict:
    def test_insufficient_data(self):
        j = InMemoryJournal()
        j.write_trajectory(_mk_traj("r1"))
        j.write_trajectory(_mk_traj("r1"))  # Implementation note.
        report = RecipeEvaluator(j).evaluate()
        assert report.scores[0].verdict == "insufficient_data"

    def test_winning(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj("r_win", success=True))
        report = RecipeEvaluator(j).evaluate()
        assert report.scores[0].verdict == "winning"
        assert report.scores[0].success_rate == 1.0

    def test_losing(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj("r_lose", success=False))
        report = RecipeEvaluator(j).evaluate()
        assert report.scores[0].verdict == "losing"

    def test_neutral(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(3):
            j.write_trajectory(_mk_traj("r_mid", success=True))
        for _ in range(2):
            j.write_trajectory(_mk_traj("r_mid", success=False))
        report = RecipeEvaluator(j).evaluate()
        assert report.scores[0].verdict == "neutral"


# ═══════════════════════════════════════════════════════════
# best / worst
# ═══════════════════════════════════════════════════════════


class TestBestWorst:
    def test_best_and_worst_identified(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(5):
            j.write_trajectory(_mk_traj("r_good", success=True, cost_usd=0.01))
        # Implementation note.
        for _ in range(5):
            j.write_trajectory(_mk_traj("r_bad", success=False, cost_usd=0.02))
        report = RecipeEvaluator(j).evaluate()
        assert report.best.recipe_id == "r_good"
        assert report.worst.recipe_id == "r_bad"
        assert report.best.score > report.worst.score

    def test_insufficient_excluded_from_best(self):
        j = InMemoryJournal()
        # Implementation note.
        j.write_trajectory(_mk_traj("r_single", success=True))
        # Implementation note.
        for i in range(4):
            j.write_trajectory(_mk_traj("r_regular", success=(i < 3)))
        report = RecipeEvaluator(j).evaluate()
        # Implementation note.
        assert report.best.recipe_id == "r_regular"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCostAwareness:
    def test_cheap_wins_over_expensive_at_same_success(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj("cheap", success=True, cost_usd=0.001))
        for _ in range(5):
            j.write_trajectory(_mk_traj("pricey", success=True, cost_usd=0.10))
        report = RecipeEvaluator(j).evaluate()
        # Implementation note.
        scores = {s.recipe_id: s.score for s in report.scores}
        assert scores["cheap"] >= scores["pricey"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConfig:
    def test_custom_min_uses(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj("r", success=True))
        report = RecipeEvaluator(j, config=RecipeEvaluatorConfig(min_uses_to_score=100)).evaluate()
        assert report.scores[0].verdict == "insufficient_data"

    def test_custom_winning_threshold(self):
        """Implementation note."""
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(9):
            j.write_trajectory(_mk_traj("r", success=True))
        j.write_trajectory(_mk_traj("r", success=False))
        report = RecipeEvaluator(
            j, config=RecipeEvaluatorConfig(min_uses_to_score=3, winning_success_threshold=0.95)
        ).evaluate()
        # Implementation note.
        assert report.scores[0].verdict == "neutral"


# ═══════════════════════════════════════════════════════════
# format_recipe_report
# ═══════════════════════════════════════════════════════════


class TestFormat:
    def test_empty_report(self):
        j = InMemoryJournal()
        report = RecipeEvaluator(j).evaluate()
        text = format_recipe_report(report)
        assert "no trajectories" in text

    def test_shows_rows(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_mk_traj("r_a", success=True))
        text = format_recipe_report(RecipeEvaluator(j).evaluate())
        assert "r_a" in text
        assert "100%" in text  # Implementation note.

    def test_truncation(self):
        j = InMemoryJournal()
        for i in range(15):
            for _ in range(3):
                j.write_trajectory(_mk_traj(f"r_{i}"))
        text = format_recipe_report(RecipeEvaluator(j).evaluate(), max_rows=3)
        assert "more" in text  # Implementation note.


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLLMPlannerRecipeHash:
    def test_hash_changes_when_rules_change(self):
        import json

        from runtime.core.cerebrum import LLMPlanner
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.memory.hemolymph import ContextComposer
        from runtime.safety.recovery import LearnedRule
        from runtime.sensing.model_router import MockModelRouter

        r = SkillRegistry()
        r.register(
            Skill(
                name="read_file",
                trusted_source="skill://public/read_file",
                handler=lambda **kw: {},
            ),
            verify_tests=False,
        )
        c = ContextComposer(registry=r, journal=InMemoryJournal())
        router = MockModelRouter(
            response=json.dumps({"reasoning": "x", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=r, composer=c)

        hash_before = planner.recipe_hash()
        planner.update_learned_rules(
            [
                LearnedRule(
                    rule_id="r1",
                    sucker_id="read_file",  # type: ignore[arg-type]
                    error_signature="timeout",
                    pattern="p",
                    mitigation="m",
                    hit_count=5,
                    severity="mid",
                )
            ]
        )
        hash_after = planner.recipe_hash()
        assert hash_before != hash_after
        assert hash_before.startswith("llm@")
        assert hash_after.startswith("llm@")

    def test_planner_sets_recipe_on_graph(self):
        import json

        from runtime.core.cerebrum import LLMPlanner
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.memory.hemolymph import ContextComposer
        from runtime.platform.models import ParsedIntent
        from runtime.sensing.model_router import MockModelRouter

        r = SkillRegistry()
        r.register(
            Skill(
                name="read_file",
                trusted_source="skill://public/read_file",
                handler=lambda **kw: {},
            ),
            verify_tests=False,
        )
        c = ContextComposer(registry=r, journal=InMemoryJournal())
        router = MockModelRouter(
            response=json.dumps({"reasoning": "x", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=r, composer=c)
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="do it")
        graph = planner.plan(intent)
        assert graph.recipe_hash is not None
        assert graph.recipe_hash.startswith("llm@")
