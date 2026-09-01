# Prompt Evolver — the evolution engine for prompt variants.
# Implements mutation, crossover, pareto frontier selection, and retirement.
# The configuration snapshots that evolution may modify are stored separately
# in runtime.safety.recovery.genome_registry (versioned JSON + git commits).
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from runtime.adapters.instrumentation import trace_stage
from runtime.platform.i18n import get_safety_relax_markers
from runtime.safety.auth.scope import TenantScope

from .pareto import pareto_frontier_by_name
from .prompt_mutator import MutationProposal, PromptMutator
from .prompt_optimizer import PromptOptimizer, VariantReport


@dataclass
class EvolutionPolicy:
    retire_on_losing: bool = True
    retire_min_uses: int = 5  # Implementation note.
    retire_verdicts: tuple[str, ...] = ("losing",)
    min_variants_after_retire: int = 1  # Implementation note.

    use_pareto: bool = False
    pareto_metrics: tuple[str, ...] = (
        "success_rate",
        "avg_cost_usd",
        "avg_step_count",
    )
    pareto_maximize: tuple[bool, ...] = (
        True,
        False,
        False,  # Implementation note.
    )

    boost_winning: bool = True
    winning_weight_multiplier: float = 1.5  # Implementation note.
    max_weight: float = 10.0

    mutate_each_step: bool = True
    mutate_from_best: bool = True  # Implementation note.
    max_total_variants: int = 10  # Implementation note.

    crossover_each_step: bool = True  # Implementation note.
    crossover_requires_winning: bool = True  # Implementation note.


@dataclass
class EvolutionStep:
    retired: list[str] = field(default_factory=list)
    boosted: list[tuple[str, float, float]] = field(default_factory=list)
    # (name, old_weight, new_weight)
    mutation: MutationProposal | None = None
    mutation_skipped_reason: str = ""
    crossover: MutationProposal | None = None
    crossover_skipped_reason: str = ""
    snapshot_before: dict[str, VariantReport] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        bits = []
        if self.retired:
            bits.append(f"retired={self.retired}")
        if self.boosted:
            bits.append(f"boosted={[b[0] for b in self.boosted]}")
        if self.mutation is not None:
            bits.append(f"mutated={self.mutation.variant.name}")
        if self.crossover is not None:
            bits.append(f"crossed={self.crossover.variant.name}")
        if self.mutation_skipped_reason:
            bits.append(f"mutate_skipped={self.mutation_skipped_reason}")
        return " · ".join(bits) or "noop"


class PromptEvolver:
    def __init__(
        self,
        optimizer: PromptOptimizer,
        mutator: PromptMutator,
        policy: EvolutionPolicy | None = None,
        *,
        guard_digest_provider: Callable[[], dict[str, Any] | None] | None = None,
        trust_score_provider: Callable[[], float | None] | None = None,
        scope: TenantScope | None = None,
    ) -> None:
        self.optimizer = optimizer
        self.mutator = mutator
        self.policy = policy or EvolutionPolicy()
        self.history: list[EvolutionStep] = []
        # P1 evolution-loop integration: when set, supplies the latest
        # GuardTelemetry digest to seed mutation prompts. Closure form
        # (lambda: GuardTelemetry().digest()) keeps the evolver
        # decoupled from the telemetry path. Default None preserves
        # legacy behaviour exactly.
        self._guard_digest_provider = guard_digest_provider
        # P0 -> P1 bridge: when set, supplies the constitution trust
        # score so the evolver can refuse mutations that would relax
        # safety constraints when the agent is currently suspect.
        # Defaults to None (no gate).
        self._trust_score_provider = trust_score_provider
        # Learning is ownership-scoped.  ``None`` is intentionally
        # legacy-only in the downstream readers, preserving local/old journal
        # compatibility without letting a process-global evolver train on
        # authenticated tenants.
        self.scope = scope

    def _fetch_guard_digest(self) -> dict[str, Any] | None:
        """Pull the latest GuardTelemetry digest, defensively.

        A failing provider (telemetry file missing, parse error, etc.)
        must NEVER block evolution — the mutator simply runs without
        the extra signal. Returns None on any failure or when no
        provider is configured.
        """
        if self._guard_digest_provider is None:
            return None
        try:
            digest = self._guard_digest_provider()
        except Exception:  # noqa: BLE001 — telemetry must not break evolution
            return None
        if not isinstance(digest, dict):
            return None
        if int(digest.get("total_hits") or 0) <= 0:
            return None
        return digest

    def _fetch_trust_score(self) -> float | None:
        """Pull the constitution trust score, defensively.

        Errors / non-float returns degrade to ``None`` so the trust
        gate becomes a no-op; the evolver keeps working unchanged.
        """
        if self._trust_score_provider is None:
            return None
        try:
            score = self._trust_score_provider()
        except Exception:  # noqa: BLE001 — trust must not break evolution
            return None
        if not isinstance(score, (int, float)):
            return None
        return float(score)

    def _mutation_relaxes_safety(self, suffix: str) -> bool:
        """Heuristic: does this mutated suffix push the agent toward
        more permissive behaviour?

        We can't run the full LLM judge inside the evolver — that would
        be too slow. Instead, check for a small bag of phrases that
        empirically correlate with "loosen the leash" prompts. False
        positives are tolerable: when the agent is suspect we'd rather
        skip a borderline mutation than risk a real relaxation.

        Tuned conservatively: matched phrases must appear as a whole
        word/segment, not as a substring of unrelated text.

        Markers are loaded from the active locale via the i18n catalog
        so safety phrases follow the user's chosen language.
        """
        if not suffix:
            return False
        markers = get_safety_relax_markers()
        if not markers:
            return False
        lowered = suffix.lower()
        return any(marker.lower() in lowered for marker in markers)

    def _trust_gate_decision(
        self,
        proposal_suffix: str,
    ) -> tuple[bool, str]:
        """Decide whether to admit a mutated variant given current trust.

        Returns ``(allow, reason)``. The reason explains what happened
        for the EvolutionStep audit trail.

        Policy:
          * No trust provider / unknown score → allow (don't gate when
            we don't have data).
          * Trust >= 0.85 ("trusted") → allow anything.
          * Trust <= 0.20 ("suspect") AND the mutation looks relaxing
            → block. Suspect agents only get tighter or neutral prompts.
          * Otherwise (neutral / suspect-but-not-relaxing) → allow.
        """
        score = self._fetch_trust_score()
        if score is None:
            return (True, "")
        try:
            from runtime.safety.validation.trust_signal import (
                classify_trust_score,
            )

            bucket = classify_trust_score(score)
        except Exception:  # noqa: BLE001 — fail-open
            return (True, "")
        if bucket != "suspect":
            return (True, "")
        if not self._mutation_relaxes_safety(proposal_suffix):
            return (True, "")
        return (
            False,
            f"trust_gate_block: trust={score:.2f} (suspect) + mutation pushes toward relaxation",
        )

    def step(self) -> EvolutionStep:
        with trace_stage("camouflage.prompt_evolver.step") as span:
            report = self.optimizer.report(scope=self.scope)
            step = EvolutionStep(snapshot_before={n: r for n, r in report.items()})

            if self.policy.retire_on_losing:
                self._retire_losers(report, step)

            if self.policy.boost_winning:
                self._boost_winners(report, step)

            if self.policy.mutate_each_step:
                self._mutate_step(report, step)

            if self.policy.crossover_each_step:
                self._crossover_step(report, step)

            self.history.append(step)
            span.set_attribute("echo.evolver.retired", len(step.retired))
            span.set_attribute("echo.evolver.boosted", len(step.boosted))
            span.set_attribute(
                "echo.evolver.mutated",
                step.mutation.variant.name if step.mutation else "",
            )
            return step

    def _retire_losers(
        self,
        report: dict[str, VariantReport],
        step: EvolutionStep,
    ) -> None:
        if self.policy.use_pareto:
            candidates = self._retire_candidates_pareto(report)
        else:
            candidates = self._retire_candidates_verdict(report)

        candidates.sort(key=lambda nr: nr[1].recipe_score.score if nr[1].recipe_score else 0.0)
        for name, _r in candidates:
            remaining = len(self.optimizer.variant_names)
            if remaining <= self.policy.min_variants_after_retire:
                break
            ok = self.optimizer.retire_variant(name)
            if ok:
                step.retired.append(name)

    def _retire_candidates_verdict(
        self,
        report: dict[str, VariantReport],
    ) -> list[tuple[str, VariantReport]]:
        return [
            (name, r)
            for name, r in report.items()
            if r.verdict in self.policy.retire_verdicts
            and r.assignments >= self.policy.retire_min_uses
        ]

    def _retire_candidates_pareto(
        self,
        report: dict[str, VariantReport],
    ) -> list[tuple[str, VariantReport]]:
        scored = {
            name: r
            for name, r in report.items()
            if r.recipe_score is not None and r.assignments >= self.policy.retire_min_uses
        }
        if len(scored) <= 1:
            return []

        metrics = self.policy.pareto_metrics
        maximize = {m: self.policy.pareto_maximize[i] for i, m in enumerate(metrics)}
        points = {
            name: {
                "success_rate": r.recipe_score.success_rate,
                "avg_cost_usd": r.recipe_score.avg_cost_usd,
                "avg_step_count": r.recipe_score.avg_step_count,
                "avg_tokens": r.recipe_score.avg_tokens,
            }
            for name, r in scored.items()
        }
        frontier = pareto_frontier_by_name(
            points,
            metrics,
            maximize=maximize,
        )
        return [(name, r) for name, r in scored.items() if name not in frontier]

    def _boost_winners(
        self,
        report: dict[str, VariantReport],
        step: EvolutionStep,
    ) -> None:
        for name, r in report.items():
            if r.verdict != "winning":
                continue
            if name not in self.optimizer.variant_names:
                continue  # Implementation note.
            old_variant = self.optimizer._variants[name]
            new_w = min(
                self.policy.max_weight,
                old_variant.weight * self.policy.winning_weight_multiplier,
            )
            if new_w > old_variant.weight:
                self.optimizer.adjust_weight(name, new_w)
                step.boosted.append((name, old_variant.weight, new_w))

    def _mutate_step(
        self,
        report: dict[str, VariantReport],
        step: EvolutionStep,
    ) -> None:
        if len(self.optimizer.variant_names) >= self.policy.max_total_variants:
            step.mutation_skipped_reason = "variant_pool_full"
            return

        base_name = self._pick_base_for_mutation(report)
        if base_name is None:
            step.mutation_skipped_reason = "no_base_candidate"
            return

        base = self.optimizer._variants[base_name]
        digest = self._fetch_guard_digest()
        proposal = self.mutator.propose(
            base=base,
            journal=self.optimizer.stack.journal,
            recipe_id=self.optimizer.planner_for(base_name).recipe_hash(),
            guard_digest=digest,
            scope=self.scope,
        )
        if proposal is None:
            step.mutation_skipped_reason = "mutator_returned_none"
            return
        # P0 -> P1: trust-aware gate. When agent is suspect, refuse a
        # mutation whose suffix looks like it would relax safety.
        proposed_suffix = getattr(proposal.variant, "system_prompt_suffix", "") or ""
        allow, gate_reason = self._trust_gate_decision(proposed_suffix)
        if not allow:
            step.mutation_skipped_reason = gate_reason
            return
        try:
            self.optimizer.add_variant(proposal.variant)
            step.mutation = proposal
        except ValueError as e:
            step.mutation_skipped_reason = f"add_failed: {e}"

    def _pick_base_for_mutation(
        self,
        report: dict[str, VariantReport],
    ) -> str | None:
        active = [(n, r) for n, r in report.items() if n in self.optimizer.variant_names]
        if not active:
            return None
        if self.policy.mutate_from_best:
            active.sort(
                key=lambda nr: nr[1].recipe_score.score if nr[1].recipe_score else -1.0,
                reverse=True,
            )
        return active[0][0]

    def _crossover_step(
        self,
        report: dict[str, VariantReport],
        step: EvolutionStep,
    ) -> None:
        if len(self.optimizer.variant_names) >= self.policy.max_total_variants:
            step.crossover_skipped_reason = "variant_pool_full"
            return

        if self.policy.crossover_requires_winning:
            active = [
                (n, r)
                for n, r in report.items()
                if n in self.optimizer.variant_names and r.verdict == "winning"
            ]
        else:
            active = [
                (n, r)
                for n, r in report.items()
                if n in self.optimizer.variant_names and r.verdict in ("winning", "neutral")
            ]

        if len(active) < 2:
            step.crossover_skipped_reason = "need_2_winners"
            return

        active.sort(
            key=lambda nr: nr[1].recipe_score.score if nr[1].recipe_score else -1.0,
            reverse=True,
        )
        a_name = active[0][0]
        b_name = active[1][0]

        parent_a = self.optimizer._variants[a_name]
        parent_b = self.optimizer._variants[b_name]
        proposal = self.mutator.propose_merge(parent_a, parent_b)
        if proposal is None:
            step.crossover_skipped_reason = "merge_returned_none"
            return
        try:
            self.optimizer.add_variant(proposal.variant)
            step.crossover = proposal
        except ValueError as e:
            step.crossover_skipped_reason = f"add_failed: {e}"
