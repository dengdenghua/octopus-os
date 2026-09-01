"""Per-member read state + presence for a cowork group (P0).

The precondition for "humans and agents are peers": in a fast multi-agent
group a member who can't see *what's new* or *who's around* drops out of the
loop. This adds the in-product state Slack has and we lacked (we only had
one-way ``ntfy`` push):

  - **read receipts** — each member's ``last_read`` position
  - **presence** — a ``last_seen`` heartbeat → online/away
  - **unread** — how much group activity a member hasn't caught up on

Unread is computed against the event-sourced membership log's monotonic
``seq`` (the group's activity clock), so it reuses the existing substrate — no
new counter. A member's floor is their join ``seq``: events from before they
were pulled in don't count as unread, so a freshly-invited member isn't
spammed with pre-join history.

The read cursor is a plain integer, so the realtime layer can later mark-read
against a message index instead of the event seq without any store change.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.memory.cowork.ids import require_cowork_id

_SCHEMA = """
CREATE TABLE IF NOT EXISTS read_state (
    thread_id    TEXT NOT NULL,
    member_id    TEXT NOT NULL,
    last_read    INTEGER NOT NULL DEFAULT 0,
    last_seen_at TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (thread_id, member_id)
);
"""

DEFAULT_ONLINE_WINDOW_S = 60


@dataclass
class MemberPresence:
    """A member's catch-up state for the presence strip."""

    member_id: str
    last_read: int
    last_seen_at: str | None
    online: bool
    unread: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "member_id": self.member_id,
            "last_read": self.last_read,
            "last_seen_at": self.last_seen_at,
            "online": self.online,
            "unread": self.unread,
        }


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class PresenceStore:
    """Read receipts + presence heartbeats, keyed by (thread, member)."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            from runtime.platform.process.paths import app_paths

            base_dir = app_paths().data_dir / "cowork"
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._db = self._dir / "presence.db"
        self._lock = threading.Lock()
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db), timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def mark_read(self, thread_id: str, member_id: str, position: int) -> None:
        """Record that ``member_id`` has caught up to ``position`` (monotonic —
        a lower position never rewinds the marker)."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        member_id = require_cowork_id(member_id, label="member_id")
        position = int(position)
        if position < 0:
            raise ValueError("position must be >= 0")
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO read_state(thread_id, member_id, last_read, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(thread_id, member_id) DO UPDATE SET "
                "last_read=MAX(last_read, excluded.last_read), updated_at=excluded.updated_at",
                (thread_id, member_id, position, _now_iso()),
            )

    def heartbeat(self, thread_id: str, member_id: str, *, now: str | None = None) -> None:
        """Presence ping — stamps ``last_seen_at`` for the member."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        member_id = require_cowork_id(member_id, label="member_id")
        ts = now or _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO read_state(thread_id, member_id, last_seen_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(thread_id, member_id) DO UPDATE SET "
                "last_seen_at=excluded.last_seen_at, updated_at=excluded.updated_at",
                (thread_id, member_id, ts, ts),
            )

    def get(self, thread_id: str, member_id: str) -> dict[str, Any]:
        """``{last_read, last_seen_at}`` for one member (defaults if unseen)."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        member_id = require_cowork_id(member_id, label="member_id")
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT last_read, last_seen_at FROM read_state WHERE thread_id=? AND member_id=?",
                (thread_id, member_id),
            ).fetchone()
        if not row:
            return {"last_read": 0, "last_seen_at": None}
        return {"last_read": int(row[0]), "last_seen_at": row[1]}

    def all(self, thread_id: str) -> dict[str, dict[str, Any]]:
        """Every recorded member's ``{last_read, last_seen_at}`` for the thread."""
        thread_id = require_cowork_id(thread_id, label="thread_id")
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT member_id, last_read, last_seen_at FROM read_state WHERE thread_id=?",
                (thread_id,),
            ).fetchall()
        return {r[0]: {"last_read": int(r[1]), "last_seen_at": r[2]} for r in rows}


def _is_online(last_seen_at: str | None, now: datetime, window_s: int) -> bool:
    if not last_seen_at:
        return False
    try:
        seen = datetime.fromisoformat(last_seen_at)
    except ValueError:
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return (now - seen).total_seconds() <= window_s


def _join_seq_by_member(events: list[Any]) -> dict[str, int]:
    """Each member's most-recent join ``seq`` — their unread floor. Events from
    before a member was pulled in don't count against them."""
    floor: dict[str, int] = {}
    for ev in events:
        if getattr(ev, "action", None) == "invite":
            target = getattr(ev, "target_id", None)
            if target:
                floor[target] = int(getattr(ev, "seq", 0) or 0)
    return floor


def group_presence(
    group_store: Any,
    presence_store: PresenceStore,
    thread_id: str,
    *,
    online_window_s: int = DEFAULT_ONLINE_WINDOW_S,
    now: datetime | None = None,
) -> list[MemberPresence]:
    """Presence + unread for every member in the thread's folded roster.

    ``head`` is the latest event ``seq``; unread is ``head - max(last_read,
    join_seq)`` clamped at zero, so pre-join history isn't counted.
    """
    current = now or datetime.now(UTC)
    events = group_store.events(thread_id)
    head = max((int(getattr(e, "seq", 0) or 0) for e in events), default=0)
    join_seq = _join_seq_by_member(events)
    state = group_store.state(thread_id)
    recorded = presence_store.all(thread_id)

    out: list[MemberPresence] = []
    for member in state.roster:
        rec = recorded.get(member.id, {"last_read": 0, "last_seen_at": None})
        floor = max(int(rec["last_read"]), join_seq.get(member.id, 0))
        out.append(
            MemberPresence(
                member_id=member.id,
                last_read=int(rec["last_read"]),
                last_seen_at=rec["last_seen_at"],
                online=_is_online(rec["last_seen_at"], current, online_window_s),
                unread=max(0, head - floor),
            )
        )
    return out


def notify_targets(
    group_store: Any,
    presence_store: PresenceStore,
    thread_id: str,
    *,
    online_window_s: int = DEFAULT_ONLINE_WINDOW_S,
    now: datetime | None = None,
) -> list[str]:
    """Members who should get a push: offline *and* have unread. The thin
    channel-delivery step (ntfy/feisho/…) consumes this list."""
    return [
        p.member_id
        for p in group_presence(
            group_store,
            presence_store,
            thread_id,
            online_window_s=online_window_s,
            now=now,
        )
        if not p.online and p.unread > 0
    ]
