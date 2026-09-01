# ruff: noqa: E402 — module-level imports below are intentionally late
"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

yaml = pytest.importorskip("yaml")

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
    dump_variants_to_yaml,
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
            model="mock/ap",
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
    def __init__(self, text):
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


class TestSerializationRoundtrip:
    def test_seed_only_roundtrip(self, tmp_path):
        vs = [PromptVariant(name="baseline")]
        text = dump_variants_to_yaml(vs)
        path = tmp_path / "v.yaml"
        path.write_text(text, encoding="utf-8")
        reloaded = load_variants_from_yaml(path)
        assert len(reloaded) == 1
        assert reloaded[0].name == "baseline"
        assert reloaded[0].generation == 0
        assert reloaded[0].origin == "seed"
        assert reloaded[0].parents == ()

    def test_lineage_fields_preserved(self, tmp_path):
        vs = [
            PromptVariant(name="base"),
            PromptVariant(
                name="child",
                system_prompt_suffix="improved",
                weight=0.2,
                description="first mutation",
                parents=("base",),
                generation=1,
                origin="mutation",
                reason="timeouts imply lazy reads",
            ),
        ]
        path = tmp_path / "v.yaml"
        path.write_text(dump_variants_to_yaml(vs), encoding="utf-8")
        reloaded = load_variants_from_yaml(path)
        assert len(reloaded) == 2
        child = reloaded[1]
        assert child.name == "child"
        assert child.parents == ("base",)
        assert child.generation == 1
        assert child.origin == "mutation"
        assert child.reason == "timeouts imply lazy reads"
        assert child.weight == 0.2

    def test_crossover_two_parents_preserved(self, tmp_path):
        vs = [
            PromptVariant(name="a"),
            PromptVariant(name="b"),
            PromptVariant(
                name="merged",
                parents=("a", "b"),
                generation=1,
                origin="crossover",
            ),
        ]
        path = tmp_path / "v.yaml"
        path.write_text(dump_variants_to_yaml(vs), encoding="utf-8")
        reloaded = load_variants_from_yaml(path)
        merged = next(v for v in reloaded if v.name == "merged")
        assert merged.parents == ("a", "b")
        assert merged.origin == "crossover"

    def test_single_quote_escape_in_reason(self, tmp_path):
        """Implementation note."""
        vs = [
            PromptVariant(
                name="x",
                reason="it's a test · can't break YAML",
            )
        ]
        text = dump_variants_to_yaml(vs)
        path = tmp_path / "v.yaml"
        path.write_text(text, encoding="utf-8")
        reloaded = load_variants_from_yaml(path)
        assert reloaded[0].reason == "it's a test · can't break YAML"


# ═══════════════════════════════════════════════════════════
# PromptOptimizer.auto_persist_path
# ═══════════════════════════════════════════════════════════


class TestAutoPersist:
    def test_no_persist_by_default(self, stack, tmp_path):
        """Implementation note."""
        PromptOptimizer(stack, [PromptVariant(name="baseline")])
        # Implementation note.
        assert not any(tmp_path.glob("*.yaml"))

    def test_persist_on_construct(self, stack, tmp_path):
        """Implementation note."""
        path = tmp_path / "variants.yaml"
        PromptOptimizer(
            stack,
            [PromptVariant(name="baseline", system_prompt_suffix="")],
            auto_persist_path=path,
        )
        assert path.exists()
        reloaded = load_variants_from_yaml(path)
        assert len(reloaded) == 1
        assert reloaded[0].name == "baseline"

    def test_add_variant_triggers_persist(self, stack, tmp_path):
        path = tmp_path / "variants.yaml"
        opt = PromptOptimizer(
            stack,
            [PromptVariant(name="baseline")],
            auto_persist_path=path,
        )
        opt.add_variant(
            PromptVariant(
                name="child",
                parents=("baseline",),
                generation=1,
                origin="mutation",
                reason="test",
            )
        )
        reloaded = load_variants_from_yaml(path)
        names = {v.name for v in reloaded}
        assert names == {"baseline", "child"}
        # Implementation note.
        child = next(v for v in reloaded if v.name == "child")
        assert child.parents == ("baseline",)
        assert child.origin == "mutation"

    def test_retire_variant_triggers_persist(self, stack, tmp_path):
        path = tmp_path / "variants.yaml"
        opt = PromptOptimizer(
            stack,
            [PromptVariant(name="a"), PromptVariant(name="b")],
            auto_persist_path=path,
        )
        opt.retire_variant("a")
        reloaded = load_variants_from_yaml(path)
        names = {v.name for v in reloaded}
        # Implementation note.
        assert names == {"b"}

    def test_adjust_weight_triggers_persist(self, stack, tmp_path):
        path = tmp_path / "variants.yaml"
        opt = PromptOptimizer(
            stack,
            [PromptVariant(name="a", weight=1.0)],
            auto_persist_path=path,
        )
        opt.adjust_weight("a", 3.5)
        reloaded = load_variants_from_yaml(path)
        assert reloaded[0].weight == 3.5

    def test_io_failure_swallowed(self, stack, tmp_path):
        """Implementation note."""
        bad_path = tmp_path / "not-a-dir" / "file"
        # Implementation note.
        (tmp_path / "not-a-dir").write_text("block", encoding="utf-8")

        opt = PromptOptimizer(
            stack,
            [PromptVariant(name="a")],
            auto_persist_path=bad_path,
        )
        # Implementation note.
        opt.add_variant(PromptVariant(name="b"))
        assert "b" in opt.variant_names


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRestartScenario:
    def test_evolve_then_reload_preserves_pool(self, stack, tmp_path):
        """Implementation note."""
        path = tmp_path / "v.yaml"

        # Session 1
        opt1 = PromptOptimizer(
            stack,
            [PromptVariant(name="baseline")],
            auto_persist_path=path,
        )
        _seed_failures(stack, opt1.planner_for("baseline").recipe_hash(), n=2)
        mutator = PromptMutator(
            router=_StubRouter(
                "<reason>baseline slow</reason><suffix>use file_stats first</suffix>"
            ),
            model="mock/m",
        )
        proposal = mutator.propose(
            base=opt1._variants["baseline"],
            journal=stack.journal,
        )
        assert proposal is not None
        opt1.add_variant(proposal.variant)
        generated_name = proposal.variant.name

        # Implementation note.
        reloaded_variants = load_variants_from_yaml(path)
        assert {v.name for v in reloaded_variants} == {"baseline", generated_name}
        # Implementation note.
        child = next(v for v in reloaded_variants if v.name == generated_name)
        assert child.parents == ("baseline",)
        assert child.origin == "mutation"
        assert "file_stats" in child.system_prompt_suffix

        opt2 = PromptOptimizer(stack, reloaded_variants, auto_persist_path=path)
        assert set(opt2.variant_names) == {"baseline", generated_name}


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestServeAutoPersist:
    def test_serve_uses_variants_path_for_both_load_and_save(self, tmp_path, monkeypatch):
        import uvicorn

        from runtime.cli import run_serve

        # Implementation note.
        variants_path = tmp_path / "variants.yaml"
        variants_path.write_text(
            "variants:\n  - name: seed\n    system_prompt_suffix: ''\n    weight: 1.0\n",
            encoding="utf-8",
        )

        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(
            "planner:\n"
            "  type: llm\n"
            "  model: mock/s\n"
            '  mock_response: \'{"reasoning":"r","nodes":[{"skill":"list_cwd","args":{"path":"."}}]}\'\n'
            "budget:\n  max_tokens: 5000\n  max_usd: 0.05\n",
            encoding="utf-8",
        )

        captured = {}

        def fake_uvicorn_run(app, host, port, log_level, ws):
            # Implementation note.
            # Implementation note.
            assert ws == "websockets-sansio"
            captured["variants_after_start"] = variants_path.read_text(encoding="utf-8")

        monkeypatch.setattr(uvicorn, "run", fake_uvicorn_run)

        rc = run_serve(
            config_path=cfg_path,
            host="127.0.0.1",
            port=8000,
            learn_interval_s=0,
            prompt_variants_path=variants_path,
            evolve_interval_s=0,
            mutator_model="mock/m",
            color=False,
        )
        assert rc == 0
        # Implementation note.
        # Implementation note.
        reloaded = load_variants_from_yaml(variants_path)
        assert any(v.name == "seed" for v in reloaded)
