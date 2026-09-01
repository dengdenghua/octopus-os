"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

from runtime.core.cerebrum import StaticPlanner
from runtime.core.cerebrum.planner import Rule
from runtime.memory.journal import InMemoryJournal, JSONLJournal
from runtime.platform.models import (
    ArmId,
    BudgetSpec,
    ExecutionResult,
    SkillId,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import (
    RewriteProposal,
    apply_proposals_to_rules,
)

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _mk_rule(
    name: str,
    seq: list[str],
    *,
    priority: int = 10,
    keywords: list[str] | None = None,
) -> Rule:
    return Rule(
        name=name,
        intent_types=["task"],
        keywords=keywords or [],
        skill_sequence=[SkillId(s) for s in seq],
        priority=priority,
    )


def _mk_proposal(**kw) -> RewriteProposal:
    defaults = {
        "kind": "lower_rule_priority",
        "rationale": "test",
        "confidence": 0.9,
        "severity": "mid",
    }
    defaults.update(kw)
    return RewriteProposal(**defaults)


def _step(step_id: int, sucker: str, success: bool = True) -> Step:
    call = ToolCall(caller="arms/x", sucker_id=sucker, args={})
    return Step(
        step_id=step_id,
        node_id=f"n{step_id}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            error_type=None if success else "oops",
        ),
    )


def _traj(steps: list[Step], *, success: bool | None = None) -> Trajectory:
    if success is None:
        success = all(s.success for s in steps)
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        steps=steps,
        outcome=TrajectoryOutcome(success=success),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestThresholds:
    def test_low_confidence_skipped(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(target_rule_name="r1", confidence=0.3, kind="lower_rule_priority")
        result = apply_proposals_to_rules(rules, [p], min_confidence=0.7)
        assert result.applied_count == 0
        assert result.outcomes[0].action == "skipped_threshold"
        assert result.rules[0].priority == 10  # Implementation note.

    def test_low_severity_skipped(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(target_rule_name="r1", severity="low", kind="lower_rule_priority")
        result = apply_proposals_to_rules(rules, [p], min_severity="mid")
        assert result.applied_count == 0
        assert result.outcomes[0].action == "skipped_threshold"

    def test_high_severity_passes_even_at_mid_threshold(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(target_rule_name="r1", severity="high", kind="lower_rule_priority")
        result = apply_proposals_to_rules(rules, [p], min_severity="mid")
        assert result.applied_count == 1


# ═══════════════════════════════════════════════════════════
# remove_degraded_step
# ═══════════════════════════════════════════════════════════


class TestRemoveDegradedStep:
    def test_step_removed(self):
        rules = [_mk_rule("r1", ["a", "b", "c"])]
        p = _mk_proposal(
            kind="remove_degraded_step",
            target_rule_name="r1",
            target_step_index=1,
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.applied_count == 1
        assert list(result.rules[0].skill_sequence) == ["a", "c"]
        assert "removed step 1" in result.outcomes[0].detail

    def test_out_of_range_skipped(self):
        rules = [_mk_rule("r1", ["a", "b"])]
        p = _mk_proposal(
            kind="remove_degraded_step",
            target_rule_name="r1",
            target_step_index=99,
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_invalid"
        assert list(result.rules[0].skill_sequence) == ["a", "b"]

    def test_unknown_rule_skipped(self):
        rules = [_mk_rule("r1", ["a", "b"])]
        p = _mk_proposal(
            kind="remove_degraded_step",
            target_rule_name="nope",
            target_step_index=0,
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_unknown_target"

    def test_missing_index_skipped(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(
            kind="remove_degraded_step",
            target_rule_name="r1",
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_invalid"


# ═══════════════════════════════════════════════════════════
# lower_rule_priority
# ═══════════════════════════════════════════════════════════


class TestLowerRulePriority:
    def test_subtract_policy_default(self):
        rules = [_mk_rule("r1", ["a"], priority=10)]
        p = _mk_proposal(kind="lower_rule_priority", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p], priority_policy="subtract", priority_step=3)
        assert result.rules[0].priority == 7

    def test_halve_policy(self):
        rules = [_mk_rule("r1", ["a"], priority=10)]
        p = _mk_proposal(kind="lower_rule_priority", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p], priority_policy="halve")
        assert result.rules[0].priority == 5

    def test_never_below_zero(self):
        rules = [_mk_rule("r1", ["a"], priority=2)]
        p = _mk_proposal(kind="lower_rule_priority", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p], priority_policy="subtract", priority_step=10)
        assert result.rules[0].priority == 0

    def test_result_resorted_by_priority(self):
        """Implementation note."""
        rules = [
            _mk_rule("high", ["a"], priority=20),
            _mk_rule("low", ["b"], priority=5),
        ]
        p = _mk_proposal(kind="lower_rule_priority", target_rule_name="high")
        result = apply_proposals_to_rules(rules, [p], priority_policy="subtract", priority_step=18)
        # Implementation note.
        assert [r.name for r in result.rules] == ["low", "high"]


# ═══════════════════════════════════════════════════════════
# propose_new_rule
# ═══════════════════════════════════════════════════════════


class TestProposeNewRule:
    def test_added_to_rules(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(
            kind="propose_new_rule",
            target_rule_name="learned_seq",
            suggested_skill_sequence=[SkillId("x"), SkillId("y")],
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.applied_count == 1
        names = [r.name for r in result.rules]
        assert "learned_seq" in names
        new = next(r for r in result.rules if r.name == "learned_seq")
        assert list(new.skill_sequence) == ["x", "y"]

    def test_duplicate_name_skipped(self):
        rules = [_mk_rule("r1", ["a"])]
        p = _mk_proposal(
            kind="propose_new_rule",
            target_rule_name="r1",
            suggested_skill_sequence=[SkillId("x")],
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_duplicate"
        assert len(result.rules) == 1

    def test_empty_skill_sequence_skipped(self):
        rules: list = []
        p = _mk_proposal(kind="propose_new_rule", suggested_skill_sequence=[])
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_invalid"

    def test_auto_name_when_target_none(self):
        rules: list = []
        p = _mk_proposal(
            kind="propose_new_rule",
            suggested_skill_sequence=[SkillId("a")],
        )
        result = apply_proposals_to_rules(rules, [p])
        assert result.applied_count == 1
        assert result.rules[0].name.startswith("learned_")


# ═══════════════════════════════════════════════════════════
# merge_redundant_adjacent
# ═══════════════════════════════════════════════════════════


class TestMergeAdjacent:
    def test_dedup_adjacent_duplicates(self):
        rules = [_mk_rule("r1", ["a", "a", "b", "c", "c", "c"])]
        p = _mk_proposal(kind="merge_redundant_adjacent", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p])
        assert result.applied_count == 1
        assert list(result.rules[0].skill_sequence) == ["a", "b", "c"]

    def test_no_adjacent_dups_skipped(self):
        rules = [_mk_rule("r1", ["a", "b", "c"])]
        p = _mk_proposal(kind="merge_redundant_adjacent", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_invalid"

    def test_non_adjacent_dups_preserved(self):
        """Implementation note."""
        rules = [_mk_rule("r1", ["a", "b", "a"])]
        p = _mk_proposal(kind="merge_redundant_adjacent", target_rule_name="r1")
        result = apply_proposals_to_rules(rules, [p])
        assert result.outcomes[0].action == "skipped_invalid"
        assert list(result.rules[0].skill_sequence) == ["a", "b", "a"]


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMixedBatch:
    def test_applied_and_skipped_counts(self):
        rules = [
            _mk_rule("r1", ["a", "b"], priority=10),
            _mk_rule("r2", ["c", "c", "d"], priority=5),
        ]
        ps = [
            _mk_proposal(
                kind="lower_rule_priority",
                target_rule_name="r1",
                confidence=0.9,
            ),
            _mk_proposal(
                kind="merge_redundant_adjacent",
                target_rule_name="r2",
                confidence=0.9,
            ),
            _mk_proposal(
                kind="lower_rule_priority",
                target_rule_name="nonexistent",
                confidence=0.9,
            ),
            _mk_proposal(
                kind="lower_rule_priority",
                target_rule_name="r1",
                confidence=0.3,  # Implementation note.
            ),
        ]
        result = apply_proposals_to_rules(rules, ps)
        assert result.applied_count == 2
        assert result.skipped_count == 2
        assert sum(1 for o in result.outcomes if o.action == "applied") == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestStaticPlannerIntegration:
    def test_apply_rewrite_proposals_mutates_self(self):
        planner = StaticPlanner(
            rules=[_mk_rule("r1", ["a"], priority=10)],
            default_budget=BudgetSpec(tokens=1000, usd=0.01),
        )
        p = _mk_proposal(kind="lower_rule_priority", target_rule_name="r1")
        result = planner.apply_rewrite_proposals([p])
        assert result.applied_count == 1
        assert planner.rules[0].priority < 10

    def test_rewrite_from_journal_one_shot(self):
        """Implementation note."""
        journal = InMemoryJournal()
        for _ in range(5):  # Implementation note.
            journal.write_trajectory(_traj([_step(0, "read_file"), _step(1, "hash_text")]))

        planner = StaticPlanner(
            rules=[],
            default_budget=BudgetSpec(tokens=1000, usd=0.01),
        )
        result = planner.rewrite_from_journal(journal)
        applied_kinds = [o.kind for o in result.outcomes if o.action == "applied"]
        assert "propose_new_rule" in applied_kinds
        assert len(planner.rules) >= 1

    def test_rewrite_empty_journal_no_change(self):
        original = [_mk_rule("r1", ["a"], priority=10)]
        planner = StaticPlanner(rules=list(original))
        result = planner.rewrite_from_journal(InMemoryJournal())
        assert result.applied_count == 0
        # Implementation note.
        assert len(planner.rules) == 1
        assert planner.rules[0].name == "r1"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConfigDrivenRewrite:
    def test_build_from_config_applies_rewrites(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        # Implementation note.
        path = tmp_path / "rw.jsonl"
        fj = JSONLJournal(path)
        for _ in range(5):
            fj.write_trajectory(_traj([_step(0, "list_cwd"), _step(1, "count_words")]))

        cfg = AgentConfig(
            planner=PlannerConfig(type="static"),
            learn=LearnConfig(
                rewrite_from_journal=str(path),
                rewrite_min_confidence=0.4,  # Implementation note.
                rewrite_min_severity="low",
            ),
        )
        stack = build_from_config(cfg)
        # Implementation note.
        names = [r.name for r in stack.planner.rules]
        assert any(n.startswith("learned_") for n in names)

    def test_missing_rewrite_file_no_crash(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        cfg = AgentConfig(
            planner=PlannerConfig(type="static"),
            learn=LearnConfig(rewrite_from_journal=str(tmp_path / "nope.jsonl")),
        )
        stack = build_from_config(cfg)
        # Implementation note.
        assert stack.planner is not None
