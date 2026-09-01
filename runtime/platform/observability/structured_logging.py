"""Structured logging — JSON events with correlation IDs.

Plain ``logging.Logger`` output is fine for humans but terrible for
log-aggregation pipelines (ELK, Loki, Datadog). This module adds a
drop-in ``StructuredFormatter`` that emits one JSON object per log
line, plus a ``correlation_context`` helper that attaches
``session_id`` / ``task_id`` / ``arm_id`` / ``request_id`` to every
log record inside a scope.

Design
------
* Contextvars for correlation IDs — works correctly with asyncio
  and threading (each thread / task gets its own stack).
* No external deps — ``json.dumps`` + ``logging.Formatter`` subclass.
* Opt-in — existing handlers keep working; install the formatter
  only where structured output is wanted (usually the file handler
  for production).
* Redaction-integrated — optionally pipes every formatted message
  through the Round-18 ``Redactor`` so secrets never land in logs.

Usage
-----

    import logging
    from runtime.platform.observability.structured_logging import (
        StructuredFormatter,
        correlation_context,
    )

    handler = logging.StreamHandler()
    handler.setFormatter(StructuredFormatter(redact=True))
    logging.getLogger().addHandler(handler)

    with correlation_context(session_id="sess-123", task_id="t1"):
        log.info("fetching config")
        # → {"ts": "...", "level": "INFO", "logger": "...",
        #    "msg": "fetching config",
        #    "session_id": "sess-123", "task_id": "t1"}

Emitting extra structured fields from the call site::

    log.info("step complete", extra={"step_id": 7, "duration_ms": 123})
    # → {..., "step_id": 7, "duration_ms": 123}
"""

from __future__ import annotations

import contextvars
import json
import logging
import time
import traceback
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

# ── Correlation IDs via contextvars ────────────────────────────
_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "echo_session_id",
    default=None,
)
_task_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "echo_task_id",
    default=None,
)
_arm_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "echo_arm_id",
    default=None,
)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "echo_request_id",
    default=None,
)
# Free-form key-value pairs additional callers can attach.
# ContextVar default is ``None`` rather than a dict literal: a mutable
# default is shared across every context that reads before any write,
# so a stray ``extra.update(...)`` in one request would leak into the
# next. Callers that need to write must ``set()`` a fresh dict first.
_extra: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "echo_extra_ctx",
    default=None,
)


@contextmanager
def correlation_context(
    *,
    session_id: str | None = None,
    task_id: str | None = None,
    arm_id: str | None = None,
    request_id: str | None = None,
    **extra: Any,
) -> Iterator[None]:
    """Attach correlation IDs to every log record emitted inside.

    Nests cleanly: inner scopes extend/override outer scopes for the
    duration of the inner block.
    """
    tokens: list[tuple[contextvars.ContextVar, contextvars.Token]] = []
    if session_id is not None:
        tokens.append((_session_id, _session_id.set(session_id)))
    if task_id is not None:
        tokens.append((_task_id, _task_id.set(task_id)))
    if arm_id is not None:
        tokens.append((_arm_id, _arm_id.set(arm_id)))
    if request_id is not None:
        tokens.append((_request_id, _request_id.set(request_id)))
    if extra:
        current = _extra.get() or {}
        merged = {**current, **extra}
        tokens.append((_extra, _extra.set(merged)))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


def get_correlation_ids() -> dict[str, Any]:
    """Return a flat dict of the current correlation IDs.

    Keys with no active value are omitted so the emitted JSON stays
    compact.
    """
    out: dict[str, Any] = {}
    for key, var in (
        ("session_id", _session_id),
        ("task_id", _task_id),
        ("arm_id", _arm_id),
        ("request_id", _request_id),
    ):
        v = var.get()
        if v is not None:
            out[key] = v
    extras = _extra.get()
    if extras:
        out.update(extras)
    return out


# ── Formatter ───────────────────────────────────────────────────


# LogRecord attrs that are populated by the stdlib. We skip these
# when harvesting ``record.__dict__`` for the JSON output because
# they'd bloat every event with duplicate info that ``asctime``,
# ``levelname``, ``name`` etc. already express cleanly.
_SKIP_ATTRS: frozenset[str] = frozenset(
    {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "asctime",
        "taskName",  # py 3.12
    }
)


class StructuredFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Output shape::

        {
            "ts": "2026-05-09T12:34:56.789Z",   # ISO-8601 UTC
            "level": "INFO",
            "logger": "echo.core.cerebrum",
            "msg": "plan built",
            "session_id": "sess-1",             # from contextvars
            "task_id": "t1",                    #
            "step_id": 3,                       # from log.info(..., extra={...})
            "exc": "traceback ...",             # when exc_info is set
        }

    Parameters
    ----------
    redact:
        When True, pipe the final ``msg`` string through the
        module-level ``Redactor`` before writing. Cheap insurance
        against a ``logger.info(api_key)`` leaking a secret.
    """

    def __init__(self, *, redact: bool = False) -> None:
        super().__init__()
        self._redact = redact
        self._redactor: Any = None
        if redact:
            try:
                from runtime.platform.observability.redactor import Redactor

                self._redactor = Redactor()
            except ImportError:  # noqa: BLE001
                self._redactor = None

    def format(self, record: logging.LogRecord) -> str:
        # Base fields.
        payload: dict[str, Any] = {
            "ts": _format_ts(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Correlation IDs from contextvars (cheap, always attached).
        payload.update(get_correlation_ids())

        # Any extras the caller passed via ``logger.info(..., extra={...})``
        # land directly on record.__dict__ — pick them up and merge.
        for key, value in record.__dict__.items():
            if key in _SKIP_ATTRS or key in payload or key.startswith("_"):
                continue
            payload[key] = _coerce_for_json(value)

        # Exception info.
        if record.exc_info:
            payload["exc"] = "".join(traceback.format_exception(*record.exc_info)).rstrip()

        if self._redactor is not None and isinstance(payload.get("msg"), str):
            payload["msg"] = self._redactor.redact(payload["msg"])

        try:
            return json.dumps(payload, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            # Last-resort fallback — coerce everything to str.
            safe = {k: str(v) for k, v in payload.items()}
            return json.dumps(safe, ensure_ascii=False)


# ── Helpers ────────────────────────────────────────────────────


def _format_ts(epoch_seconds: float) -> str:
    """ISO-8601 UTC timestamp with millisecond precision."""
    # Using time.gmtime for speed — datetime is ~3× slower at
    # million-event/hour volumes.
    ms = int((epoch_seconds - int(epoch_seconds)) * 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch_seconds)) + f".{ms:03d}Z"


def _coerce_for_json(value: Any) -> Any:
    """Render non-JSON-native values as JSON-safe scalars.

    Keeps primitives + containers as-is; coerces datetimes, UUIDs,
    Pydantic models, dataclasses, etc. to their string form so
    ``json.dumps`` doesn't raise.
    """
    # Fast path for common primitives.
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce_for_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce_for_json(v) for k, v in value.items()}
    # Pydantic BaseModel (v2).
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return value.model_dump(mode="json")
        except (TypeError, ValueError):  # noqa: BLE001
            pass
    return str(value)


__all__ = [
    "StructuredFormatter",
    "correlation_context",
    "get_correlation_ids",
]
