from __future__ import annotations

import logging

from runtime.platform.ui.server_options import (
    SensitiveQueryRedactionFilter,
    install_sensitive_query_log_redaction,
)


def test_sensitive_query_redaction_filter_scrubs_http_and_websocket_paths() -> None:
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "WebSocket %s" [accepted]',
        args=(("127.0.0.1", 5000), "/api/realtime?token=top-secret&mode=live"),
        exc_info=None,
    )

    assert SensitiveQueryRedactionFilter().filter(record) is True
    rendered = record.getMessage()
    assert "top-secret" not in rendered
    assert "/api/realtime?token=[REDACTED]&mode=live" in rendered


def test_sensitive_query_redaction_filter_scrubs_all_supported_key_names() -> None:
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="%s",
        args=("/path?access_token=one&auth_token=two&TOKEN=three&safe=value",),
        exc_info=None,
    )

    SensitiveQueryRedactionFilter().filter(record)
    rendered = record.getMessage()
    assert all(secret not in rendered for secret in ("one", "two", "three"))
    assert rendered.endswith("safe=value")


def test_install_sensitive_query_log_redaction_is_idempotent() -> None:
    loggers = [logging.getLogger("uvicorn.access"), logging.getLogger("uvicorn.error")]
    original_filters = [list(logger.filters) for logger in loggers]
    try:
        for logger in loggers:
            logger.filters = [
                item
                for item in logger.filters
                if not isinstance(item, SensitiveQueryRedactionFilter)
            ]

        install_sensitive_query_log_redaction()
        install_sensitive_query_log_redaction()

        for logger in loggers:
            installed = [
                item for item in logger.filters if isinstance(item, SensitiveQueryRedactionFilter)
            ]
            assert len(installed) == 1
    finally:
        for logger, filters in zip(loggers, original_filters, strict=True):
            logger.filters = filters

