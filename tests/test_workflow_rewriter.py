"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import (
    RewriteProposal,
    RewriterConfig,
    WorkflowRewriter,
    format_proposals_for_review,
)


def _step(
    step_id: int,
    sucker: str,
    success: bool = True,
    args: dict | None = None,
) -> Step:
    call = ToolCall(caller="arms/x", sucker_id=sucker, args=args or {})
    return Step(
        step_id=step_id,
        node_id=f"n{step_id}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            error_type=None if success else "fake_error",
        ),
    )


def _traj(
    steps: list[Step],
    *,
    success: bool | None = None,
    strategy: str = "default",
) -> Trajectory:
    if success is None:
        success = all(s.success for s in steps)
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        strategy_id=strategy,
        steps=steps,
        outcome=TrajectoryOutcome(success=success),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestProposeNewRule:
    def test_frequent_sequence_proposed(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(5):
            j.write_trajectory(
                _traj([_step(0, "read_file"), _step(1, "count_words"), _step(2, "hash_text")])
            )
        report = WorkflowRewriter(j).analyze()
        new_rule_proposals = [p for p in report.proposals if p.kind == "propose_new_rule"]
        assert len(new_rule_proposals) >= 1
        p = new_rule_proposals[0]
        assert list(p.suggested_skill_sequence) == ["read_file", "count_words", "hash_text"]
        assert p.hit_count == 5

    def test_below_threshold_ignored(self):
        j = InMemoryJournal()
        # Implementation note.
        j.write_trajectory(_traj([_step(0, "a"), _step(1, "b")]))
        report = WorkflowRewriter(j).analyze()
        assert not [p for p in report.proposals if p.kind == "propose_new_rule"]

    def test_single_step_trajectories_ignored(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_traj([_step(0, "solo")]))
        report = WorkflowRewriter(j).analyze()
        # Implementation note.
        assert not [p for p in report.proposals if p.kind == "propose_new_rule"]

    def test_swarm_aggregate_deduplicates_same_success_task(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())
        steps = [_step(0, "read_file"), _step(1, "count_words")]
        for strategy in ("default", "swarm"):
            j.write_trajectory(
                Trajectory(
                    task_id=task_id,
                    arm_id=ArmId("swarm" if strategy == "swarm" else "code_arm"),
                    strategy_id=strategy,
                    steps=steps,
                    outcome=TrajectoryOutcome(success=True),
                )
            )

        report = WorkflowRewriter(j, config=RewriterConfig(new_sequence_min_hits=1)).analyze()
        [proposal] = [p for p in report.proposals if p.kind == "propose_new_rule"]
        assert proposal.hit_count == 1


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class _FakeRule:
    """Implementation note."""

    def __init__(self, name: str, skill_sequence: list[str]):
        self.name = name
        self.skill_sequence = skill_sequence


class TestDegradedStep:
    def test_step_mostly_fails_proposed_for_removal(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(6):
            j.write_trajectory(
                _traj(
                    [
                        _step(0, "a", success=True),
                        _step(1, "b", success=True),
                        _step(2, "c", success=False),
                    ],
                    strategy="x",
                    success=False,
                )
            )
        rule = _FakeRule("x", ["a", "b", "c"])
        report = WorkflowRewriter(j).analyze(rules=[rule])
        degraded = [p for p in report.proposals if p.kind == "remove_degraded_step"]
        assert len(degraded) >= 1
        dp = degraded[0]
        assert dp.target_rule_name == "x"
        assert dp.target_step_index == 2

    def test_healthy_step_not_proposed(self):
        j = InMemoryJournal()
        for _ in range(6):
            j.write_trajectory(
                _traj(
                    [_step(0, "a"), _step(1, "b"), _step(2, "c")],
                    strategy="x",
                )
            )
        rule = _FakeRule("x", ["a", "b", "c"])
        report = WorkflowRewriter(j).analyze(rules=[rule])
        assert not [p for p in report.proposals if p.kind == "remove_degraded_step"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLowerRulePriority:
    def test_low_success_rule_flagged(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(5):
            j.write_trajectory(
                _traj(
                    [_step(0, "x", success=False)],
                    strategy="bad",
                    success=False,
                )
            )
        rule = _FakeRule("bad", ["x"])
        report = WorkflowRewriter(j).analyze(rules=[rule])
        lowers = [p for p in report.proposals if p.kind == "lower_rule_priority"]
        assert len(lowers) >= 1
        assert lowers[0].severity == "high"

    def test_healthy_rule_not_flagged(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_traj([_step(0, "x")], strategy="good"))
        rule = _FakeRule("good", ["x"])
        report = WorkflowRewriter(j).analyze(rules=[rule])
        assert not [p for p in report.proposals if p.kind == "lower_rule_priority"]


# ═══════════════════════════════════════════════════════════
# 4 · merge_redundant_adjacent
# ═══════════════════════════════════════════════════════════


class TestRedundantAdjacent:
    def test_same_skill_same_args_twice_flagged(self):
        j = InMemoryJournal()
        for _ in range(4):
            j.write_trajectory(
                _traj(
                    [
                        _step(0, "list_cwd", args={"path": "."}),
                        _step(1, "list_cwd", args={"path": "."}),
                    ]
                )
            )
        report = WorkflowRewriter(j).analyze()
        merges = [p for p in report.proposals if p.kind == "merge_redundant_adjacent"]
        assert len(merges) >= 1

    def test_different_args_not_flagged(self):
        j = InMemoryJournal()
        for _ in range(4):
            j.write_trajectory(
                _traj(
                    [
                        _step(0, "list_cwd", args={"path": "/a"}),
                        _step(1, "list_cwd", args={"path": "/b"}),
                    ]
                )
            )
        report = WorkflowRewriter(j).analyze()
        assert not [p for p in report.proposals if p.kind == "merge_redundant_adjacent"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestOrdering:
    def test_high_severity_first(self):
        j = InMemoryJournal()
        # Implementation note.
        for _ in range(6):
            j.write_trajectory(
                _traj(
                    [_step(0, "x", success=False)],
                    strategy="dead_rule",
                    success=False,
                )
            )
        # Implementation note.
        for _ in range(4):
            j.write_trajectory(
                _traj(
                    [
                        _step(0, "y", args={"k": 1}),
                        _step(1, "y", args={"k": 1}),
                    ]
                )
            )
        rule = _FakeRule("dead_rule", ["x"])
        report = WorkflowRewriter(j).analyze(rules=[rule])
        assert report.proposals
        # Implementation note.
        assert report.proposals[0].severity == "high"


# ═══════════════════════════════════════════════════════════
# 6 · format_proposals_for_review
# ═══════════════════════════════════════════════════════════


class TestFormatProposals:
    def test_empty_returns_empty(self):
        assert format_proposals_for_review([]) == ""

    def test_contains_header_and_rationale(self):
        p = RewriteProposal(
            kind="propose_new_rule",
            suggested_skill_sequence=["a", "b"],  # type: ignore[arg-type]
            rationale="test rationale",
            confidence=0.8,
            severity="mid",
            hit_count=5,
        )
        text = format_proposals_for_review([p])
        assert "WORKFLOW REWRITE PROPOSALS" in text
        assert "test rationale" in text
        assert "5x" in text


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReport:
    def test_proposals_by_kind(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_traj([_step(0, "a"), _step(1, "b"), _step(2, "c")]))
        report = WorkflowRewriter(j).analyze()
        counts = report.proposals_by_kind
        assert counts.get("propose_new_rule", 0) >= 1

    def test_max_proposals_cap(self):
        j = InMemoryJournal()
        for i in range(20):
            for _ in range(3):
                j.write_trajectory(
                    _traj([_step(0, f"s_{i}_0"), _step(1, f"s_{i}_1"), _step(2, f"s_{i}_2")])
                )
        report = WorkflowRewriter(j, config=RewriterConfig(max_proposals=5)).analyze()
        assert len(report.proposals) == 5
