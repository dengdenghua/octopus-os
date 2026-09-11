"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.models import (
    ArmId,
    CostEntry,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.experiments import (
    EvolutionPolicy,
    EvolutionStep,
    PromptEvolver,
    PromptMutator,
    PromptOptimizer,
    PromptVariant,
)
from runtime.sensing.model_router import ModelRequest, ModelResponse, ModelRouter

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def stack():
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/e",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


class _SpyRouter(ModelRouter):
    """Implementation note."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.idx = 0

    def call(self, request: ModelRequest) -> ModelResponse:
        text = self.responses[min(self.idx, len(self.responses) - 1)]
        self.idx += 1
        return ModelResponse(
            text=text,
            input_tokens=10,
            output_tokens=10,
            cost=CostEntry(usd=0.001),
            model=request.model,
            provider="mock",
        )


def _seed_outcomes(
    stack,
    recipe_hash: str,
    *,
    successes: int,
    failures: int,
) -> None:
    """Implementation note."""
    for _ in range(successes):
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=recipe_hash,
                steps=[],
                outcome=TrajectoryOutcome(
                    success=True,
                    cost=CostEntry(tokens_in=10, tokens_out=10, usd=0.0001),
                ),
            )
        )
    for _ in range(failures):
        call = ToolCall(caller="arms/a", sucker_id="list_cwd", args={})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="failed",
                error_type="timeout",
            ),
        )
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=recipe_hash,
                steps=[step],
                outcome=TrajectoryOutcome(success=False),
            )
        )


# ═══════════════════════════════════════════════════════════
# retire / adjust_weight
# ═══════════════════════════════════════════════════════════


class TestRetireAndAdjust:
    def test_retire_removes_from_pool(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a"),
                PromptVariant(name="b"),
            ],
        )
        ok = opt.retire_variant("a")
        assert ok is True
        assert "a" not in opt.variant_names
        assert "b" in opt.variant_names

    def test_retire_last_variant_rejected(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="only")])
        assert opt.retire_variant("only") is False
        assert "only" in opt.variant_names

    def test_retire_unknown_returns_false(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a"),
                PromptVariant(name="b"),
            ],
        )
        assert opt.retire_variant("ghost") is False

    def test_adjust_weight_affects_distribution(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a", weight=1.0),
                PromptVariant(name="b", weight=1.0),
            ],
        )
        opt.adjust_weight("a", 10.0)  # Implementation note.

        from runtime.platform.models import ParsedIntent

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        counts = {"a": 0, "b": 0}
        for i in range(200):
            opt.plan(intent, f"t-{i}")
            counts[opt.variant_for_task(f"t-{i}")] += 1
        # Implementation note.
        assert counts["a"] > counts["b"] * 3

    def test_adjust_weight_zero_rejected(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="a")])
        with pytest.raises(ValueError):
            opt.adjust_weight("a", 0)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPolicyThresholds:
    def test_retire_respects_min_uses(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a"),
                PromptVariant(name="b"),
            ],
        )
        a_hash = opt.planner_for("a").recipe_hash()
        _seed_outcomes(stack, a_hash, successes=0, failures=2)  # Implementation note.

        mutator = PromptMutator(router=_SpyRouter(["<suffix>x</suffix>"]), model="m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(retire_min_uses=5, mutate_each_step=False),
        )
        # Implementation note.
        opt._splitter.stats["a"].assignments = 2
        opt._splitter.stats["a"].failures = 2

        step = evolver.step()
        # Implementation note.
        assert "a" not in step.retired
        assert "a" in opt.variant_names

    def test_min_variants_after_retire(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a"),
                PromptVariant(name="b"),
            ],
        )
        # Implementation note.
        a_hash = opt.planner_for("a").recipe_hash()
        b_hash = opt.planner_for("b").recipe_hash()
        _seed_outcomes(stack, a_hash, successes=1, failures=9)
        _seed_outcomes(stack, b_hash, successes=1, failures=9)
        opt._splitter.stats["a"].assignments = 10
        opt._splitter.stats["b"].assignments = 10

        mutator = PromptMutator(router=_SpyRouter(["<suffix>x</suffix>"]), model="m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_min_uses=5,
                min_variants_after_retire=1,
                mutate_each_step=False,
            ),
        )
        evolver.step()
        # Implementation note.
        assert len(opt.variant_names) >= 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEvolutionStep:
    def test_retires_loser_and_mutates(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="good", system_prompt_suffix="Good.", weight=1.0),
                PromptVariant(name="bad", system_prompt_suffix="Bad.", weight=1.0),
            ],
        )
        good_hash = opt.planner_for("good").recipe_hash()
        bad_hash = opt.planner_for("bad").recipe_hash()
        _seed_outcomes(stack, good_hash, successes=9, failures=1)  # winning
        _seed_outcomes(stack, bad_hash, successes=1, failures=9)  # losing
        opt._splitter.stats["good"].assignments = 10
        opt._splitter.stats["bad"].assignments = 10

        mutator = PromptMutator(
            router=_SpyRouter(
                [
                    "<suffix>refined: prefer shorter plans</suffix>",
                ]
            ),
            model="mock/m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_min_uses=5,
                min_variants_after_retire=1,
                boost_winning=True,
                mutate_each_step=True,
            ),
        )
        step = evolver.step()

        assert "bad" in step.retired
        # Implementation note.
        assert any(b[0] == "good" for b in step.boosted)
        # Implementation note.
        assert step.mutation is not None
        new_name = step.mutation.variant.name
        assert new_name in opt.variant_names

    def test_variant_pool_full_skips_mutation(self, stack):
        opt = PromptOptimizer(
            stack, [PromptVariant(name=f"v{i}", system_prompt_suffix=str(i)) for i in range(5)]
        )
        mutator = PromptMutator(
            router=_SpyRouter(["<suffix>new</suffix>"]),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                max_total_variants=5,
                mutate_each_step=True,
                retire_on_losing=False,
            ),
        )
        step = evolver.step()
        assert step.mutation is None
        assert step.mutation_skipped_reason == "variant_pool_full"

    def test_mutation_skipped_when_mutator_returns_none(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="a")])
        # Implementation note.
        mutator = PromptMutator(
            router=_SpyRouter(["<suffix>x</suffix>"]),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                mutate_each_step=True,
                retire_on_losing=False,
            ),
        )
        step = evolver.step()
        assert step.mutation is None
        assert "none" in step.mutation_skipped_reason.lower()

    def test_history_accumulates(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="a")])
        mutator = PromptMutator(
            router=_SpyRouter(["<suffix>x</suffix>"]),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_on_losing=False,
                mutate_each_step=False,
                boost_winning=False,
            ),
        )
        for _ in range(3):
            evolver.step()
        assert len(evolver.history) == 3


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStepObservability:
    def test_summary_includes_retired_and_boosted(self):
        step = EvolutionStep(
            retired=["bad"],
            boosted=[("good", 1.0, 1.5)],
        )
        s = step.summary
        assert "retired" in s and "bad" in s
        assert "boosted" in s and "good" in s

    def test_summary_noop_on_empty(self):
        step = EvolutionStep()
        assert step.summary == "noop"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMultiRoundEvolution:
    def test_bad_variant_retired_across_rounds(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="good", system_prompt_suffix="G.", weight=1.0),
                PromptVariant(name="bad", system_prompt_suffix="B.", weight=1.0),
            ],
        )
        good_hash = opt.planner_for("good").recipe_hash()
        bad_hash = opt.planner_for("bad").recipe_hash()
        _seed_outcomes(stack, good_hash, successes=10, failures=0)
        _seed_outcomes(stack, bad_hash, successes=0, failures=10)
        opt._splitter.stats["good"].assignments = 10
        opt._splitter.stats["bad"].assignments = 10

        # Implementation note.
        mutator = PromptMutator(
            router=_SpyRouter(["<suffix>variant-1</suffix>", "<suffix>variant-2</suffix>"]),
            model="m",
        )
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_min_uses=5,
                min_variants_after_retire=1,
                mutate_each_step=True,
                max_total_variants=10,
            ),
        )

        evolver.step()
        # Implementation note.
        assert "bad" not in opt.variant_names
        assert "good" in opt.variant_names
