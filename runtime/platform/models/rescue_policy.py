"""Dependency-light model failover policy shared by execution layers.

This module lives in ``platform.models`` because both the core ReAct loop and
the sensing routers consume it. Keeping the policy below those layers avoids
making the core depend on the transport-facing sensing package.
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlsplit


def is_retryable_model_error(exc: BaseException) -> bool:
    """Return whether another configured provider may recover this call."""

    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "http_402",
            "http_408",
            "http_429",
            "http_500",
            "http_502",
            "http_503",
            "http_504",
            "insufficient_balance",
            "insufficient account balance",
            "模型账户余额不足",
            "rate limit",
            "too many requests",
            "readtimeout",
            "connecttimeout",
            "connection reset",
            "connection refused",
            "temporarily unavailable",
            "service unavailable",
            "upstream timeout",
        )
    )


def model_rescue_quality(model_id: str) -> int:
    """Return a deterministic name-only quality score for rescue ordering."""

    name = str(model_id or "").lower()
    score = 0
    if "codex" in name:
        score += 120
    if "code" in name or "coder" in name:
        score += 100
    if "pro" in name:
        score += 90
    if "reason" in name or "thinking" in name:
        score += 80
    if "chat" in name:
        score += 40
    if "flash" in name or "mini" in name:
        score += 10
    return score


def _upstream_key(base_url: str | None) -> str | None:
    """Coarse provider identity (host) so a switch is never within one upstream."""

    if not base_url:
        return None
    try:
        host = (urlsplit(str(base_url)).netloc or "").strip().lower()
        return host or None
    except (AttributeError, ValueError):  # noqa: BLE001 - never block rescue
        return str(base_url).strip().lower() or None


# Cross-turn stall memory. A model that silently overran its wall-clock
# deadline once is likely to do so again on the very next retry; excluding it
# from rescue for a short window breaks the "primary stalls → same slow fallback
# stalls → fail" loop across turns instead of re-selecting the identical
# name-only winner every time. Module-level so it survives the per-turn loop
# locals (``_model_timeout_recoveries`` / ``_model_failovers`` reset each turn).
_STALL_MEMORY_TTL_S = 300.0
_recent_stall_expiry: dict[str, float] = {}


def note_model_stall(
    model_id: str,
    *,
    ttl_s: float = _STALL_MEMORY_TTL_S,
    now: float | None = None,
) -> None:
    """Record that ``model_id`` recently stalled so failover skips it next turn."""

    key = str(model_id or "").strip()
    if key:
        _recent_stall_expiry[key] = (time.monotonic() if now is None else now) + max(0.0, ttl_s)


def _recently_stalled_models(now: float | None = None) -> set[str]:
    now = time.monotonic() if now is None else now
    return {key for key, expiry in _recent_stall_expiry.items() if expiry > now}


def next_custom_model_fallback(
    current_model: str,
    attempted: set[str],
    *,
    require_tool_use: bool = True,
) -> str | None:
    """Pick the strongest untried model from the live custom-model config.

    Two signals make rescue honest instead of name-only:

    - upstream diversity: a candidate routing to the same ``base_url`` host as
      the failing model is sorted last. Switching model tier on the same slow
      provider is a last resort — a genuinely different provider is the point.
    - recent-stall memory: models that stalled within a short window are
      skipped, so a retried turn no longer re-selects the exact model that
      just timed out.
    """

    try:
        from runtime.platform.process.paths import app_paths

        data = json.loads(app_paths().custom_models_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - rescue must remain optional
        return None
    if not isinstance(data, dict):
        return None

    upstream_by_model: dict[str, str | None] = {}
    for entry in data.values():
        if not isinstance(entry, dict):
            continue
        if require_tool_use and entry.get("supports_tool_use") is not True:
            continue
        upstream = _upstream_key(entry.get("base_url"))
        raw_models = entry.get("models")
        if isinstance(raw_models, list):
            for model in raw_models:
                model_id = str(model).strip()
                if model_id:
                    upstream_by_model.setdefault(model_id, upstream)
            continue
        fallback_id = str(entry.get("model") or entry.get("id") or "").strip()
        if fallback_id:
            upstream_by_model.setdefault(fallback_id, upstream)

    current_upstream = upstream_by_model.get(str(current_model or "").strip())
    indexed = list(enumerate(upstream_by_model.items()))
    ordered = [
        model
        for _idx, (model, _upstream) in sorted(
            indexed,
            key=lambda row: (
                row[1][1] == current_upstream,
                -model_rescue_quality(row[1][0]),
                row[0],
            ),
        )
    ]
    excluded = {str(model or "").strip() for model in attempted}
    excluded.add(str(current_model or "").strip())
    excluded.update(_recently_stalled_models())
    return next((model for model in ordered if model not in excluded), None)


__all__ = [
    "is_retryable_model_error",
    "model_rescue_quality",
    "next_custom_model_fallback",
    "note_model_stall",
]
