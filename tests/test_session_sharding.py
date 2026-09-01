"""Tests for the session-sharded thread store and session_index."""

from __future__ import annotations

import json
from pathlib import Path

from runtime.memory.threads import SessionIndex, ThreadStateStore
from runtime.memory.threads.session_index import IndexEntry


def _records(path: Path) -> list[dict]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


# ─── session_meta header ────────────────────────────────────


def test_per_thread_jsonl_starts_with_session_meta(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread(
        "thread-a",
        metadata={"agent": "coder"},
    )
    target = tmp_path / "agents" / "coder" / "sessions" / "thread-a.jsonl"
    records = _records(target)
    assert records[0] == {
        "type": "session_meta",
        "payload": {
            "id": "thread-a",
            "timestamp": records[0]["payload"]["timestamp"],
            "originator": "echo",
            "agent": "coder",
            "team_id": None,
        },
    }
    assert records[1]["op"] == "upsert"


def test_session_meta_ignored_on_reload(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread("thread-b", metadata={"agent": "general"})
    store.update_state("thread-b", values={"title": "Hello"})

    reloaded = ThreadStateStore(per_agent_base=tmp_path)
    thread = reloaded.get("thread-b")
    assert thread is not None
    assert thread["values"]["title"] == "Hello"


def test_custom_session_origin_is_respected(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path, session_origin="echo-desktop")
    store.ensure_thread("thread-c", metadata={"agent": "coder"})
    target = tmp_path / "agents" / "coder" / "sessions" / "thread-c.jsonl"
    meta = _records(target)[0]
    assert meta["payload"]["originator"] == "echo-desktop"


# ─── dated layout ───────────────────────────────────────────


def test_dated_layout_puts_new_threads_under_year_month(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path, dated_layout=True)
    store.ensure_thread("dated-thread", metadata={"agent": "coder"})

    sess_root = tmp_path / "agents" / "coder" / "sessions"
    flat = sess_root / "dated-thread.jsonl"
    assert not flat.exists(), "flat path should not be used under dated_layout"

    hits = list(sess_root.rglob("dated-thread.jsonl"))
    assert len(hits) == 1
    rel = hits[0].relative_to(sess_root)
    # e.g. 2026/05/dated-thread.jsonl
    assert len(rel.parts) == 3
    assert rel.parts[0].isdigit() and len(rel.parts[0]) == 4
    assert rel.parts[1].isdigit() and len(rel.parts[1]) == 2


def test_dated_layout_reads_pre_existing_flat_file(tmp_path: Path) -> None:
    # Pre-create a flat file as if it were written before the dated
    # layout was enabled. Subsequent writes should CONTINUE to go
    # there, not fork into a new dated file.
    sess_root = tmp_path / "agents" / "coder" / "sessions"
    sess_root.mkdir(parents=True)
    flat = sess_root / "legacy.jsonl"
    flat.write_text(
        json.dumps({"type": "session_meta", "payload": {"id": "legacy"}})
        + "\n"
        + json.dumps(
            {
                "op": "upsert",
                "thread_id": "legacy",
                "thread": {
                    "thread_id": "legacy",
                    "status": "idle",
                    "created_at": "2026-04-01T00:00:00Z",
                    "updated_at": "2026-04-01T00:00:00Z",
                    "metadata": {"agent": "coder"},
                    "values": {"title": "Old", "messages": [], "artifacts": []},
                },
                "state": {
                    "values": {"title": "Old", "messages": [], "artifacts": []},
                    "next": [],
                    "metadata": {"agent": "coder"},
                    "checkpoint": {"id": "x", "checkpoint_id": "x", "ts": "t"},
                    "checkpoint_id": "x",
                    "tasks": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store = ThreadStateStore(per_agent_base=tmp_path, dated_layout=True)
    store.update_state("legacy", values={"title": "Updated"})

    # No new dated file; the flat one grew.
    dated = list(sess_root.rglob("legacy.jsonl"))
    assert dated == [flat]
    records = _records(flat)
    assert records[-1]["thread"]["values"]["title"] == "Updated"


# ─── session_index ──────────────────────────────────────────


def test_session_index_is_written_alongside_threads(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread("t1", metadata={"agent": "coder"})
    store.update_state("t1", values={"title": "Greeting"})
    store.ensure_thread("t2", metadata={"agent": "general"})

    idx_path = tmp_path / "data" / "sessions" / "session_index.jsonl"
    assert idx_path.exists()

    lines = [
        json.loads(line)
        for line in idx_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # Three writes: ensure t1, update t1, ensure t2
    assert len(lines) >= 3
    thread_ids = {line["thread_id"] for line in lines if line.get("op") == "upsert"}
    assert thread_ids == {"t1", "t2"}


def test_session_index_deduplicates_no_op_upserts(tmp_path: Path) -> None:
    idx = SessionIndex(tmp_path / "idx.jsonl")
    entry = IndexEntry(
        thread_id="abc",
        title="Hi",
        status="idle",
        agent_id="coder",
        team_id=None,
        created_at="2026-05-01T00:00:00Z",
        updated_at="2026-05-01T00:00:00Z",
        file="agents/coder/sessions/abc.jsonl",
    )
    idx.upsert(entry)
    idx.upsert(entry)  # identical → should not re-append
    idx.upsert(entry)

    lines = (tmp_path / "idx.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([line for line in lines if line.strip()]) == 1


def test_session_index_reload_applies_tombstones(tmp_path: Path) -> None:
    idx = SessionIndex(tmp_path / "idx.jsonl")
    idx.upsert(
        IndexEntry(
            thread_id="gone",
            title="Bye",
            status="idle",
            agent_id=None,
            team_id=None,
            created_at="t",
            updated_at="t",
            file="x",
        )
    )
    idx.delete("gone")

    reloaded = SessionIndex(tmp_path / "idx.jsonl")
    assert "gone" not in reloaded
    assert len(reloaded) == 0


def test_session_index_compact_collapses_history(tmp_path: Path) -> None:
    path = tmp_path / "idx.jsonl"
    idx = SessionIndex(path, compaction_threshold=0)  # disable auto

    base = IndexEntry(
        thread_id="t",
        title="v1",
        status="idle",
        agent_id=None,
        team_id=None,
        created_at="t",
        updated_at="t",
        file="x",
    )
    for v in ("v1", "v2", "v3", "v4"):
        idx.upsert(IndexEntry(**{**base.__dict__, "title": v, "updated_at": v}))
    # Four distinct upserts → four lines.
    raw = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(raw) == 4

    idx.compact()
    compacted = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(compacted) == 1
    # And reload sees the latest version.
    assert SessionIndex(path).get("t").title == "v4"


def test_session_index_reindexes_legacy_threads_on_first_boot(tmp_path: Path) -> None:
    # Simulate an upgrade: per-thread files already exist, but no
    # session_index.jsonl does yet. First boot should reindex them.
    sess_root = tmp_path / "agents" / "coder" / "sessions"
    sess_root.mkdir(parents=True)
    (sess_root / "legacy.jsonl").write_text(
        json.dumps(
            {
                "op": "upsert",
                "thread_id": "legacy",
                "thread": {
                    "thread_id": "legacy",
                    "status": "idle",
                    "created_at": "2026-01-01T00:00:00Z",
                    "updated_at": "2026-01-01T00:00:00Z",
                    "metadata": {"agent": "coder"},
                    "values": {"title": "Hi", "messages": [], "artifacts": []},
                },
                "state": {
                    "values": {"title": "Hi", "messages": [], "artifacts": []},
                    "next": [],
                    "metadata": {"agent": "coder"},
                    "checkpoint": {"id": "x", "checkpoint_id": "x", "ts": "t"},
                    "checkpoint_id": "x",
                    "tasks": [],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store = ThreadStateStore(per_agent_base=tmp_path)

    idx_path = tmp_path / "data" / "sessions" / "session_index.jsonl"
    assert idx_path.exists()
    idx = SessionIndex(idx_path)
    assert "legacy" in idx
    assert idx.get("legacy").title == "Hi"
    assert idx.get("legacy").file.endswith("legacy.jsonl")
    assert store.get("legacy") is not None


# ─── index disabled / path mode ─────────────────────────────


def test_index_disabled_does_not_create_file(tmp_path: Path) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path, index_enabled=False)
    store.ensure_thread("t", metadata={"agent": "coder"})
    idx_path = tmp_path / "data" / "sessions" / "session_index.jsonl"
    assert not idx_path.exists()


def test_legacy_single_file_mode_still_supported(tmp_path: Path) -> None:
    path = tmp_path / "threads.jsonl"
    store = ThreadStateStore(path=path)
    store.ensure_thread("solo", values={"title": "Hi"})
    store.update_state("solo", values={"title": "Hi there"})
    assert path.exists()
    reloaded = ThreadStateStore(path=path)
    thread = reloaded.get("solo")
    assert thread is not None
    assert thread["values"]["title"] == "Hi there"
