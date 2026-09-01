"""Metrics router — Prometheus text export of the in-process registry.

Exposes the metrics collected by ``runtime.platform.observability.metrics`` (Round 19)
as a Prometheus-compatible ``/metrics`` endpoint so a scraper can pull
counters / gauges / histograms without polling internal APIs.

Format
------
Standard Prometheus text format (v0.0.4): one ``# HELP``, one ``# TYPE``,
then samples separated by blank lines. Content-type
``text/plain; version=0.0.4; charset=utf-8`` is what Prometheus expects.

Endpoints
---------
* ``GET /metrics`` — full registry render (the scrape target).
* ``GET /api/metrics`` — alias under ``/api`` so the frontend stays
  consistent with other observability routes.
* ``GET /api/metrics/json`` — convenience JSON view of every metric +
  current values; useful for the observability panel rather than
  Grafana.

Why two paths
-------------
Prometheus's default scrape config hits ``/metrics`` directly (no
``/api`` prefix). The UI app already mounts every other observability
endpoint under ``/api``, so we add an alias for symmetry without
forcing operators to rewrite their scrape config.
"""

from __future__ import annotations

import json
from typing import Any

try:
    from fastapi import APIRouter
    from fastapi.responses import JSONResponse, PlainTextResponse, Response

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    Response = None  # type: ignore[assignment, misc]
    PlainTextResponse = None  # type: ignore[assignment, misc]
    JSONResponse = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi

_PROM_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def create_metrics_router(*, registry: Any = None) -> Any:
    """Build the FastAPI router exposing process metrics.

    Parameters
    ----------
    registry:
        Optional ``MetricsRegistry`` instance. When ``None`` (default),
        the global registry from ``runtime.platform.observability.metrics.get_registry()``
        is used so any code that emits via the global gets reflected here.
    """
    require_fastapi(__name__)

    from runtime.platform.observability.metrics import get_registry as _global_registry

    def _resolve():
        return registry if registry is not None else _global_registry()

    router = APIRouter(tags=["metrics"])

    # Use ``response_class=PlainTextResponse`` to tell FastAPI "this
    # endpoint returns plain text" without forcing it to generate a
    # Pydantic response model from the ``Response`` type annotation
    # (which trips an unresolved forward-ref in pydantic v2).
    @router.get(
        "/metrics",
        include_in_schema=False,
        response_class=PlainTextResponse,
    )
    def prometheus_scrape():
        """Prometheus scrape endpoint. Plain text, no auth."""
        body = _resolve().render_prometheus()
        return PlainTextResponse(content=body, media_type=_PROM_CONTENT_TYPE)

    @router.get(
        "/api/metrics",
        include_in_schema=False,
        response_class=PlainTextResponse,
    )
    def prometheus_scrape_alias():
        """Alias under /api so frontend uses a consistent base path."""
        body = _resolve().render_prometheus()
        return PlainTextResponse(content=body, media_type=_PROM_CONTENT_TYPE)

    @router.get("/api/metrics/json", response_class=JSONResponse)
    def metrics_json():
        """JSON view: every metric name + per-label values.

        Shape::

            {
              "metrics": [
                {
                  "name": "echo_skill_calls_total",
                  "type": "counter",
                  "help": "Total skill invocations",
                  "samples": [
                    {"labels": {"sucker_id": "read_file"}, "value": 42},
                    ...
                  ]
                },
                ...
              ]
            }

        Histograms get a slightly richer ``samples`` shape with
        ``buckets`` / ``sum`` / ``count`` per labelset.
        """
        reg = _resolve()
        out: list[dict[str, Any]] = []
        for name in reg.names():
            instr = reg.get(name)
            if instr is None:
                continue
            entry: dict[str, Any] = {
                "name": instr.name,
                "type": _type_of(instr),
                "help": instr.help_text,
                "samples": _samples_of(instr),
            }
            out.append(entry)
        # FastAPI will call jsonable_encoder on a dict return; that
        # walks every value and re-serializes. Since our shape is
        # already JSON-safe and can include inf / datetime, dump to
        # string ourselves and wrap in Response to bypass the encoder.
        body = json.dumps(
            {"metrics": out},
            ensure_ascii=False,
            default=str,
        )
        return Response(content=body, media_type="application/json")

    return router


# ── helpers ──────────────────────────────────────────────────


def _type_of(instr: Any) -> str:
    cls = type(instr).__name__.lower()
    if cls == "counter":
        return "counter"
    if cls == "gauge":
        return "gauge"
    if cls == "histogram":
        return "histogram"
    return cls


def _samples_of(instr: Any) -> list[dict[str, Any]]:
    cls = type(instr).__name__
    if cls == "Histogram":
        out: list[dict[str, Any]] = []
        # Iterate every labelset known to the histogram.
        for label_key in list(instr._series.keys()):  # noqa: SLF001
            labels = dict(label_key) if label_key else {}
            snap = instr.snapshot(labels=labels or None)
            out.append(
                {
                    "labels": labels,
                    "count": snap["count"],
                    "sum": snap["sum"],
                    # JSON keys must be strings; convert bucket bound to str.
                    "buckets": {
                        (str(k) if k != float("inf") else "+Inf"): v
                        for k, v in snap["buckets"].items()
                    },
                }
            )
        return out
    # Counter or Gauge → simple value lookup per labelset.
    out2: list[dict[str, Any]] = []
    for label_key in list(instr._values.keys()):  # noqa: SLF001
        labels = dict(label_key) if label_key else {}
        out2.append(
            {
                "labels": labels,
                "value": instr.value(labels=labels or None),
            }
        )
    return out2


__all__ = ["create_metrics_router"]
