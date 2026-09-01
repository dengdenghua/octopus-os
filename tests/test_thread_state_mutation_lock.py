from __future__ import annotations

import json
from pathlib import Path

from runtime.memory.threads import _state_mutation_lock as mutation_lock
from runtime.memory.threads._state_mutation_lock import (
    iter_jsonl_records_reverse,
    latest_persisted_thread,
)
from runtime.memory.threads.store import ThreadStateStore


def _record(thread_id: str, revision: int, title: str) -> dict[str, object]:
    thread = {
        "thread_id": thread_id,
        "updated_at": f"2026-08-29T00:00:{revision:02d}Z",
        "values": {"title": title, "messages": []},
        "metadata": {},
    }
    return {
        "op": "upsert",
        "thread_id": thread_id,
        "thread": thread,
        "state": {"values": thread["values"]},
        "revision": revision,
        "operation_at": thread["updated_at"],
    }


def test_reverse_jsonl_reader_handles_block_boundaries_and_bad_tail(tmp_path: Path) -> None:
    path = tmp_path / "thread.jsonl"
    records = [_record("thread-1", index, "回响" * 80) for index in range(1, 5)]
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records)
        + "\n{partial",
        encoding="utf-8",
    )

    found = list(iter_jsonl_records_reverse(path, block_size=37))

    assert [record["revision"] for record in found] == [4, 3, 2, 1]


def test_latest_persisted_thread_reads_from_tail_without_path_read_text(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "threads.jsonl"
    records = [_record("thread-1", index, f"title-{index}") for index in range(1, 4)]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    def fail_read_text(*_args, **_kwargs):
        raise AssertionError("hot-path lookup must not materialize the whole journal")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    persisted = latest_persisted_thread(
        journal_path=path,
        per_agent_base=None,
        thread_id="thread-1",
    )

    assert persisted.found is True
    assert persisted.revision == 3
    assert persisted.thread is not None
    assert persisted.thread["values"]["title"] == "title-3"


def test_latest_persisted_thread_uses_known_source_without_tree_scan(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "agents" / "general" / "sessions" / "thread-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_record("thread-1", 7, "latest")) + "\n")

    def fail_candidate_scan(*_args, **_kwargs):
        raise AssertionError("known canonical source must bypass the directory scan")

    monkeypatch.setattr(mutation_lock, "_candidate_paths", fail_candidate_scan)

    persisted = latest_persisted_thread(
        journal_path=None,
        per_agent_base=tmp_path,
        thread_id="thread-1",
        source_hint=path,
    )

    assert persisted.revision == 7
    assert persisted.source_path == path


def test_latest_persisted_thread_falls_back_when_source_hint_is_stale(
    tmp_path: Path,
) -> None:
    actual = tmp_path / "agents" / "general" / "sessions" / "thread-1.jsonl"
    actual.parent.mkdir(parents=True)
    actual.write_text(json.dumps(_record("thread-1", 9, "moved")) + "\n")

    persisted = latest_persisted_thread(
        journal_path=None,
        per_agent_base=tmp_path,
        thread_id="thread-1",
        source_hint=tmp_path / "data" / "sessions" / "misc" / "thread-1.jsonl",
    )

    assert persisted.revision == 9
    assert persisted.source_path == actual


def test_store_latest_thread_reader_uses_newest_valid_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "thread.jsonl"
    records = [_record("thread-1", index, f"title-{index}") for index in range(1, 4)]
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\nnot-json\n",
        encoding="utf-8",
    )

    thread = ThreadStateStore._latest_thread_from_file(path)

    assert thread is not None
    assert thread["values"]["title"] == "title-3"


def test_persistent_store_keeps_only_latest_state_in_memory_but_serves_history(
    tmp_path: Path,
) -> None:
    store = ThreadStateStore(per_agent_base=tmp_path)
    store.ensure_thread("thread-1", metadata={"agent": "general"})
    for index in range(1, 5):
        store.update_state("thread-1", values={"title": f"title-{index}"})

    assert len(store._history["thread-1"]) == 1
    assert (
        store._history["thread-1"][0]["values"]
        is store._threads["thread-1"]["values"]
    )
    path = tmp_path / "agents" / "general" / "sessions" / "thread-1.jsonl"
    latest_record = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert latest_record["state_from_thread"] is True
    assert "values" not in latest_record["state"]
    assert "metadata" not in latest_record["state"]
    assert [
        state["values"]["title"] for state in store.get_history("thread-1", limit=3)
    ] == ["title-4", "title-3", "title-2"]

    reloaded = ThreadStateStore(per_agent_base=tmp_path)
    assert len(reloaded._history["thread-1"]) == 1
    assert [
        state["values"]["title"] for state in reloaded.get_history("thread-1", limit=3)
    ] == ["title-4", "title-3", "title-2"]


def test_scoped_store_reloads_legacy_full_state_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "general" / "sessions" / "thread-1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(_record("thread-1", 4, "legacy")) + "\n")

    store = ThreadStateStore(
        per_agent_base=tmp_path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )

    assert store.get_state("thread-1")["values"]["title"] == "legacy"


def test_in_memory_store_preserves_full_checkpoint_history() -> None:
    store = ThreadStateStore()
    store.ensure_thread("thread-1")
    for index in range(1, 4):
        store.update_state("thread-1", values={"title": f"title-{index}"})

    assert len(store.get_history("thread-1", limit=0)) == 4


def test_scoped_store_reload_reads_only_latest_snapshot_from_tail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ThreadStateStore(
        per_agent_base=tmp_path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )
    store.ensure_thread("thread-1", metadata={"agent": "general"})
    for index in range(1, 8):
        store.update_state("thread-1", values={"title": f"title-{index}"})

    def fail_read_text(*_args, **_kwargs):
        raise AssertionError("scoped startup must not materialize full journals")

    monkeypatch.setattr(Path, "read_text", fail_read_text)
    reloaded = ThreadStateStore(
        per_agent_base=tmp_path,
        index_enabled=False,
        search_enabled=False,
        feedback_enabled=False,
    )

    assert reloaded.get("thread-1")["values"]["title"] == "title-7"
    assert len(reloaded._history["thread-1"]) == 1

