"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest

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
    ExtractorConfig,
    LearnedRule,
    RuleExtractor,
    format_rules_for_prompt,
)


def _make_step(
    step_id: int,
    sucker: str,
    *,
    status: str = "success",
    error_type: str | None = None,
    args: dict | None = None,
) -> Step:
    call = ToolCall(
        caller="arms/code_arm",
        sucker_id=sucker,
        args=args or {},
    )
    return Step(
        step_id=step_id,
        node_id=f"n{step_id}",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status=status,  # type: ignore[arg-type]
            error_type=error_type,
        ),
    )


def _make_traj(
    steps: list[Step],
    *,
    overall_success: bool | None = None,
) -> Trajectory:
    if overall_success is None:
        overall_success = all(s.success for s in steps)
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        steps=steps,
        outcome=TrajectoryOutcome(success=overall_success),
    )


@pytest.fixture
def journal_with_failures():
    j = InMemoryJournal()
    # Implementation note.
    for _ in range(5):
        j.write_trajectory(
            _make_traj(
                [_make_step(0, "read_file", status="timeout", error_type="timeout")],
                overall_success=False,
            )
        )
    # Implementation note.
    for _ in range(3):
        j.write_trajectory(
            _make_traj(
                [
                    _make_step(
                        0,
                        "hash_text",
                        status="sandbox_violation",
                        error_type="PermissionError",
                        args={"path": "/etc/secret"},
                    )
                ],
                overall_success=False,
            )
        )
    # Implementation note.
    j.write_trajectory(
        _make_traj(
            [_make_step(0, "count_words", status="failed", error_type="ValueError")],
            overall_success=False,
        )
    )
    # Implementation note.
    for _ in range(2):
        j.write_trajectory(_make_traj([_make_step(0, "read_file")]))
    return j


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestExtract:
    def test_scans_all_trajectories(self, journal_with_failures):
        extractor = RuleExtractor(journal=journal_with_failures)
        report = extractor.extract()
        assert report.trajectories_scanned == 5 + 3 + 1 + 2
        assert report.failure_count == 5 + 3 + 1

    def test_success_trajectories_ignored(self, journal_with_failures):
        extractor = RuleExtractor(journal=journal_with_failures)
        report = extractor.extract()
        # Implementation note.
        for r in report.rules_produced:
            assert r.sucker_id in ("read_file", "hash_text")

    def test_below_min_hits_excluded(self, journal_with_failures):
        """Implementation note."""
        extractor = RuleExtractor(journal=journal_with_failures)
        report = extractor.extract()
        names = {r.sucker_id for r in report.rules_produced}
        assert "count_words" not in names

    def test_meets_min_hits_produces_rule(self, journal_with_failures):
        extractor = RuleExtractor(journal=journal_with_failures)
        report = extractor.extract()
        # Implementation note.
        sucker_ids = {r.sucker_id for r in report.rules_produced}
        assert "read_file" in sucker_ids
        assert "hash_text" in sucker_ids

    def test_hit_count_tracked(self, journal_with_failures):
        extractor = RuleExtractor(journal=journal_with_failures)
        report = extractor.extract()
        for r in report.rules_produced:
            if r.sucker_id == "read_file":
                assert r.hit_count == 5
            if r.sucker_id == "hash_text":
                assert r.hit_count == 3

    def test_swarm_aggregate_deduplicates_same_failed_task(self):
        j = InMemoryJournal()
        task_id = TaskId(uuid4())
        failed_steps = [_make_step(0, "read_file", status="timeout", error_type="timeout")]
        for strategy in ("default", "swarm"):
            j.write_trajectory(
                Trajectory(
                    task_id=task_id,
                    arm_id=ArmId("swarm" if strategy == "swarm" else "code_arm"),
                    strategy_id=strategy,
                    steps=failed_steps,
                    outcome=TrajectoryOutcome(success=False),
                )
            )

        report = RuleExtractor(j, config=ExtractorConfig(min_hits=1)).extract()

        [rule] = [r for r in report.rules_produced if r.sucker_id == "read_file"]
        assert rule.hit_count == 1


# ═══════════════════════════════════════════════════════════
# Severity
# ═══════════════════════════════════════════════════════════


class TestSeverity:
    def test_low_for_just_above_min(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(
                _make_traj(
                    [_make_step(0, "x", status="failed", error_type="E")],
                    overall_success=False,
                )
            )
        r = RuleExtractor(j).extract()
        assert r.rules_produced[0].severity == "low"

    def test_mid_when_cluster_grows(self):
        j = InMemoryJournal()
        for _ in range(7):
            j.write_trajectory(
                _make_traj(
                    [_make_step(0, "x", status="failed", error_type="E")],
                    overall_success=False,
                )
            )
        r = RuleExtractor(j).extract()
        assert r.rules_produced[0].severity == "mid"

    def test_high_on_very_frequent(self):
        j = InMemoryJournal()
        for _ in range(15):
            j.write_trajectory(
                _make_traj(
                    [_make_step(0, "x", status="failed", error_type="E")],
                    overall_success=False,
                )
            )
        r = RuleExtractor(j).extract()
        assert r.rules_produced[0].severity == "high"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMitigationFormation:
    def test_timeout_mentions_deadline(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(
                _make_traj(
                    [_make_step(0, "slow_skill", status="timeout", error_type="timeout")],
                    overall_success=False,
                )
            )
        report = RuleExtractor(j).extract()
        rule = report.rules_produced[0]
        assert "deadline" in rule.mitigation.lower()

    def test_sandbox_mentions_mantle(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(
                _make_traj(
                    [
                        _make_step(
                            0,
                            "dangerous",
                            status="sandbox_violation",
                            error_type="PermissionError",
                        )
                    ],
                    overall_success=False,
                )
            )
        report = RuleExtractor(j).extract()
        rule = report.rules_produced[0]
        assert "mantle" in rule.mitigation.lower() or "sandbox" in rule.mitigation.lower()

    def test_immune_reject_mentions_trust(self):
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(
                _make_traj(
                    [
                        _make_step(
                            0,
                            "shady",
                            status="immune_reject",
                            error_type="immune_reject",
                        )
                    ],
                    overall_success=False,
                )
            )
        rule = RuleExtractor(j).extract().rules_produced[0]
        assert "trust" in rule.mitigation.lower() or "immunity" in rule.mitigation.lower()


# ═══════════════════════════════════════════════════════════
# Config
# ═══════════════════════════════════════════════════════════


class TestConfig:
    def test_min_hits_customizable(self):
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(
                _make_traj(
                    [_make_step(0, "x", status="failed", error_type="E")],
                    overall_success=False,
                )
            )
        # Implementation note.
        assert RuleExtractor(j, config=ExtractorConfig(min_hits=10)).extract().rules_produced == []

    def test_max_rules_cap(self):
        j = InMemoryJournal()
        # Implementation note.
        for i in range(20):
            for _ in range(3):
                j.write_trajectory(
                    _make_traj(
                        [
                            _make_step(
                                0,
                                f"sucker_{i}",
                                status="failed",
                                error_type="E",
                            )
                        ],
                        overall_success=False,
                    )
                )
        report = RuleExtractor(j, config=ExtractorConfig(max_rules_per_run=5)).extract()
        assert len(report.rules_produced) == 5


# ═══════════════════════════════════════════════════════════
# format_rules_for_prompt
# ═══════════════════════════════════════════════════════════


class TestFormatForPrompt:
    def test_empty_returns_empty(self):
        assert format_rules_for_prompt([]) == ""

    def test_contains_header(self, journal_with_failures):
        rules = RuleExtractor(journal_with_failures).extract().rules_produced
        text = format_rules_for_prompt(rules)
        assert "LEARNED MITIGATIONS" in text

    def test_contains_severity_and_hit_count(self, journal_with_failures):
        rules = RuleExtractor(journal_with_failures).extract().rules_produced
        text = format_rules_for_prompt(rules)
        # Implementation note.
        assert "MID" in text.upper() or "LOW" in text.upper()
        assert "5x" in text or "3x" in text

    def test_truncates_when_over_budget(self):
        many = [
            LearnedRule(
                rule_id=f"r_{i}",
                sucker_id=f"sucker_{i}",  # type: ignore[arg-type]
                error_signature="e",
                pattern="x" * 200,
                mitigation="y" * 200,
                hit_count=5,
                severity="mid",
            )
            for i in range(20)
        ]
        text = format_rules_for_prompt(many, max_total_chars=500)
        assert "truncated" in text.lower()
        assert len(text) <= 800  # Implementation note.

    def test_severity_ordering(self):
        rules = [
            LearnedRule(
                rule_id="low_r",
                sucker_id="a",  # type: ignore[arg-type]
                error_signature="e",
                pattern="low pattern",
                mitigation="low mit",
                hit_count=3,
                severity="low",
            ),
            LearnedRule(
                rule_id="high_r",
                sucker_id="b",  # type: ignore[arg-type]
                error_signature="e",
                pattern="high pattern",
                mitigation="high mit",
                hit_count=20,
                severity="high",
            ),
        ]
        text = format_rules_for_prompt(rules)
        # Implementation note.
        assert text.find("high pattern") < text.find("low pattern")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEndToEndInjection:
    def test_full_cycle(self, journal_with_failures):
        extractor = RuleExtractor(journal_with_failures)
        report = extractor.extract()
        prompt_section = format_rules_for_prompt(report.rules_produced)

        # Implementation note.
        assert prompt_section.startswith("LEARNED MITIGATIONS")
        assert len(report.rules_produced) >= 2
        # Implementation note.
        for rule in report.rules_produced:
            assert rule.source_trajectory_ids
            assert rule.hit_count == len(rule.source_trajectory_ids)
