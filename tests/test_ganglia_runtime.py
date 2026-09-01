"""Implementation note."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from runtime.core.cerebrum.planner import Rule, StaticPlanner
from runtime.core.graph_runtime import GraphRuntime, TemplateResolutionError, resolve_templates
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.tool_engine import ToolExecutor
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    Budget,
    BudgetLimits,
    BudgetSpec,
    ParsedIntent,
    SkillId,
)
from runtime.safety.auth import TrustEngine
from runtime.safety.evolution.canary import CanaryConfig, CanaryManager
from runtime.safety.recovery.gepa_bridge import (
    mark_winner_proposal_applied,
    record_winner_proposal_and_canary,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestResolveTemplates:
    def test_no_template_passes_through(self):
        out = resolve_templates({"x": 1, "s": "literal"}, prev_outputs={})
        assert out == {"x": 1, "s": "literal"}

    def test_pure_template_returns_raw_value(self):
        out = resolve_templates({"x": "{n0}"}, prev_outputs={"n0": {"a": 1}})
        assert out == {"x": {"a": 1}}  # Implementation note.

    def test_dot_access_on_dict(self):
        out = resolve_templates(
            {"val": "{n0.key}"},
            prev_outputs={"n0": {"key": "hello"}},
        )
        assert out == {"val": "hello"}

    def test_output_marker_is_transparent(self):
        """``{n0.output.key}`` and ``{n0.key}`` resolve to the same
        value — the ``.output`` marker is a syntactic sugar used by
        the meta_skill template convention and the runtime strips
        it transparently.
        """
        a = resolve_templates(
            {"val": "{n0.key}"},
            prev_outputs={"n0": {"key": "hello"}},
        )
        b = resolve_templates(
            {"val": "{n0.output.key}"},
            prev_outputs={"n0": {"key": "hello"}},
        )
        assert a == b == {"val": "hello"}

    def test_nested_dot_access(self):
        out = resolve_templates(
            {"val": "{n0.a.b}"},
            prev_outputs={"n0": {"a": {"b": 42}}},
        )
        assert out == {"val": 42}

    def test_list_index_access(self):
        out = resolve_templates(
            {"val": "{n0.items.0.name}"},
            prev_outputs={"n0": {"items": [{"name": "first"}, {"name": "second"}]}},
        )
        assert out == {"val": "first"}

    def test_inline_interpolation(self):
        out = resolve_templates(
            {"path": "{n0.base}/README.md"},
            prev_outputs={"n0": {"base": "/tmp"}},
        )
        assert out == {"path": "/tmp/README.md"}

    def test_multiple_refs_same_value(self):
        out = resolve_templates(
            {"combo": "{n0.x}-{n1.y}"},
            prev_outputs={"n0": {"x": "A"}, "n1": {"y": "B"}},
        )
        assert out == {"combo": "A-B"}

    def test_unknown_node_raises(self):
        with pytest.raises(TemplateResolutionError, match="unknown node"):
            resolve_templates({"x": "{n99.key}"}, prev_outputs={"n0": {}})

    def test_missing_key_raises(self):
        with pytest.raises(TemplateResolutionError, match="not in output"):
            resolve_templates({"x": "{n0.missing}"}, prev_outputs={"n0": {"other": 1}})

    def test_bad_list_index_raises(self):
        with pytest.raises(TemplateResolutionError, match="out of range"):
            resolve_templates(
                {"x": "{n0.items.99}"},
                prev_outputs={"n0": {"items": ["a"]}},
            )

    def test_invalid_ref_form(self):
        with pytest.raises(TemplateResolutionError, match="must be a valid identifier"):
            resolve_templates({"x": "{1bad.ref}"}, prev_outputs={"n0": {}})
        # Starts with a digit — fails the identifier check.
        with pytest.raises(TemplateResolutionError, match="must be a valid identifier"):
            resolve_templates({"x": "{9.field}"}, prev_outputs={"n0": {}})


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def runtime_stack():
    calls: list[tuple[str, dict]] = []

    def _make_handler(name: str, output_fn):
        def h(**kw):
            calls.append((name, kw))
            return output_fn(kw)

        return h

    registry = SkillRegistry()
    registry.register(
        Skill(
            name="source",
            trusted_source="skill://public/source",
            handler=_make_handler("source", lambda _kw: {"path": "/root", "count": 3}),
        ),
        verify_tests=False,
    )
    registry.register(
        Skill(
            name="consumer",
            trusted_source="skill://public/consumer",
            handler=_make_handler(
                "consumer",
                lambda kw: {"got_path": kw.get("path"), "got_count": kw.get("count")},
            ),
        ),
        verify_tests=False,
    )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    return {"runtime": runtime, "calls": calls, "journal": journal}


class TestGraphRuntimeDataFlow:
    def test_n1_receives_n0_output(self, runtime_stack):
        """Implementation note."""
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="test_flow",
                    intent_types=["task"],
                    skill_sequence=[SkillId("source"), SkillId("consumer")],
                    node_args_templates=[
                        None,  # Implementation note.
                        {"path": "{n0.path}", "count": "{n0.count}"},
                    ],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="flow test")
        graph = planner.plan(intent)

        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        traj = runtime_stack["runtime"].run(
            graph, budget=budget, caller="arms/code_arm", arm_id=ArmId("code_arm")
        )

        assert traj.outcome.success
        assert len(traj.steps) == 2
        # Implementation note.
        assert traj.steps[1].result.output == {"got_path": "/root", "got_count": 3}

    def test_template_failure_aborts_pipeline(self, runtime_stack):
        """Implementation note."""
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="bad_ref",
                    intent_types=["task"],
                    skill_sequence=[SkillId("source"), SkillId("consumer")],
                    node_args_templates=[
                        None,
                        {"path": "{n99.nope}"},  # Implementation note.
                    ],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="bad ref")
        graph = planner.plan(intent)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        traj = runtime_stack["runtime"].run(
            graph, budget=budget, caller="arms/code_arm", arm_id=ArmId("code_arm")
        )
        assert not traj.outcome.success
        # Now two steps: n0 succeeded · n1 failed with template error.
        assert len(traj.steps) == 2
        assert traj.steps[0].success
        assert not traj.steps[1].success
        # Failure reason must be structured · so downstream can filter
        # on it without regex-parsing an error message.
        assert traj.steps[1].result.error_type == "TemplateResolutionError"
        assert "template_resolution" in traj.steps[1].result.stderr_tags
        # The original (unresolved) args are preserved on the failed
        # call — useful for "show me what was supposed to be passed"
        # debug views and for replay with a fixed template.
        assert traj.steps[1].action.args.get("path") == "{n99.nope}"
        # pre-executor failure → no immunity check happened, so the
        # verdict stays unset (tells readers this wasn't immunity-
        # blocked, it was template-blocked).
        assert traj.steps[1].immune_verdict is None

    def test_template_failure_written_to_journal(self, runtime_stack):
        """Implementation note."""
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="bad_ref_journal",
                    intent_types=["task"],
                    skill_sequence=[SkillId("source"), SkillId("consumer")],
                    node_args_templates=[None, {"path": "{n99.nope}"}],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(
            raw="x",
            intent_type="task",
            normalized_goal="bad ref j",
        )
        graph = planner.plan(intent)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        runtime_stack["runtime"].run(
            graph,
            budget=budget,
            caller="arms/code_arm",
            arm_id=ArmId("code_arm"),
        )
        journal = runtime_stack["journal"]
        # Collect step events and find the failed one. The journal
        # tags StepEvent with ``event_type == "step"`` (not "kind");
        # earlier this test had that wrong — leaving the assertion
        # as a regression signal against a future rename as well.
        events = list(journal.read_all())
        failed_step_events = [
            e
            for e in events
            if getattr(e, "event_type", None) == "step"
            and getattr(getattr(e, "step", None), "result", None) is not None
            and e.step.result.status == "failed"
            and e.step.result.error_type == "TemplateResolutionError"
        ]
        assert len(failed_step_events) == 1, (
            "template-resolution failure must produce exactly one failed Step event in the journal"
        )

    def test_trajectory_written_to_journal(self, runtime_stack):
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="single",
                    intent_types=["task"],
                    skill_sequence=[SkillId("source")],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="simple")
        graph = planner.plan(intent)
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        runtime_stack["runtime"].run(
            graph, budget=budget, caller="arms/code_arm", arm_id=ArmId("code_arm")
        )
        trajectories = runtime_stack["journal"].read_by_type("trajectory")
        assert len(trajectories) == 1

    def test_applied_winner_canary_receives_runtime_outcome(self, runtime_stack, tmp_path):
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="single",
                    intent_types=["task"],
                    skill_sequence=[SkillId("source")],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="winner flow")
        graph = planner.plan(intent)
        result = SimpleNamespace(
            best_avg=SimpleNamespace(
                candidate_id="cand-1",
                avg_score=0.9,
                born_at_iter=1,
                parent_id="seed",
                rationale="tighten checks",
                prompt="prompt",
            ),
            iterations_run=1,
            final_front=[],
            history=[{"iter": 0, "candidate_id": "seed", "best_avg": 0.5}],
        )
        winner = record_winner_proposal_and_canary(
            result,
            recipe_id=graph.recipe_hash,
            ledger_path=tmp_path / "proposal_ledger.jsonl",
            canary_config=CanaryConfig(state_dir=str(tmp_path / "canary")),
        )
        assert winner["ok"] is True
        applied = mark_winner_proposal_applied(
            recipe_id=graph.recipe_hash,
            candidate_id="cand-1",
            proposal_id=winner["proposal_id"],
            canary_key=winner["canary_key"],
            ledger_path=tmp_path / "proposal_ledger.jsonl",
            metadata_root=tmp_path,
        )
        assert applied["ok"] is True

        runtime = GraphRuntime(
            executor=runtime_stack["runtime"].executor,
            journal=runtime_stack["journal"],
            canary_config=CanaryConfig(state_dir=str(tmp_path / "canary")),
            evolution_metadata_root=tmp_path,
        )
        budget = Budget(
            task_id=graph.task_id,
            limits=BudgetLimits(tokens=10_000, usd=0.10),
        )
        traj = runtime.run(graph, budget=budget, caller="arms/code_arm", arm_id=ArmId("code_arm"))
        assert traj.outcome.success
        state = CanaryManager(
            CanaryConfig(state_dir=str(tmp_path / "canary")),
        ).get_state(winner["canary_key"])
        assert state is not None
        assert state.sample_count == 1
        assert state.success_count == 1
        assert state.current_rate == 1.0


class TestRuleNodeArgs:
    def test_args_for_index_with_override(self):
        r = Rule(
            name="x",
            skill_sequence=[SkillId("a"), SkillId("b")],
            node_args_templates=[None, {"custom": "v"}],
        )
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="goal")
        # Implementation note.
        args0 = r.args_for(0, intent)
        assert args0 == {"intent_goal": "goal"}
        # Implementation note.
        args1 = r.args_for(1, intent)
        assert args1 == {"intent_goal": "goal", "custom": "v"}


# ═══════════════════════════════════════════════════════════
# Parallel-layer failure → retry/replan path (regression).
# Two bugs lived here undetected: a NameError (`gi` vs `_gi`)
# in the parallel-failure loop, and a ParsedIntent built with a
# non-existent `raw_input=` field + missing `intent_type`, which
# always raised ValidationError (swallowed → replan never ran).
# ═══════════════════════════════════════════════════════════


def _boom_runtime():
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="root",
            trusted_source="skill://public/root",
            handler=lambda **kw: {"ok": True},
        ),
        verify_tests=False,
    )

    def _boom(**kw):
        raise RuntimeError("intentional failure")

    registry.register(
        Skill(
            name="boom",
            trusted_source="skill://public/boom",
            handler=_boom,
        ),
        verify_tests=False,
    )
    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    return GraphRuntime(executor=executor, journal=journal)


def _parallel_failing_graph():
    from runtime.platform.models import TaskGraph, TaskNode, WorkflowEdge

    # Two root nodes in the same topo layer both fail; one edge present
    # so the runtime takes the parallel path (use_parallel needs edges).
    return TaskGraph(
        nodes=[
            TaskNode(node_id="n0", skill_ref=SkillId("boom")),
            TaskNode(node_id="n1", skill_ref=SkillId("boom")),
            TaskNode(node_id="n2", skill_ref=SkillId("root")),
        ],
        edges=[WorkflowEdge(from_node="n0", to_node="n2")],
        budget=BudgetSpec(tokens=10_000, usd=0.10),
    )


def _budget(graph):
    return Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=10_000, usd=0.10),
    )


class TestParallelFailureRetry:
    def test_parallel_failure_does_not_nameerror(self):
        # Before the fix the parallel-failure loop referenced an unbound
        # `gi`, crashing the whole graph run with NameError. planner=None
        # so _retry_or_replan returns early — the crash was at the call
        # site, before entry.
        rt = _boom_runtime()
        graph = _parallel_failing_graph()
        traj = rt.run(
            graph,
            budget=_budget(graph),
            caller="arms/code_arm",
            arm_id=ArmId("code_arm"),
            planner=None,
        )
        assert traj is not None
        assert not traj.outcome.success

    def test_replan_intent_constructs_without_validationerror(self):
        # A fake planner whose .plan() is called proves the replan body
        # (which builds a ParsedIntent) runs without ValidationError.
        from runtime.platform.models import TaskGraph

        planned = {"called": False}

        class _FakePlanner:
            def plan(self, intent, *, model=None):
                planned["called"] = True
                # A valid ParsedIntent reached us — assert its shape.
                assert intent.intent_type == "task"
                assert intent.raw
                assert model is None
                return TaskGraph(
                    nodes=_parallel_failing_graph().nodes[:1],
                    budget=BudgetSpec(tokens=1000, usd=0.01),
                )

        rt = _boom_runtime()
        graph = _parallel_failing_graph()
        rt.run(
            graph,
            budget=_budget(graph),
            caller="arms/code_arm",
            arm_id=ArmId("code_arm"),
            planner=_FakePlanner(),
            max_replans=1,
        )
        assert planned["called"], "replan was never attempted (ParsedIntent build failed?)"
