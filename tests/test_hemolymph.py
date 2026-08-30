"""Implementation note."""

from __future__ import annotations

from uuid import uuid4

import pytest
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.execution.suckers.builtins import register_builtins
from runtime.memory.hemolymph import ContextComposer, estimate_tokens
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ParsedIntent,
    QuotaAllocation,
    TaskId,
    Trajectory,
    TrajectoryOutcome,
)

# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestEstimateTokens:
    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_short_text(self):
        assert estimate_tokens("hello") >= 1

    def test_monotonic(self):
        assert estimate_tokens("a") <= estimate_tokens("ab")
        assert estimate_tokens("ab") <= estimate_tokens("abc")


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture
def registry_with_builtins() -> SkillRegistry:
    r = SkillRegistry()
    register_builtins(r)
    return r


@pytest.fixture
def intent() -> ParsedIntent:
    return ParsedIntent(raw="x", intent_type="task", normalized_goal="count words")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicCompose:
    def test_packet_has_segments(self, registry_with_builtins, intent):
        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(
            task_info=intent,
            system_prompt="You are a helpful agent.",
            budget_tokens=10_000,
        )
        assert len(packet.segments) > 0
        assert packet.tokens_used > 0
        assert not packet.over_budget()

    def test_system_segment_contains_task_info(self, registry_with_builtins, intent):
        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(task_info=intent, budget_tokens=10_000)
        sys_segs = [s for s in packet.segments if s.bucket == "system"]
        blob = "\n".join(s.content for s in sys_segs)
        assert "task" in blob.lower()
        assert "count words" in blob

    def test_suckers_segment_lists_skills(self, registry_with_builtins, intent):
        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(task_info=intent, budget_tokens=10_000)
        sucker_segs = [s for s in packet.segments if s.bucket == "suckers"]
        assert len(sucker_segs) == 1
        content = sucker_segs[0].content
        # Implementation note.
        assert "list_cwd" in content or "read_file" in content

    def test_default_skill_catalog_hides_call_agent(self, intent):
        from runtime.execution.suckers.sub_agent import register_sub_agent_skill

        registry = SkillRegistry()
        register_builtins(registry)
        register_sub_agent_skill(registry)
        assert registry.has("call_agent")

        composer = ContextComposer(registry=registry, journal=None)
        packet = composer.compose(task_info=intent, budget_tokens=10_000)
        sucker_text = "\n".join(s.content for s in packet.segments if s.bucket == "suckers")
        assert "read_file" in sucker_text
        assert "call_agent" not in sucker_text


# ═══════════════════════════════════════════════════════════
# Progressive disclosure
# ═══════════════════════════════════════════════════════════


class TestProgressiveDisclosure:
    def test_relevant_skills_filter(self, registry_with_builtins, intent):
        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(
            task_info=intent,
            relevant_skills=["count_words"],
            budget_tokens=10_000,
        )
        sucker_segs = [s for s in packet.segments if s.bucket == "suckers"]
        content = sucker_segs[0].content
        assert "count_words" in content
        # Implementation note.
        assert "hash_text" not in content

    def test_suckers_truncated_when_over_budget(self, intent):
        # Implementation note.
        r = SkillRegistry()
        for i in range(20):
            r.register(
                Skill(
                    name=f"skill_{i}",
                    description="x" * 200,  # Implementation note.
                    trusted_source=f"skill://test/s{i}",
                    handler=lambda **kw: 0,
                )
            )
        composer = ContextComposer(registry=r, journal=None)
        packet = composer.compose(task_info=intent, budget_tokens=500)
        sucker_segs = [s for s in packet.segments if s.bucket == "suckers"]
        content = sucker_segs[0].content
        assert "truncated" in content


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBudgetAndCompression:
    def test_stays_under_budget(self, registry_with_builtins, intent):
        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(
            task_info=intent,
            system_prompt="You are a helpful agent. " * 100,  # Implementation note.
            budget_tokens=200,  # Implementation note.
        )
        # Implementation note.
        assert packet.tokens_used <= packet.total_budget_tokens

    def test_custom_quotas(self, registry_with_builtins, intent):
        custom = QuotaAllocation(system=0.50, suckers=0.30, memory=0.10, history=0.10)
        composer = ContextComposer(registry=registry_with_builtins, journal=None, quotas=custom)
        packet = composer.compose(task_info=intent, budget_tokens=10_000)
        assert packet.quotas == custom


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestMemoryRecall:
    def test_pulls_recent_trajectories(self, registry_with_builtins, intent):
        journal = InMemoryJournal()
        # Implementation note.
        for _ in range(2):
            traj = Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("code_arm"),
                steps=[],
                outcome=TrajectoryOutcome(success=True),
            )
            journal.write_trajectory(traj)

        composer = ContextComposer(registry=registry_with_builtins, journal=journal)
        packet = composer.compose(
            task_info=intent,
            arm_id=ArmId("code_arm"),
            history_cutoff_n=2,
            budget_tokens=10_000,
        )
        mem_segs = [s for s in packet.segments if s.bucket == "memory"]
        # Implementation note.
        assert len(mem_segs) >= 1

    def test_filters_by_arm(self, registry_with_builtins, intent):
        journal = InMemoryJournal()
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("arm_A"),
                outcome=TrajectoryOutcome(success=True),
            )
        )
        journal.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("arm_B"),
                outcome=TrajectoryOutcome(success=True),
            )
        )
        composer = ContextComposer(registry=registry_with_builtins, journal=journal)
        packet = composer.compose(
            task_info=intent,
            arm_id=ArmId("arm_A"),
            history_cutoff_n=5,
            budget_tokens=10_000,
        )
        blob = "\n".join(s.content for s in packet.segments if s.bucket == "memory")
        assert "arm_A" in blob
        assert "arm_B" not in blob

    def test_swarm_aggregate_deduplicates_recent_history(self, registry_with_builtins, intent):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("arm_A"),
                strategy_id="default",
                outcome=TrajectoryOutcome(success=True),
            )
        )
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                strategy_id="swarm",
                outcome=TrajectoryOutcome(success=True),
            )
        )

        composer = ContextComposer(registry=registry_with_builtins, journal=journal)
        packet = composer.compose(
            task_info=intent,
            history_cutoff_n=5,
            budget_tokens=10_000,
        )
        blob = "\n".join(s.content for s in packet.segments if s.bucket == "memory")
        assert blob.count("past trajectory:") == 1
        assert "arm=swarm" in blob

    def test_swarm_aggregate_recent_history_prefers_latest(self, registry_with_builtins, intent):
        journal = InMemoryJournal()
        task_id = TaskId(uuid4())
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                strategy_id="swarm",
                outcome=TrajectoryOutcome(success=False),
            )
        )
        journal.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                strategy_id="swarm",
                outcome=TrajectoryOutcome(success=True),
            )
        )

        composer = ContextComposer(registry=registry_with_builtins, journal=journal)
        packet = composer.compose(
            task_info=intent,
            history_cutoff_n=5,
            budget_tokens=10_000,
        )
        blob = "\n".join(s.content for s in packet.segments if s.bucket == "memory")
        assert blob.count("past trajectory:") == 1
        assert "ok=yes" in blob
        assert "ok=no" not in blob


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPacketSerialization:
    def test_round_trip(self, registry_with_builtins, intent):
        from runtime.platform.models import ContextPacket

        composer = ContextComposer(registry=registry_with_builtins, journal=None)
        packet = composer.compose(
            task_info=intent,
            system_prompt="sys",
            budget_tokens=5_000,
        )
        j = packet.model_dump_json()
        reload = ContextPacket.model_validate_json(j)
        assert reload.tokens_used == packet.tokens_used
        assert reload.packet_id == packet.packet_id
