"""Provider-error classification for the Kimi Swarm load-test family.

Split out of the former ~1960-line kimi_swarm_load_test.py — fully
self-contained (no dependency on any other kimi_swarm_* sibling), used by
kimi_swarm_load_run.py (per-step retry decisions) and
kimi_swarm_load_test.py (quota-probe failure categorization) and
kimi_swarm_proof_lookup.py (replay-derived failure summaries).
"""

from __future__ import annotations

from typing import Any


def _is_retryable_provider_error(error: str) -> bool:
    if _is_quota_limit_error(error) or _is_rate_limit_error(error):
        return False
    normalized = str(error or "").lower()
    retryable_markers = (
        "connecterror",
        "connection",
        "unexpected_eof",
        "eof occurred",
        "timeout",
        "temporarily unavailable",
        "http_500",
        "http_502",
        "http_503",
        "http_504",
        "ssl",
    )
    return any(marker in normalized for marker in retryable_markers)


def _is_quota_limit_error(error: str) -> bool:
    normalized = str(error or "").lower()
    return (
        "usage limit" in normalized
        or "quota" in normalized
        or "limit-upgrade" in normalized
        or "limit upgrade" in normalized
        or "refreshed in the next period" in normalized
        or "upgrade to get more" in normalized
    )


def _is_rate_limit_error(error: str) -> bool:
    normalized = str(error or "").lower()
    return (
        "http_429" in normalized
        or "too many requests" in normalized
        or "rate limit" in normalized
        or "rate_limit" in normalized
    )


def _failure_category(error: str) -> str:
    normalized = str(error or "").lower()
    if not normalized:
        return "unknown"
    if _is_quota_limit_error(normalized):
        return "provider_quota_limit"
    if _is_rate_limit_error(normalized):
        return "provider_rate_limit"
    if "exceeded per-step output budget" in normalized:
        return "token_budget_exceeded"
    if _is_retryable_provider_error(normalized):
        return "provider_transient"
    return "provider_error"


def _failure_summary_from_step_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    top_errors: dict[str, int] = {}
    failed_steps = 0
    retried_steps = 0
    for record in records:
        if int(record.get("attempt_count") or 0) > 1:
            retried_steps += 1
        if bool(record.get("ok")):
            continue
        failed_steps += 1
        error = str(record.get("error") or "")
        category = _failure_category(error)
        counts[category] = counts.get(category, 0) + 1
        if error:
            top_errors[error] = top_errors.get(error, 0) + 1
    primary_category = ""
    if counts:
        primary_category = max(counts, key=lambda key: counts[key])
    return {
        "schema": "echo.kimi_swarm_failure_summary.v1",
        "failed_steps": failed_steps,
        "retried_steps": retried_steps,
        "categories": dict(sorted(counts.items())),
        "primary_category": primary_category,
        "provider_quota_limited": counts.get("provider_quota_limit", 0) > 0,
        "provider_rate_limited": counts.get("provider_rate_limit", 0) > 0,
        "top_errors": [
            {"error": error, "count": count}
            for error, count in sorted(
                top_errors.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:5]
        ],
    }


def _failure_summary_from_replay(replay: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(replay, dict):
        return _failure_summary_from_step_records([])
    records = [
        item.get("detail") or {}
        for item in replay.get("evidence") or []
        if isinstance(item, dict) and item.get("action") == "kimi_swarm_load_step"
    ]
    return _failure_summary_from_step_records(
        [record for record in records if isinstance(record, dict)],
    )


__all__: list[str] = []
