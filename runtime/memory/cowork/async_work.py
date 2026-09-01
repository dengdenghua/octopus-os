"""Async coworkers — a member can be given a task to work in the background.

The biggest step toward "a workspace with colleagues" rather than a turn-by-turn
chat: assign a task to a member, they work between your turns, and the result
lands on the shared blackboard (attributed) so the whole group sees it — while
you keep talking to someone else.

This is the *data spine*: assign → claim → complete (writes to the group board) →
pending(). The actual agent execution is the one hot-path piece left as a seam —
a runner polls ``pending`` and calls ``complete``; nothing here touches the
streaming loop.
"""

from __future__ import annotations

import builtins
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from runtime.memory.cowork._group_sqlite_coordination import require_delete_journals
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.ids import (
    MAX_COWORK_MESSAGE_TEXT_LENGTH,
    normalize_actor_id,
    require_cowork_id,
)

_STATUSES = ("pending", "working", "done", "failed")
_ASYNC_TEXT_MAX_LENGTH = MAX_COWORK_MESSAGE_TEXT_LENGTH


def _normalize_async_text(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} is required")
    if len(text) > _ASYNC_TEXT_MAX_LENGTH:
        raise ValueError(f"invalid {label}: must be at most {_ASYNC_TEXT_MAX_LENGTH} chars")
    if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
        raise ValueError(f"invalid {label}: contains unsupported control characters")
    if any(ord(ch) == 127 for ch in text):
        raise ValueError(f"invalid {label}: contains unsupported control characters")
    return text


@dataclass
class AsyncTask:
    task_id: str
    thread_id: str
    assignee: str
    prompt: str
    status: str
    result: str | None
    created_by: str
    created_at: str
    updated_at: str
    attempts: int = 0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "assignee": self.assignee,
            "prompt": self.prompt,
            "status": self.status,
            "result": self.result,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AsyncWorkStore:
    """Background task queue per thread; completion posts to the group board."""

    def __init__(
        self, base_dir: Path | str | None = None, group_store: GroupStore | None = None
    ) -> None:
        from runtime.platform.process.paths import app_paths

        d = Path(base_dir) if base_dir else app_paths().data_dir / "cowork"
        if group_store is not None and d.resolve() != group_store.base_dir.resolve():
            raise ValueError("AsyncWorkStore and GroupStore must share one storage directory")
        d.mkdir(parents=True, exist_ok=True)
        self._db = d / "async_work.db"
        self._lock = threading.Lock()
        self._groups = group_store or GroupStore(base_dir=d)
        board = self._groups.blackboard("async-schema")
        board.close()
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)

    def _connect(self) -> sqlite3.Connection:
        self._db.parent.mkdir(parents=True, exist_ok=True)
        if not self._groups.events_db_path.exists():
            self._groups.ensure_storage()
        if not self._groups.board_db_path.exists():
            board = self._groups.blackboard("async-schema")
            board.close()
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        # Keep every attached participant in DELETE mode; see GroupStore's
        # cross-database thread-deletion transaction invariant.
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            conn.execute("ATTACH DATABASE ? AS group_guard", (str(self._groups.events_db_path),))
            conn.execute("ATTACH DATABASE ? AS group_board", (str(self._groups.board_db_path),))
            require_delete_journals(conn, ("main", "group_guard", "group_board"))
        except Exception:
            conn.close()
            raise
        return conn

    @staticmethod
    def _assert_thread_writable(conn: sqlite3.Connection, thread_id: str) -> None:
        from runtime.memory.cowork.group_store import GroupThreadDeletingError

        if conn.execute(
            "SELECT 1 FROM group_guard.group_thread_delete_claims WHERE thread_id=? "
            "UNION ALL SELECT 1 FROM group_guard.group_thread_delete_tombstones "
            "WHERE thread_id=? LIMIT 1",
            (thread_id, thread_id),
        ).fetchone():
            raise GroupThreadDeletingError(thread_id)

    @staticmethod
    def _write_board(
        conn: sqlite3.Connection,
        thread_id: str,
        key: str,
        value: str,
        *,
        writer: str,
    ) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        row = conn.execute(
            "SELECT writers_json FROM group_board.blackboard WHERE turn_id=? AND key=?",
            (thread_id, key),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO group_board.blackboard(turn_id, key, value_json, writers_json, "
                "write_count, overwrite_count, updated_at) VALUES(?,?,?,?,?,?,?)",
                (thread_id, key, payload, json.dumps([writer]), 1, 0, time.time()),
            )
            return
        writers = set(json.loads(row[0] or "[]"))
        writers.add(writer)
        conn.execute(
            "UPDATE group_board.blackboard SET value_json=?, writers_json=?, "
            "write_count=write_count+1, overwrite_count=overwrite_count+1, updated_at=? "
            "WHERE turn_id=? AND key=?",
            (payload, json.dumps(sorted(writers)), time.time(), thread_id, key),
        )

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS async_tasks ("
            "task_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, assignee TEXT, "
            "prompt TEXT, status TEXT, result TEXT, created_by TEXT, created_at TEXT)"
        )
        self._migrate_schema(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_async_thread ON async_tasks(thread_id, status)"
        )
        conn.commit()

    def _migrate_schema(self, conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(async_tasks)").fetchall()}
        if "updated_at" not in columns:
            conn.execute("ALTER TABLE async_tasks ADD COLUMN updated_at TEXT")
            conn.execute("UPDATE async_tasks SET updated_at = COALESCE(created_at, ?)", (_now(),))
        if "attempts" not in columns:
            conn.execute("ALTER TABLE async_tasks ADD COLUMN attempts INTEGER DEFAULT 0")
            conn.execute("UPDATE async_tasks SET attempts = COALESCE(attempts, 0)")

    def _row_to_task(self, row) -> AsyncTask:
        return AsyncTask(
            task_id=row[0],
            thread_id=row[1],
            assignee=row[2],
            prompt=row[3],
            status=row[4],
            result=row[5],
            created_by=row[6],
            created_at=row[7],
            updated_at=row[8] or row[7],
            attempts=int(row[9] or 0),
        )

    def assign(self, thread_id: str, assignee: str, prompt: str, *, actor: str) -> AsyncTask:
        thread_id = require_cowork_id(thread_id, label="thread_id")
        assignee = normalize_actor_id(assignee, label="assignee")
        prompt = _normalize_async_text(prompt, label="prompt")
        actor = normalize_actor_id(actor or "user", label="actor") or "user"
        if not assignee:
            raise ValueError("assignee is required")
        now = _now()
        task = AsyncTask(
            uuid4().hex, thread_id, assignee, prompt, "pending", None, actor, now, now, 0
        )
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            self._assert_thread_writable(conn, thread_id)
            conn.execute(
                "INSERT INTO async_tasks("
                "task_id, thread_id, assignee, prompt, status, result, "
                "created_by, created_at, updated_at, attempts"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task.task_id,
                    thread_id,
                    assignee,
                    prompt,
                    "pending",
                    None,
                    task.created_by,
                    task.created_at,
                    task.updated_at,
                    task.attempts,
                ),
            )
        return task

    def _set_status(
        self,
        task_id: str,
        status: str,
        *,
        result: str | None = None,
        expected_status: str | None = None,
    ) -> bool:
        task_id = require_cowork_id(task_id, label="task_id")
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        if result is not None:
            result = _normalize_async_text(result, label="result")
        where = "WHERE task_id=?"
        params: tuple[object, ...]
        if expected_status is None:
            params = (status, result, _now(), task_id)
        else:
            where = "WHERE task_id=? AND status=?"
            params = (status, result, _now(), task_id, expected_status)
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT thread_id FROM async_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return False
            self._assert_thread_writable(conn, str(row[0]))
            cur = conn.execute(
                "UPDATE async_tasks SET status=?, result=COALESCE(?, result), updated_at=? "  # nosec B608 — WHERE built from ? placeholders; values parameterized
                f"{where}",
                params,
            )
            return cur.rowcount > 0

    def claim(self, task_id: str) -> bool:
        """A runner takes the task (pending → working). False if not pending."""
        task_id = require_cowork_id(task_id, label="task_id")
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT thread_id FROM async_tasks WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                return False
            self._assert_thread_writable(conn, str(row[0]))
            cur = conn.execute(
                "UPDATE async_tasks SET status='working', updated_at=?, "
                "attempts=COALESCE(attempts, 0) + 1 "
                "WHERE task_id=? AND status='pending'",
                (_now(), task_id),
            )
            return cur.rowcount > 0

    def complete(self, task_id: str, result: str, *, blackboard_key: str | None = None) -> bool:
        """Mark done and post the result to the group's shared blackboard,
        attributed to the assignee — so the whole thread sees the output."""
        task_id = require_cowork_id(task_id, label="task_id")
        result = _normalize_async_text(result, label="result")
        if blackboard_key is not None:
            blackboard_key = require_cowork_id(blackboard_key, label="blackboard_key")
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM async_tasks WHERE task_id=?", (task_id,)).fetchone()
            if row is None:
                return False
            task = self._row_to_task(row)
            self._assert_thread_writable(conn, task.thread_id)
            if task.status != "working":
                return False
            conn.execute(
                "UPDATE async_tasks SET status='done', result=?, updated_at=? "
                "WHERE task_id=? AND status='working'",
                (result, _now(), task_id),
            )
            key = blackboard_key or f"task:{task.assignee}:{task_id[:8]}"
            self._write_board(
                conn,
                task.thread_id,
                key,
                result,
                writer=task.assignee,
            )
            return True

    def fail(self, task_id: str, error: str) -> bool:
        task_id = require_cowork_id(task_id, label="task_id")
        error = _normalize_async_text(error, label="error")
        return self._set_status(
            task_id,
            "failed",
            result=error,
            expected_status="working",
        )

    def recover_stale_working(
        self,
        *,
        max_age_seconds: float = 900.0,
        max_attempts: int = 3,
    ) -> dict[str, int]:
        """Return abandoned ``working`` tasks to the queue after a process crash.

        A task gets ``max_attempts`` claims before being marked failed, which
        prevents one permanently-bad prompt from being re-run forever.
        """
        try:
            max_age_seconds = max(0.0, float(max_age_seconds))
        except (TypeError, ValueError):
            max_age_seconds = 900.0
        try:
            max_attempts = max(1, min(int(max_attempts), 100))
        except (TypeError, ValueError):
            max_attempts = 3
        cutoff = (datetime.now(UTC) - timedelta(seconds=max_age_seconds)).isoformat()
        now = _now()
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            writable = (
                "AND NOT EXISTS (SELECT 1 FROM group_guard.group_thread_delete_claims "
                "WHERE thread_id=async_tasks.thread_id) "
                "AND NOT EXISTS (SELECT 1 FROM group_guard.group_thread_delete_tombstones "
                "WHERE thread_id=async_tasks.thread_id)"
            )
            failed = conn.execute(
                "UPDATE async_tasks SET status='failed', updated_at=?, "
                "result=COALESCE(result, ?) "
                "WHERE status='working' AND COALESCE(updated_at, created_at) <= ? "
                f"AND COALESCE(attempts, 0) >= ? {writable}",  # nosec B608 - fixed SQL
                (now, "task abandoned after repeated worker restarts", cutoff, max_attempts),
            ).rowcount
            requeued = conn.execute(
                "UPDATE async_tasks SET status='pending', result=NULL, updated_at=? "
                "WHERE status='working' AND COALESCE(updated_at, created_at) <= ? "
                f"AND COALESCE(attempts, 0) < ? {writable}",  # nosec B608 - fixed SQL
                (now, cutoff, max_attempts),
            ).rowcount
        return {"requeued": requeued, "failed": failed}

    def get(self, task_id: str) -> AsyncTask | None:
        task_id = require_cowork_id(task_id, label="task_id")
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            row = conn.execute("SELECT * FROM async_tasks WHERE task_id=?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def pending(self, thread_id: str) -> list[AsyncTask]:
        thread_id = require_cowork_id(thread_id, label="thread_id")
        return self._by_status(thread_id, "pending")

    def threads_with_pending(self) -> list[str]:
        """Distinct threads that have at least one pending task (for a runner)."""
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT DISTINCT thread_id FROM async_tasks WHERE status='pending'"
            ).fetchall()
        out: list[str] = []
        for row in rows:
            try:
                out.append(require_cowork_id(row[0], label="thread_id"))
            except ValueError:
                continue
        return out

    def counts(self, thread_id: str | None = None) -> dict[str, int]:
        """Task counts by status for diagnostics/UI badges."""
        if thread_id is None:
            sql = "SELECT status, COUNT(*) FROM async_tasks GROUP BY status"
            args: tuple[str, ...] = ()
        else:
            thread_id = require_cowork_id(thread_id, label="thread_id")
            sql = "SELECT status, COUNT(*) FROM async_tasks WHERE thread_id=? GROUP BY status"
            args = (thread_id,)
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(sql, args).fetchall()
        out = {status: 0 for status in _STATUSES}
        for status, count in rows:
            if status in out:
                out[str(status)] = int(count or 0)
        return out

    def list(self, thread_id: str) -> list[AsyncTask]:
        thread_id = require_cowork_id(thread_id, label="thread_id")
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM async_tasks WHERE thread_id=? ORDER BY created_at", (thread_id,)
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    def _by_status(self, thread_id: str, status: str) -> builtins.list[AsyncTask]:
        thread_id = require_cowork_id(thread_id, label="thread_id")
        if status not in _STATUSES:
            raise ValueError(f"invalid status: {status!r}")
        with self._lock, self._connect() as conn:
            self._ensure_schema(conn)
            rows = conn.execute(
                "SELECT * FROM async_tasks WHERE thread_id=? AND status=? ORDER BY created_at",
                (thread_id, status),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]
