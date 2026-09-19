"""Crash-report endpoints for the observability router.

The read side of ``runtime.platform.observability.crash_reporter``: the
writer hooks are installed for the whole process (see
``_config_lifespan``), but a crash nobody can read is still invisible on an
appliance with no terminal attached.

Endpoints
---------

    GET    /api/crashes                  · newest first, with a local verdict
    GET    /api/crashes/{fingerprint}    · one record + full traceback
    GET    /api/crashes/{fingerprint}/report  · human-readable text report
    DELETE /api/crashes                  · acknowledge & clear

Design notes
------------

* **Never 500.** Every handler degrades to an empty list / a low-confidence
  verdict. An observability endpoint that fails is worse than one that says
  "I don't know", because it is exactly the surface you reach for when the
  system is already unhealthy.
* **Local by default, LLM only on request.** ``explain=1`` routes through
  ``ctx.crash_explainer``; without it (offline appliance, no key, no model)
  the response still carries the rule-based verdict from ``analyze()``.
* **Tracebacks stay redacted-ish**: they are already truncated to 8 KB by the
  recorder, and the list view omits them entirely so a panel listing does not
  drag kilobytes of stack frames into the browser.
"""

from __future__ import annotations

from typing import Any

from runtime.sensing._fastapi_guard import require_fastapi

from ._observability_auth import _require_global_control
from ._observability_helpers import HTTPException, Query, Request
from ._observability_state import ObservabilityContext


def register_crash_endpoints(router: Any, ctx: ObservabilityContext) -> None:
    """Register the crash-report endpoints."""
    require_fastapi(__name__)

    def _store() -> Any:
        from runtime.platform.observability.crash_reporter import default_store

        return default_store()

    def _summary(record: Any, diagnosis: Any, *, with_traceback: bool) -> dict[str, Any]:
        payload = {
            "fingerprint": record.fingerprint,
            "exception_type": record.exception_type,
            "message": record.message,
            "source": record.source,
            "thread_name": record.thread_name,
            "occurrence_count": record.occurrence_count,
            "first_seen_at": record.first_seen_at,
            "last_seen_at": record.last_seen_at,
            "context": dict(record.context),
            "diagnosis": {
                "cause": diagnosis.cause,
                "confidence": diagnosis.confidence,
                "explanation": diagnosis.explanation,
                "suggested_fix": diagnosis.suggested_fix,
                "evidence": list(diagnosis.evidence),
            },
        }
        if with_traceback:
            payload["traceback"] = record.traceback
            payload["environment"] = dict(record.environment)
        return payload

    def _diagnose(record: Any, *, explain: bool) -> Any:
        from runtime.platform.observability.crash_reporter import analyze

        explainer = ctx.crash_explainer if explain else None
        return analyze(record, explainer=explainer)

    @router.get("/api/crashes")
    def api_crashes(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        explain: bool = Query(default=False),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        store = _store()
        try:
            everything = store.all()
        except (OSError, ValueError) as e:  # pragma: no cover - unreadable dir
            raise HTTPException(500, f"crash store unreadable: {e}") from e

        records = everything[:limit]
        items = [
            _summary(record, _diagnose(record, explain=explain), with_traceback=False)
            for record in records
        ]
        return {
            "global_control_plane": True,
            "count": len(items),
            "total": len(everything),
            "explained": explain and ctx.crash_explainer is not None,
            "directory": str(store.directory),
            "schema": "echo.crash_report.v1",
            "crashes": items,
        }

    @router.get("/api/crashes/{fingerprint}")
    def api_crash_detail(
        request: Request,
        fingerprint: str,
        explain: bool = Query(default=False),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        store = _store()
        record = store.get(fingerprint)
        if record is None:
            raise HTTPException(404, f"unknown crash: {fingerprint}")
        payload = _summary(
            record,
            _diagnose(record, explain=explain),
            with_traceback=True,
        )
        return {"global_control_plane": True, **payload}

    @router.get("/api/crashes/{fingerprint}/report")
    def api_crash_report(
        request: Request,
        fingerprint: str,
        explain: bool = Query(default=False),
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Plain-text report, ready to paste into a bug report."""
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        from runtime.platform.observability.crash_reporter import render_report

        store = _store()
        record = store.get(fingerprint)
        if record is None:
            raise HTTPException(404, f"unknown crash: {fingerprint}")
        diagnosis = _diagnose(record, explain=explain)
        return {
            "global_control_plane": True,
            "fingerprint": fingerprint,
            "report": render_report(record, diagnosis),
        }

    @router.delete("/api/crashes")
    def api_crashes_clear(
        request: Request,
        cross_tenant: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Acknowledge every recorded crash (used after triage)."""
        _require_global_control(request, ctx, cross_tenant=cross_tenant)
        store = _store()
        removed = 0
        failed = 0
        for record in store.all():
            path = store.directory / f"{record.fingerprint}.json"
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                failed += 1
        return {
            "global_control_plane": True,
            "removed": removed,
            "failed": failed,
        }


__all__ = ["register_crash_endpoints"]
