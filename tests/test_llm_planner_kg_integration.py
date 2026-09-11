"""Implementation note."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.memory.knowledge_graph import (
    KnowledgeGraph,
    Triple,
    format_triples_for_prompt,
)
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    ParsedIntent,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
    default_source,
)
from runtime.sensing.model_router import MockModelRouter

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _mk_triple(subj: str, pred: str, obj: str, conf: float = 0.9) -> Triple:
    return Triple(
        subject=subj,
        predicate=pred,
        object=obj,
        confidence=conf,
        source=default_source("test_kg", "inference"),
    )


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    for name in ["read_file", "hash_text"]:
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


@pytest.fixture
def planner(registry, composer) -> LLMPlanner:
    router = MockModelRouter(
        response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
    )
    return LLMPlanner(router=router, registry=registry, composer=composer)


def _populate_traj_journal() -> InMemoryJournal:
    """Implementation note."""
    j = InMemoryJournal()
    call = ToolCall(caller="arms/code_arm", sucker_id="read_file", args={"path": "a"})
    step = Step(
        step_id=0,
        node_id="n0",
        action=call,
        result=ExecutionResult(call_id=call.call_id, status="success", output={"ok": True}),
    )
    for _ in range(3):
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("code_arm"),
                steps=[step],
                outcome=TrajectoryOutcome(success=True),
            )
        )
    return j


# ═══════════════════════════════════════════════════════════
# format_triples_for_prompt
# ═══════════════════════════════════════════════════════════


class TestFormatTriplesForPrompt:
    def test_empty_returns_empty(self):
        assert format_triples_for_prompt([]) == ""

    def test_filters_below_min_confidence(self):
        triples = [_mk_triple("a", "p", "b", conf=0.3)]
        assert format_triples_for_prompt(triples, min_confidence=0.5) == ""

    def test_confidence_sort_desc(self):
        triples = [
            _mk_triple("low", "p", "o", conf=0.6),
            _mk_triple("hi", "p", "o", conf=0.95),
        ]
        out = format_triples_for_prompt(triples)
        assert "RELATED FACTS" in out
        # Implementation note.
        assert out.index("hi") < out.index("low")

    def test_max_triples_caps(self):
        triples = [_mk_triple(f"s{i}", "p", "o") for i in range(20)]
        out = format_triples_for_prompt(triples, max_triples=3)
        # header + 3 lines
        assert len([line for line in out.splitlines() if line.strip().startswith("(")]) == 3


# ═══════════════════════════════════════════════════════════
# attach_kg
# ═══════════════════════════════════════════════════════════


class TestAttachKG:
    def test_default_no_kg(self, planner):
        assert planner.kg is None
        assert planner._render_kg_section() == ""

    def test_attach_sets_fields(self, planner):
        kg = KnowledgeGraph()
        kg.add(_mk_triple("claude-opus-4-7", "released_by", "anthropic"))
        planner.attach_kg(kg, max_triples=5)
        assert planner.kg is kg
        assert planner.kg_max_triples == 5
        assert planner._kg_attached_count == 1

    def test_kg_section_rendered(self, planner):
        kg = KnowledgeGraph()
        kg.add(_mk_triple("x", "related_to", "y"))
        planner.attach_kg(kg)
        section = planner._render_kg_section()
        assert "RELATED FACTS" in section
        assert "x" in section and "y" in section


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestKGPromptInjection:
    def test_kg_facts_appear_in_system_prompt(self, planner):
        kg = KnowledgeGraph()
        kg.add(_mk_triple("python", "preferred_version", "3.11"))
        planner.attach_kg(kg)

        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        sys_msg = next(m.content for m in planner.router.call_log[0].messages if m.role == "system")
        assert "RELATED FACTS" in sys_msg
        assert "preferred_version" in sys_msg

    def test_no_kg_no_section(self, planner):
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        sys_msg = next(m.content for m in planner.router.call_log[0].messages if m.role == "system")
        assert "RELATED FACTS" not in sys_msg

    def test_empty_kg_no_section(self, planner):
        planner.attach_kg(KnowledgeGraph())
        intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
        planner.plan(intent)
        sys_msg = next(m.content for m in planner.router.call_log[0].messages if m.role == "system")
        assert "RELATED FACTS" not in sys_msg


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestRecipeHashKGBinding:
    def test_hash_changes_after_attach(self, planner):
        before = planner.recipe_hash()
        kg = KnowledgeGraph()
        kg.add(_mk_triple("a", "b", "c"))
        planner.attach_kg(kg)
        assert planner.recipe_hash() != before

    def test_hash_tracks_kg_size(self, planner):
        kg = KnowledgeGraph()
        kg.add(_mk_triple("a", "b", "c"))
        planner.attach_kg(kg)
        h1 = planner.recipe_hash()
        kg.add(_mk_triple("x", "y", "z"))
        h2 = planner.recipe_hash()
        assert h1 != h2, "recipe_hash should track KG count changes"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLearnKGFromJournal:
    def test_one_shot_builds_and_attaches(self, planner):
        journal = _populate_traj_journal()
        n = planner.learn_kg_from_journal(journal)
        assert n >= 0  # at least does not crash; KGUpdater may yield 0+ triples
        assert planner.kg is not None
        assert planner._kg_attached_count == 1

    def test_empty_journal_still_attaches(self, planner):
        n = planner.learn_kg_from_journal(InMemoryJournal())
        assert n == 0
        assert planner.kg is not None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestConfigDrivenKGLoad:
    def test_build_from_config_loads_kg(self, tmp_path):
        from runtime.memory.journal import JSONLJournal
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        # Implementation note.
        path = tmp_path / "kg-source.jsonl"
        fj = JSONLJournal(path)
        src = _populate_traj_journal()
        for ev in src.read_by_type("trajectory"):
            fj.write_trajectory(ev.trajectory)
        for ev in src.read_by_type("step"):
            fj.write(ev)

        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(learn_kg_from_journal=str(path), kg_max_triples=7),
        )
        stack = build_from_config(cfg)
        assert stack.is_llm_planner
        assert stack.planner.kg is not None
        assert stack.planner.kg_max_triples == 7
        assert stack.planner._kg_attached_count == 1

    def test_missing_kg_file_no_crash(self, tmp_path):
        from runtime.platform.config import (
            AgentConfig,
            LearnConfig,
            PlannerConfig,
            build_from_config,
        )

        cfg = AgentConfig(
            planner=PlannerConfig(type="llm", model="mock/p", mock_response='{"nodes":[]}'),
            learn=LearnConfig(learn_kg_from_journal=str(tmp_path / "nope.jsonl")),
        )
        stack = build_from_config(cfg)
        # Implementation note.
        assert stack.planner.kg is None
