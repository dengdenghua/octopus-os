"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.journal import InMemoryJournal
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
from runtime.safety.recovery import (
    ForgeConfig,
    SkillForge,
    UnsafeSkillPromotionError,
    pattern_signature,
)
from runtime.safety.recovery.skill_forge import _default_name_for

# ═══════════════════════════════════════════════════════════
# fixtures
# ═══════════════════════════════════════════════════════════


def _make_step(idx: int, sucker: str, args: dict | None = None) -> Step:
    call = ToolCall(caller="arms/code_arm", sucker_id=sucker, args=args or {})
    return Step(
        step_id=idx,
        node_id=f"n{idx}",
        action=call,
        result=ExecutionResult(call_id=call.call_id, status="success", output={"ok": True}),
    )


def _make_traj(
    suckers: list[str],
    *,
    success: bool = True,
    args: dict | None = None,
) -> Trajectory:
    steps = [_make_step(i, s, args) for i, s in enumerate(suckers)]
    return Trajectory(
        task_id=TaskId(uuid4()),
        arm_id=ArmId("code_arm"),
        steps=steps,
        outcome=TrajectoryOutcome(success=success),
    )


@pytest.fixture
def journal_with_samples():
    j = InMemoryJournal()
    # Implementation note.
    for _ in range(5):
        j.write_trajectory(_make_traj(["list_cwd", "count_words"]))
    # Implementation note.
    for _ in range(2):
        j.write_trajectory(_make_traj(["hash_text"]))
    # Implementation note.
    for _ in range(2):
        j.write_trajectory(_make_traj(["list_cwd", "count_words"], success=False))
    # Implementation note.
    j.write_trajectory(_make_traj(["list_cwd", "hash_text"]))
    return j


@pytest.fixture
def base_registry() -> SkillRegistry:
    r = SkillRegistry()
    r.register(
        Skill(
            name="list_cwd",
            trusted_source="skill://public/list_cwd",
            handler=lambda **kw: {"files": ["a", "b"]},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="count_words",
            trusted_source="skill://public/count_words",
            handler=lambda **kw: {"words": 5},
        ),
        verify_tests=False,
    )
    r.register(
        Skill(
            name="hash_text",
            trusted_source="skill://public/hash_text",
            handler=lambda **kw: {"hash": "abc123"},
        ),
        verify_tests=False,
    )
    return r


# ═══════════════════════════════════════════════════════════
# pattern_signature
# ═══════════════════════════════════════════════════════════


class TestPatternSignature:
    def test_same_sequence_same_sig(self):
        t1 = _make_traj(["a", "b", "c"])
        t2 = _make_traj(["a", "b", "c"], args={"different": 1})
        assert pattern_signature(t1) == pattern_signature(t2)

    def test_different_sequence_different_sig(self):
        t1 = _make_traj(["a", "b"])
        t2 = _make_traj(["b", "a"])
        assert pattern_signature(t1) != pattern_signature(t2)


# ═══════════════════════════════════════════════════════════
# propose
# ═══════════════════════════════════════════════════════════


class TestPropose:
    def test_single_step_trajectories_excluded(self, journal_with_samples, base_registry):
        forge = SkillForge(journal=journal_with_samples, registry=base_registry)
        candidates = forge.propose()
        # Implementation note.
        for c in candidates:
            assert len(c.underlying_sequence) >= 2

    def test_meets_min_hits_and_success_rate(self, journal_with_samples, base_registry):
        """Implementation note."""
        forge = SkillForge(
            journal=journal_with_samples,
            registry=base_registry,
            config=ForgeConfig(min_hits=3, min_success_rate=0.70),
        )
        candidates = forge.propose()
        assert len(candidates) >= 1
        names = [c.underlying_sequence for c in candidates]
        assert ["list_cwd", "count_words"] in names

    def test_insufficient_hits_excluded(self, journal_with_samples, base_registry):
        forge = SkillForge(
            journal=journal_with_samples,
            registry=base_registry,
            config=ForgeConfig(min_hits=10, min_success_rate=0.50),
        )
        candidates = forge.propose()
        # Implementation note.
        assert candidates == []

    def test_success_rate_threshold(self, journal_with_samples, base_registry):
        """Implementation note."""
        forge = SkillForge(
            journal=journal_with_samples,
            registry=base_registry,
            config=ForgeConfig(min_hits=3, min_success_rate=0.90),
        )
        assert forge.propose() == []

    def test_candidate_has_golden_tests(self, journal_with_samples, base_registry):
        forge = SkillForge(journal=journal_with_samples, registry=base_registry)
        candidates = forge.propose()
        assert candidates
        cand = candidates[0]
        assert len(cand.generated_tests) >= 1
        assert cand.generated_tests[0].tier == "golden"


# ═══════════════════════════════════════════════════════════
# shadow_validate
# ═══════════════════════════════════════════════════════════


class TestShadowValidate:
    def test_shadow_passes_for_sane_candidate(self, journal_with_samples, base_registry):
        forge = SkillForge(journal=journal_with_samples, registry=base_registry)
        [cand] = [
            c for c in forge.propose() if c.underlying_sequence == ["list_cwd", "count_words"]
        ]
        passed, report = forge.shadow_validate(cand)
        assert passed
        assert report.overall_passed

    def test_shadow_fails_when_underlying_skill_raises(self, base_registry):
        """Implementation note."""
        # Implementation note.
        bad_reg = SkillRegistry()
        bad_reg.register(
            Skill(
                name="list_cwd",
                trusted_source="skill://public/list_cwd",
                handler=lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")),
            ),
            verify_tests=False,
        )
        bad_reg.register(
            Skill(
                name="count_words",
                trusted_source="skill://public/count_words",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
        j = InMemoryJournal()
        for _ in range(5):
            j.write_trajectory(_make_traj(["list_cwd", "count_words"]))

        forge = SkillForge(journal=j, registry=bad_reg)
        [cand] = forge.propose()
        passed, _ = forge.shadow_validate(cand)
        assert not passed

    def test_shadow_runs_and_threshold_control_promotion_gate(self, base_registry):
        outcomes = iter([True, False, True])

        def flaky_count_words(**kw):
            if next(outcomes):
                return {"words": 5}
            raise RuntimeError("flaky")

        base_registry.register(
            Skill(
                name="count_words",
                trusted_source="skill://public/count_words",
                handler=flaky_count_words,
            ),
            verify_tests=False,
            replace=True,
        )
        j = InMemoryJournal()
        for _ in range(3):
            j.write_trajectory(_make_traj(["list_cwd", "count_words"]))

        forge = SkillForge(
            journal=j,
            registry=base_registry,
            config=ForgeConfig(
                min_hits=1,
                min_success_rate=0.0,
                shadow_runs=3,
                shadow_success_threshold=0.60,
            ),
        )
        [cand] = forge.propose()
        passed, report = forge.shadow_validate(cand)

        assert passed is True
        assert report.overall_passed is True
        assert report.total == 3
        assert report.passed_count == 2


class TestTrajectoryCollection:
    def test_swarm_aggregate_deduplicates_same_task_samples(self, base_registry):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        arm_steps = [_make_step(0, "list_cwd"), _make_step(1, "count_words")]

        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("arm_a"),
                strategy_id="default",
                steps=arm_steps,
                outcome=TrajectoryOutcome(success=True),
            )
        )
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                strategy_id="swarm",
                steps=arm_steps,
                outcome=TrajectoryOutcome(
                    success=True,
                    cost=CostEntry(tokens_in=10, tokens_out=5, usd=0.001),
                ),
            )
        )

        forge = SkillForge(journal=journal, registry=base_registry)
        trajs = forge._collect_successful_trajectories()

        assert len(trajs) == 1
        assert trajs[0].strategy_id == "swarm"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRunPipeline:
    def test_successful_run_promotes_candidate(self, journal_with_samples, base_registry):
        forge = SkillForge(journal=journal_with_samples, registry=base_registry)
        result = forge.run()
        assert len(result.promoted) >= 1
        # Implementation note.
        forged_names = [n for n in base_registry.all_names() if n.startswith("forged_")]
        assert forged_names

    def test_no_candidates_no_promotion(self, base_registry):
        """Implementation note."""
        empty = InMemoryJournal()
        result = SkillForge(journal=empty, registry=base_registry).run()
        assert result.candidates_total == 0
        assert result.promoted == []

    def test_forged_skill_callable_after_promotion(self, journal_with_samples, base_registry):
        forge = SkillForge(journal=journal_with_samples, registry=base_registry)
        result = forge.run()
        assert result.promoted

        forged_name = result.promoted[0]
        skill = base_registry.get(forged_name)
        # Implementation note.
        out = skill.handler()
        assert "composite_output" in out
        assert out["steps"] == 2

    def test_promotion_rejects_composite_with_dangerous_underlying_skill(self, base_registry):
        base_registry.register(
            Skill(
                name="exec_shell",
                trusted_source="skill://public/exec_shell",
                handler=lambda **kw: {"ok": True},
                affinity=["shell", "exec", "dangerous"],
            ),
            verify_tests=False,
        )
        journal = InMemoryJournal()
        for _ in range(3):
            journal.write_trajectory(_make_traj(["list_cwd", "exec_shell"]))

        forge = SkillForge(
            journal=journal,
            registry=base_registry,
            config=ForgeConfig(min_hits=1, min_success_rate=0.0),
        )
        [candidate] = forge.propose()

        with pytest.raises(UnsafeSkillPromotionError):
            forge.promote_to_public(candidate)
        assert not base_registry.has(candidate.name)

    def test_run_retires_dangerous_composite_without_registering(self, base_registry):
        base_registry.register(
            Skill(
                name="exec_shell",
                trusted_source="skill://public/exec_shell",
                handler=lambda **kw: {"ok": True},
                affinity=["shell", "exec", "dangerous"],
            ),
            verify_tests=False,
        )
        journal = InMemoryJournal()
        for _ in range(3):
            journal.write_trajectory(_make_traj(["list_cwd", "exec_shell"]))

        result = SkillForge(
            journal=journal,
            registry=base_registry,
            config=ForgeConfig(min_hits=1, min_success_rate=0.0),
        ).run()

        assert result.promoted == []
        assert result.retired
        assert not any(name.startswith("forged_") for name in base_registry.all_names())


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestNameGeneration:
    def test_uses_skill_sequence(self):
        name = _default_name_for(["list_cwd", "count_words"])
        assert name.startswith("forged_list_cwd_then_count_words_")

    def test_different_sequence_different_name(self):
        a = _default_name_for(["x", "y"])
        b = _default_name_for(["y", "x"])
        assert a != b
