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
            model="mock/mv2",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


class _ProgrammableRouter(ModelRouter):
    """Implementation note."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.idx = 0
        self.last_request: ModelRequest | None = None

    def call(self, request: ModelRequest) -> ModelResponse:
        self.last_request = request
        text = self.responses[min(self.idx, len(self.responses) - 1)]
        self.idx += 1
        return ModelResponse(
            text=text,
            input_tokens=20,
            output_tokens=20,
            cost=CostEntry(usd=0.002),
            model=request.model,
            provider="mock",
        )


def _seed_failed_trajectories(stack, recipe_hash: str, n: int = 3):
    """Implementation note."""
    for i in range(n):
        call1 = ToolCall(
            caller="arms/a",
            sucker_id="read_file",
            args={"path": f"/tmp/file_{i}.txt"},
        )
        step1 = Step(
            step_id=0,
            node_id="n0",
            action=call1,
            result=ExecutionResult(
                call_id=call1.call_id,
                status="success",
                output={"content": "short"},
            ),
        )
        call2 = ToolCall(
            caller="arms/a",
            sucker_id="edit_file",
            args={"path": f"/tmp/file_{i}.txt", "content": "new"},
        )
        step2 = Step(
            step_id=1,
            node_id="n1",
            action=call2,
            result=ExecutionResult(
                call_id=call2.call_id,
                status="failed",
                error_type="timeout",
            ),
        )
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=recipe_hash,
                steps=[step1, step2],
                outcome=TrajectoryOutcome(
                    success=False,
                    cost=CostEntry(tokens_in=50, tokens_out=30, usd=0.003),
                ),
            )
        )


def _seed_winning(stack, recipe_hash: str, n: int = 10):
    """Implementation note."""
    for _ in range(n):
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


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRichFailureSamples:
    def test_user_prompt_includes_skill_and_args(self, stack):
        from runtime.safety.experiments.prompt_optimizer import PromptVariant as V

        _seed_failed_trajectories(stack, "some_recipe", n=2)
        router = _ProgrammableRouter(["<suffix>check file size before edit</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose(base=V(name="baseline"), journal=stack.journal)

        assert proposal is not None
        # Implementation note.
        req = router.last_request
        user_msg = next(m.content for m in req.messages if m.role == "user")
        # Implementation note.
        assert "read_file" in user_msg
        assert "edit_file" in user_msg
        # Implementation note.
        assert "path=" in user_msg
        # Implementation note.
        assert "failed" in user_msg
        assert "timeout" in user_msg
        # Implementation note.
        assert "$" in user_msg
        # Implementation note.
        assert "step[0]" in user_msg

    def test_system_prompt_guides_specific_not_generic(self, stack):
        from runtime.safety.experiments.prompt_optimizer import PromptVariant as V

        _seed_failed_trajectories(stack, "r", n=1)
        router = _ProgrammableRouter(["<suffix>ok</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        mutator.propose(base=V(name="baseline"), journal=stack.journal)
        sys_msg = next(m.content for m in router.last_request.messages if m.role == "system")
        # Implementation note.
        assert "observed failures" in sys_msg or "speculate" in sys_msg

    def test_swarm_aggregate_deduplicates_same_failed_task(self, stack):
        from runtime.safety.experiments.prompt_optimizer import PromptVariant as V

        task_id = TaskId(uuid4())
        call = ToolCall(caller="arms/a", sucker_id="edit_file", args={"path": "/tmp/x"})
        failed_step = Step(
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
                task_id=task_id,
                arm_id=ArmId("a"),
                recipe_id="r",
                strategy_id="default",
                steps=[failed_step],
                outcome=TrajectoryOutcome(
                    success=False,
                    cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
                ),
            )
        )
        stack.journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                recipe_id="r",
                strategy_id="swarm",
                steps=[failed_step],
                outcome=TrajectoryOutcome(
                    success=False,
                    cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
                ),
            )
        )

        router = _ProgrammableRouter(["<suffix>ok</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose(base=V(name="baseline"), journal=stack.journal)
        assert proposal is not None
        user_msg = next(m.content for m in router.last_request.messages if m.role == "user")
        assert user_msg.count("## task") == 1

    def test_recipe_filter_keeps_only_base_variant_failures(self, stack):
        from runtime.safety.experiments.prompt_optimizer import PromptVariant as V

        _seed_failed_trajectories(stack, "target_recipe", n=2)
        _seed_failed_trajectories(stack, "other_recipe", n=3)

        router = _ProgrammableRouter(["<suffix>ok</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose(
            base=V(name="baseline"),
            journal=stack.journal,
            recipe_id="target_recipe",
        )
        assert proposal is not None
        user_msg = next(m.content for m in router.last_request.messages if m.role == "user")
        assert user_msg.count("## task") == 2


# ═══════════════════════════════════════════════════════════
# #3 · propose_merge
# ═══════════════════════════════════════════════════════════


class TestProposeMerge:
    def test_merge_produces_new_variant(self):
        a = PromptVariant(name="A", system_prompt_suffix="Be careful.")
        b = PromptVariant(name="B", system_prompt_suffix="Prefer short plans.")
        router = _ProgrammableRouter(
            ["<reason>combine both</reason>\n<suffix>Be careful AND prefer short plans.</suffix>"]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose_merge(a, b)
        assert proposal is not None
        assert proposal.variant.name.startswith("mutated_x")
        assert "careful" in proposal.variant.system_prompt_suffix.lower()
        assert proposal.variant.description.startswith("Merge of")

    def test_merge_same_parent_returns_none(self):
        v = PromptVariant(name="A", system_prompt_suffix="Same")
        router = _ProgrammableRouter(["<suffix>x</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        assert mutator.propose_merge(v, v) is None

    def test_merge_identical_suffixes_returns_none(self):
        a = PromptVariant(name="A", system_prompt_suffix="Same")
        b = PromptVariant(name="B", system_prompt_suffix="Same")
        router = _ProgrammableRouter(["<suffix>x</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        assert mutator.propose_merge(a, b) is None

    def test_merge_result_same_as_parent_returns_none(self):
        a = PromptVariant(name="A", system_prompt_suffix="Be careful.")
        b = PromptVariant(name="B", system_prompt_suffix="Prefer short plans.")
        # Implementation note.
        router = _ProgrammableRouter(["<suffix>Be careful.</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        assert mutator.propose_merge(a, b) is None

    def test_merge_uses_merge_system_prompt(self):
        a = PromptVariant(name="A", system_prompt_suffix="first")
        b = PromptVariant(name="B", system_prompt_suffix="second")
        router = _ProgrammableRouter(["<suffix>merged</suffix>"])
        mutator = PromptMutator(router=router, model="mock/m")
        mutator.propose_merge(a, b)
        sys_msg = next(m.content for m in router.last_request.messages if m.role == "system")
        assert "synthesize" in sys_msg.lower()
        assert "both" in sys_msg.lower()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEvolverCrossover:
    def test_crossover_adds_new_variant(self, stack):
        # Implementation note.
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="A", system_prompt_suffix="A-suffix"),
                PromptVariant(name="B", system_prompt_suffix="B-suffix"),
            ],
        )
        _seed_winning(stack, opt.planner_for("A").recipe_hash(), n=10)
        _seed_winning(stack, opt.planner_for("B").recipe_hash(), n=10)
        opt._splitter.stats["A"].assignments = 10
        opt._splitter.stats["A"].successes = 10
        opt._splitter.stats["B"].assignments = 10
        opt._splitter.stats["B"].successes = 10

        router = _ProgrammableRouter(
            [
                "<suffix>mutated-one</suffix>",  # propose mutation
                "<suffix>crossover-ab</suffix>",  # propose_merge crossover
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_on_losing=False,
                mutate_each_step=True,
                crossover_each_step=True,
                max_total_variants=10,
            ),
        )
        step = evolver.step()

        # Implementation note.
        assert step.crossover is not None
        assert step.crossover.variant.name.startswith("mutated_x")
        assert "Merge of" in step.crossover.variant.description

    def test_crossover_skipped_when_only_one_winner(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="A", system_prompt_suffix="A"),
                PromptVariant(name="B", system_prompt_suffix="B"),
            ],
        )
        _seed_winning(stack, opt.planner_for("A").recipe_hash(), n=10)
        # Implementation note.
        _seed_failed_trajectories(stack, opt.planner_for("B").recipe_hash(), n=8)
        stack.journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                recipe_id=opt.planner_for("B").recipe_hash(),
                steps=[],
                outcome=TrajectoryOutcome(success=True),
            )
        )
        opt._splitter.stats["A"].assignments = 10
        opt._splitter.stats["B"].assignments = 9

        router = _ProgrammableRouter(
            [
                "<suffix>x</suffix>",
                "<suffix>y</suffix>",
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_on_losing=False,
                mutate_each_step=False,
                crossover_each_step=True,
                crossover_requires_winning=True,
            ),
        )
        step = evolver.step()
        assert step.crossover is None
        assert "need_2_winners" in step.crossover_skipped_reason

    def test_crossover_pool_full_skipped(self, stack):
        opt = PromptOptimizer(
            stack, [PromptVariant(name=f"v{i}", system_prompt_suffix=f"s{i}") for i in range(5)]
        )
        router = _ProgrammableRouter(
            [
                "<suffix>new-mut</suffix>",
                "<suffix>new-x</suffix>",
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_on_losing=False,
                mutate_each_step=False,
                crossover_each_step=True,
                max_total_variants=5,  # Implementation note.
            ),
        )
        step = evolver.step()
        assert step.crossover is None
        assert "pool_full" in step.crossover_skipped_reason

    def test_crossover_disabled_by_default_policy_flag(self, stack):
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="A", system_prompt_suffix="A"),
                PromptVariant(name="B", system_prompt_suffix="B"),
            ],
        )
        _seed_winning(stack, opt.planner_for("A").recipe_hash(), n=10)
        _seed_winning(stack, opt.planner_for("B").recipe_hash(), n=10)
        opt._splitter.stats["A"].assignments = 10
        opt._splitter.stats["A"].successes = 10
        opt._splitter.stats["B"].assignments = 10
        opt._splitter.stats["B"].successes = 10

        router = _ProgrammableRouter(
            [
                "<suffix>m</suffix>",  # Implementation note.
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        # Implementation note.
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                retire_on_losing=False,
                mutate_each_step=True,
                crossover_each_step=False,
            ),
        )
        step = evolver.step()
        assert step.crossover is None
        # Implementation note.
        assert step.crossover_skipped_reason == ""
