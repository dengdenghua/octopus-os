from __future__ import annotations

from datetime import datetime, timedelta
from threading import Lock
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.safety.invariants.enforce import enforces

from .primitives import CostEntry, TaskId, new_id, now_utc

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

BudgetStatus = Literal["active", "frozen", "exceeded"]


class BudgetLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    tokens: int = Field(..., gt=0)
    usd: float = Field(..., gt=0.0)
    latency_ms: int = Field(default=600_000, gt=0)
    freeze_at_ratio: float = Field(default=0.9, ge=0.5, le=1.0)


class InsufficientBudget(Exception):
    pass


class Budget:
    def __init__(self, task_id: TaskId, limits: BudgetLimits) -> None:
        self.task_id = task_id
        self.limits = limits
        self._lock = Lock()
        self._tokens_spent: int = 0
        self._tokens_in_spent: int = 0  # split for observability
        self._tokens_out_spent: int = 0  # (same invariants · monotonic)
        self._usd_spent: float = 0.0
        self._tokens_reserved: int = 0
        self._usd_reserved: float = 0.0
        self._status: BudgetStatus = "active"
        self._reservations: dict[UUID, tuple[CostEntry, datetime]] = {}
        # Utilization warning levels already fired · used by the
        # executor to dispatch budget_warn notifications at most
        # once per crossing. Tracked here (vs outside) so it
        # survives budget reuse across multiple tool calls.
        self._warn_levels_fired: set[int] = set()

    @property
    def status(self) -> BudgetStatus:
        return self._status

    @property
    def tokens_spent(self) -> int:
        return self._tokens_spent

    @property
    def tokens_in_spent(self) -> int:
        """Input-side (prompt) tokens spent · split for observability.

        Sum of all ``CostEntry.tokens_in`` committed. Monotonic
        (BDG-I1 same as ``tokens_spent``)."""
        return self._tokens_in_spent

    @property
    def tokens_out_spent(self) -> int:
        """Output-side (completion) tokens spent · split for observability.

        Sum of all ``CostEntry.tokens_out`` committed. Monotonic."""
        return self._tokens_out_spent

    @property
    def usd_spent(self) -> float:
        return self._usd_spent

    @property
    def tokens_reserved(self) -> int:
        return self._tokens_reserved

    @property
    def usd_reserved(self) -> float:
        return self._usd_reserved

    @property
    def utilization(self) -> float:
        token_limit = max(self.limits.tokens, 1)
        usd_limit = max(self.limits.usd, 1e-12)
        by_tokens = (self._tokens_spent + self._tokens_reserved) / token_limit
        by_usd = (self._usd_spent + self._usd_reserved) / usd_limit
        return max(by_tokens, by_usd)

    # ─── reserve / commit（BDG-I2 + BDG-I3）──────────────

    @enforces("BDG-I1", "BDG-I2")
    def reserve(self, estimated: CostEntry) -> UUID:
        with trace_stage(
            "budget.reserve",
            task_id=str(self.task_id),
        ) as span:
            span.set_attribute("echo.budget.tokens_estimated", estimated.tokens)
            span.set_attribute("echo.budget.usd_estimated", estimated.usd)

            with self._lock:
                if self._status != "active":
                    raise InsufficientBudget(f"budget is {self._status}")

                new_tokens = self._tokens_spent + self._tokens_reserved + estimated.tokens
                new_usd = self._usd_spent + self._usd_reserved + estimated.usd

                if new_tokens > self.limits.tokens or new_usd > self.limits.usd:
                    self._status = "exceeded"
                    span.set_attribute("echo.budget.exceeded", True)
                    raise InsufficientBudget(
                        f"reserve rejected: would exceed limit "
                        f"(tokens {new_tokens}/{self.limits.tokens}, "
                        f"usd {new_usd:.4f}/{self.limits.usd:.4f})"
                    )

                reservation_id = new_id()
                self._reservations[reservation_id] = (estimated, now_utc())
                self._tokens_reserved += estimated.tokens
                self._usd_reserved += estimated.usd

                span.set_attribute("echo.budget.utilization", self.utilization)
                return reservation_id

    @enforces("BDG-I3")
    def commit(self, reservation_id: UUID, actual: CostEntry) -> None:
        with self._lock:
            if reservation_id not in self._reservations:
                raise KeyError(f"unknown reservation {reservation_id}")
            estimated, _ = self._reservations.pop(reservation_id)

            self._tokens_reserved = max(0, self._tokens_reserved - estimated.tokens)
            self._usd_reserved = max(0.0, self._usd_reserved - estimated.usd)
            self._tokens_spent += actual.tokens
            self._tokens_in_spent += actual.tokens_in
            self._tokens_out_spent += actual.tokens_out
            self._usd_spent += actual.usd
            # Backstop (BDG-I3): reserve() only gates on the *estimate*,
            # which is routinely low for long output-heavy turns. Once the
            # real cost is committed and blows past the ceiling, freeze the
            # budget so every later reserve fails instead of silently running
            # over budget.
            if (
                self._tokens_spent > self.limits.tokens or self._usd_spent > self.limits.usd
            ) and self._status == "active":
                self._status = "exceeded"

    def refund_stale_reservations(self, ttl_seconds: int = 30) -> int:
        cutoff = now_utc() - timedelta(seconds=ttl_seconds)
        with self._lock:
            stale_ids = [
                rid for rid, (_, reserved_at) in self._reservations.items() if reserved_at <= cutoff
            ]
            for rid in stale_ids:
                estimated, _ = self._reservations.pop(rid)
                self._tokens_reserved = max(0, self._tokens_reserved - estimated.tokens)
                self._usd_reserved = max(0.0, self._usd_reserved - estimated.usd)
        return len(stale_ids)

    def check_warn_crossing(self) -> list[int]:
        """Return the list of warn-threshold percents newly crossed
        since the last call. Thresholds: 80 · 95. Each fires at
        most once per Budget lifetime.

        Executor calls this after every ``commit`` · returned values
        are dispatched as ``NotificationEvent(kind="budget_warn")``.
        Lock-free read is OK · float comparison is atomic and the
        set mutation is protected by ``_lock``.
        """
        newly: list[int] = []
        util_pct = int(self.utilization * 100)
        with self._lock:
            for threshold in (80, 95):
                if util_pct >= threshold and threshold not in self._warn_levels_fired:
                    self._warn_levels_fired.add(threshold)
                    newly.append(threshold)
        return newly

    def freeze(self) -> None:
        with self._lock:
            if self._status == "active":
                self._status = "frozen"


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════

ImmuneVerdict = Literal["allow", "quarantine", "reject"]

Origin = Literal["builtin", "public", "custom", "external"]


class AntigenSignature(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_id: str  # "mcp://anthropic/filesystem" / "skill://public/run_sql"
    entity_type: Literal["mcp_server", "skill", "webhook_source"]
    content_hash: str
    provider_sig: str | None = None
    origin: Origin = "public"
    first_seen: datetime = Field(default_factory=now_utc)


class RiskScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    sucker_id: str
    z_score_latency: float = 0.0
    z_score_tokens: float = 0.0
    z_score_cost: float = 0.0
    arg_outlier_score: float = 0.0
    composite: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""


class ImmuneReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: ImmuneVerdict
    signature: AntigenSignature
    strategy_used: Literal["tolerance", "innate", "memory", "adaptive"] = "innate"
    risk: RiskScore | None = None
    reason: str = ""
    ts: datetime = Field(default_factory=now_utc)
