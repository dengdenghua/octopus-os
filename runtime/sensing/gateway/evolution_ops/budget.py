"""Budget subsystem for evolution operators."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .utils import _as_dt, _utcnow

_BUDGET_COMPONENTS: tuple[str, ...] = (
    "runtime",
    "reflection",
    "skill_forge",
    "recipe_forge",
    "model_benchmark",
    "mcp_vet",
    "protocol_repair",
)
_BUDGET_MAX_CALLS_PER_HOUR = 60
_BUDGET_MAX_CALLS_PER_DAY = 300
_BUDGET_BREAKER_FAILURE_THRESHOLD = 5
_BUDGET_BREAKER_COOLDOWN_SECONDS = 900
_BUDGET_BREAKER_REJECTION_MARKERS: tuple[str, ...] = (
    "breaker",
    "circuit",
    "cooldown",
    "open",
)


def _budget_snapshot(journal: Any) -> dict[str, Any]:
    now = _utcnow()
    rows = _budget_event_rows(journal)
    resets = _budget_reset_map(journal)
    rows_by_component: dict[str, list[dict[str, Any]]] = {
        component: [] for component in _BUDGET_COMPONENTS
    }

    for row in rows:
        component = str(row.get("component") or "runtime")
        rows_by_component.setdefault(component, []).append(row)
        if component != "runtime":
            rows_by_component.setdefault("runtime", []).append({**row, "aggregate_of": component})

    ordered_components = list(_BUDGET_COMPONENTS)
    ordered_components.extend(
        sorted(name for name in rows_by_component if name not in _BUDGET_COMPONENTS)
    )
    return {
        "components": [
            _budget_component(
                name,
                rows_by_component.get(name, []),
                reset_ts=resets.get(name),
                now=now,
            )
            for name in ordered_components
        ],
        "source": "journal",
        "events": len(rows),
    }


def _budget_component(
    name: str,
    rows: list[dict[str, Any]],
    *,
    reset_ts: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    hourly_floor = now - timedelta(hours=1)
    daily_floor = now - timedelta(days=1)
    hourly_rows = [row for row in rows if row["ts"] >= hourly_floor]
    daily_rows = [row for row in rows if row["ts"] >= daily_floor]
    daily_success = sum(1 for row in daily_rows if row["event_type"] == "budget_commit")
    daily_failure = sum(1 for row in daily_rows if row["event_type"] == "budget_squirt")
    daily_breaker_rejections = sum(
        1
        for row in daily_rows
        if row["event_type"] == "budget_squirt"
        and _budget_reason_mentions_breaker(row.get("reason", ""))
    )
    consecutive_failures, opened_at = _budget_consecutive_failures(
        rows,
        reset_ts=reset_ts,
    )
    breaker_state = "closed"
    if consecutive_failures >= _BUDGET_BREAKER_FAILURE_THRESHOLD and opened_at is not None:
        cooldown_until = opened_at + timedelta(seconds=_BUDGET_BREAKER_COOLDOWN_SECONDS)
        breaker_state = "open" if now < cooldown_until else "half_open"

    return {
        "name": name,
        "budget": {
            "max_calls_per_hour": _BUDGET_MAX_CALLS_PER_HOUR,
            "max_calls_per_day": _BUDGET_MAX_CALLS_PER_DAY,
            "breaker_failure_threshold": _BUDGET_BREAKER_FAILURE_THRESHOLD,
            "breaker_cooldown_seconds": _BUDGET_BREAKER_COOLDOWN_SECONDS,
        },
        "usage": {
            "hourly_used": len(hourly_rows),
            "hourly_limit": _BUDGET_MAX_CALLS_PER_HOUR,
            "hourly_remaining": max(0, _BUDGET_MAX_CALLS_PER_HOUR - len(hourly_rows)),
            "daily_used": len(daily_rows),
            "daily_limit": _BUDGET_MAX_CALLS_PER_DAY,
            "daily_remaining": max(0, _BUDGET_MAX_CALLS_PER_DAY - len(daily_rows)),
        },
        "breaker": {
            "component": name,
            "state": breaker_state,
            "consecutive_failures": consecutive_failures,
            "opened_at": opened_at.isoformat() if opened_at is not None else None,
        },
        "last_24h": {
            "success": daily_success,
            "failure": daily_failure,
            "rejected_budget": daily_failure,
            "rejected_breaker": daily_breaker_rejections,
        },
        "cost": {
            "hourly_tokens": sum(int(row.get("tokens", 0) or 0) for row in hourly_rows),
            "daily_tokens": sum(int(row.get("tokens", 0) or 0) for row in daily_rows),
            "hourly_usd": round(
                sum(float(row.get("usd", 0.0) or 0.0) for row in hourly_rows),
                6,
            ),
            "daily_usd": round(
                sum(float(row.get("usd", 0.0) or 0.0) for row in daily_rows),
                6,
            ),
        },
        "last_reset_at": reset_ts.isoformat() if reset_ts is not None else None,
    }


def _budget_event_rows(journal: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    events: list[Any] = []
    if journal is not None:
        try:
            events.extend(list(journal.read_by_type("budget_commit")))
            events.extend(list(journal.read_by_type("budget_squirt")))
        except (AttributeError, TypeError, OSError):
            from .utils import _journal_events

            events = [
                event
                for event in _journal_events(journal)
                if getattr(event, "event_type", "") in {"budget_commit", "budget_squirt"}
            ]

    for event in events:
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        cost = getattr(event, "cost", None)
        rows.append(
            {
                "event_type": str(getattr(event, "event_type", "") or ""),
                "component": _budget_component_for_event(event),
                "ts": ts,
                "reason": str(getattr(event, "reason", "") or ""),
                "tokens": int(getattr(cost, "tokens", 0) or 0),
                "usd": float(getattr(cost, "usd", 0.0) or 0.0),
            }
        )
    return sorted(rows, key=lambda row: row["ts"])


def _budget_reset_map(journal: Any) -> dict[str, datetime]:
    resets: dict[str, datetime] = {}
    if journal is None:
        return resets
    try:
        events = list(journal.read_by_type("budget_breaker_reset"))
    except (AttributeError, TypeError, OSError):
        from .utils import _journal_events

        events = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "budget_breaker_reset"
        ]
    for event in events:
        component = str(getattr(event, "component", "") or "").strip()
        if not component:
            continue
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        if component not in resets or ts >= resets[component]:
            resets[component] = ts
    return resets


def _budget_component_for_event(event: Any) -> str:
    parts = [
        str(getattr(event, "reason", "") or ""),
        str(getattr(event, "actor", "") or ""),
        str(getattr(event, "source", "") or ""),
    ]
    text = " ".join(parts).lower()
    aliases: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("reflection", ("reflection", "reflect", "consolidation")),
        ("skill_forge", ("skill_forge", "skill forge", "forged_skill")),
        ("recipe_forge", ("recipe_forge", "recipe forge", "forge")),
        ("model_benchmark", ("model_benchmark", "model benchmark", "benchmark")),
        ("mcp_vet", ("mcp_vet", "mcp vet", "mcp")),
        ("protocol_repair", ("protocol_repair", "protocol repair", "drift", "repair")),
    )
    for component, markers in aliases:
        if any(marker in text for marker in markers):
            return component
    return "runtime"


def _budget_consecutive_failures(
    rows: list[dict[str, Any]],
    *,
    reset_ts: datetime | None,
) -> tuple[int, datetime | None]:
    count = 0
    opened_at: datetime | None = None
    eligible_rows = [
        row
        for row in sorted(rows, key=lambda item: item["ts"])
        if reset_ts is None or row["ts"] > reset_ts
    ]
    for row in eligible_rows:
        if row["event_type"] == "budget_squirt":
            count += 1
            opened_at = row["ts"]
        elif row["event_type"] == "budget_commit":
            count = 0
            opened_at = None
    return count, opened_at


def _budget_reason_mentions_breaker(reason: str) -> bool:
    text = reason.lower()
    return any(marker in text for marker in _BUDGET_BREAKER_REJECTION_MARKERS)


def _write_budget_breaker_reset(
    journal: Any,
    *,
    component: str,
    reason: str = "operator_reset",
) -> bool:
    if journal is None:
        return False
    try:
        if hasattr(journal, "write_budget_breaker_reset"):
            journal.write_budget_breaker_reset(
                component=component,
                reason=reason,
                actor="operator",
            )
        else:
            from runtime.memory.journal import BudgetBreakerResetEvent

            journal.write(
                BudgetBreakerResetEvent(
                    component=component,
                    reason=reason,
                    actor="operator",
                )
            )
        return True
    except (AttributeError, TypeError, OSError):
        return False
