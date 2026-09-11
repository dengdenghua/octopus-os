"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    ParsedIntent,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
)
from runtime.safety.recovery import LearnedRule
from runtime.sensing.model_router import MockModelRouter


def _populate_failed_journal() -> InMemoryJournal:
    """Implementation note."""
    j = InMemoryJournal()

    def failed_step(sucker, err):
        call = ToolCall(caller="arms/code_arm", sucker_id=sucker, args={})
        return Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status=err,  # type: ignore[arg-type]
                error_type=err,
            ),
        )

    for _ in range(5):
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("code_arm"),
                steps=[failed_step("read_file", "timeout")],
                outcome=TrajectoryOutcome(success=False),
            )
        )
    for _ in range(3):
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("code_arm"),
                steps=[failed_step("hash_text", "sandbox_violation")],
                outcome=TrajectoryOutcome(success=False),
            )
        )
    return j


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    for name in ["read_file", "hash_text", "count_words"]:
        r.register(
            Skill(
                name=name,
                trusted_source=f"skill://public/{name}",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
    return r


@pytest.fixture
def composer(registry) -> ContextComposer:
    return ContextComposer(registry=registry, journal=InMemoryJournal())


# ═══════════════════════════════════════════════════════════
# update_learned_rules
# ═══════════════════════════════════════════════════════════


class TestUpdateLearnedRules:
    def test_default_no_rules_section(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        assert planner.learned_rules_section == ""

    def test_update_sets_section(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        rule = LearnedRule(
            rule_id="r1",
            sucker_id="read_file",  # type: ignore[arg-type]
            error_signature="timeout",
            pattern="timed out too often",
            mitigation="use streaming read",
            hit_count=6,
            severity="mid",
        )
        planner.update_learned_rules([rule])

        assert "LEARNED MITIGATIONS" in planner.learned_rules_section
        assert "timed out too often" in planner.learned_rules_section
        assert planner._rules_updated_count == 1

    def test_re_update_replaces(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        r1 = LearnedRule(
            rule_id="r1",
            sucker_id="a",  # type: ignore[arg-type]
            error_signature="e",
            pattern="first rule",
            mitigation="first fix",
            hit_count=3,
            severity="low",
        )
        r2 = LearnedRule(
            rule_id="r2",
            sucker_id="b",  # type: ignore[arg-type]
            error_signature="e",
            pattern="second rule",
            mitigation="second fix",
            hit_count=10,
            severity="high",
        )
        planner.update_learned_rules([r1])
        assert "first rule" in planner.learned_rules_section

        planner.update_learned_rules([r2])
        # Implementation note.
        assert "first rule" not in planner.learned_rules_section
        assert "second rule" in planner.learned_rules_section
        assert planner._rules_updated_count == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPromptInjection:
    def test_system_prompt_contains_mitigations_after_update(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        rule = LearnedRule(
            rule_id="r1",
            sucker_id="read_file",  # type: ignore[arg-type]
            error_signature="timeout",
            pattern="read_file times out with large files",
            mitigation="use streaming read with chunk size 4096",
            hit_count=8,
            severity="mid",
        )
        planner.update_learned_rules([rule])

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="read a file")
        planner.plan(intent)

        assert len(router.call_log) == 1
        sys_msg = next(m for m in router.call_log[0].messages if m.role == "system")
        assert "LEARNED MITIGATIONS" in sys_msg.content
        assert "streaming read with chunk size 4096" in sys_msg.content

    def test_no_mitigations_when_empty_rules(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        planner.update_learned_rules([])  # Implementation note.

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="do thing")
        planner.plan(intent)
        sys_msg = next(m for m in router.call_log[0].messages if m.role == "system")
        assert "LEARNED MITIGATIONS" not in sys_msg.content


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLearnFromJournal:
    def test_one_call_full_loop(self, registry, composer):
        """Implementation note."""
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        journal = _populate_failed_journal()

        n = planner.learn_from_journal(journal)
        assert n == 2  # read_file:timeout + hash_text:sandbox_violation

        # Implementation note.
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="read file")
        planner.plan(intent)
        sys_msg = next(m for m in router.call_log[0].messages if m.role == "system")
        assert "read_file" in sys_msg.content
        assert "hash_text" in sys_msg.content

    def test_empty_journal_produces_zero_rules(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        n = planner.learn_from_journal(InMemoryJournal())
        assert n == 0
        assert planner.learned_rules_section == ""

    def test_min_hits_respected(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        journal = _populate_failed_journal()
        # Implementation note.
        n = planner.learn_from_journal(journal, min_hits=10)
        assert n == 0


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestReflectionLoopEffect:
    def test_two_plans_see_different_prompts(self, registry, composer):
        """Implementation note."""
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        first_prompt = next(m.content for m in router.call_log[0].messages if m.role == "system")

        journal = _populate_failed_journal()
        planner.learn_from_journal(journal)

        planner.plan(intent)
        second_prompt = next(m.content for m in router.call_log[1].messages if m.role == "system")

        # Implementation note.
        assert "LEARNED MITIGATIONS" not in first_prompt
        assert "LEARNED MITIGATIONS" in second_prompt
        assert len(second_prompt) > len(first_prompt)


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _populate_successful_journal() -> InMemoryJournal:
    """Implementation note."""
    from datetime import timedelta

    from runtime.platform.models import CostEntry, now_utc

    j = InMemoryJournal()
    for _ in range(5):
        started = now_utc() - timedelta(hours=0.2)
        completed = now_utc() - timedelta(hours=0.1)
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("code_arm"),
                strategy_id="default",
                steps=[],
                outcome=TrajectoryOutcome(
                    success=True,
                    cost=CostEntry(tokens_in=150, tokens_out=150, usd=0.01),
                ),
                started_at=started,
                completed_at=completed,
            )
        )
    return j


class TestMemoryInjection:
    def test_default_no_memories_section(self, registry, composer):
        router = MockModelRouter(response=json.dumps({"reasoning": "r", "nodes": []}))
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        assert planner.learned_memories_section == ""

    def test_update_learned_memories_sets_section(self, registry, composer):
        router = MockModelRouter(response=json.dumps({"reasoning": "r", "nodes": []}))
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        from runtime.safety.recovery import MemoryConsolidator

        report = MemoryConsolidator(_populate_successful_journal()).consolidate()
        assert report.memories_produced  # sanity

        planner.update_learned_memories(report.memories_produced)
        assert "CONSOLIDATED MEMORIES" in planner.learned_memories_section
        assert planner._memories_updated_count == 1

    def test_learn_memories_from_journal_one_shot(self, registry, composer):
        router = MockModelRouter(response=json.dumps({"reasoning": "r", "nodes": []}))
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        n = planner.learn_memories_from_journal(_populate_successful_journal())
        assert n >= 1
        assert "CONSOLIDATED MEMORIES" in planner.learned_memories_section

    def test_memories_appear_in_system_prompt(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        planner.learn_memories_from_journal(_populate_successful_journal())

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        sys_msg = next(m.content for m in router.call_log[0].messages if m.role == "system")
        assert "CONSOLIDATED MEMORIES" in sys_msg

    def test_recipe_hash_changes_with_memories(self, registry, composer):
        router = MockModelRouter(response=json.dumps({"reasoning": "r", "nodes": []}))
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        h_before = planner.recipe_hash()
        planner.learn_memories_from_journal(_populate_successful_journal())
        h_after = planner.recipe_hash()
        assert h_before != h_after, "recipe_hash should reflect memory section"

    def test_rules_and_memories_coexist(self, registry, composer):
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        planner = LLMPlanner(router=router, registry=registry, composer=composer)
        planner.learn_from_journal(_populate_failed_journal())
        planner.learn_memories_from_journal(_populate_successful_journal())

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        sys_msg = next(m.content for m in router.call_log[0].messages if m.role == "system")
        assert "LEARNED MITIGATIONS" in sys_msg
        assert "CONSOLIDATED MEMORIES" in sys_msg


class TestConfigDrivenMemoryLoad:
    def test_build_from_config_loads_memories(self, tmp_path):
        """Implementation note."""
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        # Implementation note.
        mem_path = tmp_path / "mem.jsonl"
        file_journal = JSONLJournal(mem_path)
        mem_source = _populate_successful_journal()
        for t_evt in mem_source.read_by_type("trajectory"):
            file_journal.write_trajectory(t_evt.trajectory)

        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(learn_memories_from_journal=str(mem_path)),
        )
        stack = build_from_config(cfg)
        assert stack.is_llm_planner
        assert "CONSOLIDATED MEMORIES" in stack.planner.learned_memories_section
        assert stack.planner._memories_updated_count == 1

    def test_build_from_config_missing_memory_file_no_crash(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(learn_memories_from_journal=str(tmp_path / "nope.jsonl")),
        )
        stack = build_from_config(cfg)
        # Implementation note.
        assert stack.planner.learned_memories_section == ""
