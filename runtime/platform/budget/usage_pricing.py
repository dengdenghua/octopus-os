from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.budget.usage")

_PRICING: dict[str, dict[str, float]] = {
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.00},
    "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    "mimo-v2.5-pro": {"input": 0.50, "output": 2.00},
    "mimo-v2-flash": {"input": 0.05, "output": 0.20},
}


def price(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """按模型估算 USD 成本(单一定价真相源)。未知模型 → 0.0(=未定价,非免费)。

    纯函数,不记账;成本估算/报告处复用,避免各处硬编码定价。
    """
    prices = _PRICING.get(model or "")
    if prices is None:
        return 0.0
    return round(
        (input_tokens / 1_000_000) * prices["input"]
        + (output_tokens / 1_000_000) * prices["output"],
        6,
    )


@dataclass
class UsageRecord:
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    ts: str
    provider: str = ""
    agent_id: str = ""
    category: str = ""


@dataclass
class UsageConfig:
    log_path: str = "data/usage.jsonl"
    budget_usd: float | None = None
    warn_at_percent: float = 0.8


class UsagePricing:
    _instance: UsagePricing | None = None

    @classmethod
    def get(cls) -> UsagePricing:
        if cls._instance is None:
            import os

            raw = os.environ.get("ECHO_MAX_COST_USD", "").strip()
            budget: float | None = None
            if raw:
                try:
                    v = float(raw)
                    if v > 0:
                        budget = v
                except ValueError:  # expected · malformed env value leaves budget unset (no cap)
                    pass
            cls._instance = cls(UsageConfig(budget_usd=budget))
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Drop the singleton — for test isolation only.

        Production code should never call this; the singleton is a
        process-wide cost ledger and resetting it loses the running tally.
        """
        cls._instance = None

    def __init__(self, config: UsageConfig | None = None) -> None:
        self.config = config or UsageConfig()
        self._records: list[UsageRecord] = []
        self._total_cost: float = 0.0
        self._total_input: int = 0
        self._total_output: int = 0
        self._lock = threading.Lock()

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        *,
        provider: str = "",
        agent_id: str = "",
        category: str = "",
    ) -> UsageRecord:
        cost = self.compute_cost(model, input_tokens, output_tokens)
        rec = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            ts=datetime.now().isoformat(timespec="seconds"),
            provider=provider,
            agent_id=agent_id,
            category=category,
        )
        with self._lock:
            self._records.append(rec)
            self._total_cost += cost
            self._total_input += input_tokens
            self._total_output += output_tokens
        self._persist(rec)
        self._check_budget()
        return rec

    def compute_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        return price(model, input_tokens, output_tokens)

    @property
    def total_cost_usd(self) -> float:
        return round(self._total_cost, 4)

    @property
    def total_tokens(self) -> int:
        return self._total_input + self._total_output

    def summary(self) -> dict[str, Any]:
        by_model: dict[str, dict[str, Any]] = {}
        with self._lock:
            for r in self._records:
                if r.model not in by_model:
                    by_model[r.model] = {"count": 0, "cost": 0.0, "input": 0, "output": 0}
                by_model[r.model]["count"] += 1
                by_model[r.model]["cost"] += r.cost_usd
                by_model[r.model]["input"] += r.input_tokens
                by_model[r.model]["output"] += r.output_tokens
        return {
            "total_cost_usd": self.total_cost_usd,
            "total_tokens": self.total_tokens,
            "total_calls": len(self._records),
            "by_model": by_model,
            "budget_usd": self.config.budget_usd,
            "budget_used_pct": round(self._total_cost / self.config.budget_usd * 100, 1)
            if self.config.budget_usd
            else None,
        }

    def is_over_budget(self) -> bool:
        if self.config.budget_usd is None:
            return False
        return self._total_cost >= self.config.budget_usd

    def _check_budget(self) -> None:
        if self.config.budget_usd is None:
            return
        pct = self._total_cost / self.config.budget_usd
        if pct >= 1.0:
            _LOG.warning("BUDGET EXCEEDED: $%.4f / $%.2f", self._total_cost, self.config.budget_usd)
        elif pct >= self.config.warn_at_percent:
            _LOG.warning(
                "budget at %.0f%%: $%.4f / $%.2f",
                pct * 100,
                self._total_cost,
                self.config.budget_usd,
            )

    def _persist(self, rec: UsageRecord) -> None:
        if not self.config.log_path:
            return
        try:
            path = Path(self.config.log_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False, default=str) + "\n")
        except OSError as exc:
            _LOG.debug("usage persist failed: %s", exc)


__all__ = ["UsageConfig", "UsagePricing", "UsageRecord", "_PRICING"]
