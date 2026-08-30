from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from runtime.platform.models import Source

from .kg import KnowledgeGraph
from .triple import Triple

_SCHEMA = """
CREATE TABLE IF NOT EXISTS triples (
    triple_id     TEXT PRIMARY KEY,
    subject       TEXT NOT NULL,
    predicate     TEXT NOT NULL,
    object        TEXT NOT NULL,
    confidence    REAL NOT NULL,
    source_json   TEXT NOT NULL,
    ts            TEXT NOT NULL,
    valid_from    TEXT,
    valid_until   TEXT,
    status        TEXT NOT NULL,
    superseded_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_subject   ON triples(subject);
CREATE INDEX IF NOT EXISTS idx_predicate ON triples(predicate);
CREATE INDEX IF NOT EXISTS idx_object    ON triples(object);
CREATE INDEX IF NOT EXISTS idx_status    ON triples(status);
"""


class SqliteKnowledgeGraph(KnowledgeGraph):
    def __init__(
        self,
        db_path: str | Path,
        *,
        multi_valued_predicates: set[str] | None = None,
    ) -> None:
        super().__init__(multi_valued_predicates=multi_valued_predicates)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.db_path),
            isolation_level=None,  # Implementation note.
            check_same_thread=False,  # Implementation note.
        )
        self._conn.executescript(_SCHEMA)
        self._load_from_db()

    def __enter__(self) -> SqliteKnowledgeGraph:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None  # type: ignore[assignment]

    def _store(self, t: Triple) -> None:
        super()._store(t)
        self._upsert(t)

    def _replace(self, old_id: UUID, new_triple: Triple) -> None:
        super()._replace(old_id, new_triple)
        self._upsert(new_triple)

    def _load_from_db(self) -> None:
        cur = self._conn.execute(
            "SELECT triple_id, subject, predicate, object, confidence, "
            "source_json, ts, valid_from, valid_until, status, superseded_by "
            "FROM triples ORDER BY ts ASC, triple_id ASC"
        )
        for row in cur.fetchall():
            t = _row_to_triple(row)
            super()._store(t)

    def _upsert(self, t: Triple) -> None:
        assert self._conn is not None, "connection closed"
        self._conn.execute(
            "INSERT OR REPLACE INTO triples ("
            "triple_id, subject, predicate, object, confidence, "
            "source_json, ts, valid_from, valid_until, status, superseded_by"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(t.triple_id),
                t.subject,
                t.predicate,
                t.object,
                float(t.confidence),
                t.source.model_dump_json(),
                t.ts.isoformat(),
                t.valid_from.isoformat() if t.valid_from else None,
                t.valid_until.isoformat() if t.valid_until else None,
                t.status,
                str(t.superseded_by) if t.superseded_by else None,
            ),
        )


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _row_to_triple(row: tuple) -> Triple:
    (
        triple_id,
        subject,
        predicate,
        object_,
        confidence,
        source_json,
        ts,
        valid_from,
        valid_until,
        status,
        superseded_by,
    ) = row
    source = Source(**json.loads(source_json))
    return Triple(
        triple_id=UUID(triple_id),
        subject=subject,
        predicate=predicate,
        object=object_,
        confidence=confidence,
        source=source,
        ts=datetime.fromisoformat(ts),
        valid_from=datetime.fromisoformat(valid_from) if valid_from else None,
        valid_until=datetime.fromisoformat(valid_until) if valid_until else None,
        status=status,
        superseded_by=UUID(superseded_by) if superseded_by else None,
    )
