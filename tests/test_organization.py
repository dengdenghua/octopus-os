"""Tests for runtime.safety.organization — team topology evolution."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from runtime.safety.organization import (
    AgentSpec,
    CoordinationProtocol,
    Role,
    TeamTopology,
)
from runtime.safety.organization.evolver import TopologyEvolver
from runtime.safety.organization.forge import (
    PromoteResult,
    TopologyForge,
    load_registry,
    save_registry,
)
from runtime.safety.organization.performance_log import (
    read_runs,
    record_run,
)
from runtime.safety.organization.team_runner import (
    TeamRunner,
    _parse_evaluator_score,
)

# ── Topology data model ──────────────────────────────────────


def test_sequential_topology_requires_planner_or_generator() -> None:
    with pytest.raises(ValueError, match="planner or generator"):
        TeamTopology(
            name="bad",
            protocol=CoordinationProtocol.SEQUENTIAL,
            agents={Role.EVALUATOR: AgentSpec(agent_id="a")},
        )


def test_evaluator_optimizer_requires_both_roles() -> None:
    with pytest.raises(ValueError, match="evaluator_optimizer"):
        TeamTopology(
            name="bad",
            protocol=CoordinationProtocol.EVALUATOR_OPTIMIZER,
            agents={Role.GENERATOR: AgentSpec(agent_id="g")},
        )


def test_topology_fingerprint_stable() -> None:
    a = TeamTopology(
        name="t1",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="p")},
    )
    b = TeamTopology(
        name="t1-rename",  # name doesn't enter fingerprint
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="p")},
    )
    assert a.fingerprint == b.fingerprint


def test_topology_fingerprint_changes_on_agent_swap() -> None:
    a = TeamTopology(
        name="t",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="alice")},
    )
    b = TeamTopology(
        name="t",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="bob")},
    )
    assert a.fingerprint != b.fingerprint


def test_topology_roundtrips_through_dict() -> None:
    a = TeamTopology(
        name="trip",
        protocol=CoordinationProtocol.EVALUATOR_OPTIMIZER,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="g", temperature=0.7),
            Role.EVALUATOR: AgentSpec(agent_id="e"),
        },
        quality_threshold=0.75,
        max_iterations=2,
        task_bucket="bench",
    )
    b = TeamTopology.from_dict(a.to_dict())
    assert b.fingerprint == a.fingerprint
    assert b.protocol == CoordinationProtocol.EVALUATOR_OPTIMIZER
    assert b.agents[Role.GENERATOR].temperature == 0.7
    assert b.quality_threshold == 0.75


# ── Score parser ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "text, expected",
    [
        ("score: 0.85\nreason: good", 0.85),
        ("Quality 0.40 — needs work", 0.40),
        ("rating: 85/100", 0.85),
        ("no score here", None),
        ("score: 1.5", 1.0),  # clamp
    ],
)
def test_parse_evaluator_score(text, expected) -> None:
    assert _parse_evaluator_score(text) == expected


# ── TeamRunner: sequential ───────────────────────────────────


def _stub_caller(scripts: dict[str, dict[str, Any]]):
    """Build a role caller that returns a scripted reply per agent_id."""

    def caller(**kwargs):
        agent_id = kwargs["agent_id"]
        return scripts.get(agent_id, {"output": f"<{agent_id}>", "success": True})

    return caller


def test_team_runner_sequential_chains_outputs() -> None:
    topology = TeamTopology(
        name="chain",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.PLANNER: AgentSpec(agent_id="planner"),
            Role.GENERATOR: AgentSpec(agent_id="gen"),
            Role.EVALUATOR: AgentSpec(agent_id="judge"),
        },
    )
    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "planner": {"output": "plan: outline", "success": True},
                "gen": {"output": "draft v1", "success": True},
                "judge": {"output": "score: 0.8\nlooks good", "success": True},
            }
        )
    )
    result = runner.run(topology, "build foo")
    assert result.success is True
    assert result.final_output == "score: 0.8\nlooks good"
    assert [str(o.role) for o in result.role_outputs] == [
        "planner",
        "generator",
        "evaluator",
    ]
    assert result.quality_score == 0.8


def test_team_runner_sequential_degrades_on_role_error() -> None:
    topology = TeamTopology(
        name="bail",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.PLANNER: AgentSpec(agent_id="p"),
            Role.GENERATOR: AgentSpec(agent_id="g"),
        },
    )
    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "p": {"output": "", "success": False, "error": "boom"},
                "g": {"output": "should not run", "success": True},
            }
        )
    )
    result = runner.run(topology, "x")
    assert result.success is True
    assert result.final_output == "should not run"
    assert len(result.role_outputs) == 2
    assert result.degraded_roles == ["planner"]


# ── TeamRunner: evaluator_optimizer ──────────────────────────


def test_team_runner_evaluator_optimizer_passes_first_try() -> None:
    topology = TeamTopology(
        name="eo",
        protocol=CoordinationProtocol.EVALUATOR_OPTIMIZER,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="g"),
            Role.EVALUATOR: AgentSpec(agent_id="e"),
        },
        quality_threshold=0.5,
    )
    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "g": {"output": "answer A", "success": True},
                "e": {"output": "score: 0.8 great", "success": True},
            }
        )
    )
    result = runner.run(topology, "task")
    assert result.iterations == 1
    assert result.final_output == "answer A"
    assert result.success is True


def test_team_runner_evaluator_optimizer_retries_on_low_score() -> None:
    """Force two iterations: first eval is 0.2, second is 0.9."""
    call_count = {"e": 0, "g": 0}

    def caller(**kwargs):
        aid = kwargs["agent_id"]
        if aid == "g":
            call_count["g"] += 1
            return {"output": f"draft {call_count['g']}", "success": True}
        if aid == "e":
            call_count["e"] += 1
            score = 0.2 if call_count["e"] == 1 else 0.9
            return {"output": f"score: {score}", "success": True}
        return {"output": "", "success": True}

    topology = TeamTopology(
        name="eo-retry",
        protocol=CoordinationProtocol.EVALUATOR_OPTIMIZER,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="g"),
            Role.EVALUATOR: AgentSpec(agent_id="e"),
        },
        quality_threshold=0.5,
        max_iterations=3,
    )
    runner = TeamRunner(role_caller=caller)
    result = runner.run(topology, "task")
    assert result.iterations == 2
    assert result.final_output == "draft 2"
    assert result.quality_score == 0.9


def test_team_runner_evaluator_optimizer_exhausts_iterations() -> None:
    """All iterations below threshold — still returns latest draft."""

    def caller(**kwargs):
        aid = kwargs["agent_id"]
        if aid == "g":
            return {"output": "weak draft", "success": True}
        return {"output": "score: 0.1", "success": True}

    topology = TeamTopology(
        name="eo-exhaust",
        protocol=CoordinationProtocol.EVALUATOR_OPTIMIZER,
        agents={
            Role.GENERATOR: AgentSpec(agent_id="g"),
            Role.EVALUATOR: AgentSpec(agent_id="e"),
        },
        quality_threshold=0.8,
        max_iterations=2,
    )
    runner = TeamRunner(role_caller=caller)
    result = runner.run(topology, "task")
    assert result.iterations == 2
    assert result.final_output == "weak draft"
    assert result.quality_score == 0.1


def test_team_runner_emits_role_lifecycle_events() -> None:
    """Live observability: every role start / end must reach the
    emitter so the realtime gateway can show the swarm's progress
    instead of a 60-second blank stream. Regression guard for the
    "deep mode is opaque, ends with 本次回复已中断" report."""
    topology = TeamTopology(
        name="observable_chain",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.PLANNER: AgentSpec(agent_id="p"),
            Role.GENERATOR: AgentSpec(agent_id="g"),
        },
    )

    captured: list[dict[str, Any]] = []

    def _emitter(event: dict[str, Any]) -> None:
        captured.append(event)

    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "p": {"output": "outline", "success": True},
                "g": {"output": "answer", "success": True},
            }
        ),
        event_emitter=_emitter,
    )
    runner.run(topology, "x")

    starts = [e for e in captured if e["type"] == "team_role_start"]
    ends = [e for e in captured if e["type"] == "team_role_end"]
    assert len(starts) == 2, captured
    assert len(ends) == 2, captured
    # Roles must come through in the canonical sequential order.
    assert [e["role"] for e in starts] == ["planner", "generator"]
    assert [e["role"] for e in ends] == ["planner", "generator"]
    assert all(e["status"] == "success" for e in ends)
    # Output payload rides the end event so the gateway can render
    # the role's verdict without waiting on the final aggregated result.
    assert ends[0]["output"] == "outline"
    assert ends[1]["output"] == "answer"


def test_team_runner_emits_error_event_on_role_exception() -> None:
    """A role that raises must surface as ``team_role_end`` with
    status=error. Without this the gateway's stream would silently
    swallow the exception and the user would see a vanished stream."""
    topology = TeamTopology(
        name="fault",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={
            Role.PLANNER: AgentSpec(agent_id="p"),
        },
    )

    def boom(**_kwargs):
        raise RuntimeError("subagent crashed")

    captured: list[dict[str, Any]] = []
    runner = TeamRunner(
        role_caller=boom,
        event_emitter=captured.append,
    )
    runner.run(topology, "x")

    ends = [e for e in captured if e["type"] == "team_role_end"]
    assert len(ends) == 1
    assert ends[0]["status"] == "error"
    assert "subagent crashed" in ends[0]["error"]


def test_team_runner_event_emitter_failures_dont_break_run() -> None:
    """The emitter is best-effort: a buggy emitter must not abort a run."""
    topology = TeamTopology(
        name="resilient",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.PLANNER: AgentSpec(agent_id="p")},
    )

    def bad_emitter(_event: dict[str, Any]) -> None:
        raise RuntimeError("emitter went bang")

    runner = TeamRunner(
        role_caller=_stub_caller({"p": {"output": "ok", "success": True}}),
        event_emitter=bad_emitter,
    )
    result = runner.run(topology, "x")
    assert result.success is True
    assert result.final_output == "ok"


# ── performance_log ──────────────────────────────────────────


def test_performance_log_roundtrip(tmp_path: Path) -> None:
    topology = TeamTopology(
        name="log-test",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="g")},
    )
    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "g": {"output": "out", "success": True},
            }
        )
    )
    log_path = tmp_path / "perf.jsonl"
    result = runner.run(topology, "x")
    record_run(result, path=log_path)
    record_run(result, path=log_path)
    rows = read_runs(path=log_path)
    assert len(rows) == 2
    assert rows[0]["topology"] == "log-test"
    assert rows[0]["fingerprint"] == topology.fingerprint


# ── Evolver ──────────────────────────────────────────────────


def test_evolver_proposes_swap_when_other_agent_wins(tmp_path: Path) -> None:
    # Two topologies, same bucket, different generator agent.
    losing = TeamTopology(
        name="losing",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="alice")},
        task_bucket="bucket-A",
    )
    winning = TeamTopology(
        name="winning",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="bob")},
        task_bucket="bucket-A",
    )
    registry = {losing.fingerprint: losing, winning.fingerprint: winning}

    log_path = tmp_path / "perf.jsonl"
    # 6 losing runs (1 success), 6 winning runs (6 successes)
    for i in range(6):
        from runtime.safety.organization.team_runner import (
            RoleOutput,
            TeamRunResult,
        )

        rl = TeamRunResult(
            topology_name=losing.name,
            topology_fingerprint=losing.fingerprint,
            task_bucket="bucket-A",
            success=(i == 0),
            final_output="x" if i == 0 else "",
            role_outputs=[RoleOutput(role=Role.GENERATOR, agent_id="alice", output="x")],
        )
        record_run(rl, path=log_path)
        rw = TeamRunResult(
            topology_name=winning.name,
            topology_fingerprint=winning.fingerprint,
            task_bucket="bucket-A",
            success=True,
            final_output="y",
            role_outputs=[RoleOutput(role=Role.GENERATOR, agent_id="bob", output="y")],
        )
        record_run(rw, path=log_path)

    evolver = TopologyEvolver(
        log_path=log_path,
        proposals_path=tmp_path / "proposals.json",
        registry=registry,
    )
    report = evolver.analyse()
    swap_props = [p for p in report.proposals if p.kind == "swap_agent"]
    assert any(
        p.detail.get("old_agent") == "alice" and p.detail.get("new_agent") == "bob"
        for p in swap_props
    )


# ── Forge ────────────────────────────────────────────────────


def test_forge_swap_promotes_and_writes_registry(tmp_path: Path) -> None:
    base = TeamTopology(
        name="orig",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="alice")},
        task_bucket="b",
    )
    reg_path = tmp_path / "registry.json"
    save_registry({base.fingerprint: base}, path=reg_path)

    from runtime.safety.organization.evolver import Proposal

    forge = TopologyForge(registry_path=reg_path)
    result: PromoteResult = forge.promote(
        Proposal(
            kind="swap_agent",
            base_topology=base.fingerprint,
            bucket="b",
            detail={"role": "generator", "old_agent": "alice", "new_agent": "bob"},
            confidence=0.8,
            rationale="test",
        )
    )
    assert result.accepted is True
    assert result.new_topology is not None
    new_reg = load_registry(path=reg_path)
    # Original is still there + new one is added (not a replacement).
    assert base.fingerprint in new_reg
    assert result.new_topology.fingerprint in new_reg
    assert new_reg[result.new_topology.fingerprint].agents[Role.GENERATOR].agent_id == "bob"


def test_forge_rejects_unknown_proposal_kind(tmp_path: Path) -> None:
    base = TeamTopology(
        name="x",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="a")},
    )
    reg_path = tmp_path / "r.json"
    save_registry({base.fingerprint: base}, path=reg_path)

    from runtime.safety.organization.evolver import Proposal

    forge = TopologyForge(registry_path=reg_path)
    result = forge.promote(
        Proposal(
            kind="weird_unknown_kind",
            base_topology=base.fingerprint,
            bucket="b",
            detail={},
            confidence=0.5,
        )
    )
    assert result.accepted is False
    assert "unknown proposal kind" in result.reason


def test_forge_rejects_missing_base(tmp_path: Path) -> None:
    forge = TopologyForge(registry_path=tmp_path / "empty.json")
    from runtime.safety.organization.evolver import Proposal

    result = forge.promote(
        Proposal(
            kind="swap_agent",
            base_topology="does-not-exist",
            bucket="b",
            detail={"role": "generator", "new_agent": "x"},
            confidence=0.5,
        )
    )
    assert result.accepted is False
    assert "base topology not found" in result.reason


# ── gene_locks integration ───────────────────────────────────


def test_gene_locks_has_topology_mutation_kinds() -> None:
    from runtime.safety.gene_locks import MutationKind

    assert hasattr(MutationKind, "EVOLVE_TOPOLOGY")
    assert hasattr(MutationKind, "PROMOTE_TOPOLOGY")
    assert MutationKind.EVOLVE_TOPOLOGY == "evolve_topology"
    assert MutationKind.PROMOTE_TOPOLOGY == "promote_topology"


# ── End-to-end: realtime → topology route ─────────────────────


def test_turn_params_carries_topology_id() -> None:
    """``turn/start`` payload's ``topologyId`` must decode into TurnParams."""
    from runtime.protocol.items import TurnParams

    params = TurnParams.model_validate(
        {
            "threadId": "thr_1",
            "input": [],
            "topologyId": "team-A",
        }
    )
    assert params.topology_id == "team-A"


def test_topology_id_round_trips_through_alias() -> None:
    from runtime.protocol.items import TurnParams

    p = TurnParams.model_validate(
        {
            "threadId": "t",
            "input": [],
            "topologyId": "abc",
        }
    )
    dumped = p.model_dump(by_alias=True)
    assert dumped["topologyId"] == "abc"


def test_team_runner_records_run_to_perf_log(tmp_path: Path) -> None:
    """Smoke: TeamRunner + record_run end-to-end writes JSONL."""
    from runtime.safety.organization.performance_log import (
        read_runs,
        record_run,
    )

    topology = TeamTopology(
        name="e2e-team",
        protocol=CoordinationProtocol.SEQUENTIAL,
        agents={Role.GENERATOR: AgentSpec(agent_id="g")},
        task_bucket="e2e",
    )
    runner = TeamRunner(
        role_caller=_stub_caller(
            {
                "g": {"output": "answered", "success": True},
            }
        )
    )
    result = runner.run(topology, "do thing")
    assert result.success is True

    log_path = tmp_path / "perf.jsonl"
    record_run(result, path=log_path, extra={"smoke": True})
    rows = read_runs(path=log_path)
    assert len(rows) == 1
    assert rows[0]["topology"] == "e2e-team"
    assert rows[0]["task_bucket"] == "e2e"
    assert rows[0]["extra"]["smoke"] is True
