"""
Journal query router · ``/api/journal/*``.

Surfaces the SQLite-indexed view of ``runtime.memory.journal.journal``
events. Read-only — no behavior change for writers.

Endpoints
---------

    GET  /api/journal/events    list filtered events
    GET  /api/journal/stats     totals + by-type breakdown
    POST /api/journal/reindex   incremental re-ingest from a JSONL source

The router lazily creates a single process-wide ``JournalIndex``
keyed off the database path; ``reindex`` accepts an explicit
``jsonl_path`` (defaults to the active journal's backing file when
discoverable).
"""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from runtime.memory.journal.sqlite_index import JournalIndex

_INDEX_LOCK = threading.Lock()
_INDEX_INSTANCE: JournalIndex | None = None
_INDEX_DB_PATH: Path | None = None


def _resolve_default_db_path() -> Path:
    from runtime.platform.process.paths import app_paths

    return app_paths().data_dir / "journal_index.sqlite"


def _get_index(db_path: Path | None = None) -> JournalIndex:
    """Return (and lazy-init) the singleton index."""
    global _INDEX_INSTANCE, _INDEX_DB_PATH
    target = Path(db_path) if db_path is not None else _resolve_default_db_path()
    with _INDEX_LOCK:
        if _INDEX_INSTANCE is None or target != _INDEX_DB_PATH:
            if _INDEX_INSTANCE is not None:
                with contextlib.suppress(Exception):
                    _INDEX_INSTANCE.close()
            _INDEX_INSTANCE = JournalIndex(target)
            _INDEX_DB_PATH = target
        return _INDEX_INSTANCE


def create_journal_router(
    *,
    db_path: Path | None = None,
    default_jsonl_path: Path | None = None,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    """Factory.

    ``db_path`` and ``default_jsonl_path`` are mainly for tests; in
    production both default to ``runtime.platform.process.paths.app_paths()``
    derivatives plus the active journal's backing path (if found via
    the boot wiring).
    """
    router = APIRouter()

    def _auth(request: Request) -> str | None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "auth required")
        from runtime.safety.auth.principal import resolve_principal
        from runtime.safety.auth.scope import scope_from_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        request.state.journal_scope = scope_from_principal(principal)
        return principal.actor_id if principal is not None else None

    def _operator(request: Request) -> None:
        from runtime.safety.auth.principal import require_operator

        # Reindex mutates the process-global SQLite index and historically
        # accepted an arbitrary server path. Keep scoped reads available to
        # users, but reserve host-file ingestion for operational roles.
        require_operator(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )

    @router.get("/api/journal/events")
    def list_events(
        request: Request,
        event_type: str | None = Query(default=None),
        since: str | None = Query(default=None),
        until: str | None = Query(default=None),
        session_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        _auth(request)
        index = _get_index(db_path)
        rows = index.query(
            event_type=event_type,
            since=since,
            until=until,
            session_id=session_id,
            limit=limit,
            offset=offset,
            scope=getattr(request.state, "journal_scope", None),
        )
        return {"events": rows, "limit": limit, "offset": offset}

    @router.get("/api/journal/stats")
    def get_stats(request: Request) -> dict[str, Any]:
        _auth(request)
        index = _get_index(db_path)
        return index.stats(scope=getattr(request.state, "journal_scope", None))

    @router.post("/api/journal/reindex")
    def reindex(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        _operator(request)
        payload = body or {}
        path_override = payload.get("jsonl_path")
        if path_override:
            jsonl_path = Path(str(path_override))
        elif default_jsonl_path is not None:
            jsonl_path = default_jsonl_path
        else:
            raise HTTPException(
                400,
                "jsonl_path is required (no default journal path configured during boot)",
            )
        if not jsonl_path.exists():
            raise HTTPException(
                404,
                f"jsonl source {jsonl_path!s} not found",
            )
        index = _get_index(db_path)
        added = index.index_jsonl(jsonl_path)
        return {
            "added": added,
            "skipped": index.last_skipped_count,
            "source": str(jsonl_path),
        }

    return router


__all__ = ["create_journal_router"]
