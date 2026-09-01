"""GroupStore persistence: ordered event log + thread-scoped shared blackboard."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier

import pytest

from runtime.memory.cowork.async_work import AsyncWorkStore
from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import (
    GroupRoomDeletingError,
    GroupRoomLinkConflict,
    GroupRoomLinkedError,
    GroupRoomLinkMigrationRequiredError,
    GroupStore,
    GroupThreadActiveWorkError,
    GroupThreadDeletingError,
    GroupThreadLinkedError,
)


def test_append_assigns_monotonic_seq_and_folds(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="user", target_kind="human")
    )
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    events = store.events("t1")
    assert [e.seq for e in events] == [1, 2]
    assert all(e.ts for e in events)  # store stamps timestamps
    state = store.state("t1")
    assert {m.id for m in state.roster} == {"user", "alice"}


def test_member_reference_retries_are_cross_store_idempotent(tmp_path) -> None:
    first = GroupStore(base_dir=tmp_path)
    second = GroupStore(base_dir=tmp_path)
    ready = Barrier(2)

    def add(store: GroupStore) -> bool:
        ready.wait(timeout=5)
        changed, _state = store.ensure_member(
            "thread-members",
            MemberEvent(
                action="invite",
                actor="owner",
                target_id="advisor",
                target_kind="agent",
            ),
        )
        return changed is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(add, (first, second)))

    assert sorted(outcomes) == [False, True]
    assert [event.action for event in first.events("thread-members")] == ["invite"]

    removed, state = first.remove_member_if_present(
        "thread-members",
        actor="owner",
        member_id="advisor",
    )
    assert removed is not None and state.member("advisor") is None
    removed_again, unchanged = second.remove_member_if_present(
        "thread-members",
        actor="owner",
        member_id="advisor",
    )
    assert removed_again is None and unchanged.event_count == 2


def test_threads_are_isolated(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    store.append(
        "t2", MemberEvent(action="invite", actor="u", target_id="bob", target_kind="agent")
    )
    assert {m.id for m in store.state("t1").roster} == {"alice"}
    assert {m.id for m in store.state("t2").roster} == {"bob"}
    # seq restarts per thread
    assert store.events("t2")[0].seq == 1


def test_shared_blackboard_is_thread_scoped_and_survives_leave(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )
    board = store.blackboard("t1")
    board.write("decision", "ship it", writer="alice")

    # A different thread sees its own (empty) board.
    assert store.blackboard_snapshot("t2") == {}
    assert store.blackboard_snapshot("t1")["decision"] == "ship it"

    # Remove alice — her blackboard write must remain (attributed).
    store.append("t1", MemberEvent(action="leave", actor="u", target_id="alice"))
    assert "alice" not in {m.id for m in store.state("t1").roster}
    assert store.blackboard_snapshot("t1")["decision"] == "ship it"


def test_state_survives_a_fresh_store_instance(tmp_path) -> None:
    GroupStore(base_dir=tmp_path).append(
        "t1", MemberEvent(action="mode", actor="u", mode="cluster")
    )
    # A new instance over the same dir reads the persisted log.
    assert GroupStore(base_dir=tmp_path).state("t1").mode == "cluster"


def test_link_room_if_absent_selects_one_cross_store_winner(tmp_path) -> None:
    first = GroupStore(base_dir=tmp_path)
    second = GroupStore(base_dir=tmp_path)
    ready = Barrier(2)

    def reserve(store: GroupStore, room_id: str):
        ready.wait(timeout=5)
        try:
            state, created = store.link_room_if_absent("thread-race", room_id, actor="owner")
            return ("winner", state.room_id, created)
        except GroupRoomLinkConflict as exc:
            return ("conflict", exc.current_room_id, False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: reserve(*item),
                [(first, "room-a"), (second, "room-b")],
            )
        )

    winners = [result for result in results if result[0] == "winner"]
    conflicts = [result for result in results if result[0] == "conflict"]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert conflicts[0][1] == winners[0][1]
    assert first.state("thread-race").room_id == winners[0][1]
    assert [event.action for event in first.events("thread-race")] == ["room_link"]

    retried, created = second.link_room_if_absent(
        "thread-race",
        str(winners[0][1]),
        actor="owner",
    )
    assert retried.room_id == winners[0][1]
    assert created is False


def test_link_room_if_absent_reserves_a_room_for_only_one_thread(tmp_path) -> None:
    first = GroupStore(base_dir=tmp_path)
    second = GroupStore(base_dir=tmp_path)
    ready = Barrier(2)

    def reserve(store: GroupStore, thread_id: str) -> tuple[str, str]:
        ready.wait(timeout=5)
        try:
            state, _created = store.link_room_if_absent(thread_id, "shared-room", actor="owner")
            return "winner", str(state.room_id)
        except GroupRoomLinkConflict:
            return "conflict", thread_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda item: reserve(*item),
                [(first, "thread-a"), (second, "thread-b")],
            )
        )

    winner_index = next(index for index, result in enumerate(results) if result[0] == "winner")
    winner_thread = ("thread-a", "thread-b")[winner_index]
    loser_thread = "thread-b" if winner_thread == "thread-a" else "thread-a"
    assert [outcome for outcome, _value in results].count("winner") == 1
    assert [outcome for outcome, _value in results].count("conflict") == 1
    assert first.state(winner_thread).room_id == "shared-room"
    assert first.state(loser_thread).room_id is None

    with pytest.raises(GroupRoomLinkConflict):
        second.append(
            loser_thread,
            MemberEvent(action="room_link", actor="bypass", target_id="shared-room"),
        )


def test_legacy_duplicate_room_links_fail_closed_without_deleting_events(tmp_path) -> None:
    GroupStore(base_dir=tmp_path)
    event = MemberEvent(action="room_link", actor="legacy", target_id="duplicate-room")
    with sqlite3.connect(str(tmp_path / "group_events.db")) as conn:
        conn.execute("DROP TRIGGER group_room_link_event_guard")
        conn.execute("DELETE FROM group_room_links")
        for thread_id in ("thread-a", "thread-b"):
            conn.execute(
                "INSERT INTO group_events(thread_id, seq, event_json, ts) VALUES (?, 1, ?, '0')",
                (thread_id, json.dumps(event.to_dict())),
            )

    with pytest.raises(GroupRoomLinkMigrationRequiredError) as raised:
        GroupStore(base_dir=tmp_path)

    assert raised.value.duplicates == {
        "duplicate-room": ("thread-a", "thread-b"),
    }
    with sqlite3.connect(str(tmp_path / "group_events.db")) as conn:
        assert conn.execute("SELECT COUNT(*) FROM group_events").fetchone() == (2,)


def test_room_delete_reservation_and_link_choose_one_cross_store_winner(tmp_path) -> None:
    first = GroupStore(base_dir=tmp_path)
    second = GroupStore(base_dir=tmp_path)
    ready = Barrier(2)

    def reserve_link() -> str:
        ready.wait(timeout=5)
        try:
            first.link_room_if_absent("thread-delete-race", "room-delete-race", actor="owner")
        except GroupRoomDeletingError:
            return "delete"
        return "link"

    def reserve_delete() -> str:
        ready.wait(timeout=5)
        try:
            second.begin_room_delete("room-delete-race")
        except GroupRoomLinkedError:
            return "link"
        return "delete"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(reserve_link), pool.submit(reserve_delete)]
        outcomes = [future.result(timeout=5) for future in results]

    assert outcomes[0] == outcomes[1]
    if outcomes[0] == "link":
        assert first.state("thread-delete-race").room_id == "room-delete-race"
        with pytest.raises(GroupRoomLinkedError):
            second.begin_room_delete("room-delete-race")
    else:
        lease = first.room_delete_lease("room-delete-race")
        assert lease is not None and lease.finalized is False
        with pytest.raises(GroupRoomDeletingError):
            second.link_room_if_absent(
                "thread-delete-race",
                "room-delete-race",
                actor="owner",
            )


def test_finalized_room_delete_permanently_rejects_late_link(tmp_path) -> None:
    first = GroupStore(base_dir=tmp_path)
    lease = first.begin_room_delete("room-permanently-deleted")
    assert first.finalize_room_delete(lease.room_id, lease.token) is True

    reopened = GroupStore(base_dir=tmp_path)
    finalized = reopened.room_delete_lease("room-permanently-deleted")
    assert finalized is not None and finalized.finalized is True
    with pytest.raises(GroupRoomDeletingError):
        reopened.link_room_if_absent(
            "thread-late-link",
            "room-permanently-deleted",
            actor="owner",
        )


def test_group_thread_delete_refuses_any_existing_group_state(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "thread-owned-group",
        MemberEvent(action="invite", actor="owner", target_id="worker"),
    )

    with pytest.raises(GroupThreadLinkedError) as raised:
        store.begin_thread_delete("thread-owned-group")

    assert raised.value.room_id is None
    assert store.thread_delete_lease("thread-owned-group") is None
    assert [event.action for event in store.events("thread-owned-group")] == ["invite"]


def test_group_thread_delete_claim_blocks_every_late_group_writer(tmp_path) -> None:
    deleting = GroupStore(base_dir=tmp_path)
    late = GroupStore(base_dir=tmp_path)
    lease = deleting.begin_thread_delete("thread-delete-claimed")

    with pytest.raises(GroupThreadDeletingError):
        late.append(
            "thread-delete-claimed",
            MemberEvent(action="invite", actor="late", target_id="worker"),
        )
    with pytest.raises(GroupThreadDeletingError):
        late.link_room_if_absent(
            "thread-delete-claimed",
            "room-too-late",
            actor="late",
        )

    assert deleting.finalize_thread_delete(lease.thread_id, lease.token) is True
    reopened = GroupStore(base_dir=tmp_path)
    finalized = reopened.thread_delete_lease("thread-delete-claimed")
    assert finalized is not None and finalized.finalized is True
    with pytest.raises(GroupThreadDeletingError):
        reopened.append(
            "thread-delete-claimed",
            MemberEvent(action="mode", actor="late", mode="chat"),
        )


def test_group_thread_delete_serializes_and_permanently_fences_blackboard(tmp_path) -> None:
    deleting = GroupStore(base_dir=tmp_path)
    late = GroupStore(base_dir=tmp_path)
    before = late.blackboard("thread-board-before-delete")
    before.write("visible", "before", writer="worker")

    lease = deleting.begin_thread_delete("thread-board-before-delete")
    with pytest.raises(GroupThreadDeletingError):
        before.write("late", "blocked", writer="worker")
    assert deleting.finalize_thread_delete(lease.thread_id, lease.token) is True
    assert late.blackboard_snapshot(lease.thread_id) == {}
    with pytest.raises(GroupThreadDeletingError):
        late.blackboard(lease.thread_id).write("resurrect", "blocked", writer="worker")


def test_blackboard_write_and_thread_delete_choose_one_serial_order(tmp_path) -> None:
    writer_store = GroupStore(base_dir=tmp_path)
    deleting_store = GroupStore(base_dir=tmp_path)
    board = writer_store.blackboard("thread-board-race")
    ready = Barrier(2)

    def write() -> str:
        ready.wait(timeout=5)
        try:
            board.write("result", "visible", writer="worker")
        except GroupThreadDeletingError:
            return "delete-first"
        return "write-first"

    def delete() -> None:
        ready.wait(timeout=5)
        lease = deleting_store.begin_thread_delete("thread-board-race")
        deleting_store.finalize_thread_delete(lease.thread_id, lease.token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        deletion = pool.submit(delete)
        outcome = writer.result(timeout=5)
        deletion.result(timeout=5)

    assert outcome in {"write-first", "delete-first"}
    assert writer_store.blackboard_snapshot("thread-board-race") == {}


@pytest.mark.parametrize("claimed", [False, True])
def test_group_thread_delete_refuses_pending_or_working_async_task(tmp_path, claimed) -> None:
    groups = GroupStore(base_dir=tmp_path)
    work = AsyncWorkStore(base_dir=tmp_path, group_store=groups)
    task = work.assign("thread-active-work", "worker", "finish me", actor="owner")
    if claimed:
        assert work.claim(task.task_id) is True

    with pytest.raises(GroupThreadActiveWorkError):
        groups.begin_thread_delete(task.thread_id)

    assert groups.thread_delete_lease(task.thread_id) is None
    assert work.get(task.task_id).status == ("working" if claimed else "pending")


def test_group_thread_delete_clears_terminal_async_state_and_fences_late_writers(
    tmp_path,
) -> None:
    groups = GroupStore(base_dir=tmp_path)
    work = AsyncWorkStore(base_dir=tmp_path, group_store=groups)
    task = work.assign("thread-terminal-work", "worker", "finish me", actor="owner")
    assert work.claim(task.task_id) is True
    assert work.complete(task.task_id, "done", blackboard_key="result") is True

    lease = groups.begin_thread_delete(task.thread_id)
    with pytest.raises(GroupThreadDeletingError):
        work.assign(task.thread_id, "late", "too late", actor="owner")
    with pytest.raises(GroupThreadDeletingError):
        groups.blackboard(task.thread_id).write("late", "blocked", writer="late")
    assert groups.finalize_thread_delete(task.thread_id, lease.token) is True

    assert work.get(task.task_id) is None
    assert groups.blackboard_snapshot(task.thread_id) == {}


def test_async_completion_and_blackboard_projection_rollback_together(
    tmp_path, monkeypatch
) -> None:
    groups = GroupStore(base_dir=tmp_path)
    work = AsyncWorkStore(base_dir=tmp_path, group_store=groups)
    task = work.assign("thread-atomic-complete", "worker", "finish me", actor="owner")
    assert work.claim(task.task_id) is True

    def fail_projection(*_args, **_kwargs) -> None:
        raise RuntimeError("board write failed")

    monkeypatch.setattr(work, "_write_board", fail_projection)
    with pytest.raises(RuntimeError, match="board write failed"):
        work.complete(task.task_id, "done", blackboard_key="result")

    assert work.get(task.task_id).status == "working"
    assert groups.blackboard_snapshot(task.thread_id) == {}


def test_async_completion_and_thread_delete_choose_one_serial_order(tmp_path) -> None:
    work_groups = GroupStore(base_dir=tmp_path)
    delete_groups = GroupStore(base_dir=tmp_path)
    work = AsyncWorkStore(base_dir=tmp_path, group_store=work_groups)
    task = work.assign("thread-complete-race", "worker", "finish me", actor="owner")
    assert work.claim(task.task_id) is True
    ready = Barrier(2)

    def complete() -> bool:
        ready.wait(timeout=5)
        return work.complete(task.task_id, "done", blackboard_key="result")

    def delete() -> bool:
        ready.wait(timeout=5)
        try:
            lease = delete_groups.begin_thread_delete(task.thread_id)
        except GroupThreadActiveWorkError:
            return False
        delete_groups.finalize_thread_delete(task.thread_id, lease.token)
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = pool.submit(complete)
        deleted = pool.submit(delete)
        # The stores deliberately allow SQLite up to ten seconds to serialize
        # attached-database writers.  Keep the test deadline above that
        # boundary so a heavily loaded full-suite run observes the resulting
        # value (or the real SQLite error) instead of timing out first.
        assert completed.result(timeout=15) is True
        delete_won = deleted.result(timeout=15)

    if delete_won:
        assert work.get(task.task_id) is None
        assert work_groups.blackboard_snapshot(task.thread_id) == {}
    else:
        assert work.get(task.task_id).status == "done"
        assert work_groups.blackboard_snapshot(task.thread_id) == {"result": "done"}


def test_group_atomic_databases_use_delete_journal_mode(tmp_path) -> None:
    groups = GroupStore(base_dir=tmp_path)
    AsyncWorkStore(base_dir=tmp_path, group_store=groups)

    for db_path in (
        groups.events_db_path,
        groups.board_db_path,
        tmp_path / "async_work.db",
    ):
        with closing(sqlite3.connect(str(db_path))) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)

    with work_connection(AsyncWorkStore(base_dir=tmp_path, group_store=groups)) as conn:
        for schema in ("main", "group_guard", "group_board"):
            assert conn.execute(f"PRAGMA {schema}.journal_mode").fetchone() == ("delete",)
            assert conn.execute(f"PRAGMA {schema}.synchronous").fetchone() == (2,)


def work_connection(work: AsyncWorkStore):
    """Expose one private test connection as a context manager."""

    return work._connect()  # noqa: SLF001 - validates attached journal invariants


def test_group_store_migrates_closed_legacy_wal_databases_before_use(tmp_path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = (
        tmp_path / "group_events.db",
        tmp_path / "group_blackboard.db",
        tmp_path / "async_work.db",
    )
    for db_path in paths:
        with closing(sqlite3.connect(str(db_path))) as conn:
            assert conn.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
            conn.execute("CREATE TABLE legacy_marker(value TEXT)")
            conn.commit()

    groups = GroupStore(base_dir=tmp_path)
    AsyncWorkStore(base_dir=tmp_path, group_store=groups)

    for db_path in paths:
        with closing(sqlite3.connect(str(db_path))) as conn:
            assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def test_group_store_refuses_journal_migration_while_legacy_wal_writer_is_live(
    tmp_path,
) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    board_db = tmp_path / "group_blackboard.db"
    legacy = sqlite3.connect(str(board_db), timeout=1.0)
    assert legacy.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    legacy.execute("CREATE TABLE legacy_lock(value TEXT)")
    legacy.commit()
    legacy.execute("BEGIN IMMEDIATE")
    legacy.execute("INSERT INTO legacy_lock(value) VALUES ('held')")
    try:
        with pytest.raises(RuntimeError, match="draining older WAL workers"):
            GroupStore(base_dir=tmp_path)
    finally:
        legacy.rollback()
        legacy.close()

    assert GroupStore(base_dir=tmp_path).events("thread-after-drain") == []


def test_group_delete_retry_migrates_board_after_legacy_wal_worker_drains(tmp_path) -> None:
    groups = GroupStore(base_dir=tmp_path)
    groups.blackboard("thread-wal-retry").write("before", "value", writer="worker")
    lease = groups.begin_thread_delete("thread-wal-retry")
    legacy = sqlite3.connect(str(groups.board_db_path), timeout=1.0)
    assert legacy.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
    legacy.execute("BEGIN IMMEDIATE")
    legacy.execute(
        "UPDATE blackboard SET updated_at=updated_at WHERE turn_id=?",
        (lease.thread_id,),
    )
    try:
        with pytest.raises(RuntimeError, match="draining older WAL workers"):
            groups.finalize_thread_delete(lease.thread_id, lease.token)
    finally:
        legacy.rollback()
        legacy.close()

    assert groups.finalize_thread_delete(lease.thread_id, lease.token) is True
    assert groups.blackboard_snapshot(lease.thread_id) == {}
    with closing(sqlite3.connect(str(groups.board_db_path))) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("delete",)


def test_legacy_project_write_is_persisted_as_chat(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)

    stored = store.append("t1", MemberEvent(action="mode", actor="old-ui", mode="project"))

    assert stored.mode == "chat"
    assert store.events("t1")[0].mode == "chat"
    assert store.state("t1").mode == "chat"


def test_unknown_response_mode_cannot_be_persisted(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)

    with pytest.raises(ValueError, match="chat.*cluster.*swarm"):
        store.append("t1", MemberEvent(action="mode", actor="ui", mode="bogus"))

    assert store.events("t1") == []


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows cannot delete an open sqlite file; removed-under-live-store is a POSIX-only scenario",
)
def test_store_recovers_if_base_dir_is_removed(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path / "cowork")
    shutil.rmtree(store.base_dir)

    assert store.events("missing-thread") == []

