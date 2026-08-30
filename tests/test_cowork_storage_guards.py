"""Cowork storage guardrails for collaboration/session ids and message bounds."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from runtime.memory.cowork._collaboration_room_write import ProjectRoomVersionConflict
from runtime.memory.cowork.collaboration_store import CollaborationStore
from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.ids import MAX_COWORK_MESSAGE_TEXT_LENGTH
from runtime.memory.cowork.presence import PresenceStore
from runtime.memory.cowork.room_messages import RoomMessageStore


def test_group_store_rejects_invalid_thread_and_member_ids(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="thread_id"):
        store.append("../escape", MemberEvent(action="invite", actor="u", target_id="alice"))

    with pytest.raises(ValueError, match="target_id"):
        store.append("thread-1", MemberEvent(action="invite", actor="u", target_id="../agent"))


def test_room_messages_reject_invalid_ids_and_oversized_text(tmp_path) -> None:
    store = RoomMessageStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="room_id"):
        store.append("room/escape", text="hello")

    with pytest.raises(ValueError, match="participant_id"):
        store.append("room-1", text="hello", participant_id="bad participant")

    with pytest.raises(ValueError, match="text"):
        store.append("room-1", text="x" * (MAX_COWORK_MESSAGE_TEXT_LENGTH + 1))


def test_presence_store_rejects_invalid_ids_and_negative_cursor(tmp_path) -> None:
    store = PresenceStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="member_id"):
        store.heartbeat("thread-1", "bad/member")

    with pytest.raises(ValueError, match="position"):
        store.mark_read("thread-1", "alice", -1)


def test_collaboration_store_rejects_invalid_room_task_and_participant_ids(tmp_path) -> None:
    store = CollaborationStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="room_id"):
        store.upsert_room("thread-1", {"id": "../room"})

    store.upsert_room("thread-1", {"id": "room-1"})

    with pytest.raises(ValueError, match="task_id"):
        store.upsert_task("thread-1", {"id": "task/1", "room_id": "room-1"})

    with pytest.raises(ValueError, match="participant_id"):
        store.append_message(
            "thread-1",
            room_id="room-1",
            text="hello",
            participant_id="bad participant",
        )


def test_collaboration_store_keeps_compatible_email_style_ids(tmp_path) -> None:
    store = CollaborationStore(base_dir=tmp_path)
    store.upsert_room("oct:alice@example.com", {"id": "room-1"})
    seq = store.append_message(
        "oct:alice@example.com",
        room_id="room-1",
        text="hello",
        participant_id="oct:bob@example.com",
    )

    assert seq == 1
    assert store.session_id_for_room("room-1") == "oct:alice@example.com"


def test_collaboration_store_skips_corrupt_json_rows(tmp_path) -> None:
    store = CollaborationStore(base_dir=tmp_path)
    store.upsert_room("thread-1", {"id": "room-1", "name": "Room"})
    store.upsert_task(
        "thread-1",
        {
            "id": "task-ok",
            "room_id": "room-1",
            "title": "Keep this task",
            "created_at": "t0",
            "updated_at": "t0",
        },
    )

    with sqlite3.connect(str(tmp_path / "collaboration.db")) as conn:
        conn.execute(
            "UPDATE collaboration_rooms SET room_json = ? WHERE session_id = ?",
            ("{not-json", "thread-1"),
        )
        conn.execute(
            "INSERT INTO collaboration_tasks("
            "task_id, session_id, room_id, status, task_json, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("task-bad", "thread-1", "room-1", "pending", "{not-json", "t1", "t1"),
        )

    assert store.room_for_session("thread-1") is None
    assert store.room_by_id("room-1") is None
    assert [task["id"] for task in store.tasks_for_session("thread-1")] == ["task-ok"]
    assert [task["id"] for task in store.tasks_for_room("room-1")] == ["task-ok"]


def test_collaboration_store_normalizes_payload_boundaries(tmp_path) -> None:
    store = CollaborationStore(base_dir=tmp_path)

    room = store.upsert_room(
        "thread-1",
        {
            "id": "room-1",
            "name": "  Launch Room  ",
            "members": [{"id": f"m{i}"} for i in range(520)] + ["drop-me"],
            "participants": "not-a-list",
        },
    )
    assert room["name"] == "Launch Room"
    assert len(room["members"]) == 512
    assert room["participants"] == []

    task = store.upsert_task(
        "thread-1",
        {
            "id": "task-1",
            "room_id": "room-1",
            "title": "  Ship plan  ",
            "description": "   ",
            "status": "surprising",
            "metadata": "not-a-dict",
            "assignees": [{"kind": "agent", "ref": "planner"}, "drop-me"],
            "produced_artifacts": [{"ok": True}, "drop-me"],
        },
    )
    assert task["title"] == "Ship plan"
    assert task["description"] == ""
    assert task["status"] == "pending"
    assert task["metadata"] == {
        "collab_session_id": "thread-1",
        "source": "collab_session",
    }
    assert task["assignees"] == [{"kind": "agent", "ref": "planner"}]
    assert task["produced_artifacts"] == [{"ok": True}]

    with pytest.raises(ValueError, match="room"):
        store.upsert_room("thread-2", {"id": "room-big", "blob": "x" * 600_000})
    with pytest.raises(ValueError, match="task"):
        store.upsert_task(
            "thread-1",
            {"id": "task-big", "room_id": "room-1", "title": "Big", "blob": "x" * 600_000},
        )


@pytest.mark.parametrize("write_kind", ["message", "task"])
def test_for_room_writes_reject_a_room_that_moved_after_lookup(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    write_kind: str,
) -> None:
    stale = CollaborationStore(base_dir=tmp_path)
    winner = CollaborationStore(base_dir=tmp_path)
    stale.upsert_room("team:room-race", {"id": "room-race", "name": "Before"})
    resolved = Event()
    release = Event()
    real_session_id_for_room = stale.session_id_for_room

    def paused_session_id_for_room(room_id: str) -> str | None:
        session_id = real_session_id_for_room(room_id)
        resolved.set()
        assert release.wait(timeout=5)
        return session_id

    monkeypatch.setattr(stale, "session_id_for_room", paused_session_id_for_room)

    def write() -> object:
        if write_kind == "message":
            return stale.append_message_for_room("room-race", text="late message")
        return stale.upsert_task_for_room(
            "room-race",
            {"id": "late-task", "title": "Late task"},
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(write)
        assert resolved.wait(timeout=5)
        winner.upsert_room("thread-new", {"id": "room-race", "name": "After"})
        release.set()
        with pytest.raises(ProjectRoomVersionConflict, match="moved"):
            future.result(timeout=5)

    assert winner.room_for_session("team:room-race") is None
    assert winner.room_for_session("thread-new")["id"] == "room-race"
    assert winner.messages_for_session("team:room-race") == []
    assert winner.messages_for_session("thread-new") == []
    assert winner.tasks_for_session("team:room-race") == []
    assert winner.tasks_for_session("thread-new") == []


def test_project_projection_tombstone_clears_standalone_room_and_fences_late_writes(
    tmp_path,
) -> None:
    store = CollaborationStore(base_dir=tmp_path)
    store.upsert_project_room(
        session_id="project:P-deleted",
        room={"id": "project:P-deleted", "name": "Standalone"},
        project_id="P-deleted",
        generation=0,
    )
    store.upsert_project_task(
        session_id="project:P-deleted",
        room_id="project:P-deleted",
        project_id="P-deleted",
        milestone_id="M-deleted",
        task={"id": "T-deleted", "title": "Old projection"},
        binding_generation=0,
    )

    store.tombstone_project_projection("P-deleted", "PD-delete")

    room = store.room_for_session("project:P-deleted")
    assert room is not None
    assert room["project_id"] is None
    assert room["metadata"]["project_binding_generation"] == 1
    assert store.tasks_for_session("project:P-deleted") == []
    with pytest.raises(RuntimeError, match="projection was deleted"):
        store.upsert_project_room(
            session_id="project:P-deleted",
            room={"id": "project:P-deleted"},
            project_id="P-deleted",
            generation=0,
        )
    with pytest.raises(RuntimeError, match="projection was deleted"):
        store.upsert_project_task(
            session_id="project:P-deleted",
            room_id="project:P-deleted",
            project_id="P-deleted",
            milestone_id="M-deleted",
            task={"id": "T-late", "title": "Late projection"},
            binding_generation=0,
        )


def test_project_room_cannot_appear_after_a_no_room_delete_tombstone(tmp_path) -> None:
    deleting = CollaborationStore(base_dir=tmp_path)
    stale = CollaborationStore(base_dir=tmp_path)
    ready = Event()
    release = Event()

    def late_projection() -> object:
        ready.set()
        assert release.wait(timeout=5)
        return stale.upsert_project_room(
            session_id="project:P-no-room",
            room={"id": "project:P-no-room"},
            project_id="P-no-room",
            generation=0,
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(late_projection)
        assert ready.wait(timeout=5)
        deleting.tombstone_project_projection("P-no-room", "PD-no-room")
        release.set()
        with pytest.raises(RuntimeError, match="projection was deleted"):
            future.result(timeout=5)

    assert deleting.room_for_session("project:P-no-room") is None

