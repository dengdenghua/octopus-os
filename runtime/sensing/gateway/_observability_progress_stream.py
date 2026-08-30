"""Progress and SSE-stream endpoints for the observability router.

Pure structural extraction from ``_observability_router_factory.py`` (no logic
changes). Builder that registers ``/api/progress`` and the SSE feeds
(``/api/stream``, ``/api/preview/stream``, ``/api/files/stream``) onto the
router. A ``TaskProgressTracker`` is built from a request-scoped journal view
for each snapshot response so aggregated task ids cannot cross ownership.
"""

from __future__ import annotations

import json
from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import (
    _observability_scope,
    _scoped_observability_journal,
)
from ._observability_helpers import (
    _SSE_HEADERS,
    HTTPException,
    Query,
    Request,
    StreamingResponse,
    _safe_put,
    _serialize_file_rollback_event,
    _snapshot_to_dict,
)
from ._observability_state import ObservabilityContext

# Bounded replay after an SSE reconnect. The journal is append-only and
# every event carries a UUID, so ``Last-Event-ID`` resume locates the
# cursor by identity and replays everything after it. The cap keeps a
# long disconnect from flooding a slow client; the queue drain below
# closes the subscribe-before-generator race so nothing is duplicated.
_SSE_REPLAY_CAP = 500


def _drain_queue(q: Any) -> list[Any]:
    """Drain events already queued between subscribe and generator start."""
    import queue as _queue

    out: list[Any] = []
    while True:
        try:
            out.append(q.get_nowait())
        except _queue.Empty:
            return out


def _replay_events_after(
    journal: Any,
    last_event_id: str | None,
    *,
    event_filter: Any = None,
) -> list[Any]:
    """Journal events strictly after ``last_event_id`` (bounded replay).

    Unknown cursors (journal rotated/evicted) replay nothing — the client
    keeps what it has and resumes live. Reading the journal once per
    reconnect is the same cost ``/api/journal`` already pays.
    """
    if not last_event_id:
        return []
    try:
        events = journal.read_all()
    except (OSError, ValueError, AttributeError):  # pragma: no cover — journal impl-dependent
        return []
    idx = None
    for i, ev in enumerate(events):
        if str(ev.event_id) == last_event_id:
            idx = i
            break
    if idx is None:
        return []
    tail = [ev for ev in events[idx + 1 :] if event_filter is None or event_filter(ev)]
    return tail[-_SSE_REPLAY_CAP:]


def _event_base_payload(event: Any) -> dict[str, Any]:
    return {
        "event_type": event.event_type,
        "ts": event.ts.isoformat(),
        "task_id": (str(event.task_id) if event.task_id else None),
        "arm_id": event.arm_id,
    }


def _enrich_event_payload(event: Any) -> dict[str, Any]:
    from runtime.memory.journal import (
        BrowserArtifactEvent,
        FileOpEvent,
        FileRollbackEvent,
        PreviewRefreshEvent,
        SubToolEndEvent,
        SubToolStartEvent,
    )

    payload = _event_base_payload(event)
    if isinstance(event, FileOpEvent):
        payload.update(
            {
                "path": event.path,
                "action": event.action,
                "bytes_delta": event.bytes_delta,
                "old_size": event.old_size,
                "new_size": event.new_size,
                "sucker_id": event.sucker_id,
                "diff": event.diff,
            }
        )
    elif isinstance(event, FileRollbackEvent):
        payload.update(_serialize_file_rollback_event(event))
    elif isinstance(event, PreviewRefreshEvent):
        payload.update(
            {
                "target": event.target,
                "trigger_path": event.trigger_path,
                "reason": event.reason,
            }
        )
    elif isinstance(event, SubToolStartEvent):
        payload.update(
            {
                "role_id": event.role_id,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "iteration": event.iteration,
                "args_preview": event.args_preview,
                "parent_tool_use_id": event.parent_tool_use_id,
            }
        )
    elif isinstance(event, SubToolEndEvent):
        payload.update(
            {
                "role_id": event.role_id,
                "tool_call_id": event.tool_call_id,
                "tool_name": event.tool_name,
                "iteration": event.iteration,
                "is_error": event.is_error,
                "duration_ms": event.duration_ms,
                "output_preview": event.output_preview,
                "parent_tool_use_id": event.parent_tool_use_id,
            }
        )
    elif isinstance(event, BrowserArtifactEvent):
        payload.update(
            {
                "kind": event.kind,
                "url": event.url,
                "filename": event.filename,
                "caption": event.caption,
                "mime_type": event.mime_type,
                "width": event.width,
                "height": event.height,
                "thread_id": event.thread_id,
            }
        )
    return payload


def _sse_event_frame(event: Any, *, event_name: str | None) -> str:
    """One SSE block: optional ``event:`` name, resume ``id:``, payload."""
    payload = _enrich_event_payload(event)
    if event_name:
        return f"event: {event_name}\nid: {event.event_id}\ndata: {json.dumps(payload)}\n\n"
    return f"id: {event.event_id}\ndata: {json.dumps(payload)}\n\n"


def _iter_sse_frames(
    journal: Any,
    q: Any,
    last_event_id: str | None,
    *,
    event_name: str | None = None,
    event_filter: Any = None,
    catch_up: int = 0,
) -> Any:
    """Yield SSE frames for one journal feed, honouring ``Last-Event-ID``.

    Reconnect resume: when ``last_event_id`` is set, replay journal events
    strictly after that cursor (bounded); otherwise emit the last
    ``catch_up`` matching events on first connect. Events queued between
    subscribe and generator start are deduped against the replay and then
    streamed in order, so nothing is lost or doubled. The live loop keeps
    a 15s keepalive comment like the original handlers.
    """
    import queue as _queue

    queued = _drain_queue(q)
    queued_ids = {str(ev.event_id) for ev in queued}
    if last_event_id:
        replayed = _replay_events_after(journal, last_event_id, event_filter=event_filter)
    elif catch_up > 0 and event_filter is not None:
        try:
            replayed = [ev for ev in journal.read_all() if event_filter(ev)][-catch_up:]
        except (OSError, ValueError, AttributeError):  # pragma: no cover — journal impl-dependent
            replayed = []
    else:
        replayed = []
    for ev in replayed:
        if str(ev.event_id) in queued_ids:
            continue
        yield _sse_event_frame(ev, event_name=event_name)
    for ev in queued:
        yield _sse_event_frame(ev, event_name=event_name)
    yield ": connected\n\n"
    while True:
        try:
            event = q.get(timeout=15.0)
        except _queue.Empty:
            yield ": keepalive\n\n"
            continue
        yield _sse_event_frame(event, event_name=event_name)


def register_progress_stream_endpoints(router: Any, ctx: ObservabilityContext) -> None:
    """Register the progress + SSE stream endpoints."""
    require_fastapi(__name__)

    journal = ctx.journal

    # A process-global progress tracker would erase ownership metadata after
    # aggregation. Build each response from a request-scoped journal view so a
    # guessed task id cannot address another tenant's snapshot.
    from runtime.memory.journal import TaskProgressTracker

    # ─── /api/progress ──────────────────────────────────────
    @router.get("/api/progress")
    def api_progress(
        request: Request,
        task_id: str | None = None,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        with TaskProgressTracker(scoped_journal) as progress_tracker:
            if task_id is not None:
                snap = progress_tracker.get(task_id)
                if snap is None:
                    raise HTTPException(
                        404,
                        f"no events for task_id={task_id!r}",
                    )
                return _snapshot_to_dict(snap)

            snaps = progress_tracker.snapshots
            return {
                "count": progress_tracker.count(),
                "running": progress_tracker.running_count(),
                "tasks": [_snapshot_to_dict(s) for s in snaps[:50]],
            }

    # ─── /api/stream (SSE) ──────────────────────────────────
    @router.get("/api/stream")
    def api_stream(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> Any:
        import queue as _queue

        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        q: _queue.Queue[Any] = _queue.Queue(maxsize=500)
        unsubscribe = scoped_journal.subscribe(
            lambda event: _safe_put(q, event),
        )

        def _gen():
            try:
                yield from _iter_sse_frames(
                    scoped_journal,
                    q,
                    request.headers.get("last-event-id"),
                )
            finally:
                unsubscribe()

        return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @router.get("/api/preview/stream")
    def api_preview_stream(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> Any:
        import queue as _queue

        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        q: _queue.Queue[Any] = _queue.Queue(maxsize=200)

        def _only_preview(event: Any) -> None:
            if getattr(event, "event_type", "") == "preview_refresh":
                _safe_put(q, event)

        unsubscribe = scoped_journal.subscribe(_only_preview)

        def _gen():
            try:
                from runtime.memory.journal import PreviewRefreshEvent

                yield from _iter_sse_frames(
                    scoped_journal,
                    q,
                    request.headers.get("last-event-id"),
                    event_name="preview_refresh",
                    event_filter=lambda e: isinstance(e, PreviewRefreshEvent),
                    catch_up=5,
                )
            finally:
                unsubscribe()

        return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @router.get("/api/files/stream")
    def api_files_stream(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> Any:
        import queue as _queue

        scope = _observability_scope(request, ctx, cross_tenant=cross_tenant)
        scoped_journal = _scoped_observability_journal(journal, scope)
        q: _queue.Queue[Any] = _queue.Queue(maxsize=500)

        def _only_file_op(event: Any) -> None:
            if getattr(event, "event_type", "") == "file_op":
                _safe_put(q, event)

        unsubscribe = scoped_journal.subscribe(_only_file_op)

        def _gen():
            try:
                from runtime.memory.journal import FileOpEvent

                yield from _iter_sse_frames(
                    scoped_journal,
                    q,
                    request.headers.get("last-event-id"),
                    event_name="file_op",
                    event_filter=lambda e: isinstance(e, FileOpEvent),
                    catch_up=20,
                )
            finally:
                unsubscribe()

        return StreamingResponse(_gen(), media_type="text/event-stream", headers=_SSE_HEADERS)
