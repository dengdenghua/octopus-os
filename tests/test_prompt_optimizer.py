"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.models import (
    ArmId,
    CostEntry,
    ParsedIntent,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.experiments import (
    MutationProposal,
    PromptMutator,
    PromptOptimizer,
    PromptVariant,
    load_variants_from_yaml,
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
            model="mock/po",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


@pytest.fixture
def basic_variants():
    return [
        PromptVariant(name="baseline", system_prompt_suffix="", weight=1.0),
        PromptVariant(name="careful", system_prompt_suffix="Be careful.", weight=1.0),
        PromptVariant(name="direct", system_prompt_suffix="Answer directly.", weight=1.0),
    ]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConstruction:
    def test_empty_variants_rejected(self, stack):
        with pytest.raises(ValueError, match="at least one"):
            PromptOptimizer(stack, [])

    def test_static_planner_rejected(self):
        cfg = AgentConfig(planner=PlannerConfig(type="static"))
        stack = build_from_config(cfg)
        with pytest.raises(TypeError, match="only supports LLMPlanner"):
            PromptOptimizer(stack, [PromptVariant(name="x")])

    def test_variant_names_exposed(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        assert set(opt.variant_names) == {"baseline", "careful", "direct"}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRecipeHashSeparation:
    def test_different_suffixes_different_hashes(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        hashes = {name: opt.planner_for(name).recipe_hash() for name in opt.variant_names}
        # Implementation note.
        assert len(set(hashes.values())) == 3

    def test_same_suffix_same_hash(self, stack):
        v1 = PromptVariant(name="a", system_prompt_suffix="same text")
        v2 = PromptVariant(name="b", system_prompt_suffix="same text")
        opt = PromptOptimizer(stack, [v1, v2])
        assert opt.planner_for("a").recipe_hash() == opt.planner_for("b").recipe_hash()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStickyAssignment:
    def test_same_task_id_same_variant(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        tid = str(uuid4())
        picks = set()
        for _ in range(5):
            opt.plan(intent, tid)
            picks.add(opt.variant_for_task(tid))
        assert len(picks) == 1  # Implementation note.

    def test_different_task_ids_distribute(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        for i in range(60):
            opt.plan(intent, f"task-{i}")
        # Implementation note.
        by_variant: dict[str, int] = {}
        for _k, name in opt._task_variant_map.items():
            by_variant[name] = by_variant.get(name, 0) + 1
        assert len(by_variant) >= 2

    def test_plan_preserves_supplied_task_id(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        tid = uuid4()
        graph = opt.plan(intent, tid)
        # Implementation note.
        assert str(graph.task_id) == str(tid)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _traj_for_recipe(recipe_id: str, success: bool) -> Trajectory:
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("a"),
        recipe_id=recipe_id,
        steps=[],
        outcome=TrajectoryOutcome(
            success=success,
            cost=CostEntry(tokens_in=10, tokens_out=10, usd=0.0001),
        ),
    )


class TestReport:
    def test_report_maps_recipe_hash_to_variant(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        # Implementation note.
        baseline_hash = opt.planner_for("baseline").recipe_hash()
        for _ in range(10):
            stack.journal.write_trajectory(_traj_for_recipe(baseline_hash, True))
        for _ in range(1):
            stack.journal.write_trajectory(_traj_for_recipe(baseline_hash, False))
        # Implementation note.
        careful_hash = opt.planner_for("careful").recipe_hash()
        for _ in range(1):
            stack.journal.write_trajectory(_traj_for_recipe(careful_hash, True))
        for _ in range(9):
            stack.journal.write_trajectory(_traj_for_recipe(careful_hash, False))

        report = opt.report()
        assert isinstance(report, dict)
        assert "baseline" in report
        assert "careful" in report
        assert report["baseline"].verdict == "winning"
        assert report["careful"].verdict == "losing"
        assert report["baseline"].recipe_hash == baseline_hash


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAddVariant:
    def test_add_variant_succeeds(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        new = PromptVariant(name="fourth", system_prompt_suffix="Try harder.")
        opt.add_variant(new)
        assert "fourth" in opt.variant_names
        assert opt.planner_for("fourth").recipe_hash() != opt.planner_for("baseline").recipe_hash()

    def test_duplicate_name_rejected(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        with pytest.raises(ValueError, match="duplicate"):
            opt.add_variant(PromptVariant(name="baseline"))

    def test_add_preserves_existing_stats(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        # Implementation note.
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="x")
        for i in range(10):
            opt.plan(intent, f"t-{i}")
        old_total = sum(opt._splitter.stats[n].assignments for n in opt.variant_names)
        assert old_total == 10

        opt.add_variant(PromptVariant(name="late", system_prompt_suffix="late"))
        # Implementation note.
        assert opt._splitter.stats["late"].assignments == 0
        # Implementation note.
        new_total = sum(
            opt._splitter.stats[n].assignments for n in opt.variant_names if n != "late"
        )
        assert new_total == old_total


# ═══════════════════════════════════════════════════════════
# PromptMutator
# ═══════════════════════════════════════════════════════════


class _MutatorStubRouter(ModelRouter):
    """Implementation note."""

    def __init__(self, response_text: str):
        self.text = response_text
        self.calls: list[ModelRequest] = []

    def call(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            text=self.text,
            input_tokens=50,
            output_tokens=30,
            cost=CostEntry(usd=0.001),
            model=request.model,
            provider="mock",
        )


def _seed_failed_journal(stack, variant_recipe_hash: str, n: int = 5):
    """Implementation note."""
    from runtime.platform.models import ExecutionResult, Step, ToolCall

    for _i in range(n):
        call = ToolCall(caller="arms/x", sucker_id="read_file", args={})
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
                recipe_id=variant_recipe_hash,
                steps=[step],
                outcome=TrajectoryOutcome(success=False),
            )
        )


class TestPromptMutator:
    def test_no_failures_returns_none(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        router = _MutatorStubRouter("<suffix>whatever</suffix>")
        mutator = PromptMutator(router=router, model="mock/m")

        # Implementation note.
        result = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert result is None
        assert len(router.calls) == 0  # Implementation note.

    def test_propose_produces_variant(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        h = opt.planner_for("baseline").recipe_hash()
        _seed_failed_journal(stack, h, n=3)

        router = _MutatorStubRouter(
            "<reason>timeouts suggest smaller batches</reason>\n"
            "<suffix>Prefer 1-2 step plans. Check timeouts before retry.</suffix>"
        )
        mutator = PromptMutator(router=router, model="mock/m")
        result = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert isinstance(result, MutationProposal)
        assert "timeouts" in result.reason.lower() or "batch" in result.reason.lower()
        assert "Prefer" in result.variant.system_prompt_suffix
        assert result.variant.name.startswith("mutated_")

    def test_malformed_response_returns_none(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        h = opt.planner_for("baseline").recipe_hash()
        _seed_failed_journal(stack, h)

        router = _MutatorStubRouter("no suffix tag here")
        mutator = PromptMutator(router=router, model="mock/m")
        result = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert result is None

    def test_duplicate_suffix_returns_none(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        h = opt.planner_for("baseline").recipe_hash()
        _seed_failed_journal(stack, h)

        router = _MutatorStubRouter("<suffix>same improvement</suffix>")
        mutator = PromptMutator(router=router, model="mock/m")
        # Implementation note.
        r1 = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert r1 is not None
        # Implementation note.
        r2 = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert r2 is None

    def test_same_as_base_returns_none(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        h = opt.planner_for("careful").recipe_hash()
        _seed_failed_journal(stack, h)

        router = _MutatorStubRouter("<suffix>Be careful.</suffix>")  # Implementation note.
        mutator = PromptMutator(router=router, model="mock/m")
        result = mutator.propose(
            base=opt._variants["careful"],
            journal=stack.journal,
        )
        assert result is None

    def test_suffix_length_capped(self, stack, basic_variants):
        opt = PromptOptimizer(stack, basic_variants)
        h = opt.planner_for("baseline").recipe_hash()
        _seed_failed_journal(stack, h)

        long_suffix = "x" * 1000
        router = _MutatorStubRouter(f"<suffix>{long_suffix}</suffix>")
        mutator = PromptMutator(
            router=router,
            model="mock/m",
            max_suffix_chars=100,
        )
        result = mutator.propose(
            base=opt._variants["baseline"],
            journal=stack.journal,
        )
        assert result is not None
        assert len(result.variant.system_prompt_suffix) <= 105  # "xxxxxxx…"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLoadFromYaml:
    def test_load_ok(self, tmp_path):
        pytest.importorskip("yaml")
        path = tmp_path / "variants.yaml"
        path.write_text(
            "variants:\n"
            "  - name: baseline\n"
            "    system_prompt_suffix: ''\n"
            "    weight: 1.0\n"
            "  - name: careful\n"
            "    system_prompt_suffix: 'Be careful.'\n"
            "    weight: 0.5\n"
            "    description: Try conservative planning\n",
            encoding="utf-8",
        )
        vs = load_variants_from_yaml(path)
        assert len(vs) == 2
        assert vs[0].name == "baseline"
        assert vs[1].weight == 0.5
        assert "conservative" in vs[1].description


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEndEvolution:
    def test_mutator_proposal_gets_added_to_optimizer(self, stack):
        base = PromptVariant(name="baseline", system_prompt_suffix="")
        opt = PromptOptimizer(stack, [base])
        # Implementation note.
        _seed_failed_journal(stack, opt.planner_for("baseline").recipe_hash(), n=3)

        router = _MutatorStubRouter("<suffix>Prefer shorter plans · cap nodes at 3.</suffix>")
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose(base=base, journal=stack.journal)
        assert proposal is not None

        # Implementation note.
        opt.add_variant(proposal.variant)
        assert proposal.variant.name in opt.variant_names
        # Implementation note.
        assert (
            opt.planner_for(proposal.variant.name).recipe_hash()
            != opt.planner_for("baseline").recipe_hash()
        )
