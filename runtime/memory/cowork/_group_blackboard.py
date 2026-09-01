"""Thread-deletion-fenced shared blackboard."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from runtime.memory.runtime_state.blackboard_store import SqliteBlackboard

from ._group_sqlite_coordination import require_delete_journals


class GroupSqliteBlackboard(SqliteBlackboard):
    """Serialize board writes with the GroupStore deletion generation."""

    def __init__(
        self,
        db_path: str | Path,
        turn_id: str,
        *,
        guard_db_path: str | Path,
        blocked_error: Callable[[str], Exception],
    ) -> None:
        super().__init__(db_path, turn_id, journal_mode="DELETE")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._blocked_error = blocked_error
        self._conn.execute("ATTACH DATABASE ? AS group_guard", (str(guard_db_path),))
        require_delete_journals(self._conn, ("main", "group_guard"))

    def _assert_write_allowed(self, conn: sqlite3.Connection) -> None:
        row = conn.execute(
            "SELECT 1 FROM group_guard.group_thread_delete_claims WHERE thread_id=? "
            "UNION ALL SELECT 1 FROM group_guard.group_thread_delete_tombstones "
            "WHERE thread_id=? LIMIT 1",
            (self.turn_id, self.turn_id),
        ).fetchone()
        if row is not None:
            raise self._blocked_error(self.turn_id)


__all__ = ["GroupSqliteBlackboard"]
