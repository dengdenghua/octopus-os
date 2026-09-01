"""Durable KG for the LLM planner — the compounding half of the self-evolution
loop.

The live ReAct loop already re-runs ``learn_kg_from_journal`` every few turns,
but the default path rebuilds a throwaway in-memory graph each time, so learned
facts vanish on restart. ``enable_persistent_kg`` switches it to accumulate into
an on-disk SqliteKnowledgeGraph instead. These tests prove that accumulation,
survival across a simulated restart, and that the legacy ephemeral path is
unchanged when persistence is not enabled.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from runtime.core.cerebrum import LLMPlanner
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.memory.hemolymph import ContextComposer
from runtime.memory.journal import InMemoryJournal
from runtime.memory.knowledge_graph.sqlite_kg import SqliteKnowledgeGraph
from runtime.platform.models import ArmId, TaskId, Trajectory, TrajectoryOutcome
from runtime.sensing.model_router import MockModelRouter


@pytest.fixture
def registry() -> SkillRegistry:
    r = SkillRegistry()
    r.register(
        Skill(
            name="read_file",
            trusted_source="skill://public/read_file",
            handler=lambda **kw: {"ok": True},
        ),
        verify_tests=False,
    )
    return r


@pytest.fixture
def composer(registry: SkillRegistry) -> ContextComposer:
    return ContextComposer(registry=registry, journal=InMemoryJournal())


def _mk_planner(registry: SkillRegistry, composer: ContextComposer) -> LLMPlanner:
    router = MockModelRouter(
        response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
    )
    return LLMPlanner(router=router, registry=registry, composer=composer)


@pytest.fixture
def planner(registry: SkillRegistry, composer: ContextComposer) -> LLMPlanner:
    return _mk_planner(registry, composer)


def _journal(arm: str = "code_arm", strategy: str = "default") -> InMemoryJournal:
    """One successful trajectory → one ``(arm, completed_strategy, strategy)`` triple."""
    j = InMemoryJournal()
    j.write_trajectory(
        Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId(arm),
            strategy_id=strategy,
            steps=[],
            outcome=TrajectoryOutcome(success=True),
        )
    )
    return j


def test_enable_attaches_a_persistent_store(planner: LLMPlanner, tmp_path: Path) -> None:
    planner.enable_persistent_kg(tmp_path / "kg.db")
    assert planner._kg_persistent is True
    assert isinstance(planner.kg, SqliteKnowledgeGraph)


def test_learn_accumulates_across_calls(planner: LLMPlanner, tmp_path: Path) -> None:
    planner.enable_persistent_kg(tmp_path / "kg.db")
    planner.learn_kg_from_journal(_journal("arm_a", "s"))
    planner.learn_kg_from_journal(_journal("arm_b", "s"))
    # Both retained — accumulated into the store, NOT rebuilt-and-replaced.
    assert planner.kg.count() == 2


def test_knowledge_survives_restart(
    registry: SkillRegistry, composer: ContextComposer, tmp_path: Path
) -> None:
    db = tmp_path / "kg.db"

    p1 = _mk_planner(registry, composer)
    p1.enable_persistent_kg(db)
    p1.learn_kg_from_journal(_journal("arm_x", "strat_y"))
    p1.kg.close()  # simulate process shutdown

    # Fresh planner, same on-disk store: knowledge is loaded back.
    p2 = _mk_planner(registry, composer)
    loaded = p2.enable_persistent_kg(db)
    assert loaded == 1
    rows = p2.kg.query(predicate="completed_strategy")
    assert len(rows) == 1
    assert rows[0].subject == "arm_x"
    assert rows[0].object == "strat_y"
    # And recall surfaces it on the very first turn.
    assert p2._render_kg_section() != ""


def test_default_path_stays_ephemeral(planner: LLMPlanner) -> None:
    # No enable_persistent_kg → legacy in-memory rebuild, unchanged behaviour.
    planner.learn_kg_from_journal(_journal("arm_a", "s"))
    assert planner.kg.count() == 1
    # A second journal REBUILDS (replaces) rather than accumulating.
    planner.learn_kg_from_journal(_journal("arm_b", "s"))
    assert planner.kg.count() == 1
    assert planner._kg_persistent is False
    assert not isinstance(planner.kg, SqliteKnowledgeGraph)

