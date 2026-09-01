"""Shared parameterized SQLite query primitives for the trace store."""

from __future__ import annotations

import sqlite3
from typing import Any

_TRACE_TABLE_FILTERS = {
    "messages": frozenset({"thread_id", "turn_id", "agent_id", "tenant_id", "owner_actor_id"}),
    "agui_events": frozenset(
        {"thread_id", "turn_id", "task_id", "agent_id", "event_type", "tenant_id", "owner_actor_id"}
    ),
    "approvals": frozenset(
        {
            "thread_id",
            "turn_id",
            "task_id",
            "agent_id",
            "tool_call_id",
            "decision",
            "tenant_id",
            "owner_actor_id",
        }
    ),
    "llm_token_usage": frozenset(
        {"task_id", "thread_id", "turn_id", "agent_id", "tenant_id", "owner_actor_id"}
    ),
    "resume_requests": frozenset({"thread_id", "status", "tenant_id", "owner_actor_id"}),
    "agent_checkpoints": frozenset(
        {
            "task_id",
            "thread_id",
            "turn_id",
            "agent_id",
            "checkpoint_type",
            "tenant_id",
            "owner_actor_id",
        }
    ),
}
_SAFE_FILTER_COLUMNS = frozenset().union(*_TRACE_TABLE_FILTERS.values())


class _TraceStoreSqlMixin:
    def _where_params(self, filters: dict[str, str | None]) -> tuple[str, list[Any]]:
        unknown = filters.keys() - _SAFE_FILTER_COLUMNS
        if unknown:
            raise ValueError(f"unsupported trace filter columns: {sorted(unknown)}")
        clauses: list[str] = []
        params: list[Any] = []
        for key, value in filters.items():
            if value is not None:
                clauses.append(f"{key} = ?")
                params.append(value)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _query(
        self: Any,
        table: str,
        *,
        filters: dict[str, str | None],
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row]:
        allowed_filters = _TRACE_TABLE_FILTERS.get(table)
        if allowed_filters is None:
            raise ValueError(f"unsupported trace table: {table}")
        unknown = filters.keys() - allowed_filters
        if unknown:
            raise ValueError(f"unsupported filters for {table}: {sorted(unknown)}")
        where, params = self._where_params(filters)
        sql = f"SELECT * FROM {table}{where} ORDER BY id ASC LIMIT ? OFFSET ?"  # nosec B608 — table is an internal literal; values are parameterized
        params.extend([max(0, int(limit or 0)), max(0, int(offset or 0))])
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def _count_locked(self: Any, table: str, filters: dict[str, str | None] | None = None) -> int:
        allowed_filters = _TRACE_TABLE_FILTERS.get(table)
        if allowed_filters is None:
            raise ValueError(f"unsupported trace table: {table}")
        unknown = (filters or {}).keys() - allowed_filters
        if unknown:
            raise ValueError(f"unsupported filters for {table}: {sorted(unknown)}")
        where, params = self._where_params(filters or {})
        row = self._conn.execute(
            f"SELECT COUNT(*) AS c FROM {table}{where}",  # nosec B608 — table/filter identifiers passed the allowlists above; values are parameterized
            params,
        ).fetchone()
        return int(row["c"]) if row else 0

    def _consume_resume_request_locked(
        self: Any,
        request_id: int,
        consumed_at: str,
        scope_filters: dict[str, str | None],
    ) -> sqlite3.Row | None:
        """Atomically consume and reload one request while ``self._lock`` is held."""
        values: tuple[Any, ...] = ("consumed", consumed_at, int(request_id), "consumed")
        if scope_filters:
            cur = self._conn.execute(
                "UPDATE resume_requests SET status = ?, consumed_at = ? "
                "WHERE id = ? AND status != ? AND tenant_id = ? AND owner_actor_id = ?",
                (*values, scope_filters["tenant_id"], scope_filters["owner_actor_id"]),
            )
        else:
            cur = self._conn.execute(
                "UPDATE resume_requests SET status = ?, consumed_at = ? "
                "WHERE id = ? AND status != ?",
                values,
            )
        if cur.rowcount == 0:
            return None
        return self._scoped_row_by_id_locked("resume_requests", request_id, scope_filters)

    def _scoped_row_by_id_locked(
        self: Any,
        table: str,
        row_id: int,
        scope_filters: dict[str, str | None],
    ) -> sqlite3.Row | None:
        """Load an allowlisted trace row by id while ``self._lock`` is held."""
        if table == "resume_requests":
            base_query = "SELECT * FROM resume_requests WHERE id = ?"
            scoped_query = (
                "SELECT * FROM resume_requests "
                "WHERE id = ? AND tenant_id = ? AND owner_actor_id = ?"
            )
        elif table == "agent_checkpoints":
            base_query = "SELECT * FROM agent_checkpoints WHERE id = ?"
            scoped_query = (
                "SELECT * FROM agent_checkpoints "
                "WHERE id = ? AND tenant_id = ? AND owner_actor_id = ?"
            )
        else:
            raise ValueError(f"unsupported trace row table: {table}")
        if not scope_filters:
            return self._conn.execute(base_query, (int(row_id),)).fetchone()
        return self._conn.execute(
            scoped_query,
            (int(row_id), scope_filters["tenant_id"], scope_filters["owner_actor_id"]),
        ).fetchone()


__all__ = ["_TraceStoreSqlMixin"]
