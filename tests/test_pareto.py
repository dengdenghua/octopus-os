"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.models import (
    ArmId,
    CostEntry,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.experiments import (
    EvolutionPolicy,
    PromptEvolver,
    PromptMutator,
    PromptOptimizer,
    PromptVariant,
    pareto_frontier_by_name,
)
from runtime.sensing.model_router import ModelRequest, ModelResponse, ModelRouter

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestParetoAlgorithm:
    def test_empty_input(self):
        assert (
            pareto_frontier_by_name(
                {},
                ("success",),
                maximize={"success": True},
            )
            == set()
        )

    def test_single_point_on_frontier(self):
        points = {"only": {"success": 0.5}}
        out = pareto_frontier_by_name(
            points,
            ("success",),
            maximize={"success": True},
        )
        assert out == {"only"}

    def test_1d_max_single_winner(self):
        """Implementation note."""
        points = {
            "a": {"s": 0.9},
            "b": {"s": 0.5},
            "c": {"s": 0.7},
        }
        out = pareto_frontier_by_name(points, ("s",), maximize={"s": True})
        assert out == {"a"}

    def test_2d_frontier_covers_tradeoff(self):
        """Implementation note."""
        points = {
            "expensive_high": {"s": 0.9, "c": 0.05},
            "cheap_moderate": {"s": 0.6, "c": 0.001},
            "dominated": {"s": 0.5, "c": 0.05},  # Implementation note.
        }
        out = pareto_frontier_by_name(
            points,
            ("s", "c"),
            maximize={"s": True, "c": False},
        )
        assert out == {"expensive_high", "cheap_moderate"}
        assert "dominated" not in out

    def test_3d_frontier(self):
        """Implementation note."""
        points = {
            "good": {"s": 0.9, "c": 0.01, "p": 3},
            "cheap": {"s": 0.7, "c": 0.001, "p": 5},
            "fast": {"s": 0.8, "c": 0.01, "p": 2},
            "mediocre": {"s": 0.6, "c": 0.02, "p": 5},  # dominated by good
        }
        out = pareto_frontier_by_name(
            points,
            ("s", "c", "p"),
            maximize={"s": True, "c": False, "p": False},
        )
        assert "good" in out
        assert "cheap" in out
        assert "fast" in out
        assert "mediocre" not in out

    def test_all_identical_all_on_frontier(self):
        points = {n: {"s": 0.5} for n in "abc"}
        out = pareto_frontier_by_name(
            points,
            ("s",),
            maximize={"s": True},
        )
        assert out == {"a", "b", "c"}

    def test_missing_metric_skipped(self):
        """Implementation note."""
        points = {
            "complete": {"s": 0.5, "c": 0.01},
            "partial": {"s": 0.9},  # Implementation note.
        }
        out = pareto_frontier_by_name(
            points,
            ("s", "c"),
            maximize={"s": True, "c": False},
        )
        assert out == {"complete"}

    def test_dominate_requires_strict_gt_on_at_least_one(self):
        """Implementation note."""
        points = {
            "a": {"s": 0.5, "c": 0.01},
            "b": {"s": 0.5, "c": 0.01},
        }
        out = pareto_frontier_by_name(
            points,
            ("s", "c"),
            maximize={"s": True, "c": False},
        )
        assert out == {"a", "b"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def stack():
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/p",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


class _StubRouter(ModelRouter):
    def __init__(self, text: str):
        self.text = text

    def call(self, r: ModelRequest) -> ModelResponse:
        return ModelResponse(
            text=self.text,
            input_tokens=10,
            output_tokens=10,
            cost=CostEntry(usd=0.001),
            model=r.model,
            provider="mock",
        )


def _seed_outcomes(
    stack,
    recipe_hash: str,
    *,
    successes: int,
    failures: int,
    cost_per_task: float = 0.01,
    tokens_per_task: int = 200,
) -> None:
    for _ in range(successes):
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=recipe_hash,
                steps=[],
                outcome=TrajectoryOutcome(
                    success=True,
                    cost=CostEntry(
                        tokens_in=tokens_per_task // 2,
                        tokens_out=tokens_per_task // 2,
                        usd=cost_per_task,
                    ),
                ),
            )
        )
    for _ in range(failures):
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=recipe_hash,
                steps=[],
                outcome=TrajectoryOutcome(
                    success=False,
                    cost=CostEntry(
                        tokens_in=tokens_per_task // 2,
                        tokens_out=tokens_per_task // 2,
                        usd=cost_per_task,
                    ),
                ),
            )
        )


class TestEvolverParetoMode:
    def test_pareto_keeps_cheap_variant_even_with_lower_success(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="expensive", system_prompt_suffix="E"),
                PromptVariant(name="cheap", system_prompt_suffix="C"),
                PromptVariant(name="dominated", system_prompt_suffix="D"),
            ],
        )
        _seed_outcomes(
            stack,
            opt.planner_for("expensive").recipe_hash(),
            successes=9,
            failures=1,
            cost_per_task=0.05,
        )
        _seed_outcomes(
            stack,
            opt.planner_for("cheap").recipe_hash(),
            successes=6,
            failures=4,
            cost_per_task=0.001,
        )
        _seed_outcomes(
            stack,
            opt.planner_for("dominated").recipe_hash(),
            successes=5,
            failures=5,
            cost_per_task=0.05,
        )
        for n in ("expensive", "cheap", "dominated"):
            opt._splitter.stats[n].assignments = 10

        mutator = PromptMutator(
            router=_StubRouter("<suffix>x</suffix>"),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                use_pareto=True,
                retire_min_uses=5,
                min_variants_after_retire=1,
                mutate_each_step=False,
                crossover_each_step=False,
                boost_winning=False,
            ),
        )
        step = evolver.step()

        # Implementation note.
        assert "expensive" in opt.variant_names
        assert "cheap" in opt.variant_names
        assert "dominated" in step.retired
        assert "dominated" not in opt.variant_names

    def test_verdict_mode_would_clear_cheap_but_pareto_saves_it(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="expensive", system_prompt_suffix="E"),
                PromptVariant(name="cheap", system_prompt_suffix="C"),
            ],
        )
        # Implementation note.
        _seed_outcomes(
            stack,
            opt.planner_for("expensive").recipe_hash(),
            successes=9,
            failures=1,
            cost_per_task=0.05,
        )
        _seed_outcomes(
            stack,
            opt.planner_for("cheap").recipe_hash(),
            successes=2,
            failures=8,
            cost_per_task=0.001,
        )
        for n in ("expensive", "cheap"):
            opt._splitter.stats[n].assignments = 10

        mutator = PromptMutator(
            router=_StubRouter("<suffix>x</suffix>"),
            model="m",
        )

        # Implementation note.
        ev_verdict = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                use_pareto=False,
                retire_min_uses=5,
                min_variants_after_retire=1,
                mutate_each_step=False,
                crossover_each_step=False,
                boost_winning=False,
            ),
        )
        step = ev_verdict.step()
        assert "cheap" in step.retired

    def test_pareto_mode_skips_when_data_insufficient(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a", system_prompt_suffix="a"),
                PromptVariant(name="b", system_prompt_suffix="b"),
            ],
        )
        # Implementation note.
        _seed_outcomes(stack, opt.planner_for("a").recipe_hash(), successes=1, failures=1)
        opt._splitter.stats["a"].assignments = 2
        opt._splitter.stats["b"].assignments = 0

        mutator = PromptMutator(
            router=_StubRouter("<suffix>x</suffix>"),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                use_pareto=True,
                retire_min_uses=5,
                mutate_each_step=False,
                crossover_each_step=False,
            ),
        )
        step = evolver.step()
        # Implementation note.
        assert step.retired == []
        assert "a" in opt.variant_names
        assert "b" in opt.variant_names
