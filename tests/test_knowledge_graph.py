"""Implementation note."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from runtime.memory.journal import InMemoryJournal
from runtime.memory.knowledge_graph import KnowledgeGraph, Triple
from runtime.platform.models import (
    ArmId,
    ExecutionResult,
    Source,
    Step,
    TaskId,
    ToolCall,
    Trajectory,
    TrajectoryOutcome,
    default_source,
    now_utc,
)
from runtime.safety.recovery import KGUpdater


def _src(name: str = "test", stype: str = "tool") -> Source:
    return default_source(name, stype)  # type: ignore[arg-type]


def _t(
    s: str,
    p: str,
    o: str,
    *,
    confidence: float = 0.75,
    ts: datetime | None = None,
) -> Triple:
    return Triple(
        subject=s,
        predicate=p,
        object=o,
        confidence=confidence,
        source=_src(),
        ts=ts or now_utc(),
    )


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestTripleModel:
    def test_basic_construction(self):
        t = _t("alice", "knows", "bob")
        assert t.subject == "alice"
        assert t.predicate == "knows"
        assert t.object == "bob"
        assert t.status == "active"

    def test_confidence_bounded(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            Triple(subject="a", predicate="b", object="c", confidence=1.5, source=_src())

    def test_sp_key(self):
        t = _t("alice", "knows", "bob")
        assert t.sp_key == ("alice", "knows")


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestAdd:
    def test_first_add_accepted(self):
        kg = KnowledgeGraph()
        r = kg.add(_t("claude", "version", "4.7"))
        assert r.verdict == "accepted"
        assert kg.count() == 1

    def test_duplicate_spo_ignored(self):
        kg = KnowledgeGraph()
        kg.add(_t("claude", "version", "4.7", confidence=0.8))
        r = kg.add(_t("claude", "version", "4.7", confidence=0.9))
        assert r.verdict == "ignored_lower_conf"
        assert "duplicate" in r.reason
        assert kg.count() == 1

    def test_higher_confidence_supersedes(self):
        kg = KnowledgeGraph()
        kg.add(_t("claude", "version", "4.6", confidence=0.6))
        r = kg.add(_t("claude", "version", "4.7", confidence=0.9))
        assert r.verdict == "superseded_old"
        assert len(r.superseded_ids) == 1
        # Implementation note.
        actives = kg.query(subject="claude", predicate="version")
        assert len(actives) == 1
        assert actives[0].object == "4.7"

    def test_lower_confidence_ignored(self):
        kg = KnowledgeGraph()
        kg.add(_t("claude", "version", "4.7", confidence=0.9))
        r = kg.add(_t("claude", "version", "4.5", confidence=0.6))
        assert r.verdict == "ignored_lower_conf"
        assert kg.count() == 1

    def test_supersede_is_all_or_nothing_across_existing_actives(self):
        kg = KnowledgeGraph(multi_valued_predicates={"version"})
        low = _t("claude", "version", "4.6", confidence=0.4)
        high = _t("claude", "version", "4.8", confidence=0.9)
        kg.add(low)
        kg.add(high)
        kg._multi_valued.clear()  # noqa: SLF001 - simulate a predicate policy migration.

        r = kg.add(_t("claude", "version", "4.7", confidence=0.7))

        assert r.verdict == "ignored_lower_conf"
        archived = kg.query(
            subject="claude",
            predicate="version",
            include_archived=True,
        )
        by_id = {t.triple_id: t for t in archived}
        assert by_id[low.triple_id].status == "active"
        assert by_id[high.triple_id].status == "active"
        assert kg.count() == 2

    def test_replace_reindexes_when_identity_fields_change(self):
        kg = KnowledgeGraph()
        original = _t("old-subject", "old-predicate", "old-object")
        kg.add(original)
        moved = original.model_copy(
            update={
                "subject": "new-subject",
                "predicate": "new-predicate",
                "object": "new-object",
            }
        )

        kg._replace(original.triple_id, moved)  # noqa: SLF001

        assert kg.query(subject="old-subject", include_archived=True) == []
        assert kg.query(predicate="old-predicate", include_archived=True) == []
        assert kg.query(object="old-object", include_archived=True) == []
        assert kg.query(subject="new-subject", include_archived=True) == [moved]
        assert kg.query(predicate="new-predicate", include_archived=True) == [moved]
        assert kg.query(object="new-object", include_archived=True) == [moved]

    def test_equal_confidence_recency_wins(self):
        kg = KnowledgeGraph()
        ts_old = now_utc() - timedelta(hours=2)
        kg.add(_t("x", "is", "old", confidence=0.7, ts=ts_old))
        r = kg.add(_t("x", "is", "new", confidence=0.7))
        assert r.verdict == "superseded_old"
        assert kg.query(subject="x")[0].object == "new"

    def test_different_predicates_coexist(self):
        kg = KnowledgeGraph()
        kg.add(_t("claude", "version", "4.7"))
        kg.add(_t("claude", "creator", "anthropic"))
        assert kg.count() == 2
        assert len(kg.query(subject="claude")) == 2


# ═══════════════════════════════════════════════════════════
# query
# ═══════════════════════════════════════════════════════════


class TestQuery:
    def test_query_by_subject(self):
        kg = KnowledgeGraph()
        kg.add(_t("a", "p1", "x"))
        kg.add(_t("a", "p2", "y"))
        kg.add(_t("b", "p1", "z"))
        assert len(kg.query(subject="a")) == 2
        assert len(kg.query(subject="b")) == 1

    def test_query_by_predicate(self):
        kg = KnowledgeGraph()
        kg.add(_t("a", "knows", "b"))
        kg.add(_t("c", "knows", "d"))
        kg.add(_t("a", "hates", "c"))
        assert len(kg.query(predicate="knows")) == 2

    def test_query_by_object(self):
        kg = KnowledgeGraph()
        kg.add(_t("a", "loves", "X"))
        kg.add(_t("b", "hates", "X"))
        assert len(kg.query(object="X")) == 2

    def test_wildcard_returns_all(self):
        kg = KnowledgeGraph()
        # Implementation note.
        for i in range(3):
            kg.add(_t("s", "mentions", f"obj_{i}"))
        assert len(kg.query()) == 3

    def test_archived_excluded_by_default(self):
        kg = KnowledgeGraph()
        # Implementation note.
        kg.add(_t("x", "version", "old", ts=now_utc() - timedelta(hours=1)))
        kg.add(_t("x", "version", "new"))
        assert len(kg.query(subject="x")) == 1
        assert len(kg.query(subject="x", include_archived=True)) == 2


# ═══════════════════════════════════════════════════════════
# neighbors
# ═══════════════════════════════════════════════════════════


class TestNeighbors:
    def test_1_hop_out(self):
        kg = KnowledgeGraph()
        kg.add(_t("alice", "knows", "bob"))
        kg.add(_t("alice", "works_at", "acme"))
        assert len(kg.neighbors("alice", hops=1)) == 2

    def test_1_hop_in(self):
        kg = KnowledgeGraph()
        kg.add(_t("alice", "knows", "bob"))
        kg.add(_t("carol", "knows", "bob"))
        assert len(kg.neighbors("bob", hops=1)) == 2


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


def _step(sucker: str, output: dict, success: bool = True) -> Step:
    call = ToolCall(caller="arms/test", sucker_id=sucker, args={})
    return Step(
        step_id=0,
        node_id="n0",
        action=call,
        result=ExecutionResult(
            call_id=call.call_id,
            status="success" if success else "failed",
            output=output,
        ),
    )


class TestKGUpdater:
    def test_web_search_yields_query_returned_triples(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        output = {
            "query": "claude 4.7",
            "backend": "ddg",
            "results": [
                {"title": "Release notes", "url": "https://anthropic.com/news"},
                {"title": "Tutorial", "url": "https://claude.com/tutorial"},
            ],
        }
        j.write_step(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("intel_collector/x"),
            step=_step("web_search", output),
        )

        report = KGUpdater(journal=j, kg=kg).update()
        # 2 URL × (returned + has_title) = 4 triples
        assert report.triples_proposed == 4
        assert report.triples_accepted == 4

        # Implementation note.
        assert len(kg.query(subject="query:claude 4.7", predicate="returned")) == 2
        titles = kg.query(predicate="has_title")
        assert any("Release notes" in t.object for t in titles)

    def test_fetch_url_yields_status_and_size(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        output = {
            "url": "https://example.com/",
            "status_code": 200,
            "length": 1234,
            "content": "...",
            "truncated": False,
        }
        j.write_step(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("intel_collector/x"),
            step=_step("fetch_url", output),
        )

        KGUpdater(journal=j, kg=kg).update()
        statuses = kg.query(subject="https://example.com/", predicate="has_status")
        sizes = kg.query(subject="https://example.com/", predicate="has_size_bytes")
        fetched = kg.query(subject="https://example.com/", predicate="fetched_at")
        assert len(statuses) == 1 and statuses[0].object == "200"
        assert len(sizes) == 1 and sizes[0].object == "1234"
        assert len(fetched) == 1

    def test_failed_step_produces_no_triples(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        j.write_step(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("x"),
            step=_step("web_search", {"error": "boom"}, success=False),
        )
        report = KGUpdater(journal=j, kg=kg).update()
        assert report.triples_proposed == 0

    def test_trajectory_success_yields_arm_completed_triple(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        traj = Trajectory(
            task_id=TaskId(uuid4()),
            arm_id=ArmId("code_arm"),
            strategy_id="my_strategy",
            steps=[],
            outcome=TrajectoryOutcome(success=True),
        )
        j.write_trajectory(traj)
        KGUpdater(journal=j, kg=kg).update()
        results = kg.query(subject="code_arm", predicate="completed_strategy")
        assert len(results) == 1
        assert results[0].object == "my_strategy"

    def test_failed_trajectory_yields_nothing(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        j.write_trajectory(
            Trajectory(
                task_id=TaskId(uuid4()),
                arm_id=ArmId("x"),
                steps=[],
                outcome=TrajectoryOutcome(success=False),
            )
        )
        report = KGUpdater(journal=j, kg=kg).update()
        assert report.triples_proposed == 0

    def test_swarm_aggregate_deduplicates_same_task_trajectory_triples(self):
        j = InMemoryJournal()
        kg = KnowledgeGraph()
        task_id = TaskId(uuid4())
        j.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("code_arm"),
                strategy_id="default",
                steps=[],
                outcome=TrajectoryOutcome(success=True),
            )
        )
        j.write_trajectory(
            Trajectory(
                task_id=task_id,
                arm_id=ArmId("swarm"),
                strategy_id="swarm",
                steps=[],
                outcome=TrajectoryOutcome(success=True),
            )
        )

        report = KGUpdater(journal=j, kg=kg).update()
        assert report.triples_proposed == 1
        results = kg.query(predicate="completed_strategy")
        assert len(results) == 1
        assert results[0].subject == "swarm"
        assert results[0].object == "swarm"


# ═══════════════════════════════════════════════════════════
# Implementation note.
# ═══════════════════════════════════════════════════════════


class TestIntelToKG:
    def test_full_intel_kg_pipeline(self):
        from runtime.execution.suckers import Skill, SkillRegistry
        from runtime.safety.recovery import IntelCollector, IntelSource

        r = SkillRegistry()
        r.register(
            Skill(
                name="web_search",
                trusted_source="skill://public/web_search",
                handler=lambda query="", **kw: {
                    "query": query,
                    "backend": "mock",
                    "results": [
                        {"title": "Result 1", "url": f"https://mock/{query}/1"},
                        {"title": "Result 2", "url": f"https://mock/{query}/2"},
                    ],
                },
            ),
            verify_tests=False,
        )

        j = InMemoryJournal()
        IntelCollector(
            sources=[
                IntelSource(source_id="q1", query="claude"),
                IntelSource(source_id="q2", query="openai"),
            ],
            journal=j,
            registry=r,
        ).run_once()

        kg = KnowledgeGraph()
        KGUpdater(journal=j, kg=kg).update()

        # 2 queries × 2 results × 2 triples（returned + has_title）= 8
        # + 1 trajectory = completed_strategy triple = 9
        assert kg.count() >= 8
        # Implementation note.
        assert len(kg.query(subject="query:claude", predicate="returned")) == 2
