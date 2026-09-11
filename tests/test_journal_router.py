"""Tests for runtime.sensing.gateway.journal_router."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.sensing.gateway.journal_router import create_journal_router


def _seed_jsonl(path: Path, n: int = 5) -> None:
    """Write n journal-shaped records mixing event_types."""
    types = ["task_started", "tool_call", "task_completed"]
    lines = []
    for i in range(n):
        lines.append(
            json.dumps(
                {
                    "schema_version": 1,
                    "event_id": f"event-{i:04d}",
                    "event_type": types[i % len(types)],
                    "ts": f"2026-05-0{(i % 9) + 1}T10:00:00+00:00",
                    "task_id": f"task-{i % 3}",
                    "agent_id": "coder",
                    "conversation_id": f"thread-{i % 2}",
                    "source": "test",
                }
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def jsonl_path(tmp_path: Path) -> Path:
    p = tmp_path / "journal.jsonl"
    _seed_jsonl(p, n=10)
    return p


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "journal_index.sqlite"


@pytest.fixture
def client(db_path: Path, jsonl_path: Path) -> TestClient:
    """Build the app + router but don't auto-index — tests drive
    the indexer via the /reindex endpoint to assert behavior."""
    app = FastAPI()
    app.include_router(
        create_journal_router(db_path=db_path, default_jsonl_path=jsonl_path),
    )
    return TestClient(app)


def test_stats_empty_before_reindex(client: TestClient) -> None:
    r = client.get("/api/journal/stats")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_reindex_imports_records(
    client: TestClient,
    jsonl_path: Path,
) -> None:
    r = client.post("/api/journal/reindex")
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == 10
    assert body["skipped"] == 0
    assert body["source"].endswith("journal.jsonl")


def test_reindex_uses_explicit_path(
    client: TestClient,
    tmp_path: Path,
) -> None:
    other = tmp_path / "other.jsonl"
    _seed_jsonl(other, n=3)
    r = client.post(
        "/api/journal/reindex",
        json={"jsonl_path": str(other)},
    )
    assert r.status_code == 200
    assert r.json()["added"] == 3


def test_reindex_404_when_path_missing(client: TestClient) -> None:
    r = client.post(
        "/api/journal/reindex",
        json={"jsonl_path": "/does/not/exist.jsonl"},
    )
    assert r.status_code == 404


def test_query_filters_by_event_type(client: TestClient) -> None:
    client.post("/api/journal/reindex")
    r = client.get(
        "/api/journal/events",
        params={"event_type": "task_started"},
    )
    assert r.status_code == 200
    rows = r.json()["events"]
    assert len(rows) > 0
    assert all(row["event_type"] == "task_started" for row in rows)


def test_query_pagination(client: TestClient) -> None:
    client.post("/api/journal/reindex")
    page1 = client.get(
        "/api/journal/events",
        params={"limit": 3, "offset": 0},
    ).json()["events"]
    page2 = client.get(
        "/api/journal/events",
        params={"limit": 3, "offset": 3},
    ).json()["events"]
    assert len(page1) == 3
    assert len(page2) == 3
    assert {e["event_id"] for e in page1}.isdisjoint({e["event_id"] for e in page2})


def test_query_filters_by_session(client: TestClient) -> None:
    client.post("/api/journal/reindex")
    r = client.get(
        "/api/journal/events",
        params={"session_id": "thread-1"},
    )
    rows = r.json()["events"]
    assert rows
    # The query returns raw payloads — session_id maps to
    # conversation_id in the source schema.
    assert all(row["conversation_id"] == "thread-1" for row in rows)


def test_stats_after_reindex(client: TestClient) -> None:
    client.post("/api/journal/reindex")
    stats = client.get("/api/journal/stats").json()
    assert stats["total"] == 10
    assert "task_started" in stats["by_type"]
