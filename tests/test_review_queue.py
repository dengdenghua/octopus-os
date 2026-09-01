from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from runtime.memory.learning.review_queue import ReviewQueue


def _review(task_id: str = "turn-1") -> dict:
    return {
        "schema": "echo.task_run_review.v1",
        "task_id": task_id,
        "thread_id": "thread-1",
        "turn_id": task_id,
        "agent_id": "agent-a",
        "status": "failed",
        "learning_candidates": [
            {
                "kind": "failure_pattern",
                "priority": "P0",
                "memory_bucket": "experience",
                "title": "Tool failure pattern: exec_shell",
                "text": "Add preflight validation before retrying exec_shell.",
            }
        ],
        "backlog_candidates": [
            {
                "priority": "P1",
                "experiment": "Create deterministic replay case",
                "hypothesis": "A replay case prevents repeating this failure.",
                "minimal_implementation": "Convert replay.steps into a fixture.",
                "validation_metric": "Replay passes before prompt changes land.",
            }
        ],
    }


def test_review_queue_adds_review_candidates(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")

    result = queue.add_from_task_run_review(
        _review(),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )
    rows = queue.items()

    assert result["created"] == 2
    assert result["updated"] == 0
    assert rows["total"] == 2
    assert {item["target_bucket"] for item in rows["items"]} == {
        "experience",
        "experiment_backlog",
    }
    assert all(item["status"] == "pending" for item in rows["items"])
    assert rows["items"][0]["priority"] == "P0"


def test_review_queue_deduplicates_without_overwriting(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")

    queue.add_from_task_run_review(
        _review("turn-1"),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )
    result = queue.add_from_task_run_review(
        _review("turn-2"),
        now=datetime(2026, 6, 8, 1, 0, tzinfo=UTC),
    )
    rows = queue.items()["items"]

    assert result["created"] == 0
    assert result["updated"] == 2
    assert len(rows) == 2
    assert all(item["occurrences"] == 2 for item in rows)
    assert all(item["created_at"].startswith("2026-06-07") for item in rows)
    assert all(item["last_seen_at"].startswith("2026-06-08") for item in rows)
    assert all(item["source_task_ids"] == ["turn-1", "turn-2"] for item in rows)


def test_review_queue_decisions_change_status(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    queue.add_from_task_run_review(_review())
    item_id = queue.items(target_bucket="experience")["items"][0]["id"]

    result = queue.decide(
        item_id,
        action="promoted",
        reason="This should become a guardrail.",
        promoted_to="rule_candidate",
        now=datetime(2026, 6, 7, 2, 0, tzinfo=UTC),
    )

    item = result["item"]
    assert item["status"] == "promoted"
    assert item["promoted_to"] == "rule_candidate"
    assert item["decision_reason"] == "This should become a guardrail."
    assert item["decided_at"].startswith("2026-06-07T02:00:00")
    assert queue.summary()["pending_count"] == 1
    assert queue.summary()["by_status"] == {"pending": 1, "promoted": 1}


def test_review_queue_filters_and_rejects_bad_decisions(tmp_path: Path) -> None:
    queue = ReviewQueue(tmp_path / "review_queue.json")
    queue.add_from_task_run_review(_review())
    item_id = queue.items(priority="P1")["items"][0]["id"]

    queue.decide(item_id, action="rejected", reason="Not worth running.")

    assert queue.items(status="pending")["total"] == 1
    assert queue.items(status="rejected")["total"] == 1
    assert queue.items(source_task_id="turn-1")["total"] == 2
    with pytest.raises(ValueError):
        queue.decide(item_id, action="unknown")
    with pytest.raises(KeyError):
        queue.decide("missing", action="archived")
