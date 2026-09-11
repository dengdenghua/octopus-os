"""Tests for runtime.memory.journal.sqlite_index."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from runtime.memory.journal.sqlite_index import JournalIndex

# ─── Helpers ────────────────────────────────────────────────


def _ts(year: int, month: int, day: int, hour: int = 0) -> str:
    return datetime(
        year,
        month,
        day,
        hour,
        tzinfo=UTC,
    ).isoformat()


def _record(
    *,
    event_type: str = "step",
    ts: str | None = None,
    conversation_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    rec: dict = {
        "schema_version": 1,
        "event_type": event_type,
        "ts": ts or _ts(2026, 1, 1),
    }
    if conversation_id is not None:
        rec["conversation_id"] = conversation_id
    if agent_id is not None:
        rec["agent_id"] = agent_id
    if task_id is not None:
        rec["task_id"] = task_id
    if extra:
        rec.update(extra)
    return rec


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def index(tmp_path: Path) -> JournalIndex:
    idx = JournalIndex(tmp_path / "journal_index.sqlite")
    yield idx
    idx.close()


# ─── 1. Empty database ──────────────────────────────────────


def test_empty_db_returns_empty_query(index: JournalIndex) -> None:
    assert index.query() == []


def test_empty_db_stats_zeros(index: JournalIndex) -> None:
    s = index.stats()
    assert s["total"] == 0
    assert s["by_type"] == {}
    assert s["last_indexed_at"] is None


# ─── 2. Index 100 mixed records, query by event_type ────────


def test_index_and_filter_by_event_type(
    index: JournalIndex,
    tmp_path: Path,
) -> None:
    jsonl = tmp_path / "j.jsonl"
    records: list[dict] = []
    for i in range(60):
        records.append(_record(event_type="step", ts=_ts(2026, 1, 1, i % 24)))
    for i in range(40):
        records.append(_record(event_type="trajectory", ts=_ts(2026, 1, 2, i % 24)))
    _write_jsonl(jsonl, records)

    inserted = index.index_jsonl(jsonl)
    assert inserted == 100

    steps = index.query(event_type="step", limit=1000)
    trajs = index.query(event_type="trajectory", limit=1000)
    assert len(steps) == 60
    assert len(trajs) == 40
    assert all(r["event_type"] == "step" for r in steps)
    assert all(r["event_type"] == "trajectory" for r in trajs)

    s = index.stats()
    assert s["total"] == 100
    assert s["by_type"] == {"step": 60, "trajectory": 40}
    assert s["last_indexed_at"] is not None


# ─── 3. Time range filter ───────────────────────────────────


def test_time_range_filter(index: JournalIndex, tmp_path: Path) -> None:
    jsonl = tmp_path / "j.jsonl"
    recs = [
        _record(ts=_ts(2026, 1, 1)),
        _record(ts=_ts(2026, 1, 5)),
        _record(ts=_ts(2026, 1, 10)),
        _record(ts=_ts(2026, 1, 15)),
        _record(ts=_ts(2026, 1, 20)),
    ]
    _write_jsonl(jsonl, recs)
    index.index_jsonl(jsonl)

    in_range = index.query(
        since=_ts(2026, 1, 5),
        until=_ts(2026, 1, 15),
        limit=100,
    )
    assert len(in_range) == 3

    only_since = index.query(since=_ts(2026, 1, 15), limit=100)
    assert len(only_since) == 2

    only_until = index.query(until=_ts(2026, 1, 5), limit=100)
    assert len(only_until) == 2


# ─── 4. Session filter ──────────────────────────────────────


def test_session_filter(index: JournalIndex, tmp_path: Path) -> None:
    jsonl = tmp_path / "j.jsonl"
    recs = [
        _record(conversation_id="conv-A"),
        _record(conversation_id="conv-A"),
        _record(conversation_id="conv-B"),
        _record(conversation_id=None),
    ]
    _write_jsonl(jsonl, recs)
    index.index_jsonl(jsonl)

    a = index.query(session_id="conv-A", limit=100)
    b = index.query(session_id="conv-B", limit=100)
    assert len(a) == 2
    assert len(b) == 1
    assert all(r["conversation_id"] == "conv-A" for r in a)


# ─── 5. Incremental indexing ────────────────────────────────


def test_incremental_indexing_only_new_lines(
    index: JournalIndex,
    tmp_path: Path,
) -> None:
    jsonl = tmp_path / "j.jsonl"
    _write_jsonl(jsonl, [_record() for _ in range(5)])
    first = index.index_jsonl(jsonl)
    assert first == 5

    # Re-index without changes — nothing new.
    none_added = index.index_jsonl(jsonl)
    assert none_added == 0
    assert index.stats()["total"] == 5

    # Append more lines, re-index — only new ones added.
    _write_jsonl(jsonl, [_record() for _ in range(3)])
    added = index.index_jsonl(jsonl)
    assert added == 3
    assert index.stats()["total"] == 8


# ─── 6. Malformed lines skipped ─────────────────────────────


def test_malformed_lines_skipped(
    index: JournalIndex,
    tmp_path: Path,
) -> None:
    jsonl = tmp_path / "j.jsonl"
    # Mix of valid + malformed lines.
    valid_a = json.dumps(_record(event_type="step"))
    valid_b = json.dumps(_record(event_type="trajectory"))
    valid_c = json.dumps(_record(event_type="step"))
    bad_1 = "{this is not json"
    bad_2 = "[1, 2, 3]"  # JSON but not a dict
    with jsonl.open("w", encoding="utf-8") as f:
        f.write(valid_a + "\n")
        f.write(bad_1 + "\n")
        f.write(valid_b + "\n")
        f.write(bad_2 + "\n")
        f.write(valid_c + "\n")

    inserted = index.index_jsonl(jsonl)
    assert inserted == 3
    assert index.last_skipped_count == 2

    s = index.stats()
    assert s["total"] == 3
    assert s["by_type"] == {"step": 2, "trajectory": 1}


# ─── 7. WAL mode ────────────────────────────────────────────


def test_wal_mode_set(tmp_path: Path) -> None:
    db_path = tmp_path / "wal.sqlite"
    idx = JournalIndex(db_path)
    try:
        row = idx._conn.execute(  # noqa: SLF001
            "PRAGMA journal_mode;"
        ).fetchone()
        # Row factory returns a Row mapping; first column is the mode.
        mode = row[0] if row else None
        assert str(mode).lower() == "wal"
    finally:
        idx.close()


# ─── 8. Limit + offset ──────────────────────────────────────


def test_limit_and_offset(index: JournalIndex, tmp_path: Path) -> None:
    jsonl = tmp_path / "j.jsonl"
    recs = [_record(event_type="step", ts=_ts(2026, 1, 1, i)) for i in range(20)]
    _write_jsonl(jsonl, recs)
    index.index_jsonl(jsonl)

    page1 = index.query(limit=5, offset=0)
    page2 = index.query(limit=5, offset=5)
    page3 = index.query(limit=5, offset=15)

    assert len(page1) == 5
    assert len(page2) == 5
    assert len(page3) == 5
    # Pages should not overlap (oldest-first ordering by id).
    p1_ts = [r["ts"] for r in page1]
    p2_ts = [r["ts"] for r in page2]
    assert set(p1_ts).isdisjoint(set(p2_ts))


# ─── 9. Multiple JSONL sources tracked independently ────────


def test_multiple_sources_tracked_independently(
    index: JournalIndex,
    tmp_path: Path,
) -> None:
    j1 = tmp_path / "src1.jsonl"
    j2 = tmp_path / "src2.jsonl"

    _write_jsonl(j1, [_record(event_type="step") for _ in range(4)])
    _write_jsonl(j2, [_record(event_type="trajectory") for _ in range(7)])

    assert index.index_jsonl(j1) == 4
    assert index.index_jsonl(j2) == 7
    assert index.stats()["total"] == 11

    # Append to j1 only — re-indexing j2 finds nothing new.
    _write_jsonl(j1, [_record(event_type="step") for _ in range(2)])

    assert index.index_jsonl(j2) == 0
    assert index.index_jsonl(j1) == 2
    assert index.stats()["total"] == 13

    s = index.stats()
    assert s["by_type"] == {"step": 6, "trajectory": 7}
