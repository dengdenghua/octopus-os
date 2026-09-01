"""Shared server options for every Uvicorn entrypoint."""

import logging
import re
from typing import Any

UVICORN_WEBSOCKET_PROTOCOL = "websockets-sansio"
_SENSITIVE_QUERY_VALUE = re.compile(
    r"([?&](?:token|access_token|auth_token)=)([^&#\s\"']*)",
    flags=re.IGNORECASE,
)


def _redact_sensitive_query_value(value: Any) -> Any:
    """Return a logging argument with credential query values removed."""

    if isinstance(value, str):
        return _SENSITIVE_QUERY_VALUE.sub(r"\1[REDACTED]", value)
    if isinstance(value, tuple):
        return tuple(_redact_sensitive_query_value(item) for item in value)
    if isinstance(value, list):
        return [_redact_sensitive_query_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_sensitive_query_value(item) for key, item in value.items()}
    return value


class SensitiveQueryRedactionFilter(logging.Filter):
    """Scrub credential-like query parameters before Uvicorn formats logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_sensitive_query_value(record.msg)
        record.args = _redact_sensitive_query_value(record.args)
        return True


def install_sensitive_query_log_redaction() -> None:
    """Protect both HTTP access and WebSocket handshake log channels."""

    for logger_name in ("uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        if not any(isinstance(item, SensitiveQueryRedactionFilter) for item in logger.filters):
            logger.addFilter(SensitiveQueryRedactionFilter())


def run_uvicorn(app: Any, **kwargs: Any) -> None:
    """Run Uvicorn on the maintained SansIO WebSocket implementation."""

    # Keep Uvicorn optional at module-import time.  CLI entrypoints perform
    # their own dependency check before building the application, while this
    # late import also keeps embedded/test callers deterministic.
    import uvicorn

    install_sensitive_query_log_redaction()
    uvicorn.run(app, ws=UVICORN_WEBSOCKET_PROTOCOL, **kwargs)


__all__ = [
    "SensitiveQueryRedactionFilter",
    "UVICORN_WEBSOCKET_PROTOCOL",
    "install_sensitive_query_log_redaction",
    "run_uvicorn",
]
