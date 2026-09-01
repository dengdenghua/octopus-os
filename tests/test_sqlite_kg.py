"""Implementation note."""

from __future__ import annotations

from pathlib import Path

from runtime.memory.knowledge_graph import (
    KnowledgeGraph,
    SqliteKnowledgeGraph,
    Triple,
)
from runtime.platform.models import default_source

# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _mk_triple(
    subj: str,
    pred: str,
    obj: str,
    *,
    conf: float = 0.8,
) -> Triple:
    return Triple(
        subject=subj,
        predicate=pred,
        object=obj,
        confidence=conf,
        source=default_source("test", "inference"),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestBasicOps:
    def test_empty_graph(self, tmp_path: Path):
        with SqliteKnowledgeGraph(tmp_path / "kg.db") as kg:
            assert kg.count() == 0
            assert kg.query() == []

    def test_add_and_query(self, tmp_path: Path):
        with SqliteKnowledgeGraph(tmp_path / "kg.db") as kg:
            kg.add(_mk_triple("claude", "version", "4.7"))
            results = kg.query(subject="claude")
            assert len(results) == 1
            assert results[0].object == "4.7"

    def test_count_active_only(self, tmp_path: Path):
        with SqliteKnowledgeGraph(tmp_path / "kg.db") as kg:
            kg.add(_mk_triple("a", "p", "b", conf=0.5))
            kg.add(_mk_triple("a", "p", "c", conf=0.9))  # Implementation note.
            assert kg.count(active_only=True) == 1
            assert kg.count(active_only=False) == 2

    def test_neighbors(self, tmp_path: Path):
        with SqliteKnowledgeGraph(tmp_path / "kg.db") as kg:
            kg.add(_mk_triple("x", "r", "y"))
            kg.add(_mk_triple("z", "r", "x"))
            ns = kg.neighbors("x", hops=1)
            assert len(ns) == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPersistence:
    def test_reopen_restores_triples(self, tmp_path: Path):
        db = tmp_path / "kg.db"
        # Implementation note.
        kg1 = SqliteKnowledgeGraph(db)
        kg1.add(_mk_triple("claude", "version", "4.7"))
        kg1.add(_mk_triple("python", "version", "3.11"))
        kg1.close()

        # Implementation note.
        kg2 = SqliteKnowledgeGraph(db)
        try:
            assert kg2.count() == 2
            assert kg2.query(subject="claude")[0].object == "4.7"
            assert kg2.query(subject="python")[0].object == "3.11"
        finally:
            kg2.close()

    def test_superseded_status_persists(self, tmp_path: Path):
        """Implementation note."""
        db = tmp_path / "kg.db"
        kg1 = SqliteKnowledgeGraph(db)
        kg1.add(_mk_triple("a", "p", "old", conf=0.5))
        kg1.add(_mk_triple("a", "p", "new", conf=0.9))
        kg1.close()

        kg2 = SqliteKnowledgeGraph(db)
        try:
            # Implementation note.
            active = kg2.query(subject="a")
            assert len(active) == 1
            assert active[0].object == "new"
            # Implementation note.
            all_triples = kg2.query(subject="a", include_archived=True)
            assert len(all_triples) == 2
            statuses = sorted(t.status for t in all_triples)
            assert statuses == ["active", "superseded"]
        finally:
            kg2.close()

    def test_multi_valued_predicate_persists(self, tmp_path: Path):
        """Implementation note."""
        db = tmp_path / "kg.db"
        kg1 = SqliteKnowledgeGraph(db)
        kg1.add(_mk_triple("q1", "returned", "http://a"))
        kg1.add(_mk_triple("q1", "returned", "http://b"))
        kg1.add(_mk_triple("q1", "returned", "http://c"))
        kg1.close()

        kg2 = SqliteKnowledgeGraph(db)
        try:
            results = kg2.query(subject="q1", predicate="returned")
            objects = sorted(t.object for t in results)
            assert objects == ["http://a", "http://b", "http://c"]
        finally:
            kg2.close()

    def test_idempotent_across_reopen(self, tmp_path: Path):
        """Implementation note."""
        db = tmp_path / "kg.db"
        kg1 = SqliteKnowledgeGraph(db)
        kg1.add(_mk_triple("a", "b", "c"))
        kg1.close()

        kg2 = SqliteKnowledgeGraph(db)
        try:
            result = kg2.add(_mk_triple("a", "b", "c"))
            assert result.verdict == "ignored_lower_conf"
            assert "duplicate" in result.reason
            assert kg2.count() == 1
        finally:
            kg2.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestPathHandling:
    def test_creates_parent_directories(self, tmp_path: Path):
        nested = tmp_path / "deep" / "nested" / "kg.db"
        with SqliteKnowledgeGraph(nested) as kg:
            kg.add(_mk_triple("x", "y", "z"))
        assert nested.exists()

    def test_str_path_accepted(self, tmp_path: Path):
        with SqliteKnowledgeGraph(str(tmp_path / "kg.db")) as kg:
            kg.add(_mk_triple("a", "b", "c"))
            assert kg.count() == 1

    def test_context_manager_closes_conn(self, tmp_path: Path):
        kg = SqliteKnowledgeGraph(tmp_path / "kg.db")
        with kg:
            kg.add(_mk_triple("a", "b", "c"))
        # Implementation note.
        assert kg._conn is None


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestParityWithInMemory:
    def test_same_sequence_yields_same_queries(self, tmp_path: Path):
        triples = [
            _mk_triple("a", "p1", "1"),
            _mk_triple("a", "p1", "2", conf=0.9),  # supersedes
            _mk_triple("a", "p2", "x"),
            _mk_triple("b", "p1", "y"),
            _mk_triple("query", "returned", "url1"),  # multi-valued
            _mk_triple("query", "returned", "url2"),
        ]
        mem = KnowledgeGraph()
        for t in triples:
            mem.add(t)

        with SqliteKnowledgeGraph(tmp_path / "kg.db") as sql:
            for t in triples:
                sql.add(t)

            assert mem.count() == sql.count()
            mem_objs = sorted(t.object for t in mem.query())
            sql_objs = sorted(t.object for t in sql.query())
            assert mem_objs == sql_objs

            # Implementation note.
            mem_nb = {t.triple_id for t in mem.neighbors("a")}
            sql_nb = {t.triple_id for t in sql.neighbors("a")}
            assert mem_nb == sql_nb

    def test_kg_updater_works_with_sqlite(self, tmp_path: Path):
        """Implementation note."""
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
        from runtime.safety.recovery import KGUpdater

        j = InMemoryJournal()
        call = ToolCall(caller="arms/x", sucker_id="list_cwd", args={"path": "."})
        step = Step(
            step_id=0,
            node_id="n0",
            action=call,
            result=ExecutionResult(
                call_id=call.call_id,
                status="success",
                output={"path": "/tmp", "count": 5},
            ),
        )
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("a"),
                steps=[step],
                outcome=TrajectoryOutcome(success=True),
            )
        )

        db = tmp_path / "kg.db"
        kg1 = SqliteKnowledgeGraph(db)
        KGUpdater(j, kg1).update()
        count1 = kg1.count()
        assert count1 >= 1
        kg1.close()

        kg2 = SqliteKnowledgeGraph(db)
        try:
            # Implementation note.
            assert kg2.count() == count1
        finally:
            kg2.close()


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestLLMPlannerCompat:
    def test_attach_sqlite_kg_to_llm_planner(self, tmp_path: Path):
        """Implementation note."""
        import json

        from runtime.core.cerebrum import LLMPlanner
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.memory.hemolymph import ContextComposer
        from runtime.memory.journal import InMemoryJournal
        from runtime.platform.models import ParsedIntent
        from runtime.sensing.model_router import MockModelRouter

        registry = SkillRegistry()
        registry.register(
            Skill(
                name="read_file",
                trusted_source="skill://public/read_file",
                handler=lambda **kw: {"ok": True},
            ),
            verify_tests=False,
        )
        router = MockModelRouter(
            response=json.dumps({"reasoning": "r", "nodes": [{"skill": "read_file", "args": {}}]})
        )
        composer = ContextComposer(registry=registry, journal=InMemoryJournal())
        planner = LLMPlanner(router=router, registry=registry, composer=composer)

        with SqliteKnowledgeGraph(tmp_path / "kg.db") as kg:
            kg.add(_mk_triple("python", "preferred_version", "3.11"))
            planner.attach_kg(kg)

            intent = ParsedIntent(raw="x", intent_type="task", normalized_goal="y")
            planner.plan(intent)
            sys_msg = next(m.content for m in router.call_log[0].messages if m.role == "system")
            assert "RELATED FACTS" in sys_msg
            assert "preferred_version" in sys_msg
