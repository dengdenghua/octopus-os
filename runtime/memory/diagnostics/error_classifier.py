from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum

_LOG = logging.getLogger("echo.safety.error_classifier")


class ErrorCategory(StrEnum):
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    TIMEOUT = "timeout"
    CONTENT_FILTER = "content_filter"
    CONTEXT_LENGTH = "context_length"
    SERVER = "server"
    NETWORK = "network"
    TOOL = "tool"
    UNKNOWN = "unknown"


class RecoveryAction(StrEnum):
    RETRY = "retry"
    RETRY_WITH_BACKOFF = "retry_with_backoff"
    SWITCH_KEY = "switch_key"
    SWITCH_MODEL = "switch_model"
    REDUCE_CONTEXT = "reduce_context"
    ABORT = "abort"
    IGNORE = "ignore"


@dataclass
class ErrorClassification:
    category: ErrorCategory
    action: RecoveryAction
    is_retryable: bool
    backoff_sec: float
    message: str


_RATE_LIMIT_PATTERNS = [
    re.compile(r"rate.?limit", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"429"),
    re.compile(r"quota exceeded", re.IGNORECASE),
    re.compile(r"requests per minute", re.IGNORECASE),
]

_AUTH_PATTERNS = [
    re.compile(r"invalid.?api.?key", re.IGNORECASE),
    re.compile(r"unauthorized", re.IGNORECASE),
    re.compile(r"401"),
    re.compile(r"authentication", re.IGNORECASE),
    re.compile(r"invalid.?credential", re.IGNORECASE),
]

_TIMEOUT_PATTERNS = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed?out", re.IGNORECASE),
    re.compile(r"deadline exceeded", re.IGNORECASE),
    re.compile(r"504"),
    re.compile(r"gateway.?timeout", re.IGNORECASE),
]

_CONTENT_FILTER_PATTERNS = [
    re.compile(r"content.?filter", re.IGNORECASE),
    re.compile(r"safety", re.IGNORECASE),
    re.compile(r"policy", re.IGNORECASE),
    re.compile(r"flagged", re.IGNORECASE),
    re.compile(r"refused", re.IGNORECASE),
]

_CONTEXT_LENGTH_PATTERNS = [
    re.compile(r"context.?length", re.IGNORECASE),
    re.compile(r"maximum.?context", re.IGNORECASE),
    re.compile(r"token.?limit", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
]

_SERVER_PATTERNS = [
    re.compile(r"500"),
    re.compile(r"502"),
    re.compile(r"503"),
    re.compile(r"internal.?server.?error", re.IGNORECASE),
    re.compile(r"service.?unavailable", re.IGNORECASE),
]

_NETWORK_PATTERNS = [
    re.compile(r"connection", re.IGNORECASE),
    re.compile(r"network", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"ENOTFOUND", re.IGNORECASE),
    re.compile(r"dns", re.IGNORECASE),
]


def classify_error(
    error: Exception | str,
    *,
    status_code: int | None = None,
    provider: str = "",
) -> ErrorClassification:
    msg = str(error)

    if status_code == 429 or _matches(msg, _RATE_LIMIT_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            action=RecoveryAction.RETRY_WITH_BACKOFF,
            is_retryable=True,
            backoff_sec=30.0,
            message="Rate limited · retry with backoff or switch key",
        )

    if status_code == 401 or _matches(msg, _AUTH_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.AUTH,
            action=RecoveryAction.SWITCH_KEY,
            is_retryable=True,
            backoff_sec=0.0,
            message="Auth error · switch credential",
        )

    if status_code in (504, 408) or _matches(msg, _TIMEOUT_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.TIMEOUT,
            action=RecoveryAction.RETRY,
            is_retryable=True,
            backoff_sec=5.0,
            message="Timeout · retry",
        )

    if _matches(msg, _CONTENT_FILTER_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.CONTENT_FILTER,
            action=RecoveryAction.ABORT,
            is_retryable=False,
            backoff_sec=0.0,
            message="Content filtered · abort",
        )

    if _matches(msg, _CONTEXT_LENGTH_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.CONTEXT_LENGTH,
            action=RecoveryAction.REDUCE_CONTEXT,
            is_retryable=True,
            backoff_sec=0.0,
            message="Context too long · reduce and retry",
        )

    if status_code in (500, 502, 503) or _matches(msg, _SERVER_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.SERVER,
            action=RecoveryAction.RETRY_WITH_BACKOFF,
            is_retryable=True,
            backoff_sec=10.0,
            message="Server error · retry with backoff",
        )

    if _matches(msg, _NETWORK_PATTERNS):
        return ErrorClassification(
            category=ErrorCategory.NETWORK,
            action=RecoveryAction.RETRY,
            is_retryable=True,
            backoff_sec=3.0,
            message="Network error · retry",
        )

    return ErrorClassification(
        category=ErrorCategory.UNKNOWN,
        action=RecoveryAction.IGNORE,
        is_retryable=False,
        backoff_sec=0.0,
        message="Unknown error",
    )


def _matches(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(text) for p in patterns)


__all__ = [
    "ErrorCategory",
    "ErrorClassification",
    "RecoveryAction",
    "classify_error",
]
