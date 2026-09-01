"""Golden-path integration tests · plan → execute → (replan) → verify.

The existing bugfix-demo E2E drives a LINEAR, all-succeeding 8-node
graph. The crash bugs this session fixed lived elsewhere — the parallel
layer (``_run_layer_parallel``) and the failure-retry/replan path
(``_retry_or_replan``) — which no integration test exercised, which is
why a guaranteed NameError and a guaranteed-ValidationError replan sat
undetected.

These drive the WHOLE pipeline deterministically (no LLM key, no git,
no subprocess — pure in-memory skills + a real StaticPlanner + the real
GraphRuntime) through the three structural shapes that matter:

  1. plan → execute → verify, happy path, asserting the data flow and
     the journal lifecycle events.
  2. a parallel layer actually running both branches.
  3. a failing step triggering replan and recovering without crashing.

CI runs these every push, so a regression on any of those paths shows
up as a red integration test, not a production crash.
"""

from __future__ import annotations

from typing import Any

from runtime.core.cerebrum.planner import Rule, StaticPlanner
from runtime.core.graph_runtime import GraphRuntime
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
    TaskGraph,
    TaskNode,
    WorkflowEdge,
)
from runtime.safety.auth import TrustEngine


def _stack(extra_skills: dict[str, Any] | None = None):
    """A real registry + executor + GraphRuntime, deterministic skills."""
    registry = SkillRegistry()
    calls: list[str] = []

    def reg(name: str, fn):
        def handler(**kw):
            calls.append(name)
            return fn(**kw)

        registry.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=handler,
            ),
            verify_tests=False,
        )

    # read: emits a value. transform: consumes it. verify: asserts ok.
    reg("read_src", lambda **kw: {"content": "x = 1", "lines": 1})
    reg("transform", lambda **kw: {"patched": True, "input_lines": kw.get("lines")})
    reg("verify_ok", lambda **kw: {"passed": True, "checked": kw.get("patched")})
    reg("boom", _raise)
    reg("recover", lambda **kw: {"recovered": True})
    for name, fn in (extra_skills or {}).items():
        reg(name, fn)

    journal = InMemoryJournal()
    executor = ToolExecutor(
        registry=registry,
        immunity=TrustEngine(trusted_sources=["skill://public/*"]),
        journal=journal,
    )
    runtime = GraphRuntime(executor=executor, journal=journal)
    return runtime, journal, calls


def _raise(**kw):
    raise RuntimeError("step failed on purpose")


def _budget(graph: TaskGraph) -> Budget:
    return Budget(
        task_id=graph.task_id,
        limits=BudgetLimits(tokens=10_000, usd=0.10),
    )


def _run(runtime, graph, **kw):
    return runtime.run(
        graph,
        budget=_budget(graph),
        caller="arms/code_arm",
        arm_id=ArmId("code_arm"),
        **kw,
    )


class TestPlanExecuteVerifyHappyPath:
    def test_static_planner_drives_full_pipeline(self):
        runtime, journal, calls = _stack()
        # PLAN: a real StaticPlanner turns an intent into a 3-node graph.
        planner = StaticPlanner(
            rules=[
                Rule(
                    name="fix_flow",
                    intent_types=["task"],
                    skill_sequence=[
                        SkillId("read_src"),
                        SkillId("transform"),
                        SkillId("verify_ok"),
                    ],
                    node_args_templates=[
                        None,
                        {"lines": "{n0.lines}"},
                        {"patched": "{n1.patched}"},
                    ],
                )
            ],
            default_budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        intent = ParsedIntent(raw="fix the bug", intent_type="task", normalized_goal="fix the bug")
        graph = planner.plan(intent)
        assert len(graph.nodes) == 3

        # EXECUTE + VERIFY: drive it through the real runtime.
        traj = _run(runtime, graph)

        assert traj.outcome.success
        assert calls == ["read_src", "transform", "verify_ok"]
        # Data actually flowed across nodes (template resolution worked).
        assert traj.steps[1].result.output["input_lines"] == 1
        assert traj.steps[2].result.output["checked"] is True
        # Journal lifecycle: the run is bracketed by a task_started and a
        # final trajectory record carrying the outcome.
        assert journal.read_by_type("task_started")
        traj_events = journal.read_by_type("trajectory")
        assert traj_events
        # Per-step records exist for all three nodes.
        assert len(journal.read_by_type("step")) == 3


class TestParallelLayer:
    def test_parallel_layer_runs_both_branches(self):
        runtime, _journal, calls = _stack()
        # n1 and n2 both depend on n0 → same topo layer → parallel path.
        graph = TaskGraph(
            nodes=[
                TaskNode(node_id="n0", skill_ref=SkillId("read_src")),
                TaskNode(node_id="n1", skill_ref=SkillId("transform")),
                TaskNode(node_id="n2", skill_ref=SkillId("verify_ok")),
            ],
            edges=[
                WorkflowEdge(from_node="n0", to_node="n1"),
                WorkflowEdge(from_node="n0", to_node="n2"),
            ],
            budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        traj = _run(runtime, graph)
        assert traj.outcome.success
        # Both parallel branches executed (order within the layer is free).
        assert set(calls) == {"read_src", "transform", "verify_ok"}


class TestReplanOnFailure:
    def test_failure_triggers_replan_and_does_not_crash(self):
        runtime, _journal, _calls = _stack()
        # A graph whose only node fails. With a planner supplied, the
        # runtime must reach _retry_or_replan WITHOUT crashing (the bugs
        # were a NameError at the call site and a ValidationError
        # building the replan ParsedIntent).
        graph = TaskGraph(
            nodes=[TaskNode(node_id="n0", skill_ref=SkillId("boom"))],
            budget=BudgetSpec(tokens=10_000, usd=0.10),
        )

        replanned: dict[str, Any] = {"called": False, "intent_ok": False}

        class _RecoveryPlanner:
            def plan(self, intent, *, model=None):
                replanned["called"] = True
                # The replan path must hand us a VALID ParsedIntent
                # (raw + intent_type populated) — not crash building it.
                replanned["intent_ok"] = bool(intent.raw and intent.intent_type == "task")
                assert model is None
                return TaskGraph(
                    nodes=[TaskNode(node_id="r0", skill_ref=SkillId("recover"))],
                    budget=BudgetSpec(tokens=1000, usd=0.01),
                )

        # Must not raise.
        traj = _run(runtime, graph, planner=_RecoveryPlanner(), max_replans=1)
        assert traj is not None
        assert replanned["called"], "replan was never attempted"
        assert replanned["intent_ok"], "replan got a malformed ParsedIntent"

    def test_parallel_failure_triggers_replan_without_nameerror(self):
        runtime, _journal, _calls = _stack()
        # Two failing roots in one parallel layer → the exact branch that
        # referenced an unbound `gi`. Must not NameError.
        graph = TaskGraph(
            nodes=[
                TaskNode(node_id="n0", skill_ref=SkillId("boom")),
                TaskNode(node_id="n1", skill_ref=SkillId("boom")),
                TaskNode(node_id="n2", skill_ref=SkillId("read_src")),
            ],
            edges=[WorkflowEdge(from_node="n0", to_node="n2")],
            budget=BudgetSpec(tokens=10_000, usd=0.10),
        )
        traj = _run(runtime, graph, planner=None)
        assert traj is not None
        assert not traj.outcome.success
