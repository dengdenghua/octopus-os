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
            model="mock/lin",
            mock_response=json.dumps(
                {
                    "reasoning": "r",
                    "nodes": [{"skill": "list_cwd", "args": {"path": "."}}],
                }
            ),
        )
    )
    return build_from_config(cfg)


class _SeqRouter(ModelRouter):
    def __init__(self, texts: list[str]):
        self.texts = texts
        self.idx = 0

    def call(self, r: ModelRequest) -> ModelResponse:
        text = self.texts[min(self.idx, len(self.texts) - 1)]
        self.idx += 1
        return ModelResponse(
            text=text,
            input_tokens=10,
            output_tokens=10,
            cost=CostEntry(usd=0.001),
            model=r.model,
            provider="mock",
        )


def _seed_failures(stack, recipe_hash: str, n: int = 2):
    for _ in range(n):
        call = ToolCall(caller="arms/a", sucker_id="read_file", args={})
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
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestSeedDefaults:
    def test_seed_variant_defaults(self):
        v = PromptVariant(name="baseline")
        assert v.generation == 0
        assert v.origin == "seed"
        assert v.parents == ()
        assert v.reason == ""

    def test_custom_metadata_survives_roundtrip(self):
        """Implementation note."""
        v = PromptVariant(
            name="x",
            parents=("a", "b"),
            generation=2,
            origin="crossover",
            reason="manual hint",
        )
        assert v.parents == ("a", "b")
        assert v.generation == 2
        assert v.origin == "crossover"
        assert v.reason == "manual hint"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMutationLineage:
    def test_mutation_fills_parents_and_generation(self, stack):
        base = PromptVariant(name="baseline", system_prompt_suffix="", generation=0)
        _seed_failures(stack, "any_recipe", n=2)

        router = _SeqRouter(
            [
                "<reason>timeout suggests lazy reads</reason>\n"
                "<suffix>check file_stats first</suffix>"
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose(base=base, journal=stack.journal)

        assert proposal is not None
        v = proposal.variant
        assert v.origin == "mutation"
        assert v.parents == ("baseline",)
        assert v.generation == 1
        assert "timeout" in v.reason.lower() or "lazy" in v.reason.lower()

    def test_mutation_generation_increments_from_base(self, stack):
        """Implementation note."""
        base = PromptVariant(name="gen0")
        _seed_failures(stack, "r", n=2)

        router = _SeqRouter(
            [
                "<suffix>gen1 suffix</suffix>",
                "<suffix>gen2 suffix</suffix>",
            ]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        p1 = mutator.propose(base=base, journal=stack.journal)
        assert p1.variant.generation == 1

        p2 = mutator.propose(base=p1.variant, journal=stack.journal)
        assert p2 is not None
        assert p2.variant.generation == 2
        assert p2.variant.parents == (p1.variant.name,)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestCrossoverLineage:
    def test_crossover_has_two_parents(self):
        a = PromptVariant(name="careful", system_prompt_suffix="X", generation=1)
        b = PromptVariant(name="fast", system_prompt_suffix="Y", generation=2)

        router = _SeqRouter(
            ["<reason>combine care + speed</reason><suffix>be careful but quick</suffix>"]
        )
        mutator = PromptMutator(router=router, model="mock/m")
        proposal = mutator.propose_merge(a, b)

        assert proposal is not None
        v = proposal.variant
        assert v.origin == "crossover"
        # Implementation note.
        assert set(v.parents) == {"careful", "fast"}
        # generation = max + 1 = 3
        assert v.generation == 3
        assert "combine" in v.reason.lower() or "care" in v.reason.lower()


# ═══════════════════════════════════════════════════════════
# optimizer.lineage()
# ═══════════════════════════════════════════════════════════


class TestLineageQuery:
    def test_single_seed_lineage(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="only")])
        chain = opt.lineage("only")
        assert len(chain) == 1
        assert chain[0].name == "only"
        assert chain[0].origin == "seed"

    def test_deep_linear_lineage(self, stack):
        """Implementation note."""
        gen0 = PromptVariant(name="gen0")
        gen1 = PromptVariant(
            name="gen1",
            parents=("gen0",),
            generation=1,
            origin="mutation",
        )
        gen2 = PromptVariant(
            name="gen2",
            parents=("gen1",),
            generation=2,
            origin="mutation",
        )
        opt = PromptOptimizer(stack, [gen0])
        opt.add_variant(gen1)
        opt.add_variant(gen2)

        chain = opt.lineage("gen2")
        assert [v.name for v in chain] == ["gen2", "gen1", "gen0"]
        assert chain[-1].origin == "seed"

    def test_lineage_unknown_name_returns_empty(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="a")])
        assert opt.lineage("ghost") == []

    def test_retired_variant_still_queryable(self, stack):
        """Implementation note."""
        opt = PromptOptimizer(
            stack,
            [
                PromptVariant(name="a"),
                PromptVariant(name="b"),
            ],
        )
        child = PromptVariant(
            name="c",
            parents=("a",),
            generation=1,
            origin="mutation",
        )
        opt.add_variant(child)
        opt.retire_variant("a")

        # Implementation note.
        chain = opt.lineage("c")
        assert [v.name for v in chain] == ["c", "a"]


# ═══════════════════════════════════════════════════════════
# optimizer.ancestors_tree()
# ═══════════════════════════════════════════════════════════


class TestAncestorsTree:
    def test_seed_tree(self, stack):
        opt = PromptOptimizer(stack, [PromptVariant(name="only")])
        tree = opt.ancestors_tree("only")
        assert tree["name"] == "only"
        assert tree["origin"] == "seed"
        assert tree["generation"] == 0
        assert "parents" not in tree

    def test_crossover_branches(self, stack):
        """Implementation note."""
        a = PromptVariant(name="a", generation=0)
        b = PromptVariant(name="b", generation=0)
        merged = PromptVariant(
            name="merged",
            parents=("a", "b"),
            generation=1,
            origin="crossover",
        )
        opt = PromptOptimizer(stack, [a, b])
        opt.add_variant(merged)

        tree = opt.ancestors_tree("merged")
        assert tree["origin"] == "crossover"
        assert len(tree["parents"]) == 2
        parent_names = {p["name"] for p in tree["parents"]}
        assert parent_names == {"a", "b"}

    def test_deep_mixed_tree(self, stack):
        """Implementation note."""
        a = PromptVariant(name="a", generation=0)
        a1 = PromptVariant(
            name="a1",
            parents=("a",),
            generation=1,
            origin="mutation",
        )
        b = PromptVariant(name="b", generation=0)
        b1 = PromptVariant(
            name="b1",
            parents=("b",),
            generation=1,
            origin="mutation",
        )
        merged = PromptVariant(
            name="merged",
            parents=("a1", "b1"),
            generation=2,
            origin="crossover",
        )
        opt = PromptOptimizer(stack, [a, b])
        opt.add_variant(a1)
        opt.add_variant(b1)
        opt.add_variant(merged)

        tree = opt.ancestors_tree("merged")
        assert tree["generation"] == 2
        # Implementation note.
        parents_by_name = {p["name"]: p for p in tree["parents"]}
        assert set(parents_by_name) == {"a1", "b1"}
        # Implementation note.
        a1_tree = parents_by_name["a1"]
        assert a1_tree["parents"][0]["name"] == "a"
        assert a1_tree["parents"][0]["origin"] == "seed"
