from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from runtime.memory.learning.experience_ledger import ExperienceLedger
from runtime.memory.learning.promotion_applier import PromotionApplier
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.safety.evolution.proposal_ledger import ProposalLedger


def _review(task_id: str = "turn-1") -> dict:
    return {
        "schema": "echo.task_run_review.v1",
        "task_id": task_id,
        "thread_id": "thread-1",
        "turn_id": task_id,
        "agent_id": "agent-a",
        "status": "completed",
        "learning_candidates": [
            {
                "kind": "success_pattern",
                "priority": "P1",
                "memory_bucket": "experience",
                "title": "Useful tool sequence",
                "text": "Read the relevant files before editing.",
            }
        ],
        "backlog_candidates": [
            {
                "priority": "P0",
                "experiment": "Replay fixture",
                "hypothesis": "Replay prevents repeating the failure.",
                "minimal_implementation": "Convert the replay to a fixture.",
                "validation_metric": "Replay passes.",
            }
        ],
    }


def _applier(tmp_path: Path, queue: ReviewQueue) -> PromotionApplier:
    return PromotionApplier(
        review_queue=queue,
        experience_ledger=ExperienceLedger(tmp_path / "experience.json"),
        proposal_ledger=ProposalLedger(tmp_path / "proposal_ledger.jsonl"),
        audit_path=tmp_path / "promotion_audit.json",
    )


def test_promotion_applier_plans_and_applies_experience_items(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    queue.add_from_task_run_review(_review())
    item = queue.items(target_bucket="experience")["items"][0]
    queue.decide(item["id"], action="promoted", promoted_to="experience")
    applier = _applier(tmp_path, queue)

    plan = applier.plan()
    applied = applier.apply(now=datetime(2026, 6, 7, 3, 0, tzinfo=UTC))
    second_plan = applier.plan()
    audit = applier.audit()
    ledger_rows = ExperienceLedger(tmp_path / "experience.json").records()

    assert plan["dry_run"] is True
    assert plan["applicable"] == 1
    assert applied["applied"] == 1
    assert applied["results"][0]["artifact"]["type"] == "experience_ledger"
    assert second_plan["skipped"] == 1
    assert second_plan["actions"][0]["reason"] == "already applied"
    assert audit["total"] == 1
    assert audit["records"][0]["review_queue_item_id"] == item["id"]
    assert ledger_rows["total"] == 1
    assert ledger_rows["records"][0]["memory_bucket"] == "experience"


def test_promotion_applier_applies_experiment_backlog_to_experience_ledger(
    tmp_path: Path,
) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    queue.add_from_task_run_review(_review())
    item = queue.items(target_bucket="experiment_backlog")["items"][0]
    queue.decide(item["id"], action="promoted", promoted_to="experiment_backlog")
    applier = _applier(tmp_path, queue)

    result = applier.apply()
    rows = ExperienceLedger(tmp_path / "experience.json").records(
        bucket="experiment_backlog",
    )

    assert result["applied"] == 1
    assert rows["total"] == 1
    assert rows["records"][0]["kind"] == "backlog_candidate"


def test_promotion_applier_applies_rule_candidates_to_proposal_ledger(
    tmp_path: Path,
) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    queue.add_from_task_run_review(_review())
    item = queue.items(target_bucket="experience")["items"][0]
    queue.decide(item["id"], action="promoted", promoted_to="rule_candidate")
    applier = _applier(tmp_path, queue)

    result = applier.apply()
    proposals = ProposalLedger(tmp_path / "proposal_ledger.jsonl").query(
        kind="review_queue_rule_candidate",
    )

    assert result["applied"] == 1
    assert result["results"][0]["artifact"]["type"] == "proposal_ledger"
    assert len(proposals) == 1
    assert proposals[0].metadata["review_queue_item_id"] == item["id"]
