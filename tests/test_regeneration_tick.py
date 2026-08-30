"""Integration tests for RegenerationScheduler._tick_once 10-phase orchestration.

Spec source: prior deep-analysis identified this as the single largest
test blind spot — every stage component (RuleExtractor /
MemoryConsolidator / WorkflowRewriter / RecipeEvaluator / GEPA /
SkillForge / TopologyEvolver / EvolutionFitness / DriftMonitor /
Canary) had its own unit tests, but the orchestrator that sequences
them and threads data between them had zero coverage.

Coverage goals pinned here:

  * all 10 stages run on a single tick (happy path)
  * a failure in one stage does NOT short-circuit later stages
    (independent try/except is the documented design)
  * WorkflowRewriter (stage 3) actually reads learned_rules.json
    written by RuleExtractor (stage 1) — the one real cross-stage
    data dependency
  * GEPA consumes ``regeneration.gepa_auto_apply`` feature flag
    (False → auto_apply=False in payload; True → auto_apply=True)
  * tick_count increments and last_summary is populated
  * SkillForge stage skips gracefully when executor has no registry
"""

from __future__ import annotations

import json
import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from runtime.safety.recovery.scheduler import (
    RegenerationScheduler,
    SchedulerConfig,
)

# ── Fakes for each stage component ────────────────────────────────


class _FakeRuleExtractionReport:
    trajectories_scanned = 3
    failure_count = 1
    clusters_formed = 2
    rules_produced = [{"id": "r1", "pattern": "x"}]


class _FakeRuleExtractor:
    def __init__(self, *, journal: Any) -> None:
        self.journal = journal

    def extract(self) -> _FakeRuleExtractionReport:
        return _FakeRuleExtractionReport()


class _FakeConsolidationReport:
    trajectories_scanned = 10
    clusters_formed = 1
    memories_produced = [{"id": "m1"}]


class _FakeMemoryConsolidator:
    def __init__(self, *, journal: Any) -> None:
        self.journal = journal

    def consolidate(self) -> _FakeConsolidationReport:
        return _FakeConsolidationReport()


class _FakeWorkflowRewriterResult:
    proposals = [{"id": "w1", "action": "swap"}]

    def __init__(self) -> None:
        self.extra_field = "ok"


class _FakeWorkflowRewriter:
    def __init__(self, *, journal: Any) -> None:
        self.journal = journal
        # Capture the rules argument so we can assert cross-stage flow.
        self.received_rules: list[Any] | None = None

    def analyze(self, *, rules: list[Any]) -> _FakeWorkflowRewriterResult:
        self.received_rules = rules
        return _FakeWorkflowRewriterResult()


class _FakeRecipeEvalResult:
    scores = [{"recipe": "a", "score": 0.8}]


class _FakeRecipeEvaluator:
    def __init__(self, *, journal: Any) -> None:
        self.journal = journal

    def evaluate(self) -> _FakeRecipeEvalResult:
        return _FakeRecipeEvalResult()


class _FakeGepaTickResult:
    elapsed_s = 0.42
    recipes_scanned = 5
    recipes_promoted = 1
    results = [{"recipe": "a", "delta": 0.1}]


class _FakeGepaModule:
    bound_stack: Any = None
    last_apply: bool | None = None

    @classmethod
    def bind_stack(cls, stack: Any) -> None:
        cls.bound_stack = stack

    @classmethod
    def run_tick(cls, *, apply: bool, journal: Any) -> _FakeGepaTickResult:
        cls.last_apply = apply
        return _FakeGepaTickResult()


class _FakeForgeAutoTick(_FakeGepaModule):
    """Alias used in scheduler.py: `from runtime.safety.recovery import forge_auto_tick as _fat`."""


class _FakeRegistry:
    """Tool registry stub for SkillForge stage."""


class _FakeAgentRegistry:
    def __init__(self, *agent_ids: str) -> None:
        self._agent_ids = agent_ids

    def all_ids(self) -> list[str]:
        return sorted(self._agent_ids)


class _FakeExecutor:
    def __init__(self, registry: Any | None = None) -> None:
        self.registry = registry


class _FakePlanner:
    """Planner stub that records injected rules / memories."""

    def __init__(self) -> None:
        self.injected_rules: list[Any] | None = None
        self.injected_memories: list[Any] | None = None

    def update_learned_rules(self, rules: list[Any]) -> None:
        self.injected_rules = rules

    def update_learned_memories(self, memories: list[Any]) -> None:
        self.injected_memories = memories


class _FakeJournal:
    """Journal stub — _tick_once only reads it as an opaque token."""


class _FakeStack:
    def __init__(
        self,
        *,
        executor: Any | None = None,
        planner: Any | None = None,
        config: Any = None,
    ) -> None:
        self.journal = _FakeJournal()
        self.executor = executor
        self.planner = planner
        self.config = config


class _FakeFitnessReport:
    class _L1:
        score = 0.7
        trend = "up"

    class _Gov:
        score = 0.9
        penalty = 0.0
        reasons: list[str] = []

    l1 = _L1()
    l2 = None
    governance = _Gov()
    agent_id = "default"
    combined = 0.75
    verdict = "healthy"


class _FakeDriftReport:
    has_drift = False
    max_severity = "none"
    events: list[Any] = []


class _FakeCanarySkill:
    def __init__(self, name: str, phase: str, rate: float) -> None:
        self.skill_name = name
        # scheduler.py reads s.phase.value — provide a SimpleNamespace so
        # we don't have to build a real Enum instance.
        self.phase = types.SimpleNamespace(value=phase)
        self.current_rate = rate


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_global_state() -> Iterator[None]:
    """Reset process-wide state that other tests may pollute.

    Full-suite runs revealed cross-test contamination:
    - ``feature_flags`` snapshot (other tests call ``configure()`` with
      custom file paths and never restore, leaving a stale override)
    - ``_FakeForgeAutoTick`` class variables (``last_apply`` persists
      across tests, and if the real ``forge_auto_tick`` module runs the
      fake's class vars stay stale)
    - ``RegenerationScheduler`` singleton (defensive; ``isolated_scheduler``
      also resets, but an autouse reset covers tests that don't use it)
    """
    from runtime.platform.runtime_policy import feature_flags as _ff

    _ff.configure(None)
    _ff.reload()
    _FakeForgeAutoTick.last_apply = None
    _FakeForgeAutoTick.bound_stack = None
    RegenerationScheduler.reset()
    yield
    _ff.configure(None)
    _ff.reload()
    _FakeForgeAutoTick.last_apply = None
    _FakeForgeAutoTick.bound_stack = None
    RegenerationScheduler.reset()


@pytest.fixture
def isolated_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[RegenerationScheduler]:
    """A RegenerationScheduler with no prior singleton state.

    ``RegenerationScheduler.get()`` caches a process-wide singleton; we
    bypass it by constructing directly and resetting the class singleton
    so other tests aren't affected.
    """
    RegenerationScheduler.reset()
    sched = RegenerationScheduler()
    # Wire a fake stack — each test can reach in and override pieces.
    sched._stack = _FakeStack(
        executor=_FakeExecutor(registry=_FakeRegistry()),
        planner=_FakePlanner(),
        config=types.SimpleNamespace(name="test-agent"),
    )
    sched._config = SchedulerConfig(
        interval_sec=600,
        initial_delay_sec=0,
        output_dir=str(tmp_path),
        enabled=True,
    )
    sched.bind_agent_registry(_FakeAgentRegistry("coder"))
    from runtime.safety.evolution import auto_trigger

    monkeypatch.setattr(auto_trigger, "_has_score_history", lambda _agent_id: True)
    yield sched
    RegenerationScheduler.reset()


@pytest.fixture
def patched_stages(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace every stage component module with a fake.

    Returns a dict of the fakes so tests can assert on post-tick state.
    """
    fakes: dict[str, Any] = {
        "rule_extractor": _FakeRuleExtractor,
        "memory_consolidator": _FakeMemoryConsolidator,
        "workflow_rewriter": _FakeWorkflowRewriter,
        "recipe_evaluator": _FakeRecipeEvaluator,
        "forge_auto_tick": _FakeForgeAutoTick,
        "fitness_report": _FakeFitnessReport(),
        "drift_report": _FakeDriftReport(),
        "canary_skills": [_FakeCanarySkill("skill_a", "shadow", 0.1)],
    }

    # RuleExtractor
    fake_re_mod = types.ModuleType("runtime.safety.recovery.rule_extractor")
    fake_re_mod.RuleExtractor = _FakeRuleExtractor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.rule_extractor", fake_re_mod)

    # MemoryConsolidator
    fake_mc_mod = types.ModuleType("runtime.safety.recovery.memory_consolidator")
    fake_mc_mod.MemoryConsolidator = _FakeMemoryConsolidator  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.memory_consolidator", fake_mc_mod)

    # WorkflowRewriter
    fake_wr_mod = types.ModuleType("runtime.safety.recovery.workflow_rewriter")
    fake_wr_mod.WorkflowRewriter = _FakeWorkflowRewriter  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.workflow_rewriter", fake_wr_mod)

    # RecipeEvaluator
    fake_re_eval = types.ModuleType("runtime.safety.recovery.recipe_evaluator")
    fake_re_eval.RecipeEvaluator = _FakeRecipeEvaluator  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.recipe_evaluator", fake_re_eval)

    # forge_auto_tick (GEPA)
    fake_fat = types.ModuleType("runtime.safety.recovery.forge_auto_tick")
    fake_fat.bind_stack = _FakeForgeAutoTick.bind_stack  # type: ignore[attr-defined]
    fake_fat.run_tick = _FakeForgeAutoTick.run_tick  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.forge_auto_tick", fake_fat)
    # `from runtime.safety.recovery import forge_auto_tick` resolves via the
    # package object's attribute first, bypassing sys.modules once any prior
    # test imported the real submodule. Patch the attribute too so the fake
    # wins regardless of import order in full-suite runs.
    import runtime.safety.recovery as _recovery_pkg

    monkeypatch.setattr(_recovery_pkg, "forge_auto_tick", fake_fat, raising=False)

    # SkillForge — leave registry in place so the stage runs the happy path
    fake_sf = types.ModuleType("runtime.safety.recovery.skill_forge")

    class _FakeSkillForgeResult:
        promoted: list[str] = []
        evolution_candidates = [{"candidate_id": "forged_skill_a"}]

    class _FakeSkillForge:
        def __init__(self, *, journal: Any, registry: Any) -> None:
            pass

        @classmethod
        def for_governed_rollout(
            cls,
            *,
            journal: Any,
            registry: Any,
        ) -> _FakeSkillForge:
            return cls(journal=journal, registry=registry)

        def run(self) -> _FakeSkillForgeResult:
            return _FakeSkillForgeResult()

    fake_sf.SkillForge = _FakeSkillForge  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.skill_forge", fake_sf)

    # TopologyEvolver
    fake_org_evolver = types.ModuleType("runtime.safety.organization.evolver")

    class _FakeTopologyReport:
        proposals: list[Any] = []
        buckets_analysed = 0

    class _FakeTopologyEvolver:
        def __init__(self, *, proposals_path: Any, registry: Any) -> None:
            pass

        def tick(self) -> _FakeTopologyReport:
            return _FakeTopologyReport()

    fake_org_evolver.TopologyEvolver = _FakeTopologyEvolver  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.organization.evolver", fake_org_evolver)

    fake_org_forge = types.ModuleType("runtime.safety.organization.forge")
    fake_org_forge.load_registry = lambda: object()  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.organization.forge", fake_org_forge)

    # Evolution fitness
    fake_fit = types.ModuleType("runtime.safety.evolution.fitness")
    fake_fit.compute_fitness = (  # type: ignore[attr-defined]
        lambda agent_id, *, publish_event=True: fakes["fitness_report"]
    )
    monkeypatch.setitem(sys.modules, "runtime.safety.evolution.fitness", fake_fit)

    # DriftMonitor
    fake_drift = types.ModuleType("runtime.safety.evolution.drift_monitor")

    class _FakeDriftMonitor:
        def __init__(self, agent_id: str) -> None:
            pass

        def check(self, *, publish_events: bool = True) -> _FakeDriftReport:
            return fakes["drift_report"]

    fake_drift.DriftMonitor = _FakeDriftMonitor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.evolution.drift_monitor", fake_drift)

    # Canary
    fake_canary = types.ModuleType("runtime.safety.evolution.canary")

    class _FakeCanaryManager:
        def list_active(self) -> list[Any]:
            return fakes["canary_skills"]

    fake_canary.CanaryManager = _FakeCanaryManager  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.evolution.canary", fake_canary)

    return fakes


# ── Tests ──────────────────────────────────────────────────────────


def test_tick_once_runs_all_ten_stages(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    tmp_path: Path,
) -> None:
    """A single tick must execute all 10 stages and write all 9 JSON files."""
    isolated_scheduler._tick_once()

    # All 9 expected output files
    expected_files = [
        "learned_rules.json",
        "learned_memories.json",
        "workflow_proposals.json",
        "recipe_scores.json",
        "gepa_proposals.json",
        "forged_skills.json",
        "evolution_fitness.json",
        "evolution_drift.json",
        "evolution_canary.json",
    ]
    for name in expected_files:
        out = tmp_path / name
        assert out.exists(), f"stage output missing: {name}"
        # Each file must be valid JSON with a tick field
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["tick"] == 1

    summary = isolated_scheduler._last_summary
    assert summary["tick"] == 1
    assert summary["rules"] == 1
    assert summary["memories"] == 1
    assert summary["proposals"] == 1
    assert summary["recipe_scores"] == 1
    assert summary["gepa_proposals"] == 1
    assert summary["forged"] == 1

    memory_payload = json.loads((tmp_path / "learned_memories.json").read_text())
    assert memory_payload["scanned"] == 10
    assert memory_payload["clusters_formed"] == 1
    assert memory_payload["produced"] == 1
    assert memory_payload["memories"] == [{"id": "m1"}]
    assert summary["evolution_fitness"] == "healthy"
    assert summary["evolution_canaries"] == 1


def test_tick_once_uses_late_bound_scored_agents_not_instance_name(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from runtime.safety.evolution import auto_trigger

    isolated_scheduler._stack.config.name = "my-echo"
    isolated_scheduler.bind_agent_registry(None)
    assert isolated_scheduler._resolve_evolution_agent_ids() == ()

    monkeypatch.setattr(
        auto_trigger,
        "_has_score_history",
        lambda agent_id: agent_id in {"coder", "researcher"},
    )
    isolated_scheduler.bind_agent_registry(
        _FakeAgentRegistry("my-echo", "coder", "researcher", "../outside"),
    )
    assert isolated_scheduler._resolve_evolution_agent_ids() == ("coder", "researcher")

    fitness_calls: list[tuple[str, bool]] = []
    fake_fitness = types.ModuleType("runtime.safety.evolution.fitness")

    def _compute_fitness(agent_id: str, *, publish_event: bool = True) -> Any:
        fitness_calls.append((agent_id, publish_event))
        healthy = agent_id == "coder"
        return types.SimpleNamespace(
            agent_id=agent_id,
            l1=types.SimpleNamespace(score=0.9 if healthy else 0.6, trend="stable"),
            l2=None,
            governance=types.SimpleNamespace(score=1.0, penalty=0.0, reasons=[]),
            combined=0.9 if healthy else 0.6,
            verdict="healthy" if healthy else "degraded",
        )

    fake_fitness.compute_fitness = _compute_fitness  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.evolution.fitness", fake_fitness)

    drift_calls: list[tuple[str, bool]] = []
    drift_constructed: list[str] = []
    fake_drift = types.ModuleType("runtime.safety.evolution.drift_monitor")

    class _PerAgentDriftMonitor:
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            drift_constructed.append(agent_id)

        def check(self, *, publish_events: bool = True) -> Any:
            drift_calls.append((self.agent_id, publish_events))
            regressed = self.agent_id == "researcher"
            events = (
                [
                    types.SimpleNamespace(
                        kind="score_regression",
                        severity="warning",
                        detail="score dropped",
                    ),
                ]
                if regressed
                else []
            )
            return types.SimpleNamespace(
                agent_id=self.agent_id,
                has_drift=regressed,
                max_severity="warning" if regressed else "none",
                events=events,
            )

    fake_drift.DriftMonitor = _PerAgentDriftMonitor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.evolution.drift_monitor", fake_drift)

    isolated_scheduler._tick_once()

    assert fitness_calls == [("coder", False), ("researcher", False)]
    assert drift_calls == [("coder", False), ("researcher", False)]

    fitness_payload = json.loads((tmp_path / "evolution_fitness.json").read_text())
    assert fitness_payload["agent_ids"] == ["coder", "researcher"]
    assert fitness_payload["agent_id"] == "researcher"  # legacy worst-agent field
    assert fitness_payload["verdict"] == "degraded"  # legacy worst verdict
    assert fitness_payload["combined"] == 0.6
    assert [entry["agent_id"] for entry in fitness_payload["agents"]] == [
        "coder",
        "researcher",
    ]
    assert "my-echo" not in str(fitness_payload)

    drift_payload = json.loads((tmp_path / "evolution_drift.json").read_text())
    assert drift_payload["agent_ids"] == ["coder", "researcher"]
    assert drift_payload["has_drift"] is True  # legacy aggregate field
    assert drift_payload["max_severity"] == "warning"  # legacy aggregate field
    assert drift_payload["events"][0]["agent_id"] == "researcher"
    assert "my-echo" not in str(drift_payload)

    summary = isolated_scheduler._last_summary
    assert summary["evolution_fitness_agents"] == {
        "coder": "healthy",
        "researcher": "degraded",
    }
    assert summary["evolution_drift_agents"] == {
        "coder": "none",
        "researcher": "warning",
    }
    assert summary["evolution_fitness"] == "degraded"
    assert summary["evolution_combined"] == 0.6

    # A second scheduler tick must reuse each monitor; otherwise all of
    # DriftMonitor's instance baselines would reset before they can compare.
    isolated_scheduler._tick_once()
    assert drift_constructed == ["coder", "researcher"]
    assert drift_calls == [
        ("coder", False),
        ("researcher", False),
        ("coder", False),
        ("researcher", False),
    ]

    isolated_scheduler.bind_agent_registry(_FakeAgentRegistry("coder"))
    assert set(isolated_scheduler._drift_monitors) == {"coder"}


def test_tick_once_increments_tick_count(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
) -> None:
    """Each tick must increment the global tick counter."""
    assert isolated_scheduler._tick_count == 0
    isolated_scheduler._tick_once()
    assert isolated_scheduler._tick_count == 1
    isolated_scheduler._tick_once()
    assert isolated_scheduler._tick_count == 2
    # The latest summary reflects the latest tick number
    assert isolated_scheduler._last_summary["tick"] == 2


def test_tick_once_stage_failure_does_not_short_circuit(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """If RuleExtractor (stage 1) raises, stages 2-10 must still run.

    The orchestrator wraps each stage in its own try/except so a buggy
    stage cannot abort the whole regeneration tick. This pins that
    contract: a stage-1 failure surfaces as ``summary["rules"] == "err"``
    but later stages still write their output files.
    """

    # Make RuleExtractor.extract() blow up
    class _ExplodingRuleExtractor:
        def __init__(self, *, journal: Any) -> None:
            pass

        def extract(self):  # noqa: ANN201
            raise RuntimeError("simulated stage-1 failure")

    fake_re_mod = types.ModuleType("runtime.safety.recovery.rule_extractor")
    fake_re_mod.RuleExtractor = _ExplodingRuleExtractor  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.rule_extractor", fake_re_mod)

    isolated_scheduler._tick_once()

    summary = isolated_scheduler._last_summary
    # Stage 1 recorded the error
    assert summary["rules"] == "err"
    # learned_rules.json was NOT written (stage 1 failed before the write)
    assert not (tmp_path / "learned_rules.json").exists()
    # But every later stage's output file IS present
    for name in [
        "learned_memories.json",
        "workflow_proposals.json",
        "recipe_scores.json",
        "gepa_proposals.json",
        "forged_skills.json",
        "evolution_fitness.json",
        "evolution_drift.json",
        "evolution_canary.json",
    ]:
        assert (tmp_path / name).exists(), f"later stage skipped: {name}"


def test_tick_once_workflow_rewriter_reads_rule_extractor_output(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Stage 3 (WorkflowRewriter) must read learned_rules.json written by stage 1.

    This is the one real cross-stage data dependency: stage 1 writes
    ``learned_rules.json``, stage 3 opens it and passes the rules to
    WorkflowRewriter.analyze(rules=...). A regression that broke the
    file handoff (e.g. stage 3 reading the wrong path, or stage 1
    failing to flush) would silently feed an empty list to stage 3.
    """
    # Capture the WorkflowRewriter instance so we can inspect received_rules
    captured: list[_FakeWorkflowRewriter] = []

    original_init = _FakeWorkflowRewriter.__init__

    def spying_init(self, *, journal: Any) -> None:
        original_init(self, journal=journal)
        captured.append(self)

    _FakeWorkflowRewriter.__init__ = spying_init  # type: ignore[assignment]
    try:
        isolated_scheduler._tick_once()
    finally:
        _FakeWorkflowRewriter.__init__ = original_init  # type: ignore[assignment]

    assert len(captured) == 1
    received = captured[0].received_rules
    assert received is not None, "WorkflowRewriter.analyze was never called"
    # Stage 1 produced 1 rule (see _FakeRuleExtractionReport)
    assert len(received) == 1
    assert received[0]["id"] == "r1"


def test_tick_once_gepa_consumes_feature_flag_false(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GEPA stage reads regeneration.gepa_auto_apply flag (default False)."""
    # Ensure the flag is off (its default)
    monkeypatch.setenv("ECHO_FF_REGENERATION_GEPA_AUTO_APPLY", "0")
    # Feature flags cache a process-wide snapshot; force a re-resolve
    # so the env override takes effect for this tick.
    from runtime.platform.runtime_policy import feature_flags as _ff

    _ff.reload()

    isolated_scheduler._tick_once()

    # _FakeForgeAutoTick captured the apply kwarg
    assert _FakeForgeAutoTick.last_apply is False
    # And the payload reflects it
    summary = isolated_scheduler._last_summary
    assert summary["gepa_promoted"] == 1  # fake result regardless of flag


def test_tick_once_gepa_consumes_feature_flag_true(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When regeneration.gepa_auto_apply is on, GEPA receives apply=True."""
    monkeypatch.setenv("ECHO_FF_REGENERATION_GEPA_AUTO_APPLY", "1")
    from runtime.platform.runtime_policy import feature_flags as _ff

    _ff.reload()

    isolated_scheduler._tick_once()

    assert _FakeForgeAutoTick.last_apply is True
    gepa_payload = json.loads((tmp_path / "gepa_proposals.json").read_text())
    assert gepa_payload["auto_apply"] is True


def test_tick_once_skill_forge_skips_without_registry(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
) -> None:
    """When executor has no registry, SkillForge stage records a skip, not an error."""
    # Strip the registry off the executor
    isolated_scheduler._stack.executor.registry = None

    isolated_scheduler._tick_once()

    summary = isolated_scheduler._last_summary
    assert summary["forged"] == "skip(no_registry)"


def test_tick_once_propagates_rules_to_planner(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
) -> None:
    """Stage 1 also pushes rules into the planner via update_learned_rules."""
    isolated_scheduler._tick_once()

    planner: _FakePlanner = isolated_scheduler._stack.planner
    assert planner.injected_rules is not None
    assert len(planner.injected_rules) == 1
    # And memories from stage 2
    assert planner.injected_memories is not None
    assert len(planner.injected_memories) == 1


def test_tick_once_records_stage_error_type_in_summary(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SkillForge stage records the exception type in summary (not bare 'err').

    A bare 'err' hid recurring failure patterns; the scheduler keeps the
    type name so a duplicate-name crash loop is visible without grepping
    logs. Pin this so a future refactor doesn't revert to bare 'err'.
    """

    class _ExplodingSkillForge:
        def __init__(self, *, journal: Any, registry: Any) -> None:
            pass

        @classmethod
        def for_governed_rollout(
            cls,
            *,
            journal: Any,
            registry: Any,
        ) -> _ExplodingSkillForge:
            return cls(journal=journal, registry=registry)

        def run(self):  # noqa: ANN201
            raise ValueError("duplicate name")

    fake_sf = types.ModuleType("runtime.safety.recovery.skill_forge")
    fake_sf.SkillForge = _ExplodingSkillForge  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.skill_forge", fake_sf)

    isolated_scheduler._tick_once()

    summary = isolated_scheduler._last_summary
    assert summary["forged"] == "err:ValueError"


def test_tick_once_legacy_skill_forge_fails_closed(
    isolated_scheduler: RegenerationScheduler,
    patched_stages: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mixed-version forge must never fall back to direct live promotion."""

    class _LegacySkillForge:
        constructed = False

        def __init__(self, *, journal: Any, registry: Any) -> None:
            type(self).constructed = True

        def run(self):  # noqa: ANN201
            raise AssertionError("legacy direct-promotion path must not run")

    fake_sf = types.ModuleType("runtime.safety.recovery.skill_forge")
    fake_sf.SkillForge = _LegacySkillForge  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "runtime.safety.recovery.skill_forge", fake_sf)

    isolated_scheduler._tick_once()

    assert isolated_scheduler._last_summary["forged"] == ("err:GovernedSkillForgeUnavailable")
    assert _LegacySkillForge.constructed is False
    assert not (tmp_path / "forged_skills.json").exists()

