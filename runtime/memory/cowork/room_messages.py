"""Durable append-only log for Team Room messages.

Room chat used to be live-broadcast only, with a 20-line in-memory ring per
room — so a reconnect, restart, or any catch-up lost the transcript. This is a
small sqlite append-only log (ordered ``seq`` computed atomically inside the
INSERT, like ``group_store``) so room messages survive and can be replayed /
caught up on / searched.

Keyed by ``room_id`` (the team room). Independent of the cowork ``thread_id``
log; unifying the two is the larger collaboration-session refactor.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.memory.cowork.ids import (
    normalize_display_name,
    normalize_search_query,
    optional_cowork_id,
    require_cowork_id,
    require_message_text,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS room_messages (
    room_id        TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    participant_id TEXT,
    display_name   TEXT,
    text           TEXT NOT NULL,
    ts             TEXT NOT NULL,
    PRIMARY KEY (room_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_room_messages_room ON room_messages(room_id);
"""


def _default_dir() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "teamroom"


class RoomMessageStore:
    """Append-only, ordered room message log (sqlite, WAL, multi-process safe)."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        self._dir = Path(base_dir) if base_dir else _default_dir()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = self._dir / "room_messages.db"
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    @property
    def base_dir(self) -> Path:
        return self._dir

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def append(
        self,
        room_id: str,
        *,
        text: str,
        participant_id: str = "",
        display_name: str = "",
    ) -> int:
        """Append a line, stamping a per-room monotonic ``seq`` + ``ts``. The
        next ``seq`` is computed inside the INSERT so concurrent appends never
        collide. Returns the assigned seq."""
        room_id = require_cowork_id(room_id, label="room_id")
        participant_id = optional_cowork_id(participant_id, label="participant_id")
        display_name = normalize_display_name(display_name)
        text = require_message_text(text)
        ts = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO room_messages(room_id, seq, participant_id, display_name, text, ts) "
                "VALUES (?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM room_messages "
                "WHERE room_id = ?), ?, ?, ?, ?) RETURNING seq",
                (room_id, room_id, participant_id, display_name, text, ts),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def history(
        self, room_id: str, *, limit: int = 200, after_seq: int = 0
    ) -> list[dict[str, Any]]:
        """Messages for a room in order, those with ``seq > after_seq`` (for
        reconnect catch-up), capped at ``limit`` (the most recent ``limit``)."""
        room_id = require_cowork_id(room_id, label="room_id")
        limit = max(1, min(2000, limit))
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT seq, participant_id, display_name, text, ts FROM room_messages "
                "WHERE room_id = ? AND seq > ? ORDER BY seq DESC LIMIT ?",
                (room_id, int(after_seq), limit),
            ).fetchall()
        return [
            {
                "seq": int(r[0]),
                "participant_id": r[1] or "",
                "display_name": r[2] or "",
                "text": r[3],
                "ts": r[4],
            }
            for r in reversed(rows)
        ]

    def search(self, room_id: str, query: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Case-insensitive substring search over a room's messages (newest
        first). Small per-room volume → a plain scan, no FTS engine."""
        room_id = require_cowork_id(room_id, label="room_id")
        q = normalize_search_query(query)
        if not q:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT seq, participant_id, display_name, text, ts FROM room_messages "
                "WHERE room_id = ? AND lower(text) LIKE ? ORDER BY seq DESC LIMIT ?",
                (room_id, f"%{q}%", max(1, min(200, limit))),
            ).fetchall()
        return [
            {
                "seq": int(r[0]),
                "participant_id": r[1] or "",
                "display_name": r[2] or "",
                "text": r[3],
                "ts": r[4],
            }
            for r in rows
        ]
