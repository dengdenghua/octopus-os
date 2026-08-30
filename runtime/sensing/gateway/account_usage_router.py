from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from runtime.safety.auth.scope import scope_from_request


def _envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data, "error": None, "meta": {}}


def _money(value: float) -> str:
    text = f"{max(0.0, value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _iso(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _month_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    start = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def _period_window(period: str | None) -> tuple[str, datetime | None, datetime | None]:
    normalized = (period or "month").strip().lower()
    now = datetime.now(UTC)
    if normalized in {"all", "lifetime"}:
        return "all", None, None
    if normalized in {"day", "today"}:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return "day", start, start + timedelta(days=1)
    if normalized in {"week", "7d"}:
        start = now - timedelta(days=7)
        return "week", start, now
    start, end = _month_window(now)
    return "month", start, end


def _in_window(
    value: datetime | None,
    start: datetime | None,
    end: datetime | None,
) -> bool:
    if value is None:
        return True
    current = value.astimezone(UTC)
    if start is not None and current < start:
        return False
    return not (end is not None and current >= end)


@dataclass(frozen=True)
class _UsageRow:
    event_id: str
    event_type: str
    cost: float
    tokens_input: int
    tokens_output: int
    created_at: datetime | None
    model: str | None = None
    product: str | None = None
    source: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def total_tokens(self) -> int:
        return self.tokens_input + self.tokens_output


def _event_id(ev: Any) -> str:
    return str(getattr(ev, "event_id", "") or "")


def _usage_rows(
    journal: Any,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    scope: Any = None,
) -> list[_UsageRow]:
    rows: list[_UsageRow] = []

    try:
        token_events = list(journal.read_by_type("token_usage", scope=scope))
    except (OSError, ImportError, AttributeError, TypeError, ValueError):
        token_events = []
    for ev in token_events:
        if not _in_window(getattr(ev, "ts", None), start, end):
            continue
        input_tokens = int(getattr(ev, "input_tokens", 0) or 0)
        output_tokens = int(getattr(ev, "output_tokens", 0) or 0)
        rows.append(
            _UsageRow(
                event_id=_event_id(ev),
                event_type="completion",
                cost=float(getattr(ev, "cost_usd", 0.0) or 0.0),
                tokens_input=max(0, input_tokens),
                tokens_output=max(0, output_tokens),
                model=str(getattr(ev, "model", "") or "") or None,
                product="chat",
                source="journal:token_usage",
                created_at=getattr(ev, "ts", None),
                metadata={
                    "task_id": str(getattr(ev, "task_id", "") or ""),
                    "iteration": int(getattr(ev, "iteration", 0) or 0),
                    "conversation_id": getattr(ev, "conversation_id", None),
                    "agent_id": getattr(ev, "agent_id", None),
                },
            )
        )

    try:
        budget_events = list(journal.read_by_type("budget_commit", scope=scope))
    except (OSError, ImportError, AttributeError, TypeError, ValueError):
        budget_events = []
    for ev in budget_events:
        if not _in_window(getattr(ev, "ts", None), start, end):
            continue
        cost = getattr(ev, "cost", None)
        input_tokens = int(getattr(cost, "tokens_in", 0) or 0)
        output_tokens = int(getattr(cost, "tokens_out", 0) or 0)
        rows.append(
            _UsageRow(
                event_id=_event_id(ev),
                event_type="agent_run",
                cost=float(getattr(cost, "usd", 0.0) or 0.0),
                tokens_input=max(0, input_tokens),
                tokens_output=max(0, output_tokens),
                product="agent",
                source="journal:budget_commit",
                created_at=getattr(ev, "ts", None),
                metadata={
                    "task_id": str(getattr(ev, "task_id", "") or ""),
                    "actor": getattr(ev, "actor", None),
                    "reason": getattr(ev, "reason", ""),
                },
            )
        )

    return sorted(
        rows, key=lambda row: row.created_at or datetime.min.replace(tzinfo=UTC), reverse=True
    )


def _summary(rows: list[_UsageRow], period: str, *, user_id: str = "local") -> dict[str, Any]:
    by_model: dict[str, dict[str, Any]] = {}
    by_event_type: dict[str, int] = {}
    total_cost = 0.0
    total_tokens = 0

    for row in rows:
        total_cost += row.cost
        total_tokens += row.total_tokens
        by_event_type[row.event_type] = by_event_type.get(row.event_type, 0) + 1
        if row.model:
            model_entry = by_model.setdefault(row.model, {"count": 0, "_cost": 0.0})
            model_entry["count"] += 1
            model_entry["_cost"] += row.cost

    rendered_by_model = {
        model: {"count": int(data["count"]), "cost": _money(float(data["_cost"]))}
        for model, data in by_model.items()
    }
    most_used_model = None
    if rendered_by_model:
        most_used_model = max(
            rendered_by_model.items(),
            key=lambda item: (item[1]["count"], item[0]),
        )[0]

    return {
        "user_id": user_id,
        "period": period,
        "total_cost": _money(total_cost),
        "total_requests": len(rows),
        "total_tokens": total_tokens,
        "by_model": rendered_by_model,
        "by_event_type": by_event_type,
        "most_used_model": most_used_model,
    }


def create_account_usage_router(
    *,
    journal: Any,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> APIRouter:
    def _auth_dep(request: Request) -> None:
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        if require_auth and principal is None:
            raise HTTPException(401, "authentication required")
        request.state.usage_scope = scope_from_request(request)
        request.state.usage_actor = principal.actor_id if principal is not None else "local"

    router = APIRouter(tags=["account", "usage"], dependencies=[Depends(_auth_dep)])

    @router.get("/api/account/usage")
    def account_usage(request: Request) -> dict[str, Any]:
        period_start, period_end = _month_window()
        rows = _usage_rows(
            journal,
            start=period_start,
            end=period_end,
            scope=getattr(request.state, "usage_scope", None),
        )
        total_tokens = sum(row.total_tokens for row in rows)
        total_cost = sum(row.cost for row in rows)
        return _envelope(
            {
                "user_id": getattr(request.state, "usage_actor", "local"),
                "plan_id": "local",
                "period_start": _iso(period_start),
                "period_end": _iso(period_end),
                "requests_used": len(rows),
                "requests_limit": 0,
                "tokens_used": total_tokens,
                "tokens_limit": 0,
                "cost_incurred": _money(total_cost),
                "bonus_used": "0",
                "requests_remaining": 0,
                "usage_percentage": 0,
            }
        )

    @router.get("/api/account/usage/summary")
    def account_usage_summary(
        request: Request,
        period: str = Query(default="month"),
    ) -> dict[str, Any]:
        normalized, start, end = _period_window(period)
        return _envelope(
            _summary(
                _usage_rows(
                    journal,
                    start=start,
                    end=end,
                    scope=getattr(request.state, "usage_scope", None),
                ),
                normalized,
                user_id=getattr(request.state, "usage_actor", "local"),
            )
        )

    @router.get("/api/account/usage/events")
    def account_usage_events(
        request: Request,
        limit: int = Query(default=20, ge=1, le=200),
        page: int = Query(default=1, ge=1),
        event_type: str | None = Query(default=None),
        period: str = Query(default="month"),
    ) -> dict[str, Any]:
        _normalized, start, end = _period_window(period)
        rows = _usage_rows(
            journal,
            start=start,
            end=end,
            scope=getattr(request.state, "usage_scope", None),
        )
        if event_type:
            rows = [row for row in rows if row.event_type == event_type]
        total = len(rows)
        offset = (page - 1) * limit
        selected = rows[offset : offset + limit]
        return _envelope(
            {
                "data": [
                    {
                        "event_id": row.event_id,
                        "user_id": getattr(request.state, "usage_actor", "local"),
                        "event_type": row.event_type,
                        "product": row.product,
                        "model": row.model,
                        "cost": _money(row.cost),
                        "tokens_input": row.tokens_input,
                        "tokens_output": row.tokens_output,
                        "metadata": row.metadata or {},
                        "source": row.source,
                        "created_at": _iso(row.created_at),
                    }
                    for row in selected
                ],
                "pagination": {
                    "total": total,
                    "page": page,
                    "page_size": limit,
                    "total_pages": ceil(total / limit) if total else 0,
                },
            }
        )

    @router.get("/api/account/billing")
    def account_billing_summary(request: Request) -> dict[str, Any]:
        period_start, period_end = _month_window()
        rows = _usage_rows(
            journal,
            start=period_start,
            end=period_end,
            scope=getattr(request.state, "usage_scope", None),
        )
        current_period_cost = _money(sum(row.cost for row in rows))
        return _envelope(
            {
                "user_id": getattr(request.state, "usage_actor", "local"),
                "current_balance": "0",
                "bonus_balance": "0",
                "current_period_cost": current_period_cost,
                "last_invoice_date": None,
                "last_invoice_amount": None,
                "next_billing_date": None,
            }
        )

    @router.get("/api/account/billing/history")
    def account_billing_history(
        limit: int = Query(default=20, ge=1, le=200),
        page: int = Query(default=1, ge=1),
    ) -> dict[str, Any]:
        return _envelope(
            {
                "data": [],
                "pagination": {
                    "total": 0,
                    "page": page,
                    "page_size": limit,
                    "total_pages": 0,
                },
            }
        )

    return router


__all__ = ["create_account_usage_router"]
