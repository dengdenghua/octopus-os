"""
runtime.platform.observability.logging_config · centralized logging setup.

Call ``configure_logging()`` once at process start (CLI entry point,
FastAPI lifespan, etc.) to establish consistent formatting and
level control across all ``runtime.*`` loggers.

Environment variables
---------------------
ECHO_LOG_LEVEL   default INFO · one of DEBUG/INFO/WARNING/ERROR
ECHO_LOG_FORMAT  default ``%(asctime)s [%(levelname)s] %(name)s: %(message)s``.
                    Set to ``json`` (or ``structured``) to emit one JSON
                    object per line via ``StructuredFormatter`` — correlation
                    IDs + secret redaction, suited to log-aggregation
                    pipelines (ELK / Loki / Datadog). Any other value is used
                    as a printf format string (the historical default).
"""

from __future__ import annotations

import logging
import os
import sys

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_LEVEL = "INFO"

_NOISY_LOGGERS = (
    "uvicorn.access",
    "httpx",
    "httpcore",
    "urllib3",
    "asyncio",
    "multipart",
)


def configure_logging() -> None:
    """Apply project-wide logging configuration.

    Safe to call multiple times — subsequent calls are no-ops if the
    root logger already has handlers (prevents duplicate output in
    tests that call ``configure_logging()`` per fixture setup).
    """
    level_name = os.environ.get("ECHO_LOG_LEVEL", _DEFAULT_LEVEL).upper()
    fmt = os.environ.get("ECHO_LOG_FORMAT", _DEFAULT_FORMAT)

    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_build_formatter(fmt))
    root.addHandler(handler)
    root.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def _build_formatter(fmt: str) -> logging.Formatter:
    """Select the log formatter.

    ``ECHO_LOG_FORMAT=json`` (or ``structured``) installs the JSON
    ``StructuredFormatter`` — one JSON object per line with correlation
    IDs and secret redaction, suited to log-aggregation pipelines. Any
    other value is treated as a printf format string (the historical
    default). Falling back to plain text on any import/construction
    failure keeps process startup from ever dying on logging setup.
    """
    if fmt.strip().lower() in {"json", "structured"}:
        try:
            from runtime.platform.observability.structured_logging import (
                StructuredFormatter,
            )

            return StructuredFormatter(redact=True)
        except Exception:  # pragma: no cover - defensive: never fail logging setup
            return logging.Formatter(_DEFAULT_FORMAT)
    return logging.Formatter(fmt)
