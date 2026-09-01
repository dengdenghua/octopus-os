"""Persistence for the thread-group model: an append-only membership event log
plus the thread-scoped shared blackboard.

The membership log is sqlite (ordered, append-only, multi-process safe via an
atomic ``seq`` computed inside the INSERT). The shared blackboard reuses the
existing ``SqliteBlackboard`` — it already persists with per-key writer
attribution + audit; we simply namespace it by ``thread_id`` instead of
``turn_id``, which is the whole "promote the per-turn board to a group board"
change. No new storage engine.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from runtime.memory.cowork.group import (
    VALID_MODES,
    ContextGrant,
    GroupState,
    MemberEvent,
    fold_state,
    normalize_group_mode,
)
from runtime.memory.cowork.ids import (
    normalize_actor_id,
    optional_cowork_id,
    require_cowork_id,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_events (
    thread_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    event_json  TEXT NOT NULL,
    ts          TEXT NOT NULL,
    PRIMARY KEY (thread_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_group_events_thread ON group_events(thread_id);
CREATE TABLE IF NOT EXISTS group_room_links (
    thread_id  TEXT PRIMARY KEY,
    room_id    TEXT NOT NULL UNIQUE,
    actor      TEXT NOT NULL,
    linked_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_room_delete_claims (
    room_id     TEXT PRIMARY KEY,
    token       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    owner_id    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS group_room_delete_tombstones (
    room_id     TEXT PRIMARY KEY,
    token       TEXT NOT NULL,
    deleted_at  REAL NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    owner_id    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS group_thread_delete_claims (
    thread_id   TEXT PRIMARY KEY,
    token       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    owner_id    TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS group_thread_delete_tombstones (
    thread_id   TEXT PRIMARY KEY,
    token       TEXT NOT NULL,
    deleted_at  REAL NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT '',
    owner_id    TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS group_room_link_event_guard
BEFORE INSERT ON group_events
WHEN json_extract(NEW.event_json, '$.action') = 'room_link'
  AND NOT EXISTS (
      SELECT 1 FROM group_room_links
      WHERE thread_id=NEW.thread_id
        AND room_id=json_extract(NEW.event_json, '$.target_id')
  )
BEGIN SELECT RAISE(ABORT, 'room link reservation required'); END;
CREATE TRIGGER IF NOT EXISTS group_thread_event_delete_guard
BEFORE INSERT ON group_events
WHEN EXISTS (
    SELECT 1 FROM group_thread_delete_claims WHERE thread_id=NEW.thread_id
    UNION ALL SELECT 1 FROM group_thread_delete_tombstones WHERE thread_id=NEW.thread_id
)
BEGIN SELECT RAISE(ABORT, 'group thread is deleting'); END;
CREATE TRIGGER IF NOT EXISTS group_thread_room_link_delete_guard
BEFORE INSERT ON group_room_links
WHEN EXISTS (
    SELECT 1 FROM group_thread_delete_claims WHERE thread_id=NEW.thread_id
    UNION ALL SELECT 1 FROM group_thread_delete_tombstones WHERE thread_id=NEW.thread_id
)
BEGIN SELECT RAISE(ABORT, 'group thread is deleting'); END;
CREATE TRIGGER IF NOT EXISTS group_thread_room_link_update_delete_guard
BEFORE UPDATE ON group_room_links
WHEN EXISTS (
    SELECT 1 FROM group_thread_delete_claims WHERE thread_id=NEW.thread_id
    UNION ALL SELECT 1 FROM group_thread_delete_tombstones WHERE thread_id=NEW.thread_id
)
BEGIN SELECT RAISE(ABORT, 'group thread is deleting'); END;
"""


class GroupRoomLinkConflict(RuntimeError):
    """A collaboration thread already reserved a different Team Room."""

    def __init__(self, thread_id: str, current_room_id: str, requested_room_id: str) -> None:
        self.thread_id = thread_id
        self.current_room_id = current_room_id
        self.requested_room_id = requested_room_id
        super().__init__(
            f"collaboration thread {thread_id!r} is already linked to room {current_room_id!r}"
        )


class GroupRoomLinkMigrationRequiredError(RuntimeError):
    """Legacy event rows claim one room for multiple collaboration threads."""

    def __init__(self, duplicates: dict[str, tuple[str, ...]]) -> None:
        self.duplicates = duplicates
        super().__init__("legacy room links require an explicit migration")


class GroupRoomLinkedError(RuntimeError):
    """A Team Room delete lost to a canonical collaboration reservation."""

    def __init__(self, room_id: str, thread_id: str) -> None:
        self.room_id = room_id
        self.thread_id = thread_id
        super().__init__(f"room is linked to collaboration thread: {room_id} -> {thread_id}")


class GroupRoomDeletingError(RuntimeError):
    """A collaboration link attempted to reuse a deleting/deleted room."""

    def __init__(self, room_id: str) -> None:
        self.room_id = room_id
        super().__init__(f"room delete in progress: {room_id}")


class GroupThreadLinkedError(RuntimeError):
    """A thread still owns canonical group state and cannot be deleted."""

    def __init__(self, thread_id: str, room_id: str | None = None) -> None:
        self.thread_id = thread_id
        self.room_id = room_id
        super().__init__(f"thread still owns collaboration group state: {thread_id}")


class GroupThreadDeletingError(RuntimeError):
    """A normal group writer targeted a deleting/deleted thread."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"group thread delete in progress: {thread_id}")


class GroupThreadActiveWorkError(RuntimeError):
    """A thread has pending or executing async work and cannot be deleted."""

    def __init__(self, thread_id: str) -> None:
        self.thread_id = thread_id
        super().__init__(f"group thread has active async work: {thread_id}")


@dataclass(slots=True, frozen=True)
class GroupRoomDeleteLease:
    room_id: str
    token: str
    resumed: bool
    finalized: bool
    tenant_id: str = ""
    owner_id: str = ""


@dataclass(slots=True, frozen=True)
class GroupThreadDeleteLease:
    thread_id: str
    token: str
    resumed: bool
    finalized: bool
    tenant_id: str = ""
    owner_id: str = ""


def _room_delete_scope_allowed(
    stored_tenant_id: object,
    stored_owner_id: object,
    *,
    tenant_id: str,
    owner_id: str,
) -> bool:
    if not tenant_id and not owner_id:
        return True
    return (
        str(stored_tenant_id or "").strip() == tenant_id
        and str(stored_owner_id or "").strip() == owner_id
    )


def _thread_delete_scope_allowed(
    stored_tenant_id: object,
    stored_owner_id: object,
    *,
    tenant_id: str,
    owner_id: str,
) -> bool:
    return _room_delete_scope_allowed(
        stored_tenant_id,
        stored_owner_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
    )


def _thread_delete_exists(conn: sqlite3.Connection, thread_id: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM group_thread_delete_claims WHERE thread_id=? "
            "UNION ALL SELECT 1 FROM group_thread_delete_tombstones "
            "WHERE thread_id=? LIMIT 1",
            (thread_id, thread_id),
        ).fetchone()
        is not None
    )


def _default_dir() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "cowork"


class GroupStore:
    """Append-only membership events + the thread-scoped shared blackboard."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._dir = Path(base_dir) if base_dir else _default_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._events_db = self._dir / "group_events.db"
        self._board_db = self._dir / "group_blackboard.db"
        self._lock = threading.Lock()
        from ._group_sqlite_coordination import migrate_delete_journals

        migrate_delete_journals((self._events_db, self._board_db, self._dir / "async_work.db"))
        self._ensure_schema()

    @property
    def base_dir(self) -> Path:
        return self._dir

    @property
    def events_db_path(self) -> Path:
        return self._events_db

    @property
    def board_db_path(self) -> Path:
        return self._board_db

    def _connect(self) -> sqlite3.Connection:
        self._dir.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._events_db), timeout=10.0)
        # DELETE mode is a correctness boundary: thread deletion atomically
        # commits across this DB plus the same-directory async/board DBs.
        # SQLite does not guarantee attached-database atomicity under WAL;
        # deployments switching from older WAL workers must drain them first.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript(_SCHEMA)
        return conn

    def ensure_storage(self) -> None:
        """Recreate the durable delete guard after external directory loss."""

        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT thread_id, event_json, seq, ts FROM group_events ORDER BY thread_id, seq"
            ).fetchall()
            by_thread: dict[str, list[MemberEvent]] = {}
            for thread_id, event_json, seq, ts in rows:
                event = MemberEvent.from_dict(_load(event_json))
                event.seq = int(seq)
                event.ts = str(ts)
                by_thread.setdefault(str(thread_id), []).append(event)
            room_threads: dict[str, list[str]] = {}
            current_links: dict[str, str] = {}
            for thread_id, events in by_thread.items():
                room_id = fold_state(events).room_id
                if not room_id:
                    continue
                current_links[thread_id] = room_id
                room_threads.setdefault(room_id, []).append(thread_id)
            duplicates = {
                room_id: tuple(sorted(thread_ids))
                for room_id, thread_ids in room_threads.items()
                if len(thread_ids) > 1
            }
            if duplicates:
                raise GroupRoomLinkMigrationRequiredError(duplicates)
            for thread_id, room_id in current_links.items():
                existing = conn.execute(
                    "SELECT room_id FROM group_room_links WHERE thread_id=?",
                    (thread_id,),
                ).fetchone()
                if existing is not None and str(existing[0]) != room_id:
                    raise GroupRoomLinkMigrationRequiredError(
                        {str(existing[0]): (thread_id,), room_id: (thread_id,)}
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO group_room_links(thread_id, room_id, actor, linked_at) "
                    "VALUES (?, ?, 'legacy-migration', ?)",
                    (thread_id, room_id, datetime.now(UTC).isoformat()),
                )

    # ── membership events ────────────────────────────────────────────────────
    def append(self, thread_id: str, event: MemberEvent) -> MemberEvent:
        """Append a membership/mode event, stamping ``seq`` + ``ts``. The next
        ``seq`` is computed inside the INSERT so concurrent appends never collide."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        event.actor = normalize_actor_id(event.actor)
        event.target_id = optional_cowork_id(event.target_id, label="target_id")
        if event.action == "room_link":
            if not event.target_id:
                raise ValueError("room_link target_id is required")
            self.link_room_if_absent(
                thread_id,
                event.target_id,
                actor=event.actor,
            )
            linked = next(
                (
                    candidate
                    for candidate in reversed(self.events(thread_id))
                    if candidate.action == "room_link" and candidate.target_id == event.target_id
                ),
                None,
            )
            if linked is None:  # pragma: no cover - guarded by the same transaction
                raise RuntimeError("room link reservation committed without its event")
            return linked
        if event.action == "mode":
            normalized_mode = normalize_group_mode(event.mode)
            if normalized_mode is None:
                raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
            event.mode = normalized_mode
        event.ts = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            cur = conn.execute(
                "INSERT INTO group_events(thread_id, seq, event_json, ts) "
                "VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM group_events "
                "WHERE thread_id = ?), ?, ?) RETURNING seq",
                (thread_id, thread_id, _dump(event), event.ts),
            )
            row = cur.fetchone()
            event.seq = int(row[0]) if row else 0
        return event

    def ensure_member(
        self,
        thread_id: str,
        event: MemberEvent,
    ) -> tuple[MemberEvent | None, GroupState]:
        """Add one session member exactly once and return the canonical fold.

        Membership is a reference to an existing actor/agent id, not a cloned
        identity.  The folded read and conditional append share one SQLite
        write transaction so retries (including retries from another process)
        cannot create duplicate timeline events.
        """

        if event.action != "invite":
            raise ValueError("ensure_member requires an invite event")
        thread_id = require_cowork_id(thread_id, label="thread_id")
        event.actor = normalize_actor_id(event.actor)
        event.target_id = require_cowork_id(event.target_id, label="target_id")
        event.ts = datetime.now(UTC).isoformat()

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            rows = conn.execute(
                "SELECT event_json, seq, ts FROM group_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
            existing_events: list[MemberEvent] = []
            for event_json, seq, ts in rows:
                existing = MemberEvent.from_dict(_load(event_json))
                existing.seq = int(seq)
                existing.ts = str(ts)
                existing_events.append(existing)
            current = fold_state(existing_events)
            member = current.member(event.target_id)
            if member is not None:
                if member.kind != event.target_kind:
                    raise ValueError(
                        f"member kind collision for {event.target_id}: "
                        f"{member.kind} != {event.target_kind}"
                    )
                return None, current

            event.seq = int(rows[-1][1]) + 1 if rows else 1
            conn.execute(
                "INSERT INTO group_events(thread_id, seq, event_json, ts) VALUES (?, ?, ?, ?)",
                (thread_id, event.seq, _dump(event), event.ts),
            )
            return event, fold_state([*existing_events, event])

    def remove_member_if_present(
        self,
        thread_id: str,
        *,
        actor: str,
        member_id: str,
    ) -> tuple[MemberEvent | None, GroupState]:
        """Remove one session member exactly once.

        A retry after the member has already left is a successful no-op and
        does not grow the event log.  ACL is intentionally outside this store;
        callers must authorize against the owning thread/room before invoking
        the mutation.
        """

        thread_id = require_cowork_id(thread_id, label="thread_id")
        actor = normalize_actor_id(actor)
        member_id = require_cowork_id(member_id, label="member_id")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            rows = conn.execute(
                "SELECT event_json, seq, ts FROM group_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
            existing_events: list[MemberEvent] = []
            for event_json, seq, ts in rows:
                existing = MemberEvent.from_dict(_load(event_json))
                existing.seq = int(seq)
                existing.ts = str(ts)
                existing_events.append(existing)
            current = fold_state(existing_events)
            if current.member(member_id) is None:
                return None, current

            event = MemberEvent(action="leave", actor=actor, target_id=member_id)
            event.seq = int(rows[-1][1]) + 1 if rows else 1
            event.ts = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO group_events(thread_id, seq, event_json, ts) VALUES (?, ?, ?, ?)",
                (thread_id, event.seq, _dump(event), event.ts),
            )
            return event, fold_state([*existing_events, event])

    def replace_agent_roster(
        self,
        thread_id: str,
        *,
        actor: str,
        agent_ids: list[str],
        mode: str,
    ) -> tuple[list[MemberEvent], GroupState]:
        """Atomically reconcile the agent roster and collaboration mode.

        The current fold, diff calculation, and all resulting event inserts run
        under one ``BEGIN IMMEDIATE`` transaction. Human members are preserved;
        the supplied ids are the complete desired *agent* roster. Returning the
        post-transaction fold lets callers replace optimistic UI state with the
        canonical server state in one response.
        """

        thread_id = require_cowork_id(thread_id, label="thread_id")
        actor = normalize_actor_id(actor)
        normalized_mode = normalize_group_mode(mode)
        if normalized_mode is None:
            raise ValueError(f"mode must be one of {sorted(VALID_MODES)}")
        mode = normalized_mode

        desired: list[str] = []
        seen: set[str] = set()
        for raw_id in agent_ids:
            member_id = require_cowork_id(raw_id, label="agent_id")
            if member_id in seen:
                continue
            seen.add(member_id)
            desired.append(member_id)

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            rows = conn.execute(
                "SELECT event_json, seq, ts FROM group_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
            existing_events: list[MemberEvent] = []
            for event_json, seq, ts in rows:
                event = MemberEvent.from_dict(_load(event_json))
                event.seq = int(seq)
                event.ts = str(ts)
                existing_events.append(event)
            current = fold_state(existing_events)
            current_by_id = {member.id: member for member in current.roster}

            events: list[MemberEvent] = []
            desired_set = set(desired)
            for member in current.roster:
                if member.kind == "agent" and member.id not in desired_set:
                    events.append(MemberEvent(action="leave", actor=actor, target_id=member.id))
            for member_id in desired:
                current_member = current_by_id.get(member_id)
                if current_member is not None and current_member.kind != "agent":
                    raise ValueError(f"agent_id collides with human member: {member_id}")
                if current_member is None or current_member.role != "participant":
                    events.append(
                        MemberEvent(
                            action="invite",
                            actor=actor,
                            target_id=member_id,
                            target_kind="agent",
                            role="participant",
                            grant=ContextGrant(),
                        )
                    )
            if current.mode != mode:
                events.append(
                    MemberEvent(action="mode", actor=actor, mode=mode)  # type: ignore[arg-type]
                )

            next_seq = int(rows[-1][1]) + 1 if rows else 1
            for offset, event in enumerate(events):
                event.seq = next_seq + offset
                event.ts = datetime.now(UTC).isoformat()
                conn.execute(
                    "INSERT INTO group_events(thread_id, seq, event_json, ts) VALUES (?, ?, ?, ?)",
                    (thread_id, event.seq, _dump(event), event.ts),
                )

            return events, fold_state([*existing_events, *events])

    def link_room_if_absent(
        self,
        thread_id: str,
        room_id: str,
        *,
        actor: str,
    ) -> tuple[GroupState, bool]:
        """Atomically reserve the one Team Room owned by a collaboration thread.

        The folded-state read and ``room_link`` append share one
        ``BEGIN IMMEDIATE`` transaction. Competing processes therefore either
        observe the same idempotent winner or receive a conflict before they
        mutate Team/Collaboration read models.
        """

        thread_id = require_cowork_id(thread_id, label="thread_id")
        room_id = require_cowork_id(room_id, label="room_id")
        actor = normalize_actor_id(actor)
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            if conn.execute(
                "SELECT 1 FROM group_room_delete_claims WHERE room_id=? "
                "UNION ALL SELECT 1 FROM group_room_delete_tombstones "
                "WHERE room_id=? LIMIT 1",
                (room_id, room_id),
            ).fetchone():
                raise GroupRoomDeletingError(room_id)
            rows = conn.execute(
                "SELECT event_json, seq, ts FROM group_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
            existing_events: list[MemberEvent] = []
            for event_json, seq, ts in rows:
                event = MemberEvent.from_dict(_load(event_json))
                event.seq = int(seq)
                event.ts = str(ts)
                existing_events.append(event)
            current = fold_state(existing_events)
            if current.room_id:
                owner = conn.execute(
                    "SELECT thread_id FROM group_room_links WHERE room_id=?",
                    (current.room_id,),
                ).fetchone()
                if owner is not None and str(owner[0]) != thread_id:
                    raise GroupRoomLinkConflict(
                        thread_id,
                        current.room_id,
                        room_id,
                    )
                conn.execute(
                    "INSERT OR IGNORE INTO group_room_links(thread_id, room_id, actor, linked_at) "
                    "VALUES (?, ?, ?, ?)",
                    (thread_id, current.room_id, actor, datetime.now(UTC).isoformat()),
                )
                if current.room_id != room_id:
                    raise GroupRoomLinkConflict(thread_id, current.room_id, room_id)
                return current, False

            thread_claim = conn.execute(
                "SELECT room_id FROM group_room_links WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            if thread_claim is not None and str(thread_claim[0]) != room_id:
                raise GroupRoomLinkConflict(thread_id, str(thread_claim[0]), room_id)
            room_claim = conn.execute(
                "SELECT thread_id FROM group_room_links WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if room_claim is not None and str(room_claim[0]) != thread_id:
                raise GroupRoomLinkConflict(thread_id, room_id, room_id)

            conn.execute(
                "INSERT OR IGNORE INTO group_room_links(thread_id, room_id, actor, linked_at) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, room_id, actor, datetime.now(UTC).isoformat()),
            )

            event = MemberEvent(action="room_link", actor=actor, target_id=room_id)
            event.seq = int(rows[-1][1]) + 1 if rows else 1
            event.ts = datetime.now(UTC).isoformat()
            conn.execute(
                "INSERT INTO group_events(thread_id, seq, event_json, ts) VALUES (?, ?, ?, ?)",
                (thread_id, event.seq, _dump(event), event.ts),
            )
            return fold_state([*existing_events, event]), True

    def thread_delete_lease(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> GroupThreadDeleteLease | None:
        """Read an authorized durable collaboration-thread deletion lease."""

        thread_id = require_cowork_id(thread_id, label="thread_id")
        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT token, tenant_id, owner_id, 1 FROM group_thread_delete_tombstones "
                "WHERE thread_id=? UNION ALL SELECT token, tenant_id, owner_id, 0 "
                "FROM group_thread_delete_claims WHERE thread_id=? LIMIT 1",
                (thread_id, thread_id),
            ).fetchone()
        if row is None:
            return None
        if not _thread_delete_scope_allowed(
            row[1],
            row[2],
            tenant_id=tenant_id,
            owner_id=owner_id,
        ):
            raise PermissionError("thread deletion belongs to another principal")
        return GroupThreadDeleteLease(
            thread_id=thread_id,
            token=str(row[0]),
            resumed=True,
            finalized=bool(row[3]),
            tenant_id=str(row[1] or ""),
            owner_id=str(row[2] or ""),
        )

    def begin_thread_delete(
        self,
        thread_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> GroupThreadDeleteLease:
        """Reserve an empty group thread against every future group writer."""

        thread_id = require_cowork_id(thread_id, label="thread_id")
        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        async_db = self._dir / "async_work.db"
        with self._lock, closing(self._connect()) as conn, conn:
            from ._group_sqlite_coordination import require_delete_journals

            if async_db.exists():
                conn.execute("ATTACH DATABASE ? AS async_work_guard", (str(async_db),))
            require_delete_journals(
                conn,
                ("main", "async_work_guard") if async_db.exists() else ("main",),
            )
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT token, tenant_id, owner_id, 1 FROM group_thread_delete_tombstones "
                "WHERE thread_id=? UNION ALL SELECT token, tenant_id, owner_id, 0 "
                "FROM group_thread_delete_claims WHERE thread_id=? LIMIT 1",
                (thread_id, thread_id),
            ).fetchone()
            if row is not None:
                if not _thread_delete_scope_allowed(
                    row[1],
                    row[2],
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                ):
                    raise PermissionError("thread deletion belongs to another principal")
                return GroupThreadDeleteLease(
                    thread_id=thread_id,
                    token=str(row[0]),
                    resumed=True,
                    finalized=bool(row[3]),
                    tenant_id=str(row[1] or ""),
                    owner_id=str(row[2] or ""),
                )
            linked = conn.execute(
                "SELECT room_id FROM group_room_links WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            has_events = conn.execute(
                "SELECT 1 FROM group_events WHERE thread_id=? LIMIT 1",
                (thread_id,),
            ).fetchone()
            if linked is not None or has_events is not None:
                raise GroupThreadLinkedError(
                    thread_id,
                    str(linked[0]) if linked is not None else None,
                )
            async_table = (
                conn.execute(
                    "SELECT 1 FROM async_work_guard.sqlite_master "
                    "WHERE type='table' AND name='async_tasks'"
                ).fetchone()
                if async_db.exists()
                else None
            )
            if (
                async_table is not None
                and conn.execute(
                    "SELECT 1 FROM async_work_guard.async_tasks WHERE thread_id=? "
                    "AND status IN ('pending', 'working') LIMIT 1",
                    (thread_id,),
                ).fetchone()
            ):
                raise GroupThreadActiveWorkError(thread_id)
            token = f"GTD-{uuid4().hex}"
            conn.execute(
                "INSERT INTO group_thread_delete_claims"
                "(thread_id, token, created_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                (thread_id, token, time.time(), tenant_id, owner_id),
            )
            return GroupThreadDeleteLease(
                thread_id=thread_id,
                token=token,
                resumed=False,
                finalized=False,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

    def finalize_thread_delete(self, thread_id: str, token: str) -> bool:
        """Convert the exact thread claim into a permanent group tombstone."""

        thread_id = require_cowork_id(thread_id, label="thread_id")
        token = require_cowork_id(token, label="thread_delete_token")
        async_db = self._dir / "async_work.db"
        with self._lock, closing(self._connect()) as conn, conn:
            from ._group_sqlite_coordination import require_delete_journals

            if async_db.exists():
                conn.execute("ATTACH DATABASE ? AS async_work_guard", (str(async_db),))
            if self._board_db.exists():
                conn.execute("ATTACH DATABASE ? AS group_board", (str(self._board_db),))
            schemas = ["main"]
            if async_db.exists():
                schemas.append("async_work_guard")
            if self._board_db.exists():
                schemas.append("group_board")
            require_delete_journals(conn, schemas)
            conn.execute("BEGIN IMMEDIATE")
            tombstone = conn.execute(
                "SELECT token FROM group_thread_delete_tombstones WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
            if tombstone is not None:
                if str(tombstone[0]) != token:
                    raise GroupThreadDeletingError(thread_id)
            else:
                claim = conn.execute(
                    "SELECT token, tenant_id, owner_id FROM group_thread_delete_claims "
                    "WHERE thread_id=?",
                    (thread_id,),
                ).fetchone()
                if claim is None or str(claim[0]) != token:
                    raise GroupThreadDeletingError(thread_id)
                conn.execute(
                    "DELETE FROM group_thread_delete_claims WHERE thread_id=? AND token=?",
                    (thread_id, token),
                )
                conn.execute(
                    "INSERT INTO group_thread_delete_tombstones"
                    "(thread_id, token, deleted_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                    (thread_id, token, time.time(), str(claim[1] or ""), str(claim[2] or "")),
                )
            if (
                async_db.exists()
                and conn.execute(
                    "SELECT 1 FROM async_work_guard.sqlite_master "
                    "WHERE type='table' AND name='async_tasks'"
                ).fetchone()
            ):
                conn.execute(
                    "DELETE FROM async_work_guard.async_tasks WHERE thread_id=?",
                    (thread_id,),
                )
            if (
                self._board_db.exists()
                and conn.execute(
                    "SELECT 1 FROM group_board.sqlite_master "
                    "WHERE type='table' AND name='blackboard'"
                ).fetchone()
            ):
                conn.execute("DELETE FROM group_board.blackboard WHERE turn_id=?", (thread_id,))
        return True

    def room_delete_lease(
        self,
        room_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> GroupRoomDeleteLease | None:
        """Read an authorized durable room-deletion claim or tombstone."""

        room_id = require_cowork_id(room_id, label="room_id")
        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        with self._lock, self._connect() as conn:
            tombstone = conn.execute(
                "SELECT token, tenant_id, owner_id FROM group_room_delete_tombstones "
                "WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if tombstone is not None:
                if not _room_delete_scope_allowed(
                    tombstone[1],
                    tombstone[2],
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                ):
                    raise PermissionError("room deletion belongs to another principal")
                return GroupRoomDeleteLease(
                    room_id=room_id,
                    token=str(tombstone[0]),
                    resumed=True,
                    finalized=True,
                    tenant_id=str(tombstone[1] or ""),
                    owner_id=str(tombstone[2] or ""),
                )
            claim = conn.execute(
                "SELECT token, tenant_id, owner_id FROM group_room_delete_claims WHERE room_id=?",
                (room_id,),
            ).fetchone()
        if claim is None:
            return None
        if not _room_delete_scope_allowed(
            claim[1],
            claim[2],
            tenant_id=tenant_id,
            owner_id=owner_id,
        ):
            raise PermissionError("room deletion belongs to another principal")
        return GroupRoomDeleteLease(
            room_id=room_id,
            token=str(claim[0]),
            resumed=True,
            finalized=False,
            tenant_id=str(claim[1] or ""),
            owner_id=str(claim[2] or ""),
        )

    def begin_room_delete(
        self,
        room_id: str,
        *,
        tenant_id: str = "",
        owner_id: str = "",
    ) -> GroupRoomDeleteLease:
        """Reserve an unlinked Team Room against every concurrent link."""

        room_id = require_cowork_id(room_id, label="room_id")
        tenant_id = str(tenant_id or "").strip()
        owner_id = str(owner_id or "").strip()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tombstone = conn.execute(
                "SELECT token, tenant_id, owner_id FROM group_room_delete_tombstones "
                "WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if tombstone is not None:
                if not _room_delete_scope_allowed(
                    tombstone[1],
                    tombstone[2],
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                ):
                    raise PermissionError("room deletion belongs to another principal")
                return GroupRoomDeleteLease(
                    room_id=room_id,
                    token=str(tombstone[0]),
                    resumed=True,
                    finalized=True,
                    tenant_id=str(tombstone[1] or ""),
                    owner_id=str(tombstone[2] or ""),
                )
            claim = conn.execute(
                "SELECT token, tenant_id, owner_id FROM group_room_delete_claims WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if claim is not None:
                if not _room_delete_scope_allowed(
                    claim[1],
                    claim[2],
                    tenant_id=tenant_id,
                    owner_id=owner_id,
                ):
                    raise PermissionError("room deletion belongs to another principal")
                return GroupRoomDeleteLease(
                    room_id=room_id,
                    token=str(claim[0]),
                    resumed=True,
                    finalized=False,
                    tenant_id=str(claim[1] or ""),
                    owner_id=str(claim[2] or ""),
                )
            linked = conn.execute(
                "SELECT thread_id FROM group_room_links WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if linked is not None:
                raise GroupRoomLinkedError(room_id, str(linked[0]))
            token = f"RD-{uuid4().hex}"
            conn.execute(
                "INSERT INTO group_room_delete_claims"
                "(room_id, token, created_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                (room_id, token, time.time(), tenant_id, owner_id),
            )
            return GroupRoomDeleteLease(
                room_id=room_id,
                token=token,
                resumed=False,
                finalized=False,
                tenant_id=tenant_id,
                owner_id=owner_id,
            )

    def finalize_room_delete(self, room_id: str, token: str) -> bool:
        """Convert the exact room reservation into a permanent link tombstone."""

        room_id = require_cowork_id(room_id, label="room_id")
        token = require_cowork_id(token, label="room_delete_token")
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            tombstone = conn.execute(
                "SELECT token FROM group_room_delete_tombstones WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if tombstone is not None:
                if str(tombstone[0]) != token:
                    raise GroupRoomDeletingError(room_id)
                return True
            claim = conn.execute(
                "SELECT token, tenant_id, owner_id FROM group_room_delete_claims WHERE room_id=?",
                (room_id,),
            ).fetchone()
            if claim is None or str(claim[0]) != token:
                raise GroupRoomDeletingError(room_id)
            conn.execute(
                "DELETE FROM group_room_delete_claims WHERE room_id=? AND token=?",
                (room_id, token),
            )
            conn.execute(
                "INSERT INTO group_room_delete_tombstones"
                "(room_id, token, deleted_at, tenant_id, owner_id) VALUES (?, ?, ?, ?, ?)",
                (room_id, token, time.time(), str(claim[1] or ""), str(claim[2] or "")),
            )
            return True

    def events(self, thread_id: str) -> list[MemberEvent]:
        thread_id = require_cowork_id(thread_id, label="thread_id")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT event_json, seq, ts FROM group_events WHERE thread_id = ? ORDER BY seq",
                (thread_id,),
            ).fetchall()
        out: list[MemberEvent] = []
        for event_json, seq, ts in rows:
            ev = MemberEvent.from_dict(_load(event_json))
            ev.seq = int(seq)
            ev.ts = str(ts)
            out.append(ev)
        return out

    def state(self, thread_id: str, until_seq: int | None = None) -> GroupState:
        """The folded group (roster + mode). ``until_seq`` replays to a point."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        return fold_state(self.events(thread_id), until_seq=until_seq)

    def delete_thread(self, thread_id: str) -> bool:
        """Remove all state owned by a newly-created collaboration thread.

        Normal membership changes remain append-only.  This narrow deletion
        primitive exists for cross-store creation compensation: a project-group
        saga can remove its private, not-yet-returned thread when a later room
        or projection write fails.  The blackboard namespace is included so a
        retry cannot recover half-created group state.
        """

        thread_id = require_cowork_id(thread_id, label="thread_id")
        deleted = False
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if _thread_delete_exists(conn, thread_id):
                raise GroupThreadDeletingError(thread_id)
            cur = conn.execute("DELETE FROM group_events WHERE thread_id = ?", (thread_id,))
            deleted = cur.rowcount > 0
            claim = conn.execute("DELETE FROM group_room_links WHERE thread_id = ?", (thread_id,))
            deleted = deleted or claim.rowcount > 0
        if self._board_db.exists():
            with self._lock, sqlite3.connect(str(self._board_db), timeout=5.0) as conn:
                # The board DB is lazily initialized, so tolerate a file that
                # exists without the table (for example after interrupted setup).
                table = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='blackboard'"
                ).fetchone()
                if table is not None:
                    cur = conn.execute("DELETE FROM blackboard WHERE turn_id = ?", (thread_id,))
                    deleted = deleted or cur.rowcount > 0
        return deleted

    # ── thread-scoped shared blackboard ──────────────────────────────────────
    def blackboard(self, thread_id: str):
        """The group's shared blackboard — the existing SqliteBlackboard, but
        namespaced by ``thread_id`` so it persists across turns and members."""
        from runtime.memory.cowork._group_blackboard import GroupSqliteBlackboard

        thread_id = require_cowork_id(thread_id, label="thread_id")
        return GroupSqliteBlackboard(
            self._board_db,
            thread_id,
            guard_db_path=self._events_db,
            blocked_error=GroupThreadDeletingError,
        )

    def blackboard_snapshot(self, thread_id: str) -> dict:
        """All shared-board keys → values for the thread (empty if none)."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        board = self.blackboard(thread_id)
        snap = getattr(board, "snapshot", None)
        if callable(snap):
            return snap()
        # Fallback: reconstruct from keys() if snapshot isn't exposed.
        keys: list[str] = getattr(board, "keys", lambda: [])()
        return {k: board.read(k) for k in keys}


def _dump(event: MemberEvent) -> str:
    import json

    return json.dumps(event.to_dict(), ensure_ascii=False, default=str)


def _load(text: str) -> dict:
    import json

    data = json.loads(text)
    return data if isinstance(data, dict) else {}
