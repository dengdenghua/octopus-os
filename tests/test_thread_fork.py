"""Thread fork tests — dsh ``sessions.fork`` port (completed-turn prefix)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.threads.session_title import SessionTitleService
from runtime.memory.threads.store import ForkUnavailableError, ThreadStateStore
from runtime.sensing.gateway.thread_state_router import create_thread_state_router


def _messages(*turn_specs: list[dict]) -> list[dict]:
    """Flatten turn specs; each spec starts with a human message."""
    out: list[dict] = []
    for spec in turn_specs:
        out.extend(spec)
    return out


H1 = {"type": "human", "content": "第一轮问题"}
A1 = {"type": "ai", "content": "第一轮回答", "tool_calls": [{"id": "t1"}]}
H2 = {"type": "human", "content": "第二轮问题"}
A2 = {"type": "ai", "content": "第二轮回答"}
H3 = {"type": "human", "content": "第三轮问题(未完成)"}


def _source() -> tuple[ThreadStateStore, str]:
    store = ThreadStateStore()
    thread = store.create(
        values={
            "title": "源标题",
            "messages": _messages([H1, A1], [H2, A2], [H3]),
        },
        metadata={"agent": "echo", "owner_actor_id": "alice"},
    )
    return store, thread["thread_id"]


def test_fork_without_anchor_excludes_open_last_turn() -> None:
    store, source_id = _source()
    child = store.fork_thread(source_id)
    seeded = child["values"]["messages"]
    assert seeded == [H1, A1, H2, A2]
    assert child["metadata"]["parent_thread_id"] == source_id
    assert child["metadata"]["parent_message_index"] == 3


def test_fork_empty_seed_when_only_open_turn() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"messages": [H3]})
    child = store.fork_thread(thread["thread_id"])
    assert child["values"]["messages"] == []
    assert child["metadata"]["parent_message_index"] == -1


def test_fork_empty_seed_without_messages() -> None:
    store = ThreadStateStore()
    thread = store.create()
    child = store.fork_thread(thread["thread_id"])
    assert child["values"]["messages"] == []


def test_fork_anchor_in_middle_turn_cuts_there() -> None:
    store, source_id = _source()
    # anchor = A1 (index 1) → seed through first turn only
    child = store.fork_thread(source_id, at_message_index=1)
    assert child["values"]["messages"] == [H1, A1]
    assert child["metadata"]["parent_message_index"] == 1


def test_fork_anchor_on_completed_last_turn_includes_whole_turn() -> None:
    store = ThreadStateStore()
    thread = store.create(values={"messages": _messages([H1, A1], [H2, A2])})
    # anchor = H2 (index 2) → the fork includes that whole turn
    child = store.fork_thread(thread["thread_id"], at_message_index=2)
    assert child["values"]["messages"] == [H1, A1, H2, A2]


def test_fork_anchor_on_open_turn_raises() -> None:
    store, source_id = _source()
    with pytest.raises(ForkUnavailableError):
        store.fork_thread(source_id, at_message_index=4)  # H3, no ai yet


def test_fork_out_of_range_anchor_falls_back_to_last_completed() -> None:
    store, source_id = _source()
    child = store.fork_thread(source_id, at_message_index=99)
    assert child["values"]["messages"] == [H1, A1, H2, A2]
    child2 = store.fork_thread(source_id, at_message_index=-5)
    assert child2["values"]["messages"] == [H1, A1, H2, A2]


def test_fork_inherits_metadata_and_title() -> None:
    store, source_id = _source()
    child = store.fork_thread(source_id)
    assert child["metadata"]["agent"] == "echo"
    assert child["metadata"]["owner_actor_id"] == "alice"
    assert child["values"]["title"] == "源标题"


def test_fork_does_not_inherit_project_binding_metadata() -> None:
    store, source_id = _source()
    source = store.set_project_binding_metadata(source_id, "P-source", generation=1)
    assert source["metadata"]["project_id"] == "P-source"

    child = store.fork_thread(source_id)

    assert "project_id" not in child["metadata"]
    assert "project_home" not in child["metadata"]
    assert "project_binding_generation" not in child["metadata"]
    assert child["metadata"]["agent"] == "echo"
    assert child["metadata"]["parent_thread_id"] == source_id


def test_fork_title_override() -> None:
    store, source_id = _source()
    child = store.fork_thread(source_id, title="  分支会话 ")
    assert child["values"]["title"] == "分支会话"


def test_fork_deep_copies_messages() -> None:
    store, source_id = _source()
    child = store.fork_thread(source_id)
    child["values"]["messages"][0]["content"] = "mutated"
    source = store.get(source_id)
    assert source["values"]["messages"][0]["content"] == "第一轮问题"


def test_fork_unknown_thread_raises() -> None:
    with pytest.raises(KeyError):
        ThreadStateStore().fork_thread("missing")


def test_fork_round_trip_survives_store_reload() -> None:
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "threads.jsonl"
        store = ThreadStateStore(path=path)
        source_id = store.create(values={"messages": _messages([H1, A1], [H2, A2])})["thread_id"]
        child = store.fork_thread(source_id)
        child_id = child["thread_id"]
        reloaded = ThreadStateStore(path=path)
        assert reloaded.get(child_id)["metadata"]["parent_thread_id"] == source_id
        assert len(reloaded.get(child_id)["values"]["messages"]) == 4


# ─── HTTP layer ─────────────────────────────────────────────


def _client(store: ThreadStateStore) -> TestClient:
    app = FastAPI()
    app.include_router(create_thread_state_router(store=store))
    return TestClient(app)


def test_fork_endpoint_returns_new_thread() -> None:
    store = ThreadStateStore()
    source_id = store.create(values={"messages": _messages([H1, A1], [H2, A2])})["thread_id"]
    client = _client(store)
    response = client.post(f"/api/threads/{source_id}/fork", json={})
    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] != source_id
    assert payload["seeded_messages"] == 4


def test_fork_endpoint_anchor_index() -> None:
    store = ThreadStateStore()
    source_id = store.create(values={"messages": _messages([H1, A1], [H2, A2])})["thread_id"]
    client = _client(store)
    response = client.post(f"/api/threads/{source_id}/fork", json={"at_message_index": 0})
    assert response.status_code == 200
    assert response.json()["seeded_messages"] == 2


def test_fork_endpoint_open_turn_conflict() -> None:
    store = ThreadStateStore()
    source_id = store.create(values={"messages": [H3]})["thread_id"]
    client = _client(store)
    response = client.post(f"/api/threads/{source_id}/fork", json={"at_message_index": 0})
    assert response.status_code == 409
    assert response.json()["detail"] == "fork-unavailable"


def test_fork_endpoint_validation_and_missing() -> None:
    store = ThreadStateStore()
    source_id = store.create(values={"messages": [H1, A1]})["thread_id"]
    client = _client(store)
    assert (
        client.post(f"/api/threads/{source_id}/fork", json={"at_message_index": "x"}).status_code
        == 400
    )
    assert client.post("/api/threads/missing/fork", json={}).status_code == 404


def test_forked_thread_title_service_reads_child() -> None:
    store = ThreadStateStore()
    source_id = store.create(values={"title": "源标题", "messages": _messages([H1, A1])})[
        "thread_id"
    ]
    child = store.fork_thread(source_id)
    snapshot = SessionTitleService(store).get(child["thread_id"])
    assert snapshot.title == "源标题"

