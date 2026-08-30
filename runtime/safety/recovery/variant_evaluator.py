from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

_LOG = logging.getLogger("echo.gepa.variant_eval")


@dataclass
class VariantStat:
    """Per-variant aggregate · one entry per (base_recipe, variant)."""

    variant_id: str  # "" for legacy (no #suffix), "__default__"
    # for control, "vA"/"vB"/... for named
    uses: int = 0
    successes: int = 0
    avg_step_count: float = 0.0
    avg_cost_usd: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.successes / self.uses) if self.uses else 0.0

    @property
    def wilson_lower(self) -> float:
        if self.uses == 0:
            return 0.0
        n = self.uses
        p = self.successes / n
        z = 1.96  # 95% one-tailed lower
        denom = 1 + z * z / n
        center = p + z * z / (2 * n)
        margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
        return max(0.0, (center - margin) / denom)


@dataclass
class VariantComparison:
    """All variants sharing one base recipe · the comparison the
    operator (or auto-promote) reasons over."""

    base_recipe_id: str
    variants: list[VariantStat] = field(default_factory=list)

    @property
    def total_uses(self) -> int:
        return sum(v.uses for v in self.variants)

    def best(self) -> VariantStat | None:
        actives = [v for v in self.variants if v.uses > 0]
        if not actives:
            return None
        return max(actives, key=lambda v: v.wilson_lower)


def collect_variant_stats(
    journal: Any,
    *,
    base_recipe_id: str | None = None,
    scope: TenantScope | None = None,
) -> list[VariantComparison]:
    """Scan trajectory events · group by (base, variant) · return
    one comparison per base recipe.

    When ``base_recipe_id`` is given, returns only that base's
    comparison · single-element list (or empty when no data).
    """
    try:
        evs = read_learning_events(journal, "trajectory", scope=scope)
    except (OSError, ValueError, TypeError, AttributeError):
        return []
    # Aggregate.
    grouped: dict[str, dict[str, VariantStat]] = {}
    # base_recipe_id → variant_id → stat
    for ev in evs:
        traj = getattr(ev, "trajectory", None)
        if traj is None:
            continue
        rid = getattr(traj, "recipe_id", None)
        if not rid:
            continue
        # Split base from variant suffix.
        if "#" in rid:
            base, variant = rid.split("#", 1)
        else:
            base, variant = rid, ""
        if base_recipe_id and base != base_recipe_id:
            continue
        bucket = grouped.setdefault(base, {})
        stat = bucket.setdefault(variant, VariantStat(variant_id=variant))
        stat.uses += 1
        outcome = getattr(traj, "outcome", None)
        if (
            outcome is not None
            and getattr(outcome, "success", False)
            and not getattr(outcome, "degraded", False)
        ):
            stat.successes += 1
        # Step count is cheap to track.
        stat.avg_step_count = (
            stat.avg_step_count * (stat.uses - 1) + len(traj.steps or [])
        ) / stat.uses
        # Cost · pull from outcome if available.
        cost = float(getattr(getattr(outcome, "cost", None), "usd", 0) or 0)
        stat.avg_cost_usd = (stat.avg_cost_usd * (stat.uses - 1) + cost) / stat.uses
    # Materialise.
    out: list[VariantComparison] = []
    for base, bucket in grouped.items():
        cmp_ = VariantComparison(base_recipe_id=base)
        # Stable sort: known variants by id, then "" (legacy) last.
        for vid in sorted(
            bucket.keys(),
            key=lambda v: (v == "", v == "__default__", v),
        ):
            cmp_.variants.append(bucket[vid])
        out.append(cmp_)
    out.sort(key=lambda c: -c.total_uses)
    return out


# ═══════════════════════════════════════════════════════════
# Auto-promote · proposes new weights based on stats
# ═══════════════════════════════════════════════════════════


@dataclass
class PromoteProposal:
    """Suggested weight reshuffle · returned to the operator who
    decides whether to call ``set_weights`` to commit."""

    base_recipe_id: str
    weights: dict[str, int]  # variant_id → new_weight
    default_weight: int | None = None  # None = leave unchanged
    rationale: str = ""
    winner_variant_id: str | None = None
    winner_lower_bound: float = 0.0
    runner_up_lower_bound: float = 0.0


def propose_weights(
    comparison: VariantComparison,
    *,
    min_uses: int = 10,
    min_lead: float = 0.10,
) -> PromoteProposal | None:
    """Look at the per-variant stats and decide whether the data
    supports promoting a winner. Returns None when the comparison
    isn't actionable (insufficient data, no clear winner, etc.).

    Heuristic:
      * Any variant with uses < min_uses keeps its current weight
        in the proposal (handled at apply-time · we just say
        "leave it" by omitting it from the returned weights map).
      * The variant with the highest wilson_lower wins iff its
        lead over the runner-up's wilson_lower is ≥ min_lead.
      * Winner gets weight=10. Other named variants get weight=1
        (don't kill them entirely · keep collecting evidence).
      * Control branch (``__default__``) keeps its weight ·
        operator owns the control-group decision.
    """
    # Skip the legacy "" bucket and __default__ for "winner" purposes ·
    # they're not promotable variants.
    promotable = [
        v
        for v in comparison.variants
        if v.variant_id and v.variant_id != "__default__" and v.uses >= min_uses
    ]
    if len(promotable) < 2:
        return None
    promotable.sort(key=lambda v: -v.wilson_lower)
    winner, runner_up = promotable[0], promotable[1]
    lead = winner.wilson_lower - runner_up.wilson_lower
    if lead < min_lead:
        return None
    weights = {}
    for v in promotable:
        weights[v.variant_id] = 10 if v.variant_id == winner.variant_id else 1
    return PromoteProposal(
        base_recipe_id=comparison.base_recipe_id,
        weights=weights,
        default_weight=None,  # leave control branch alone
        winner_variant_id=winner.variant_id,
        winner_lower_bound=winner.wilson_lower,
        runner_up_lower_bound=runner_up.wilson_lower,
        rationale=(
            f"variant {winner.variant_id} leads {runner_up.variant_id} "
            f"by {lead * 100:.1f}pp on the 95% Wilson lower bound "
            f"({winner.wilson_lower:.3f} vs {runner_up.wilson_lower:.3f}; "
            f"{winner.uses}/{runner_up.uses} samples)"
        ),
    )


__all__ = [
    "VariantStat",
    "VariantComparison",
    "PromoteProposal",
    "collect_variant_stats",
    "propose_weights",
]
