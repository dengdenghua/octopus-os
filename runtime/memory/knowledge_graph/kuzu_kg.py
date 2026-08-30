from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from runtime.platform.models import Source

from .kg import KnowledgeGraph
from .triple import Triple

_LOG = logging.getLogger("echo.kg.kuzu")

try:
    import kuzu  # type: ignore[import-untyped]

    KUZU_AVAILABLE = True
except ImportError:
    KUZU_AVAILABLE = False
    kuzu = None


_SCHEMA_TABLES = [
    """
    CREATE NODE TABLE IF NOT EXISTS Entity (
        name STRING PRIMARY KEY
    )
    """,
    """
    CREATE REL TABLE IF NOT EXISTS TripleRel (
        FROM Entity TO Entity,
        predicate STRING,
        confidence DOUBLE,
        triple_id STRING,
        status STRING,
        ts STRING,
        source_json STRING,
        valid_from STRING,
        valid_until STRING,
        superseded_by STRING
    )
    """,
]


class KuzuKnowledgeGraph(KnowledgeGraph):
    def __init__(
        self,
        db_path: str | Path,
        *,
        multi_valued_predicates: set[str] | None = None,
    ) -> None:
        if not KUZU_AVAILABLE:
            raise ImportError(
                "kuzu not installed · pip install kuzu · 或使用 SqliteKnowledgeGraph 作为替代后端",
            )
        super().__init__(multi_valued_predicates=multi_valued_predicates)
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(self.db_path))
        self._conn = kuzu.Connection(self._db)
        for ddl in _SCHEMA_TABLES:
            try:
                self._conn.execute(ddl)
            except Exception as exc:
                _LOG.debug("kuzu DDL skipped (may already exist): %s", exc)
        self._load_from_kuzu()

    def __enter__(self) -> KuzuKnowledgeGraph:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None

    def _store(self, t: Triple) -> None:
        super()._store(t)
        self._upsert_kuzu(t)

    def _replace(self, old_id: UUID, new_triple: Triple) -> None:
        super()._replace(old_id, new_triple)
        self._upsert_kuzu(new_triple)

    def _load_from_kuzu(self) -> None:
        try:
            result = self._conn.execute(
                "MATCH (s:Entity)-[r:TripleRel]->(o:Entity) "
                "RETURN s.name, r.predicate, o.name, r.confidence, "
                "r.triple_id, r.status, r.ts, r.source_json, "
                "r.valid_from, r.valid_until, r.superseded_by"
            )
        except Exception as exc:
            _LOG.warning("kuzu load failed (empty db?): %s", exc)
            return
        while result.has_next():
            row = result.get_next()
            t = _kuzu_row_to_triple(row)
            if t is not None:
                super()._store(t)

    def _upsert_kuzu(self, t: Triple) -> None:
        try:
            self._conn.execute(
                "MERGE (s:Entity {name: $subj})",
                {"subj": t.subject},
            )
            self._conn.execute(
                "MERGE (o:Entity {name: $obj})",
                {"obj": t.object},
            )
            self._conn.execute(
                "MATCH (s:Entity {name: $subj}), (o:Entity {name: $obj}) "
                "CREATE (s)-[r:TripleRel {"
                "  predicate: $pred, confidence: $conf, triple_id: $tid, "
                "  status: $status, ts: $ts, source_json: $src, "
                "  valid_from: $vf, valid_until: $vu, superseded_by: $sb "
                "}]->(o)",
                {
                    "subj": t.subject,
                    "obj": t.object,
                    "pred": t.predicate,
                    "conf": float(t.confidence),
                    "tid": str(t.triple_id),
                    "status": t.status,
                    "ts": t.ts.isoformat(),
                    "src": t.source.model_dump_json(),
                    "vf": t.valid_from.isoformat() if t.valid_from else "",
                    "vu": t.valid_until.isoformat() if t.valid_until else "",
                    "sb": str(t.superseded_by) if t.superseded_by else "",
                },
            )
        except Exception as exc:
            _LOG.warning("kuzu upsert failed for %s: %s", t.triple_id, exc)

    def shortest_path(
        self,
        start: str,
        end: str,
        *,
        max_hops: int = 3,
    ) -> list[list[dict[str, str]]]:
        try:
            result = self._conn.execute(
                "MATCH p = shortestPath((s:Entity {name: $start})-[:TripleRel*1.."
                + str(max_hops)
                + "]->(o:Entity {name: $end})) "
                "RETURN p",
                {"start": start, "end": end},
            )
        except Exception as exc:
            _LOG.warning("kuzu shortest_path query failed: %s", exc)
            return []
        paths: list[list[dict[str, str]]] = []
        while result.has_next():
            row = result.get_next()
            path = _parse_kuzu_path(row[0] if row else None)
            if path:
                paths.append(path)
        return paths

    def pattern_match(
        self,
        *,
        subject_pattern: str | None = None,
        predicate_pattern: str | None = None,
        object_pattern: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        where_parts: list[str] = []
        params: dict[str, str] = {}
        if subject_pattern:
            where_parts.append("s.name LIKE $sp")
            params["sp"] = subject_pattern.replace("*", "%")
        if predicate_pattern:
            where_parts.append("r.predicate LIKE $pp")
            params["pp"] = predicate_pattern.replace("*", "%")
        if object_pattern:
            where_parts.append("o.name LIKE $op")
            params["op"] = object_pattern.replace("*", "%")
        where_clause = " AND ".join(where_parts) if where_parts else "true"
        try:
            result = self._conn.execute(
                f"MATCH (s:Entity)-[r:TripleRel]->(o:Entity) "
                f"WHERE {where_clause} "
                f"RETURN s.name, r.predicate, o.name, r.confidence, r.status "
                f"LIMIT {limit}",
                params,
            )
        except Exception as exc:
            _LOG.warning("kuzu pattern_match failed: %s", exc)
            return []
        matches: list[dict[str, Any]] = []
        while result.has_next():
            row = result.get_next()
            matches.append(
                {
                    "subject": row[0],
                    "predicate": row[1],
                    "object": row[2],
                    "confidence": row[3],
                    "status": row[4],
                }
            )
        return matches


def _kuzu_row_to_triple(row: list) -> Triple | None:
    try:
        (
            subject,
            predicate,
            object_,
            confidence,
            triple_id,
            status,
            ts,
            source_json,
            valid_from,
            valid_until,
            superseded_by,
        ) = row
        source = (
            Source(**json.loads(source_json))
            if source_json
            else Source(
                source_id="kuzu",
                source_type="system",
            )
        )
        return Triple(
            triple_id=UUID(triple_id) if triple_id else UUID(int=0),
            subject=subject,
            predicate=predicate,
            object=object_,
            confidence=float(confidence) if confidence else 0.75,
            source=source,
            ts=datetime.fromisoformat(ts) if ts else datetime.now(),
            valid_from=datetime.fromisoformat(valid_from)
            if valid_from and valid_from != ""
            else None,
            valid_until=datetime.fromisoformat(valid_until)
            if valid_until and valid_until != ""
            else None,
            status=status or "active",
            superseded_by=UUID(superseded_by) if superseded_by and superseded_by != "" else None,
        )
    except Exception as exc:
        _LOG.warning("kuzu row parse failed: %s", exc)
        return None


def _parse_kuzu_path(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    edges: list[dict[str, str]] = []
    try:
        if hasattr(raw, "rels"):
            for rel in raw.rels:
                src_name = ""
                dst_name = ""
                if hasattr(rel, "src_node") and hasattr(rel.src_node, "name"):
                    src_name = rel.src_node.name
                if hasattr(rel, "dst_node") and hasattr(rel.dst_node, "name"):
                    dst_name = rel.dst_node.name
                edges.append(
                    {
                        "subject": src_name,
                        "predicate": getattr(rel, "predicate", ""),
                        "object": dst_name,
                    }
                )
    except Exception as exc:
        _LOG.warning("kuzu path parse failed: %s", exc)
    return edges
