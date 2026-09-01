"""SQLite-backed query index over the JSONL journal.

This is a strictly read-side optimization. The JSONL file in
``journal.py`` remains the source of truth; this module consumes it
incrementally and maintains a small SQLite database that supports
fast lookups by ``event_type``, time range, and ``session_id``
(mapped to the journal's ``conversation_id`` field, with ``agent_id``
and ``task_id`` indexed alongside for completeness).

The indexer is idempotent and tracks the last byte offset it consumed
per source file, so re-runs only ingest new lines. Malformed JSONL
lines are skipped, counted, and never break the import.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any

from runtime.platform.io.atomic import (
    atomic_write_json,  # noqa: F401  # available for companion JSON
)

# ═══════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           TEXT,
    tenant_id    TEXT,
    owner_actor_id TEXT,
    event_type   TEXT,
    session_id   TEXT,
    agent_id     TEXT,
    task_id      TEXT,
    payload      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_scope ON events(tenant_id, owner_actor_id);

CREATE TABLE IF NOT EXISTS state (
    jsonl_path        TEXT PRIMARY KEY,
    last_byte_offset  INTEGER NOT NULL DEFAULT 0,
    last_indexed_at   TEXT
);
"""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_ts(value: Any) -> str | None:
    """Best-effort: keep a comparable ISO-8601 string. The journal
    writes ``ts`` via pydantic ``datetime.isoformat()`` already, so
    this is just defensive — strings pass through, numerics get
    converted, anything weird becomes ``None``.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


# ═══════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════


class JournalIndex:
    """SQLite query index over a JSONL journal.

    Schema notes:
        * The journal's ``conversation_id`` field is exposed as
          ``session_id`` here — that's the public name from the spec
          and the closest semantic match in the existing record.
        * ``agent_id`` and ``task_id`` are also indexed for callers
          that want them; the public ``query`` API exposes
          ``session_id`` because that's what was specified.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            isolation_level=None,  # autocommit; we manage txns explicitly
        )
        self._conn.row_factory = sqlite3.Row
        # WAL mode: better concurrent read while indexing happens.
        # PRAGMA returns one row; we don't need the value but execute it.
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        # Lazy schema creation — idempotent thanks to IF NOT EXISTS.
        self._conn.executescript(_SCHEMA)
        columns = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(events)").fetchall()
        }
        if "tenant_id" not in columns:
            self._conn.execute("ALTER TABLE events ADD COLUMN tenant_id TEXT")
        if "owner_actor_id" not in columns:
            self._conn.execute("ALTER TABLE events ADD COLUMN owner_actor_id TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_scope ON events(tenant_id, owner_actor_id)"
        )

    # ── Indexing ─────────────────────────────────────────────

    def index_jsonl(self, jsonl_path: str | Path) -> int:
        """Incrementally index ``jsonl_path``.

        Tracks the byte offset already consumed in the ``state`` table
        so re-runs only ingest new lines. Returns the count of newly
        indexed records (malformed lines are skipped and reported via
        a separate ``skipped_count`` attribute on the result-bearing
        object — but per the public API we return only the int count
        of valid new records). The cumulative skipped count is also
        recorded so ``stats()`` can surface it later if needed.
        """
        source = str(Path(jsonl_path).resolve())
        path = Path(source)

        with self._lock:
            row = self._conn.execute(
                "SELECT last_byte_offset FROM state WHERE jsonl_path = ?",
                (source,),
            ).fetchone()
            last_offset = int(row["last_byte_offset"]) if row else 0

            if not path.exists():
                # File missing — nothing to index, but make sure the
                # state row is preserved so a future appearance still
                # picks up where we left off. (Don't reset offset.)
                return 0

            try:
                size = path.stat().st_size
            except OSError:
                return 0

            if size < last_offset:
                # File shrank (truncated/rotated) → re-index from start.
                last_offset = 0

            if size == last_offset:
                # Nothing new — but still bump last_indexed_at so the
                # stats reflect freshness.
                self._conn.execute(
                    "INSERT INTO state(jsonl_path, last_byte_offset, "
                    "last_indexed_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(jsonl_path) DO UPDATE SET "
                    "last_indexed_at=excluded.last_indexed_at",
                    (source, last_offset, _now_iso()),
                )
                return 0

            with path.open("rb") as f:
                f.seek(last_offset)
                new_bytes = f.read()
            new_offset = last_offset + len(new_bytes)
            text = new_bytes.decode("utf-8", errors="replace")

            inserted = 0
            skipped = 0
            self._conn.execute("BEGIN")
            try:
                for raw in text.splitlines():
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if not isinstance(data, dict):
                            skipped += 1
                            continue
                    except json.JSONDecodeError:
                        skipped += 1
                        continue

                    ts = _normalize_ts(data.get("ts"))
                    event_type = data.get("event_type")
                    if event_type is not None:
                        event_type = str(event_type)
                    # Map "session_id" → conversation_id (the journal's
                    # nearest concept). Accept either if a future writer
                    # sets one explicitly.
                    session_id = data.get("conversation_id")
                    if session_id is None:
                        session_id = data.get("session_id")
                    agent_id = data.get("agent_id")
                    task_id = data.get("task_id")
                    tenant_id = data.get("tenant_id")
                    owner_actor_id = data.get("owner_actor_id") or data.get("actor")

                    self._conn.execute(
                        "INSERT INTO events(ts, tenant_id, owner_actor_id, event_type, session_id, "
                        "agent_id, task_id, payload) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            ts,
                            str(tenant_id) if tenant_id is not None else None,
                            str(owner_actor_id) if owner_actor_id is not None else None,
                            event_type,
                            str(session_id) if session_id is not None else None,
                            str(agent_id) if agent_id is not None else None,
                            str(task_id) if task_id is not None else None,
                            line,
                        ),
                    )
                    inserted += 1

                self._conn.execute(
                    "INSERT INTO state(jsonl_path, last_byte_offset, "
                    "last_indexed_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(jsonl_path) DO UPDATE SET "
                    "last_byte_offset=excluded.last_byte_offset, "
                    "last_indexed_at=excluded.last_indexed_at",
                    (source, new_offset, _now_iso()),
                )
                self._conn.execute("COMMIT")
            except (OSError, sqlite3.Error):
                self._conn.execute("ROLLBACK")
                raise

            # Stash the latest skipped count on the instance so the
            # caller can inspect it without changing the int return
            # contract demanded by the public API.
            self._last_skipped_count = skipped
            return inserted

    @property
    def last_skipped_count(self) -> int:
        """Skipped (malformed) line count from the most recent
        ``index_jsonl`` call. Defaults to 0 before any call.
        """
        return getattr(self, "_last_skipped_count", 0)

    # ── Queries ──────────────────────────────────────────────

    def query(
        self,
        *,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        scope: Any = None,
    ) -> list[dict]:
        """Return matching event payloads, oldest-first.

        Filters compose with AND. ``since``/``until`` are compared as
        ISO-8601 strings (the journal writes timestamps in a sortable
        ISO format, so lexicographic comparison gives correct ordering
        for UTC timestamps with the same offset). ``session_id`` maps
        to the journal's ``conversation_id`` field.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if until is not None:
            clauses.append("ts <= ?")
            params.append(until)
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if scope is not None and not getattr(scope, "allow_cross_tenant", False):
            clauses.extend(["tenant_id = ?", "owner_actor_id = ?"])
            params.extend([scope.tenant_id, scope.actor_id])

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = "SELECT payload FROM events" + where + " ORDER BY id ASC LIMIT ? OFFSET ?"  # nosec B608 — WHERE built from ? placeholders; values parameterized
        params.append(int(limit))
        params.append(int(offset))

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        out: list[dict] = []
        for r in rows:
            try:
                obj = json.loads(r["payload"])
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                continue
        return out

    def stats(self, *, scope: Any = None) -> dict:
        """Return a small summary suitable for dashboards.

        Shape::

            {
                "total": int,
                "by_type": {event_type: count, ...},
                "last_indexed_at": str | None,
            }
        """
        with self._lock:
            scoped = scope is not None and not getattr(scope, "allow_cross_tenant", False)
            scope_params: tuple[Any, ...] = ()
            if scoped:
                scope_params = (scope.tenant_id, scope.actor_id)
                total_sql = (
                    "SELECT COUNT(*) AS c FROM events WHERE tenant_id = ? AND owner_actor_id = ?"
                )
                by_type_sql = (
                    "SELECT event_type, COUNT(*) AS c FROM events "
                    "WHERE tenant_id = ? AND owner_actor_id = ? AND "
                    "event_type IS NOT NULL GROUP BY event_type"
                )
            else:
                total_sql = "SELECT COUNT(*) AS c FROM events"
                by_type_sql = (
                    "SELECT event_type, COUNT(*) AS c FROM events "
                    "WHERE event_type IS NOT NULL GROUP BY event_type"
                )
            total_row = self._conn.execute(total_sql, scope_params).fetchone()
            total = int(total_row["c"]) if total_row else 0

            by_type: dict[str, int] = {}
            for r in self._conn.execute(
                by_type_sql,
                scope_params,
            ).fetchall():
                by_type[str(r["event_type"])] = int(r["c"])

            last_row = self._conn.execute("SELECT MAX(last_indexed_at) AS m FROM state").fetchone()
            last_indexed_at = last_row["m"] if last_row else None

        return {
            "total": total,
            "by_type": by_type,
            "last_indexed_at": last_indexed_at,
        }

    # ── Lifecycle ────────────────────────────────────────────

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._conn.close()

    def __enter__(self) -> JournalIndex:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


__all__ = ["JournalIndex"]
