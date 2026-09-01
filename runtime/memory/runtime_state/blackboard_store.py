"""Durable, cross-process backend for the blackboard.

The in-memory ``Blackboard`` (``blackboard.py``) coordinates sub-agents inside
ONE process. This SQLite-backed variant gives the *same* interface but a file
as the substrate, so agents in **separate processes** that share a turn_id and
a DB file coordinate on the same workspace — the step from in-process
decomposition toward genuine distributed coordination. WAL + a busy-timeout
make concurrent multi-process read/write safe; rows are namespaced by turn_id,
so one DB file holds many turns.

Opt-in: ``get_blackboard`` uses this only when ``ECHO_BLACKBOARD_DB`` points
at a file; otherwise the fast in-memory board is unchanged.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackboard (
    turn_id        TEXT NOT NULL,
    key            TEXT NOT NULL,
    value_json     TEXT NOT NULL,
    writers_json   TEXT NOT NULL,
    write_count    INTEGER NOT NULL,
    overwrite_count INTEGER NOT NULL,
    updated_at     REAL NOT NULL,
    PRIMARY KEY (turn_id, key)
);
CREATE INDEX IF NOT EXISTS idx_bb_turn ON blackboard(turn_id);
"""


class SqliteBlackboard:
    """A turn-scoped namespace inside a shared SQLite file. Interface-compatible
    with the in-memory ``Blackboard`` (write / read / keys / snapshot / audit)."""

    def __init__(
        self,
        db_path: str | Path,
        turn_id: str,
        *,
        journal_mode: str = "WAL",
    ) -> None:
        self.turn_id = turn_id
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # manual transactions
            check_same_thread=False,  # guarded by self._lock
            timeout=5.0,
        )
        normalized_journal = str(journal_mode or "WAL").strip().upper()
        if normalized_journal not in {"DELETE", "WAL"}:
            raise ValueError("journal_mode must be DELETE or WAL")
        self._conn.execute(f"PRAGMA journal_mode={normalized_journal}")  # nosec B608 - allowlist
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)

    def _assert_write_allowed(self, conn: sqlite3.Connection) -> None:
        """Subclass hook evaluated inside the same transaction as a write."""

    def write(self, key: str, value: Any, *, writer: str | None = None) -> None:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        now = time.time()
        with self._lock:
            cur = self._conn
            cur.execute("BEGIN IMMEDIATE")
            try:
                self._assert_write_allowed(cur)
                row = cur.execute(
                    "SELECT writers_json FROM blackboard WHERE turn_id=? AND key=?",
                    (self.turn_id, key),
                ).fetchone()
                if row is None:
                    initial_writers = [writer] if writer else []
                    cur.execute(
                        "INSERT INTO blackboard(turn_id, key, value_json, writers_json,"
                        " write_count, overwrite_count, updated_at) VALUES(?,?,?,?,?,?,?)",
                        (self.turn_id, key, payload, json.dumps(initial_writers), 1, 0, now),
                    )
                else:
                    writers = set(json.loads(row[0] or "[]"))
                    if writer:
                        writers.add(writer)
                    cur.execute(
                        "UPDATE blackboard SET value_json=?, writers_json=?,"
                        " write_count=write_count+1, overwrite_count=overwrite_count+1,"
                        " updated_at=? WHERE turn_id=? AND key=?",
                        (payload, json.dumps(sorted(writers)), now, self.turn_id, key),
                    )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def read(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM blackboard WHERE turn_id=? AND key=?",
                (self.turn_id, key),
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return default

    def keys(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key FROM blackboard WHERE turn_id=? ORDER BY key",
                (self.turn_id,),
            ).fetchall()
        return [r[0] for r in rows]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json FROM blackboard WHERE turn_id=?",
                (self.turn_id,),
            ).fetchall()
        out: dict[str, Any] = {}
        for key, vj in rows:
            try:
                out[key] = json.loads(vj)
            except (ValueError, TypeError):
                out[key] = vj
        return out

    def audit(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value_json, writers_json, write_count, overwrite_count"
                " FROM blackboard WHERE turn_id=?",
                (self.turn_id,),
            ).fetchall()
        value_sizes = {r[0]: len(r[1] or "") for r in rows}
        writers_by_key = {r[0]: json.loads(r[2] or "[]") for r in rows}
        return {
            "key_count": len(rows),
            "write_count": sum(r[3] for r in rows),
            "overwrite_count": sum(r[4] for r in rows),
            "contested_keys": [k for k, w in writers_by_key.items() if len(w) > 1],
            "large_value_keys": [k for k, s in value_sizes.items() if s > 8_000],
            "value_sizes": value_sizes,
            "writers_by_key": writers_by_key,
            "durable": True,
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()


# ── per-(db, turn) instance cache · reuse connections across calls ──
_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str], SqliteBlackboard] = {}


def get_sqlite_blackboard(db_path: str | Path, turn_id: str) -> SqliteBlackboard:
    key = (str(db_path), turn_id)
    with _CACHE_LOCK:
        bb = _CACHE.get(key)
        if bb is None:
            bb = SqliteBlackboard(db_path, turn_id)
            _CACHE[key] = bb
        return bb


def reset_sqlite_cache_for_tests() -> None:
    with _CACHE_LOCK:
        for bb in _CACHE.values():
            with contextlib.suppress(Exception):
                bb.close()
        _CACHE.clear()
