"""Shared helpers for the observability router factory.

Pure structural extraction from ``observability_router.py`` (no logic
changes). Carries the module-level helper functions, the standard SSE
headers constant, and the optional-fastapi import guard that the
factory submodule relies on.
"""

from __future__ import annotations

import contextlib
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Query, Request
    from fastapi.responses import StreamingResponse

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Query = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    StreamingResponse = None  # type: ignore[assignment, misc]

# Standard SSE headers · keep proxies (nginx, Cloudflare) from buffering or
# killing long-lived event streams. X-Accel-Buffering disables nginx response
# buffering; Cache-Control/Connection keep the stream open. The generators
# below already emit a 15s ``: keepalive`` comment to defeat idle timeouts.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _safe_put(q: Any, event: Any) -> None:
    """Push event into a bounded queue with back-pressure protection.

    If the queue is full we drop the OLDEST event and retry · the
    alternative (dropping the new one) would stall a slow
    consumer's view of recent events. Journal throughput is never
    blocked on a slow SSE client.
    """
    try:
        q.put_nowait(event)
    except (OSError, ValueError):
        with contextlib.suppress(OSError, ValueError):
            q.get_nowait()
        with contextlib.suppress(OSError, ValueError):
            q.put_nowait(event)


def _snapshot_to_dict(snap: Any) -> dict[str, Any]:
    """TaskProgressSnapshot → JSON-safe dict."""
    return {
        "task_id": snap.task_id,
        "total_nodes": snap.total_nodes,
        "nodes_completed": snap.nodes_completed,
        "progress_pct": snap.progress_pct,
        "current_node_id": snap.current_node_id,
        "current_node_index": snap.current_node_index,
        "status": snap.status,
        "strategy": snap.strategy,
        "task_type": snap.task_type,
        "started_at": (snap.started_at.isoformat() if snap.started_at else None),
        "last_event_ts": (snap.last_event_ts.isoformat() if snap.last_event_ts else None),
        "tokens_spent": snap.tokens_spent,
        "usd_spent": snap.usd_spent,
    }


def _safe_call(fn: Any) -> dict[str, Any]:
    """Wrap a reflection-producer call so one failure doesn't
    bring down the aggregate response · returns ``{"error": "..."}``
    for the failed producer and the others still populate."""
    try:
        return fn()
    except (OSError, ImportError, ValueError, TypeError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _skill_forge_stub(
    journal: Any,
    registry: Any,
    *,
    scope: Any = None,
) -> dict[str, Any]:
    from runtime.safety.recovery import ForgeConfig, SkillForge

    result = SkillForge(
        journal,
        registry,
        config=ForgeConfig(governed_rollout=True),
        scope=scope,
    ).run()
    return {
        "candidates": result.candidates_total,
        "promoted": len(result.promoted),
        "governed": len(result.governed),
        "retired": len(result.retired),
    }


def _serialize_rollback_entry(entry: Any) -> dict[str, Any]:
    content = getattr(entry, "content", None)
    return {
        "path": getattr(entry, "path", ""),
        "action": getattr(entry, "action", ""),
        "expected_current_sha256": getattr(
            entry,
            "expected_current_sha256",
            "",
        ),
        "source_event_id": getattr(entry, "source_event_id", ""),
        "content_bytes": (len(content.encode("utf-8")) if isinstance(content, str) else None),
    }


def _serialize_rollback_result(
    result: Any,
    *,
    dry_run: bool,
    matched_events: int,
    event_id: str | None,
    task_id: str | None,
    path: str | None,
    project_root: str | None,
) -> dict[str, Any]:
    return {
        "dry_run": dry_run,
        "matched_events": matched_events,
        "event_id": event_id,
        "task_id": task_id,
        "path": path,
        "project_root": project_root,
        "applied": int(getattr(result, "applied", 0) or 0),
        "skipped": int(getattr(result, "skipped", 0) or 0),
        "failed": int(getattr(result, "failed", 0) or 0),
        "entries": [
            _serialize_rollback_entry(entry) for entry in getattr(result, "entries", ()) or ()
        ],
        "errors": list(getattr(result, "errors", ()) or ()),
    }


def _serialize_file_rollback_event(event: Any) -> dict[str, Any]:
    return {
        "event_type": getattr(event, "event_type", "file_rollback"),
        "event_id": str(getattr(event, "event_id", "") or ""),
        "ts": (event.ts.isoformat() if getattr(event, "ts", None) is not None else None),
        "dry_run": bool(getattr(event, "dry_run", False)),
        "project_root": getattr(event, "project_root", ""),
        "event_id_filter": getattr(event, "event_id_filter", None),
        "task_id_filter": getattr(event, "task_id_filter", None),
        "path_filter": getattr(event, "path_filter", None),
        "applied": int(getattr(event, "applied", 0) or 0),
        "skipped": int(getattr(event, "skipped", 0) or 0),
        "failed": int(getattr(event, "failed", 0) or 0),
        "source_event_ids": list(
            getattr(event, "source_event_ids", []) or [],
        ),
        "paths": list(getattr(event, "paths", []) or []),
        "errors": list(getattr(event, "errors", []) or []),
    }


__all__ = [
    "FASTAPI_AVAILABLE",
    "APIRouter",
    "Depends",
    "HTTPException",
    "Query",
    "Request",
    "StreamingResponse",
    "_SSE_HEADERS",
    "_safe_put",
    "_snapshot_to_dict",
    "_safe_call",
    "_skill_forge_stub",
    "_serialize_rollback_entry",
    "_serialize_rollback_result",
    "_serialize_file_rollback_event",
]
