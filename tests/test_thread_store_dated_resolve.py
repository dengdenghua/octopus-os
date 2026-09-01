"""Regression: dated-layout thread file resolution must pick the LATEST month.

When a thread has files in several dated dirs (touched across months), the
resolver must return the newest — the old ``hits[0]`` returned whatever
``rglob`` yielded first (filesystem-arbitrary), which could be a stale month for
both reads and appends.
"""

from __future__ import annotations

import json

from runtime.memory.threads.store import ThreadStateStore


def _session_record(thread_id: str, title: str, updated_at: str) -> str:
    thread = {
        "thread_id": thread_id,
        "status": "idle",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": updated_at,
        "metadata": {"agent": "coder"},
        "values": {"title": title, "messages": [], "artifacts": []},
    }
    state = {
        "values": thread["values"],
        "next": [],
        "metadata": thread["metadata"],
        "checkpoint": {"id": title, "checkpoint_id": title, "ts": updated_at},
        "checkpoint_id": title,
        "tasks": [],
    }
    return (
        json.dumps({"type": "session_meta", "payload": {"id": thread_id}})
        + "\n"
        + json.dumps(
            {
                "op": "upsert",
                "thread_id": thread_id,
                "thread": thread,
                "state": state,
            }
        )
        + "\n"
    )


def test_resolve_thread_file_picks_latest_dated_month(tmp_path):
    store = ThreadStateStore(per_agent_base=tmp_path / "base", dated_layout=True)
    sess_root = tmp_path / "sess"
    # Create out of order on purpose; correctness must not depend on order.
    for ym in ("2026/05", "2026/04", "2026/06"):
        d = sess_root / ym
        d.mkdir(parents=True)
        (d / "t.jsonl").write_text("{}\n", encoding="utf-8")

    chosen = store._resolve_thread_file(sess_root, "t")
    assert chosen == sess_root / "2026" / "06" / "t.jsonl"


def test_resolve_thread_file_flat_still_wins(tmp_path):
    # An existing flat file short-circuits (upgrade flat→dated never orphans it).
    store = ThreadStateStore(per_agent_base=tmp_path / "base", dated_layout=True)
    sess_root = tmp_path / "sess"
    sess_root.mkdir(parents=True)
    (sess_root / "t.jsonl").write_text("{}\n", encoding="utf-8")
    (sess_root / "2026" / "06").mkdir(parents=True)
    (sess_root / "2026" / "06" / "t.jsonl").write_text("{}\n", encoding="utf-8")

    assert store._resolve_thread_file(sess_root, "t") == sess_root / "t.jsonl"


def test_load_per_agent_tree_uses_latest_dated_file(tmp_path):
    sess_root = tmp_path / "base" / "agents" / "coder" / "sessions"
    old_dir = sess_root / "2026" / "05"
    new_dir = sess_root / "2026" / "06"
    new_dir.mkdir(parents=True)
    old_dir.mkdir(parents=True)
    # Create newest first so filesystem traversal order cannot make the
    # assertion pass by accident.
    (new_dir / "t.jsonl").write_text(
        _session_record("t", "new-title", "2026-06-01T00:00:00Z"),
        encoding="utf-8",
    )
    (old_dir / "t.jsonl").write_text(
        _session_record("t", "old-title", "2026-05-01T00:00:00Z"),
        encoding="utf-8",
    )

    store = ThreadStateStore(per_agent_base=tmp_path / "base", dated_layout=True)

    assert store.get("t")["values"]["title"] == "new-title"


def test_load_per_agent_tree_flat_file_wins_over_dated_file(tmp_path):
    sess_root = tmp_path / "base" / "agents" / "coder" / "sessions"
    dated_dir = sess_root / "2026" / "06"
    dated_dir.mkdir(parents=True)
    sess_root.mkdir(parents=True, exist_ok=True)
    (dated_dir / "t.jsonl").write_text(
        _session_record("t", "dated-title", "2026-06-01T00:00:00Z"),
        encoding="utf-8",
    )
    (sess_root / "t.jsonl").write_text(
        _session_record("t", "flat-title", "2026-04-01T00:00:00Z"),
        encoding="utf-8",
    )

    store = ThreadStateStore(per_agent_base=tmp_path / "base", dated_layout=True)

    assert store.get("t")["values"]["title"] == "flat-title"

