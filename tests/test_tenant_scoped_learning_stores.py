from __future__ import annotations

from pathlib import Path

import pytest

from runtime.memory.learning.experience_ledger import ExperienceLedger
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus


def _scope(tenant: str, actor: str, *, cross_tenant: bool = False) -> TenantScope:
    return TenantScope(tenant_id=tenant, actor_id=actor, allow_cross_tenant=cross_tenant)


def _review(title: str, task_id: str) -> dict:
    return {
        "schema": "echo.task_run_review.v1",
        "task_id": task_id,
        "thread_id": f"thread-{task_id}",
        "turn_id": task_id,
        "agent_id": "agent-a",
        "status": "failed",
        "learning_candidates": [
            {
                "kind": "failure_pattern",
                "priority": "P1",
                "memory_bucket": "experience",
                "title": title,
                "text": f"Fix {title} before retrying.",
            }
        ],
        "backlog_candidates": [],
    }


def test_scoped_default_paths_partition_tenant_and_owner(tmp_path: Path) -> None:
    base = tmp_path / "experience.json"
    alice = tenant_scoped_path(base, _scope("tenant-a", "alice"))
    bob = tenant_scoped_path(base, _scope("tenant-b", "bob"))

    assert alice != bob
    assert alice.parent.parent == tmp_path / "tenants"
    assert alice.name == base.name
    assert tenant_scoped_path(base, None) == base


def test_experience_rows_are_isolated_and_legacy_is_hidden(tmp_path: Path) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")
    alice = _scope("tenant-a", "alice")
    bob = _scope("tenant-b", "bob")
    admin = _scope("tenant-a", "admin", cross_tenant=True)

    ledger.add_from_task_run_review(_review("Alice lesson", "alice-task"), scope=alice)
    ledger.add_from_task_run_review(_review("Bob lesson", "bob-task"), scope=bob)
    ledger.add_from_task_run_review(_review("Legacy lesson", "legacy-task"))

    assert ledger.records(scope=alice)["total"] == 1
    assert ledger.records(scope=alice)["records"][0]["title"] == "Alice lesson"
    assert ledger.records(scope=bob)["total"] == 1
    assert ledger.records(scope=bob)["records"][0]["title"] == "Bob lesson"
    assert ledger.records(scope=admin)["total"] == 3
    assert ledger.records(scope=alice)["records"][0]["tenant_id"] == "tenant-a"
    assert ledger.records(scope=alice)["records"][0]["owner_actor_id"] == "alice"


def test_experience_contradiction_updates_cannot_cross_scope(tmp_path: Path) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")
    alice = _scope("tenant-a", "alice")
    bob = _scope("tenant-b", "bob")

    ledger.add_from_task_run_review(_review("Bob lesson", "bob-task"), scope=bob)
    bob_record = ledger.records(scope=bob)["records"][0]
    alice_review = _review("Alice correction", "alice-task")
    alice_review["learning_candidates"][0]["contradicts_record_ids"] = [bob_record["id"]]
    ledger.add_from_task_run_review(alice_review, scope=alice)

    assert (
        ledger.records(scope=bob)["records"][0]["memory_quality"]["contradiction_status"] == "none"
    )


def test_review_queue_scope_protects_decisions_and_legacy_rows(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    alice = _scope("tenant-a", "alice")
    bob = _scope("tenant-b", "bob")
    admin = _scope("tenant-a", "admin", cross_tenant=True)

    queue.add_from_task_run_review(_review("Alice review", "alice-task"), scope=alice)
    queue.add_from_task_run_review(_review("Bob review", "bob-task"), scope=bob)
    queue.add_from_task_run_review(_review("Legacy review", "legacy-task"))

    alice_item = queue.items(scope=alice)["items"][0]
    assert queue.items(scope=alice)["total"] == 1
    assert queue.items(scope=bob)["total"] == 1
    assert queue.items(scope=admin)["total"] == 3

    with pytest.raises(KeyError):
        queue.decide(alice_item["id"], action="rejected", scope=bob)

    result = queue.decide(alice_item["id"], action="promoted", scope=alice)
    assert result["item"]["status"] == "promoted"
    assert queue.items(status="promoted", scope=alice)["total"] == 1
    assert queue.items(status="promoted", scope=bob)["total"] == 0


def test_proposal_ledger_filters_query_stats_and_mutations(tmp_path: Path) -> None:
    ledger = ProposalLedger(tmp_path / "proposals.jsonl")
    alice = _scope("tenant-a", "alice")
    bob = _scope("tenant-b", "bob")
    admin = _scope("tenant-a", "admin", cross_tenant=True)

    alice_proposal = ledger.propose(kind="a", description="Alice", scope=alice)
    ledger.propose(kind="b", description="Bob", scope=bob)
    legacy = ledger.propose(kind="legacy", description="Legacy")

    assert [r.description for r in ledger.query(scope=alice)] == ["Alice"]
    assert [r.description for r in ledger.query(scope=bob)] == ["Bob"]
    assert ledger.stats(scope=alice)["total"] == 1
    assert ledger.stats(scope=admin)["total"] == 3
    assert ledger.query(scope=alice)[0].tenant_id == "tenant-a"

    assert ledger.accept(legacy.proposal_id, scope=alice) is None
    assert ledger.query(status=ProposalStatus.ACCEPTED, scope=alice) == []
    assert ledger.accept(alice_proposal.proposal_id, scope=alice) is not None
    assert ledger.query(status=ProposalStatus.ACCEPTED, scope=alice)[0].description == "Alice"

