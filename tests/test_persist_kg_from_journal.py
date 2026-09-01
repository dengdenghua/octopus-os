"""Tests for ``persist_kg_from_journal`` — the apply half of the KG reflection
signal.

The standalone ``KGUpdater`` against an in-memory graph discards everything on
return; this wires the durable persist so facts distilled from past experience
survive into a store the live agent's KG recall can read back. The tests prove
the triples actually hit disk (survive a fresh open), that re-running is
idempotent, and that a failed trajectory yields nothing.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from runtime.memory.journal import InMemoryJournal
from runtime.memory.knowledge_graph.sqlite_kg import SqliteKnowledgeGraph
from runtime.platform.models import ArmId, TaskId, Trajectory, TrajectoryOutcome
from runtime.safety.recovery import persist_kg_from_journal


def _journal_with_success() -> InMemoryJournal:
    """A journal with one successful trajectory → one ``completed_strategy`` triple."""
    j = InMemoryJournal()
    j.write_trajectory(
        Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("code_arm"),
            strategy_id="default",
            steps=[],
            outcome=TrajectoryOutcome(success=True),
        )
    )
    return j


def test_triples_survive_a_fresh_open(tmp_path: Path) -> None:
    db = tmp_path / "kg.db"
    report = persist_kg_from_journal(_journal_with_success(), db)
    assert report.triples_accepted == 1

    # Re-open the store from scratch: proves the triple hit disk, not just the
    # in-memory graph that KGUpdater discarded before.
    with SqliteKnowledgeGraph(db) as kg:
        assert kg.count() == 1
        rows = kg.query(predicate="completed_strategy")
        assert len(rows) == 1
        assert rows[0].subject == "code_arm"
        assert rows[0].object == "default"


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    db = tmp_path / "kg.db"
    j = _journal_with_success()
    persist_kg_from_journal(j, db)
    persist_kg_from_journal(j, db)  # same journal, same store, again

    with SqliteKnowledgeGraph(db) as kg:
        assert kg.count() == 1  # de-duped — the graph did not grow


def test_failed_trajectory_persists_nothing(tmp_path: Path) -> None:
    db = tmp_path / "kg.db"
    j = InMemoryJournal()
    j.write_trajectory(
        Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("x"),
            steps=[],
            outcome=TrajectoryOutcome(success=False),
        )
    )
    report = persist_kg_from_journal(j, db)
    assert report.triples_accepted == 0
    with SqliteKnowledgeGraph(db) as kg:
        assert kg.count() == 0

