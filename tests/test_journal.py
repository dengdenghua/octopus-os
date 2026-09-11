"""Implementation note."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.journal import (
    BudgetBreakerResetEvent,
    BudgetEvent,
    CurriculumGoalDecisionEvent,
    ImmuneEvent,
    InMemoryJournal,
    JSONLJournal,
    McpProposalDecisionEvent,
    ProtocolDriftDecisionEvent,
    SkillProposalDecisionEvent,
    TrajectoryEvent,
)
from runtime.platform.models import (
    AntigenSignature,
    CostEntry,
    ExecutionResult,
    Step,
    ToolCall,
)
from runtime.safety.invariants import InvariantViolation


@pytest.fixture
def sample_step_for_journal(sample_cost):
    call = ToolCall(caller="arm:code_arm", sucker_id="read_file", args={})
    r = ExecutionResult(call_id=call.call_id, status="success", cost=sample_cost)
    return Step(step_id=0, node_id="n0", action=call, result=r)


class TestInMemoryJournal:
    def test_write_and_read(self, sample_step_for_journal, sample_trajectory):
        j = InMemoryJournal()
        j.write_step(
            task_id=sample_trajectory.task_id,
            arm_id=sample_trajectory.arm_id,
            step=sample_step_for_journal,
        )
        j.write_trajectory(sample_trajectory)
        all_events = j.read_all()
        assert len(all_events) == 2

    def test_read_by_task(self, sample_trajectory, sample_step_for_journal):
        j = InMemoryJournal()
        j.write_step(sample_trajectory.task_id, "code_arm", sample_step_for_journal)
        j.write_trajectory(sample_trajectory)
        only = j.read_by_task(sample_trajectory.task_id)
        assert len(only) == 2

    def test_read_by_type(self, sample_trajectory, sample_step_for_journal):
        j = InMemoryJournal()
        j.write_step(sample_trajectory.task_id, "code_arm", sample_step_for_journal)
        j.write_trajectory(sample_trajectory)
        steps = j.read_by_type("step")
        trajs = j.read_by_type("trajectory")
        assert len(steps) == 1
        assert len(trajs) == 1

    def test_append_only_backbone(self):
        """Implementation note."""
        j = InMemoryJournal()
        # Implementation note.
        with pytest.raises(InvariantViolation):
            j._events.pop()


class TestJSONLJournal:
    def test_write_and_read_roundtrip(self, tmp_path: Path, sample_trajectory):
        j = JSONLJournal(tmp_path / "journal.jsonl")
        j.write_trajectory(sample_trajectory)

        reloaded = j.read_all()
        assert len(reloaded) == 1
        assert isinstance(reloaded[0], TrajectoryEvent)
        assert reloaded[0].trajectory.trajectory_id == sample_trajectory.trajectory_id

    def test_append_survives_process_boundary(self, tmp_path: Path, sample_trajectory):
        path = tmp_path / "journal.jsonl"
        # Implementation note.
        j1 = JSONLJournal(path)
        j1.write_trajectory(sample_trajectory)
        # Implementation note.
        j2 = JSONLJournal(path)
        j2.write_trajectory(sample_trajectory)
        # Implementation note.
        reader = JSONLJournal(path)
        assert len(reader.read_all()) == 2

    def test_immune_event_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        sig = AntigenSignature(
            entity_id="skill://public/x",
            entity_type="skill",
            content_hash="abc",
        )
        j.write_immune("allow", sig, reason="test")
        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], ImmuneEvent)
        assert events[0].verdict == "allow"

    def test_budget_event_serialization(self, tmp_path: Path, sample_trajectory):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_budget(
            "budget_commit",
            task_id=sample_trajectory.task_id,
            cost=CostEntry(tokens_in=100, tokens_out=50, usd=0.001),
        )
        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], BudgetEvent)
        assert events[0].cost.tokens == 150

    def test_budget_breaker_reset_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_budget_breaker_reset(
            component="runtime",
            reason="operator reset",
            actor="operator",
        )

        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], BudgetBreakerResetEvent)
        assert events[0].component == "runtime"
        assert events[0].reason == "operator reset"

    def test_skill_proposal_decision_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_skill_proposal_decision(
            proposal_name="forged_demo",
            candidate_id="abc12345",
            decision="rejected",
            reason="operator rejected",
            details={"source_sample_count": 3},
        )

        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], SkillProposalDecisionEvent)
        assert events[0].proposal_name == "forged_demo"
        assert events[0].decision == "rejected"
        assert events[0].details["source_sample_count"] == 3

    def test_curriculum_goal_decision_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_curriculum_goal_decision(
            goal_id=123,
            cluster_key="skill:read_file:failed:FileNotFoundError",
            status="in_progress",
            covered_by="forged_reader",
            details={"failure_count": 3},
        )

        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], CurriculumGoalDecisionEvent)
        assert events[0].goal_id == 123
        assert events[0].status == "in_progress"
        assert events[0].covered_by == "forged_reader"

    def test_mcp_proposal_decision_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_mcp_proposal_decision(
            server_name="github",
            status="vetted",
            reason="operator_vet",
            details={"risk_level": "high"},
        )

        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], McpProposalDecisionEvent)
        assert events[0].server_name == "github"
        assert events[0].status == "vetted"
        assert events[0].details["risk_level"] == "high"

    def test_protocol_drift_decision_serialization(self, tmp_path: Path):
        j = JSONLJournal(tmp_path / "j.jsonl")
        j.write_protocol_drift_decision(
            drift_id=42,
            protocol_id="http_api_contract",
            status="acknowledged",
            reason="operator_acknowledged",
            details={"summary": "404 on /api/example"},
        )

        events = j.read_all()
        assert len(events) == 1
        assert isinstance(events[0], ProtocolDriftDecisionEvent)
        assert events[0].drift_id == 42
        assert events[0].protocol_id == "http_api_contract"
        assert events[0].status == "acknowledged"
