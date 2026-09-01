from __future__ import annotations

from pathlib import Path

from runtime.safety.evolution.auto_verifier_metrics import (
    BATCH_SCHEMA,
    DECISION_SCHEMA,
    DRIFT_REPAIR_ITEM_SCHEMA,
    DRIFT_REPAIR_QUEUE_SCHEMA,
    SCHEMA,
    command_family,
    explain_verification_ranking,
    queue_verifier_drift_backlog,
    rank_verification_commands,
    recent_auto_verifier_batches,
    recent_auto_verifier_decisions,
    record_auto_verifier_batch,
    record_auto_verifier_decision,
    record_auto_verifier_metric,
    summarize_auto_verifier_metrics,
)


def test_auto_verifier_metrics_summarize_by_family(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    record_auto_verifier_metric(
        command="python -m ruff check src/foo.py",
        kind="lint",
        ok=True,
        exit_code=0,
        duration_ms=120,
        target="src/foo.py",
        path=path,
    )
    record_auto_verifier_metric(
        command="python -m pytest tests/test_foo.py -q",
        kind="test",
        ok=False,
        exit_code=1,
        duration_ms=300,
        target="src/foo.py",
        path=path,
    )

    report = summarize_auto_verifier_metrics(path=path)

    assert report["schema"] == SCHEMA
    assert report["total"] == 2
    assert report["pass_rate"] == 0.5
    by_family = {row["family"]: row for row in report["families"]}
    assert by_family["ruff"]["pass_rate"] == 1.0
    assert by_family["pytest"]["fail_count"] == 1
    assert report["top_failures"] == [
        {"command": "python -m pytest tests/test_foo.py -q", "count": 1}
    ]
    assert report["alerts"] == []


def test_auto_verifier_metrics_alerts_on_family_drift(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    for ok in (False, False, True):
        record_auto_verifier_metric(
            command="python -m ruff check src/foo.py",
            kind="lint",
            ok=ok,
            exit_code=0 if ok else 1,
            duration_ms=90,
            target="src/foo.py",
            path=path,
        )

    report = summarize_auto_verifier_metrics(path=path)

    assert report["alerts"] == [
        {
            "family": "ruff",
            "severity": "critical",
            "total": 3,
            "fail_count": 2,
            "pass_rate": 0.333,
            "latest_ts": report["families"][0]["latest_ts"],
            "top_command": "python -m ruff check src/foo.py",
            "message": "ruff verifier family is drifting: 2/3 recent runs failed",
        }
    ]


def test_auto_verifier_drift_can_enter_repair_backlog(tmp_path: Path) -> None:
    metrics_path = tmp_path / "auto_verifier_metrics.jsonl"
    review_queue_path = tmp_path / "review_queue.json"
    for ok in (False, False, True):
        record_auto_verifier_metric(
            command="python -m ruff check src/foo.py",
            kind="lint",
            ok=ok,
            exit_code=0 if ok else 1,
            duration_ms=90,
            target="src/foo.py",
            path=metrics_path,
        )

    result = queue_verifier_drift_backlog(
        metrics_path=metrics_path,
        review_queue_path=review_queue_path,
    )
    again = queue_verifier_drift_backlog(
        metrics_path=metrics_path,
        review_queue_path=review_queue_path,
    )

    assert result["schema"] == DRIFT_REPAIR_QUEUE_SCHEMA
    assert result["created"] == 1
    assert result["updated"] == 0
    assert again["created"] == 0
    assert again["updated"] == 1
    item = result["items"][0]
    assert item["source"] == "auto_verifier_metrics"
    assert item["candidate_kind"] == "verifier_drift:ruff"
    assert item["priority"] == "P0"
    assert item["target_bucket"] == "experiment_backlog"
    assert "repair_route" in item["tags"]
    assert item["metadata"]["schema"] == DRIFT_REPAIR_ITEM_SCHEMA
    assert item["metadata"]["alert"]["family"] == "ruff"
    assert item["metadata"]["repair_route"]["blocks_auto_promotion"] is True
    assert item["metadata"]["repair_route"]["suggested_command"] == (
        "python -m ruff check src/foo.py"
    )


def test_command_family_classifies_supported_verifiers() -> None:
    assert command_family("python -m ruff check src/foo.py") == "ruff"
    assert command_family("python -m pytest tests/test_foo.py -q") == "pytest"
    assert command_family("cd frontend && pnpm check") == "pnpm_check"
    assert command_family("cd frontend && pnpm vitest run src/foo.test.ts") == "pnpm_vitest"


def test_rank_verification_commands_uses_history_within_same_priority(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    record_auto_verifier_metric(
        command="python -m ruff check src/foo.py",
        kind="lint",
        ok=False,
        exit_code=1,
        duration_ms=400,
        path=path,
    )
    record_auto_verifier_metric(
        command="python -m pytest tests/test_foo.py -q",
        kind="test",
        ok=True,
        exit_code=0,
        duration_ms=120,
        path=path,
    )
    commands = [
        {"command": "python -m ruff check src/foo.py", "priority": 1},
        {"command": "python -m pytest tests/test_foo.py -q", "priority": 1},
    ]

    ranked = rank_verification_commands(commands, path=path)

    assert [item["command"] for item in ranked] == [
        "python -m pytest tests/test_foo.py -q",
        "python -m ruff check src/foo.py",
    ]


def test_rank_verification_commands_preserves_stronger_static_priority(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    record_auto_verifier_metric(
        command="python -m ruff check src/foo.py",
        kind="lint",
        ok=False,
        exit_code=1,
        duration_ms=400,
        path=path,
    )
    record_auto_verifier_metric(
        command="python -m pytest tests/test_foo.py -q",
        kind="test",
        ok=True,
        exit_code=0,
        duration_ms=120,
        path=path,
    )
    commands = [
        {"command": "python -m ruff check src/foo.py", "priority": 1},
        {"command": "python -m pytest tests/test_foo.py -q", "priority": 2},
    ]

    ranked = rank_verification_commands(commands, path=path)

    assert ranked[0]["command"] == "python -m ruff check src/foo.py"


def test_rank_verification_commands_demotes_drifting_family(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    for _ in range(3):
        record_auto_verifier_metric(
            command="python -m ruff check src/foo.py",
            kind="lint",
            ok=False,
            exit_code=1,
            duration_ms=400,
            path=path,
        )
    record_auto_verifier_metric(
        command="python -m pytest tests/test_foo.py -q",
        kind="test",
        ok=True,
        exit_code=0,
        duration_ms=120,
        path=path,
    )
    commands = [
        {"command": "python -m ruff check src/foo.py", "priority": 1},
        {"command": "python -m pytest tests/test_foo.py -q", "priority": 2},
    ]

    ranked = rank_verification_commands(commands, path=path)
    ranking = explain_verification_ranking(commands, path=path)

    assert ranked[0]["command"] == "python -m pytest tests/test_foo.py -q"
    assert ranking[0]["command"] == "python -m pytest tests/test_foo.py -q"
    assert ranking[1]["command"] == "python -m ruff check src/foo.py"
    assert "drift_penalty=2.0" in ranking[1]["reason"]


def test_explain_verification_ranking_includes_operator_reason(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_metrics.jsonl"
    record_auto_verifier_metric(
        command="python -m ruff check src/foo.py",
        kind="lint",
        ok=True,
        exit_code=0,
        duration_ms=80,
        path=path,
    )

    ranking = explain_verification_ranking(
        [{"command": "python -m ruff check src/foo.py", "priority": 1}],
        path=path,
    )

    assert ranking[0]["rank"] == 1
    assert ranking[0]["family"] == "ruff"
    assert ranking[0]["history_count"] == 1.0
    assert "smoothed pass_rate" in ranking[0]["reason"]


def test_recent_auto_verifier_decisions_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_decisions.jsonl"
    candidates = [
        {
            "rank": 1,
            "command": "python -m ruff check src/foo.py",
            "reason": "priority=1; no history for ruff, using neutral score",
        }
    ]

    record_auto_verifier_decision(
        candidates=candidates,
        selected_command="python -m ruff check src/foo.py",
        path=path,
    )

    decisions = recent_auto_verifier_decisions(path=path)
    assert decisions[0]["schema"] == DECISION_SCHEMA
    assert decisions[0]["selected_command"] == "python -m ruff check src/foo.py"
    assert decisions[0]["candidates"] == candidates


def test_recent_auto_verifier_batches_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "auto_verifier_decisions.jsonl"

    record_auto_verifier_batch(
        candidate_count=3,
        commands=["python -m ruff check src/foo.py", "python -m pytest tests/test_foo.py -q"],
        passed_count=2,
        stop_reason="exhausted",
        path=path,
    )

    batches = recent_auto_verifier_batches(path=path)
    assert batches[0]["schema"] == BATCH_SCHEMA
    assert batches[0]["candidate_count"] == 3
    assert batches[0]["attempted_count"] == 2
    assert batches[0]["passed_count"] == 2
    assert batches[0]["complete"] is True
    assert batches[0]["stop_reason"] == "exhausted"

