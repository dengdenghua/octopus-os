from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from runtime.memory.learning.experience_ledger import ExperienceLedger


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
                "priority": "P0",
                "experiment": "Create deterministic replay case",
                "hypothesis": "A replay case prevents repeating this failure.",
                "minimal_implementation": "Convert replay.steps into a fixture.",
                "validation_metric": "Replay passes before prompt changes land.",
            }
        ],
    }


def test_experience_ledger_commits_review_candidates(tmp_path: Path) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")

    result = ledger.add_from_task_run_review(
        _review(),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )
    rows = ledger.records()

    assert result["created"] == 2
    assert result["updated"] == 0
    assert rows["total"] == 2
    assert {row["memory_bucket"] for row in rows["records"]} == {
        "experience",
        "experiment_backlog",
    }
    assert rows["records"][0]["priority"] == "P0"
    assert rows["records"][0]["source_task_ids"] == ["turn-1"]


def test_experience_ledger_deduplicates_without_overwriting(
    tmp_path: Path,
) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")

    ledger.add_from_task_run_review(
        _review("turn-1"),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )
    result = ledger.add_from_task_run_review(
        _review("turn-2"),
        now=datetime(2026, 6, 8, 1, 0, tzinfo=UTC),
    )
    rows = ledger.records()["records"]

    assert result["created"] == 0
    assert result["updated"] == 2
    assert len(rows) == 2
    assert all(row["occurrences"] == 2 for row in rows)
    assert all(row["created_at"].startswith("2026-06-07") for row in rows)
    assert all(row["last_seen_at"].startswith("2026-06-08") for row in rows)
    assert all(row["source_task_ids"] == ["turn-1", "turn-2"] for row in rows)


def test_experience_ledger_weekly_summary_groups_current_week(
    tmp_path: Path,
) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")

    ledger.add_from_task_run_review(
        _review("turn-old"),
        now=datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
    )
    ledger.add_from_task_run_review(
        _review("turn-current"),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )

    current = ledger.weekly_summary(week_start="2026-06-01")
    next_week = ledger.weekly_summary(week_start="2026-06-08")

    assert current["schema"] == "echo.experience_weekly_summary.v1"
    assert current["week_start"] == "2026-06-01"
    assert current["record_count"] == 2
    assert current["by_priority"] == {"P0": 2}
    assert current["next_actions"]
    assert next_week["record_count"] == 0


def test_experience_ledger_filters_records(tmp_path: Path) -> None:
    ledger = ExperienceLedger(tmp_path / "experience.json")
    ledger.add_from_task_run_review(
        _review(),
        now=datetime(2026, 6, 7, 1, 0, tzinfo=UTC),
    )

    backlog = ledger.records(bucket="experiment_backlog")
    failures = ledger.records(kind="failure_pattern")

    assert backlog["total"] == 1
    assert backlog["records"][0]["kind"] == "backlog_candidate"
    assert failures["total"] == 1
    assert failures["records"][0]["memory_bucket"] == "experience"
