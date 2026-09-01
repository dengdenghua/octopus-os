"""Session search and query engine.

Full-text search across thread messages using SQLite FTS5, plus export
capabilities for the Echo Native Session API v2.

Architecture:
- SQLite FTS5 for full-text indexing of all messages
- Incremental indexing on thread updates
- Search by content, title, agent, date range
- Export to Markdown

Schema:
- threads_fts: FTS5 virtual table (thread_id, title, content)
- threads_meta: Regular table (thread_id, agent_id, team_id, created_at, updated_at)

Search examples:
- search("authentication bug") → all threads discussing auth bugs
- search("authentication", agent_id="coder") → filter by agent
- search("token", after="2026-08-01") → date-filtered search
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    """One search hit with snippet context."""

    thread_id: str
    title: str
    snippet: str
    agent_id: str | None = None
    team_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    rank: float = 0.0


class SessionSearchIndex:
    """SQLite FTS5-backed search index for thread content.

    Thread-safe: uses a single connection with RLock for all operations.
    Writes are immediate (no buffering), reads use shared cache.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._ensure_db()

    def _ensure_db(self) -> None:
        """Create database and tables if they don't exist."""
        with self._lock:
            if self._conn is None:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(
                    str(self._db_path),
                    check_same_thread=False,
                    isolation_level="IMMEDIATE",
                )
                self._conn.row_factory = sqlite3.Row

            # Create metadata table
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS threads_meta (
                    thread_id TEXT PRIMARY KEY,
                    agent_id TEXT,
                    team_id TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
                """
            )

            # Create FTS5 virtual table
            # Using 'content=threads_meta' would link to the meta table,
            # but we want independent FTS content, so use contentless FTS5
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS threads_fts USING fts5(
                    thread_id UNINDEXED,
                    title,
                    content,
                    tokenize='porter unicode61'
                )
                """
            )
            self._conn.commit()

    def index_thread(
        self,
        thread_id: str,
        title: str,
        messages: list[dict[str, Any]],
        *,
        agent_id: str | None = None,
        team_id: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        """Index or update a thread's content.

        Extracts text from all messages and indexes them as one document.
        """
        if self._conn is None:
            return

        # Extract text content from messages
        text_parts = []
        for msg in messages:
            content = msg.get("content", "")

            if isinstance(content, str):
                text_parts.append(content)
            elif isinstance(content, list):
                # Handle multi-part content
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text = part.get("text", "")
                        if text:
                            text_parts.append(str(text))

        combined_content = "\n\n".join(text_parts)

        with self._lock:
            # Update metadata
            self._conn.execute(
                """
                INSERT OR REPLACE INTO threads_meta
                (thread_id, agent_id, team_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, agent_id, team_id, created_at, updated_at),
            )

            # Update FTS content
            # Delete old entry first (if exists)
            self._conn.execute(
                "DELETE FROM threads_fts WHERE thread_id = ?",
                (thread_id,),
            )

            # Insert new content
            self._conn.execute(
                """
                INSERT INTO threads_fts (thread_id, title, content)
                VALUES (?, ?, ?)
                """,
                (thread_id, title, combined_content),
            )

            self._conn.commit()

    def search(
        self,
        query: str,
        *,
        agent_id: str | None = None,
        team_id: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 50,
    ) -> list[SearchResult]:
        """Search across all indexed threads.

        Args:
            query: Search query (FTS5 syntax supported)
            agent_id: Filter by agent
            team_id: Filter by team
            after: ISO date string, include threads updated after this
            before: ISO date string, include threads updated before this
            limit: Max results (default 50)

        Returns:
            List of SearchResult, ordered by relevance (rank)
        """
        if self._conn is None or not query.strip():
            return []

        # Build WHERE clauses for filters
        filters = []
        params: list[Any] = [query]

        if agent_id:
            filters.append("m.agent_id = ?")
            params.append(agent_id)

        if team_id:
            filters.append("m.team_id = ?")
            params.append(team_id)

        if after:
            filters.append("m.updated_at >= ?")
            params.append(after)

        if before:
            filters.append("m.updated_at <= ?")
            params.append(before)

        where_clause = " AND " + " AND ".join(filters) if filters else ""

        params.append(limit)

        sql = f"""
            SELECT
                f.thread_id,
                f.title,
                snippet(threads_fts, 2, '<mark>', '</mark>', '...', 64) as snippet,
                m.agent_id,
                m.team_id,
                m.created_at,
                m.updated_at,
                rank
            FROM threads_fts f
            LEFT JOIN threads_meta m ON f.thread_id = m.thread_id
            WHERE threads_fts MATCH ?
            {where_clause}
            ORDER BY rank
            LIMIT ?
        """

        with self._lock:
            try:
                cursor = self._conn.execute(sql, params)
                results = []
                for row in cursor.fetchall():
                    results.append(
                        SearchResult(
                            thread_id=row["thread_id"],
                            title=row["title"],
                            snippet=row["snippet"],
                            agent_id=row["agent_id"],
                            team_id=row["team_id"],
                            created_at=row["created_at"],
                            updated_at=row["updated_at"],
                            rank=row["rank"],
                        )
                    )
                return results
            except sqlite3.Error as e:
                logger.warning("Search query failed", exc_info=e)
                return []

    def delete_thread(self, thread_id: str) -> None:
        """Remove a thread from the search index."""
        if self._conn is None:
            return

        with self._lock:
            self._conn.execute(
                "DELETE FROM threads_fts WHERE thread_id = ?",
                (thread_id,),
            )
            self._conn.execute(
                "DELETE FROM threads_meta WHERE thread_id = ?",
                (thread_id,),
            )
            self._conn.commit()

    def optimize(self) -> None:
        """Optimize the FTS5 index (merge segments, rebuild stats).

        Call this periodically (e.g., on app startup or after bulk updates).
        """
        if self._conn is None:
            return

        with self._lock:
            try:
                self._conn.execute("INSERT INTO threads_fts(threads_fts) VALUES('optimize')")
                self._conn.commit()
            except sqlite3.Error:  # noqa: BLE001 — search is best-effort
                pass

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def __del__(self) -> None:
        self.close()


__all__ = [
    "SearchResult",
    "SessionSearchIndex",
]
