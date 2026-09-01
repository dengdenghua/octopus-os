from __future__ import annotations

from pathlib import Path

from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.safety.evolution.repair_route_quality import (
    PROMOTION_CANDIDATE_SCHEMA,
    PROMOTION_QUEUE_SCHEMA,
    SCHEMA,
    compute_repair_route_quality,
    queue_repair_route_promotion_candidates,
)


def test_repair_route_quality_groups_failed_turn_routes(tmp_path: Path) -> None:
    ledger_path = tmp_path / "proposal_ledger.jsonl"
    ledger = ProposalLedger(ledger_path)
    ledger.propose(
        kind="turn_failure",
        description="verification failed",
        metadata={
            "failure_source": "verification_failed",
            "primary_repair_route": "test_driven_repair",
            "goal": "fix checkout flow",
            "has_code_changes": True,
            "verification_count": 1,
            "failed_verifications": [{"command": "pytest"}],
        },
    )
    ledger.propose(
        kind="turn_failure",
        description="verification required",
        metadata={
            "failure_source": "verification_required",
            "primary_repair_route": "verification_required",
            "goal": "edit settings page",
            "has_code_changes": True,
            "verification_count": 0,
            "failed_verifications": [],
            "verification_plan": {
                "schema": "echo.verification_plan.v1",
                "commands": [
                    {
                        "kind": "lint",
                        "command": "python -m ruff check src/foo.py",
                        "reason": "edited Python file",
                    }
                ],
            },
        },
    )
    ledger.propose(
        kind="memory_promotion",
        description="unrelated",
        metadata={"primary_repair_route": "noise"},
    )

    report = compute_repair_route_quality(ledger_path=ledger_path)

    assert report["schema"] == SCHEMA
    assert report["score"] < 0.85
    assert report["ready"] is False
    assert set(report["quality_gate"]["blockers"]) == {
        "failed_verifications",
        "unverified_code_changes",
    }
    assert report["total_failures"] == 2
    assert report["summary"]["failed_verification_total"] == 1
    assert report["summary"]["unverified_code_changes"] == 1
    by_route = {row["route"]: row for row in report["routes"]}
    assert by_route["test_driven_repair"]["count"] == 1
    assert by_route["verification_required"]["unverified_code_changes"] == 1
    assert by_route["verification_required"]["recommended_commands"] == [
        {"command": "python -m ruff check src/foo.py", "count": 1}
    ]
    assert any("verifier" in item for item in report["recommendations"])


def test_repair_route_quality_empty_ledger_is_ready(tmp_path: Path) -> None:
    ledger_path = tmp_path / "proposal_ledger.jsonl"

    report = compute_repair_route_quality(ledger_path=ledger_path)

    assert report["schema"] == SCHEMA
    assert report["score"] == 1.0
    assert report["ready"] is True
    assert report["quality_gate"]["blockers"] == []
    assert report["quality_gate"]["signals"]["total_failures"] == 0


def test_repair_route_quality_emits_promotion_candidates(tmp_path: Path) -> None:
    ledger_path = tmp_path / "proposal_ledger.jsonl"
    ledger = ProposalLedger(ledger_path)
    for index in range(2):
        ledger.propose(
            kind="turn_failure",
            description=f"verification failed {index}",
            metadata={
                "failure_source": "verification_failed",
                "primary_repair_route": "test_driven_repair",
                "goal": "fix checkout flow",
                "has_code_changes": True,
                "verification_count": 1,
                "failed_verifications": [{"command": "pytest"}],
                "verification_plan": {
                    "schema": "echo.verification_plan.v1",
                    "commands": [
                        {
                            "kind": "test",
                            "command": "python -m pytest tests/test_checkout.py -q",
                            "reason": "checkout flow changed",
                        }
                    ],
                },
            },
        )

    report = compute_repair_route_quality(ledger_path=ledger_path)
    candidate = report["promotion_candidates"][0]

    assert candidate["schema"] == PROMOTION_CANDIDATE_SCHEMA
    assert candidate["route"] == "test_driven_repair"
    assert candidate["priority"] == "P0"
    assert candidate["evidence"]["count"] == 2
    assert candidate["evidence"]["failed_verification_count"] == 2
    assert candidate["promotion_gate"]["blocks_auto_promotion"] is True


def test_repair_route_promotion_candidates_queue_to_review(tmp_path: Path) -> None:
    ledger_path = tmp_path / "proposal_ledger.jsonl"
    review_queue_path = tmp_path / "review_queue.json"
    ledger = ProposalLedger(ledger_path)
    for index in range(2):
        ledger.propose(
            kind="turn_failure",
            description=f"verification required {index}",
            metadata={
                "failure_source": "verification_required",
                "primary_repair_route": "verification_required",
                "goal": "edit settings page",
                "has_code_changes": True,
                "verification_count": 0,
                "failed_verifications": [],
            },
        )

    result = queue_repair_route_promotion_candidates(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )
    again = queue_repair_route_promotion_candidates(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )

    assert result["schema"] == PROMOTION_QUEUE_SCHEMA
    assert result["created"] == 1
    assert result["updated"] == 0
    assert again["created"] == 0
    assert again["updated"] == 1
    item = result["items"][0]
    assert item["source"] == "repair_route_quality"
    assert item["candidate_kind"] == "repair_route_promotion:verification_required"
    assert item["target_bucket"] == "experiment_backlog"
    assert item["metadata"]["promotion_candidate"]["route"] == "verification_required"
    assert "promotion_candidate" in item["tags"]


def test_repair_route_quality_counts_review_queue_governance(
    tmp_path: Path,
) -> None:
    ledger_path = tmp_path / "proposal_ledger.jsonl"
    review_queue_path = tmp_path / "review_queue.json"
    ledger = ProposalLedger(ledger_path)
    for index in range(3):
        ledger.propose(
            kind="turn_failure",
            description=f"verification required {index}",
            metadata={
                "failure_source": "verification_required",
                "primary_repair_route": "verification_required",
                "goal": "edit settings page",
                "has_code_changes": True,
                "verification_count": 1,
                "failed_verifications": [{"command": "pytest"}],
            },
        )

    before = compute_repair_route_quality(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )
    queued = queue_repair_route_promotion_candidates(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )
    pending = compute_repair_route_quality(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )

    assert before["score"] < 0.85
    assert queued["created"] == 1
    assert pending["score"] > before["score"]
    assert pending["ready"] is False
    assert pending["quality_gate"]["blockers"] == ["pending_repair_route_review"]
    assert pending["quality_gate"]["signals"]["governed_route_count"] == 1
    assert pending["quality_gate"]["signals"]["pending_governance_count"] == 1
    route = pending["routes"][0]
    assert route["governance"]["covered"] is True
    assert route["governance"]["status"] == "pending"
    assert pending["promotion_candidates"][0]["status"] == "operator_review_pending"

    from runtime.memory.learning.review_queue import ReviewQueue

    ReviewQueue(review_queue_path).decide(
        queued["items"][0]["id"],
        action="promoted",
        reason="passing rerun attached",
    )
    decided = compute_repair_route_quality(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
    )

    assert decided["score"] >= 0.85
    assert decided["ready"] is True
    assert decided["quality_gate"]["blockers"] == []
    assert decided["routes"][0]["governance"]["status"] == "promoted"

