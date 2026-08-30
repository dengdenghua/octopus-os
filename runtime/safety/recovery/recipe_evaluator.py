from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from runtime.adapters.instrumentation import trace_stage
from runtime.memory.journal import Journal, TrajectoryEvent
from runtime.platform.models import Trajectory
from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

RecipeVerdict = Literal["winning", "neutral", "losing", "insufficient_data"]


class RecipeScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    recipe_id: str
    uses: int
    successes: int
    success_rate: float
    avg_cost_usd: float
    avg_tokens: float
    avg_step_count: float
    first_seen: datetime
    last_seen: datetime
    verdict: RecipeVerdict
    score: float  # composite: 0..1


class RecipeEvaluationReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    trajectories_scanned: int
    recipes_found: int
    scores: list[RecipeScore] = Field(default_factory=list)

    @property
    def best(self) -> RecipeScore | None:
        active = [s for s in self.scores if s.verdict != "insufficient_data"]
        if not active:
            return None
        return max(active, key=lambda s: s.score)

    @property
    def worst(self) -> RecipeScore | None:
        active = [s for s in self.scores if s.verdict != "insufficient_data"]
        if not active:
            return None
        return min(active, key=lambda s: s.score)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class RecipeEvaluatorConfig:
    min_uses_to_score: int = 3  # Implementation note.
    winning_success_threshold: float = 0.75
    losing_success_threshold: float = 0.35
    w_success: float = 0.7
    w_cost: float = 0.2  # Implementation note.
    w_speed: float = 0.1  # Implementation note.


# ═══════════════════════════════════════════════════════════
# RecipeEvaluator
# ═══════════════════════════════════════════════════════════


class RecipeEvaluator:
    def __init__(
        self,
        journal: Journal,
        config: RecipeEvaluatorConfig | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        self.journal = journal
        self.config = config or RecipeEvaluatorConfig()
        self.scope = scope

    def evaluate(self) -> RecipeEvaluationReport:
        with trace_stage("regeneration.recipe_evaluator.evaluate"):
            trajs = self._collect_trajectories()

            groups: dict[str, list[Trajectory]] = defaultdict(list)
            for t in trajs:
                if t.recipe_id:
                    groups[t.recipe_id].append(t)

            scores: list[RecipeScore] = []
            all_costs = [t.outcome.cost.usd for g in groups.values() for t in g]
            median_cost = _median(all_costs) if all_costs else 0.01

            for recipe_id, cluster in groups.items():
                scores.append(self._score_recipe(recipe_id, cluster, median_cost))

            scores.sort(key=lambda s: -s.score)

            return RecipeEvaluationReport(
                trajectories_scanned=len(trajs),
                recipes_found=len(groups),
                scores=scores,
            )

    def _collect_trajectories(self) -> list[Trajectory]:
        events = read_learning_events(
            self.journal,
            "trajectory",
            scope=self.scope,
        )
        trajs = [e.trajectory for e in events if isinstance(e, TrajectoryEvent)]

        # Swarm tasks can emit both per-arm trajectories and one
        # aggregated ``strategy_id="swarm"`` trajectory for the same
        # task_id. When the aggregate exists, it is the whole-task
        # sample we want; keeping both copies inflates recipe uses and
        # biases success/cost statistics.
        by_task: dict[str, list[tuple[int, Trajectory]]] = defaultdict(list)
        order: list[str] = []
        for idx, traj in enumerate(trajs):
            key = str(traj.task_id)
            if key not in by_task:
                order.append(key)
            by_task[key].append((idx, traj))

        selected: list[Trajectory] = []
        for key in order:
            bucket = by_task[key]
            swarm_bucket = [item for item in bucket if item[1].strategy_id == "swarm"]
            if swarm_bucket:
                # The latest append is the authoritative retry/resume result.
                # Outcome-based selection could let an old clean/degraded row
                # hide the terminal aggregate actually written last.
                selected.append(max(swarm_bucket, key=lambda item: item[0])[1])
                continue
            selected.extend(traj for _idx, traj in bucket)
        return selected

    def _score_recipe(
        self,
        recipe_id: str,
        cluster: list[Trajectory],
        median_cost: float,
    ) -> RecipeScore:
        uses = len(cluster)
        successes = sum(1 for t in cluster if t.outcome.success and not t.outcome.degraded)
        success_rate = successes / uses

        costs = [t.outcome.cost.usd for t in cluster]
        tokens = [t.outcome.cost.tokens for t in cluster]
        step_counts = [t.step_count for t in cluster]
        avg_cost = sum(costs) / uses
        avg_tokens = sum(tokens) / uses
        avg_steps = sum(step_counts) / uses

        starts = [t.started_at for t in cluster]
        ends = [t.completed_at for t in cluster]
        first_seen = min(starts)
        last_seen = max(ends)

        if uses < self.config.min_uses_to_score:
            verdict: RecipeVerdict = "insufficient_data"
        elif success_rate >= self.config.winning_success_threshold:
            verdict = "winning"
        elif success_rate <= self.config.losing_success_threshold:
            verdict = "losing"
        else:
            verdict = "neutral"

        # Composite score (0..1)
        cost_score = 0.5 if median_cost <= 0 else _clamp01(median_cost / max(avg_cost, 1e-6))
        speed_score = 1.0 / max(avg_steps, 1.0)
        score = (
            self.config.w_success * success_rate
            + self.config.w_cost * cost_score
            + self.config.w_speed * speed_score
        )
        score = _clamp01(score)

        return RecipeScore(
            recipe_id=recipe_id,
            uses=uses,
            successes=successes,
            success_rate=success_rate,
            avg_cost_usd=avg_cost,
            avg_tokens=avg_tokens,
            avg_step_count=avg_steps,
            first_seen=first_seen,
            last_seen=last_seen,
            verdict=verdict,
            score=score,
        )


# ═══════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2


def format_recipe_report(report: RecipeEvaluationReport, *, max_rows: int = 10) -> str:
    if not report.scores:
        return "RECIPE EVALUATION: (no trajectories with recipe_id found)"

    lines = [
        f"RECIPE EVALUATION · {report.recipes_found} recipe(s) over {report.trajectories_scanned} trajectories",
    ]
    for s in report.scores[:max_rows]:
        lines.append(
            f"  [{s.verdict:<17}] {s.recipe_id:<20} · "
            f"score={s.score:.2f} · "
            f"uses={s.uses} · "
            f"success={s.success_rate * 100:.0f}% · "
            f"avg_cost=${s.avg_cost_usd:.4f}"
        )
    if len(report.scores) > max_rows:
        lines.append(f"  ... ({len(report.scores) - max_rows} more)")
    return "\n".join(lines)
