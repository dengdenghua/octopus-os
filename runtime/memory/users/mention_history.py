"""Persist user @-mention history across threads.

Records every @plugin/@skill/@agent/@pack mention extracted from user
prompts, keyed by an actor identifier (user id, fallback "anonymous").
The store sits behind a tiny SQLite database alongside the rest of
the runtime's local data so it survives process restarts.

The history is read by:

- ``mentions/autocomplete`` API to rank previously-used resources
  higher when the user types ``@``.
- ``input_mentions`` parser metadata to surface frequently-paired
  agents/skills as suggestions.

Schema is intentionally minimal — first/last seen timestamps + count.
We don't store the prompt text (privacy) or full thread context.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class MentionStat:
    """One row from the history index."""

    actor: str
    type: str  # plugin | skill | agent | pack
    identifier: str
    count: int
    first_seen_ts: float
    last_seen_ts: float


_DDL = """
CREATE TABLE IF NOT EXISTS mention_history (
    actor TEXT NOT NULL,
    type TEXT NOT NULL,
    identifier TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    first_seen_ts REAL NOT NULL,
    last_seen_ts REAL NOT NULL,
    PRIMARY KEY (actor, type, identifier)
);

CREATE INDEX IF NOT EXISTS idx_mention_history_actor_recent
    ON mention_history(actor, last_seen_ts DESC);
"""


class MentionHistoryStore:
    """SQLite-backed mention history.

    Thread-safe via a single lock; SQLite's own thread-safety is
    relaxed in default builds and our access pattern is low-volume,
    so a coarse lock is fine.
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_DDL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self._path), isolation_level=None)
        try:
            with self._lock:
                yield conn
        finally:
            conn.close()

    # ── Writes ─────────────────────────────────────────────

    def record(
        self,
        actor: str,
        kind: str,
        identifier: str,
        *,
        ts: float,
    ) -> None:
        """Upsert one mention occurrence.

        ``actor`` is whatever stable id the caller has — typically the
        user id from the auth layer, or ``"anonymous"`` when the
        runtime isn't auth-bound. Empty actors are normalized to
        "anonymous" so we still get cross-thread aggregation in dev.
        """
        actor = (actor or "anonymous").strip() or "anonymous"
        kind = (kind or "").strip().lower()
        identifier = (identifier or "").strip()
        if kind not in {"plugin", "skill", "agent", "pack"} or not identifier:
            return
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO mention_history (
                        actor, type, identifier, count,
                        first_seen_ts, last_seen_ts
                    )
                    VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT(actor, type, identifier) DO UPDATE SET
                        count = count + 1,
                        last_seen_ts = excluded.last_seen_ts
                    """,
                    (actor, kind, identifier, ts, ts),
                )
        except sqlite3.Error as exc:
            _LOG.debug("mention_history upsert failed: %s", exc)

    def record_batch(
        self,
        actor: str,
        items: list[tuple[str, str]],
        *,
        ts: float,
    ) -> None:
        """Record many ``(kind, identifier)`` pairs at once."""
        for kind, identifier in items:
            self.record(actor, kind, identifier, ts=ts)

    # ── Reads ──────────────────────────────────────────────

    def top_for_actor(
        self,
        actor: str,
        *,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[MentionStat]:
        """Return the actor's most-used mentions, recent-first.

        Sort key: count DESC, last_seen_ts DESC. This gives
        "favourite" semantics when count is high, "recent" when
        count is low — which is what most users expect from
        autocomplete ranking.
        """
        actor = (actor or "anonymous").strip() or "anonymous"
        try:
            with self._connect() as conn:
                if kind:
                    rows = conn.execute(
                        """
                        SELECT actor, type, identifier, count,
                            first_seen_ts, last_seen_ts
                        FROM mention_history
                        WHERE actor = ? AND type = ?
                        ORDER BY count DESC, last_seen_ts DESC
                        LIMIT ?
                        """,
                        (actor, kind.lower(), max(1, min(200, int(limit)))),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT actor, type, identifier, count,
                            first_seen_ts, last_seen_ts
                        FROM mention_history
                        WHERE actor = ?
                        ORDER BY count DESC, last_seen_ts DESC
                        LIMIT ?
                        """,
                        (actor, max(1, min(200, int(limit)))),
                    ).fetchall()
        except sqlite3.Error as exc:
            _LOG.debug("mention_history read failed: %s", exc)
            return []
        return [
            MentionStat(
                actor=row[0],
                type=row[1],
                identifier=row[2],
                count=int(row[3]),
                first_seen_ts=float(row[4]),
                last_seen_ts=float(row[5]),
            )
            for row in rows
        ]

    def has_used(self, actor: str, kind: str, identifier: str) -> bool:
        """Return True if the actor has used this mention before."""
        actor = (actor or "anonymous").strip() or "anonymous"
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT count FROM mention_history
                    WHERE actor = ? AND type = ? AND identifier = ?
                    """,
                    (actor, kind.lower(), identifier),
                ).fetchone()
        except sqlite3.Error:
            return False
        return bool(row and int(row[0]) > 0)


# ── Module-level singleton wiring ────────────────────────

_INSTANCE: MentionHistoryStore | None = None
_INSTANCE_LOCK = threading.Lock()


def get_mention_history_store(path: Path | str | None = None) -> MentionHistoryStore:
    """Return the process-wide history store, creating it on first use."""
    global _INSTANCE
    if _INSTANCE is not None and path is None:
        return _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is not None and path is None:
            return _INSTANCE
        store_path = Path(path) if path else _default_store_path()
        _INSTANCE = MentionHistoryStore(store_path)
        return _INSTANCE


def reset_mention_history_store_for_tests() -> None:
    """Clear the singleton — pytest only."""
    global _INSTANCE
    with _INSTANCE_LOCK:
        _INSTANCE = None


def _default_store_path() -> Path:
    """Resolve the default SQLite path under runtime/data."""
    try:
        from runtime.platform.process.paths import data_dir

        return Path(data_dir()) / "mention_history.sqlite"
    except (ImportError, AttributeError):
        return Path("data") / "mention_history.sqlite"


__all__ = [
    "MentionHistoryStore",
    "MentionStat",
    "get_mention_history_store",
    "reset_mention_history_store_for_tests",
]
