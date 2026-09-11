"""Implementation note."""

from __future__ import annotations

import json
import time
from uuid import uuid4

import pytest

from runtime.core.nerves.bus import TypedEventBus
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
    AutoRetireConfig,
    AutoRetireScheduler,
    EvolutionPolicy,
    EvolverStepTriggered,
    PromptEvolver,
    PromptMutator,
    PromptOptimizer,
    PromptVariant,
    VariantRetired,
)
from runtime.sensing.model_router import ModelRequest, ModelResponse, ModelRouter

# ═══════════════════════════════════════════════════════════
# Implementation note.
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


def _seed(stack, recipe_hash: str, *, successes: int, failures: int) -> None:
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


def _make_evolver_with_losers(stack) -> PromptEvolver:
    """Implementation note."""
    opt = PromptOptimizer(
        stack,
        [
            PromptVariant(name="a", system_prompt_suffix="<!--suffix-a-->"),
            PromptVariant(name="b", system_prompt_suffix="<!--suffix-b-->"),
        ],
    )
    a_hash = opt.planner_for("a").recipe_hash()
    b_hash = opt.planner_for("b").recipe_hash()
    _seed(stack, a_hash, successes=1, failures=9)
    _seed(stack, b_hash, successes=9, failures=1)
    opt._splitter.stats["a"].assignments = 10
    opt._splitter.stats["a"].failures = 9
    opt._splitter.stats["a"].successes = 1
    opt._splitter.stats["b"].assignments = 10
    opt._splitter.stats["b"].successes = 9
    opt._splitter.stats["b"].failures = 1

    mutator = PromptMutator(router=_SpyRouter(["<suffix>x</suffix>"]), model="m")
    return PromptEvolver(
        opt,
        mutator,
        EvolutionPolicy(
            retire_min_uses=5,
            mutate_each_step=False,
            crossover_each_step=False,
        ),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTickGuards:
    def test_first_tick_blocked_by_interval(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=60.0,
                min_assignments_since_last=1,
                min_variants_to_act=2,
            ),
        )
        sched._last_step_at = time.time()  # Implementation note.
        result = sched.tick()
        assert result is None
        assert sched.stats.total_steps == 0

    def test_insufficient_new_data_blocks(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=0.0,
                min_assignments_since_last=100,
                min_variants_to_act=2,
            ),
        )
        # Implementation note.
        assert sched.tick() is None

    def test_too_few_variants_blocks(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="solo")])
        mutator = PromptMutator(router=_SpyRouter(["<suffix>x</suffix>"]), model="m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                mutate_each_step=False,
                crossover_each_step=False,
            ),
        )
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=0.0,
                min_assignments_since_last=0,
                min_variants_to_act=2,
            ),
        )
        assert sched.tick() is None

    def test_tick_steps_when_all_guards_pass(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=0.0,
                min_assignments_since_last=0,
                min_variants_to_act=2,
            ),
        )
        # Implementation note.
        evolver.optimizer._splitter.stats["a"].assignments += 5
        step = sched.tick()
        assert step is not None
        assert "a" in step.retired
        assert sched.stats.total_steps == 1
        assert sched.stats.total_retired == 1


# ═══════════════════════════════════════════════════════════
# observe_evaluation
# ═══════════════════════════════════════════════════════════


class TestObserveEvaluation:
    def test_losing_triggers_immediate_step(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=10000.0,  # Implementation note.
                min_assignments_since_last=10000,
                losing_threshold_for_immediate=1,
            ),
        )
        step = sched.observe_evaluation()
        assert step is not None
        assert "a" in step.retired

    def test_no_losing_below_threshold_respects_guards(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="x", system_prompt_suffix="<!--x-->"),
                PromptVariant(name="y", system_prompt_suffix="<!--y-->"),
            ],
        )
        mutator = PromptMutator(router=_SpyRouter(["<suffix>z</suffix>"]), model="m")
        evolver = PromptEvolver(
            opt,
            mutator,
            EvolutionPolicy(
                mutate_each_step=False,
                crossover_each_step=False,
            ),
        )
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=10000.0,
                min_assignments_since_last=10000,
                losing_threshold_for_immediate=1,
            ),
        )
        sched._last_step_at = time.time()  # Implementation note.
        assert sched.observe_evaluation() is None


# ═══════════════════════════════════════════════════════════
# force_step
# ═══════════════════════════════════════════════════════════


class TestForceStep:
    def test_force_step_bypasses_guards(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=99999.0,
                min_assignments_since_last=99999,
            ),
        )
        step = sched.force_step(trigger="manual-test")
        assert step is not None
        assert sched.stats.total_steps == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEventPublishing:
    def test_variant_retired_event_published(self, stack):
        bus = TypedEventBus()
        retired_events: list[VariantRetired] = []
        bus.subscribe(VariantRetired, retired_events.append)

        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            bus=bus,
            config=AutoRetireConfig(
                min_interval_seconds=0.0,
                min_assignments_since_last=0,
                min_variants_to_act=2,
            ),
        )
        sched.force_step()
        assert len(retired_events) == 1
        assert retired_events[0].variant_name == "a"
        assert retired_events[0].reason == "losing_verdict"

    def test_evolver_step_event_always_published(self, stack):
        bus = TypedEventBus()
        step_events: list[EvolverStepTriggered] = []
        bus.subscribe(EvolverStepTriggered, step_events.append)

        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(evolver, bus=bus)
        sched.force_step(trigger="test")

        assert len(step_events) == 1
        assert step_events[0].trigger == "test"
        assert step_events[0].retired_count == 1

    def test_no_bus_no_events_no_crash(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(evolver)  # bus=None
        step = sched.force_step()
        assert step is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPeriodicTaskAdapter:
    def test_as_periodic_task_returns_callable(self, stack):
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(
            evolver,
            config=AutoRetireConfig(
                min_interval_seconds=0.0,
                min_assignments_since_last=0,
            ),
        )
        task = sched.as_periodic_task()
        assert callable(task)

        # Implementation note.
        before = sched.stats.total_ticks
        task()
        assert sched.stats.total_ticks == before + 1

    def test_task_swallows_exceptions(self, stack):
        """Implementation note."""
        evolver = _make_evolver_with_losers(stack)
        sched = AutoRetireScheduler(evolver)

        # Implementation note.
        def boom():
            raise RuntimeError("boom")

        sched.evolver.step = boom  # type: ignore[method-assign]

        # Implementation note.
        task = sched.as_periodic_task()
        # Implementation note.
        sched._last_step_at = 0.0
        sched._assignments_at_last_step = 0
        # Implementation note.
        # Implementation note.
        try:
            task()  # should not raise
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"periodic task raised: {e}")
