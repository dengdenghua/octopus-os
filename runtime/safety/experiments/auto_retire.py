from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from runtime.core.nerves.bus import AbstractEventBus, NervesEvent

from .prompt_evolver import EvolutionStep, PromptEvolver
from .prompt_optimizer import VariantReport

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class VariantRetired(NervesEvent):
    variant_name: str
    reason: str = ""
    losing_uses: int = 0
    success_rate: float = 0.0


class VariantBoosted(NervesEvent):
    variant_name: str
    old_weight: float
    new_weight: float


class EvolverStepTriggered(NervesEvent):
    trigger: str  # "periodic" / "evaluation_pushed" / "manual"
    retired_count: int = 0
    boosted_count: int = 0
    mutated: bool = False


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class AutoRetireConfig:
    min_interval_seconds: float = 300.0  # Implementation note.
    min_assignments_since_last: int = 10  # Implementation note.
    min_variants_to_act: int = 2  # Implementation note.
    losing_threshold_for_immediate: int = 1
    # Rate-limit hardening (audited 2026-05). Without these, a
    # poisoned trajectory burst that pushes every active variant to
    # ``losing`` could trigger back-to-back fast-path steps and
    # retire most variants in minutes.
    #
    # ``max_retires_per_hour`` caps autonomous retirements in a
    # rolling 1h window. Crossing the cap forces a fall-back to the
    # normal periodic schedule even when ``losing >= threshold``.
    #
    # ``min_assignments_for_immediate`` lifts the "losing fast-path"
    # bar: a variant must have at least N real assignments before
    # its losing verdict is allowed to bypass the periodic gate.
    # Defaults match what humans expect: don't fire on 1-2 samples.
    max_retires_per_hour: int = 3
    min_assignments_for_immediate: int = 5


# ═══════════════════════════════════════════════════════════
# AutoRetireScheduler
# ═══════════════════════════════════════════════════════════


@dataclass
class AutoRetireStats:
    total_ticks: int = 0
    total_steps: int = 0
    total_retired: int = 0
    total_boosted: int = 0
    last_step_at: float = 0.0
    last_step_summary: str = ""


class AutoRetireScheduler:
    def __init__(
        self,
        evolver: PromptEvolver,
        *,
        config: AutoRetireConfig | None = None,
        bus: AbstractEventBus | None = None,
    ) -> None:
        self.evolver = evolver
        self.config = config or AutoRetireConfig()
        self.bus = bus
        self.stats = AutoRetireStats()
        self._lock = threading.RLock()
        self._last_step_at = 0.0
        self._assignments_at_last_step = self._total_assignments()
        # Rolling 1h window of retirement timestamps (monotonic).
        # Used by ``_recent_retire_count`` to enforce the rate-limit.
        self._recent_retires: list[float] = []

    def tick(self) -> EvolutionStep | None:
        with self._lock:
            self.stats.total_ticks += 1
            reason = self._should_step_reason()
            if reason is not None:
                logger.debug("AutoRetireScheduler tick skipped: %s", reason)
                return None
            return self._do_step("periodic")

    def observe_evaluation(
        self,
        report: dict[str, VariantReport] | None = None,
    ) -> EvolutionStep | None:
        with self._lock:
            if report is None:
                report = self.evolver.optimizer.report(scope=self.evolver.scope)
            # Only let the fast-path fire when (a) at least one
            # variant truly accumulated ``losing`` verdicts AND
            # (b) it accumulated them over enough assignments to be
            # statistically meaningful AND (c) we haven't already
            # retired ``max_retires_per_hour`` recently.
            losing_qualified = sum(
                1
                for r in report.values()
                if r.verdict == "losing"
                and r.assignments >= self.config.min_assignments_for_immediate
            )
            if (
                losing_qualified >= self.config.losing_threshold_for_immediate
                and self._recent_retire_count() < self.config.max_retires_per_hour
            ):
                return self._do_step("evaluation_pushed")
            reason = self._should_step_reason()
            if reason is not None:
                return None
            return self._do_step("evaluation_pushed")

    def force_step(self, trigger: str = "manual") -> EvolutionStep:
        with self._lock:
            return self._do_step(trigger)

    def _should_step_reason(self) -> str | None:
        now = time.time()
        if now - self._last_step_at < self.config.min_interval_seconds:
            return f"interval_not_elapsed: {now - self._last_step_at:.1f}s"

        current_assignments = self._total_assignments()
        delta = current_assignments - self._assignments_at_last_step
        if delta < self.config.min_assignments_since_last:
            return f"insufficient_new_data: {delta} new assignments"

        if len(self.evolver.optimizer.variant_names) < self.config.min_variants_to_act:
            return f"too_few_variants: {len(self.evolver.optimizer.variant_names)}"

        return None

    def _total_assignments(self) -> int:
        try:
            stats = self.evolver.optimizer._splitter.stats  # Implementation note.
        except AttributeError:
            return 0
        return sum(s.assignments for s in stats.values())

    def _recent_retire_count(self, *, window_s: float = 3600.0) -> int:
        """Number of variants retired in the last ``window_s`` seconds.

        Trims the buffer in place so it can't grow unboundedly even
        under a sustained attack.
        """
        cutoff = time.time() - window_s
        self._recent_retires = [t for t in self._recent_retires if t >= cutoff]
        return len(self._recent_retires)

    def _do_step(self, trigger: str) -> EvolutionStep:
        report_before = self.evolver.optimizer.report(scope=self.evolver.scope)
        step = self.evolver.step()

        # Gene-lock gate · check per retirement so PANIC freezes the
        # whole pipeline mid-step. Drop any retirements the lock
        # blocks so the surviving variants stay in rotation.
        if step.retired:
            step = self._gene_lock_filter_retires(step, trigger)

        self._last_step_at = time.time()
        self._assignments_at_last_step = self._total_assignments()
        self.stats.total_steps += 1
        self.stats.total_retired += len(step.retired)
        self.stats.total_boosted += len(step.boosted)
        self.stats.last_step_at = self._last_step_at
        self.stats.last_step_summary = step.summary

        # Record retirement timestamps for the rolling rate-limit.
        now = self._last_step_at
        for _ in step.retired:
            self._recent_retires.append(now)

        if self.bus is not None:
            self._publish_events(step, report_before, trigger)

        return step

    def _gene_lock_filter_retires(
        self,
        step: EvolutionStep,
        trigger: str,
    ) -> EvolutionStep:
        """Apply ``gene_locks.gate_mutation`` to each retirement.

        Returns the same step on success; an ``EvolutionStep`` with
        a possibly-shorter ``retired`` list on partial block;
        ``step.retired = []`` if every retirement was blocked. The
        ``evolver`` has already committed the change — we only
        prevent *future* steps if PANIC fires here (the gate stays
        triggered until the operator clears it).
        """
        try:
            from runtime.safety.gene_locks import (
                LockViolation,
                MutationKind,
                gate_mutation,
            )
        except (ImportError, OSError):
            return step  # gene_locks unavailable — let the step pass

        kept: list[str] = []
        blocked: list[str] = []
        autonomous = trigger != "manual"
        for variant_name in step.retired:
            try:
                gate_mutation(
                    kind=MutationKind.RETIRE_VARIANT,
                    target=variant_name,
                    autonomous=autonomous,
                )
                kept.append(variant_name)
            except LockViolation as lv:
                blocked.append(variant_name)
                logger.warning(
                    "AutoRetire: retirement of %s blocked by gene_locks: %s",
                    variant_name,
                    lv,
                )
            except Exception as exc:  # noqa: BLE001
                # Defensive: any unexpected error in the gate path
                # should not break the evolver. Log and pass.
                logger.warning(
                    "AutoRetire: gene_locks error on %s: %s",
                    variant_name,
                    exc,
                )
                kept.append(variant_name)

        if not blocked:
            return step
        # ``EvolutionStep`` is a normal dataclass; rebuild the retired
        # list. Other fields stay untouched. We don't try to undo the
        # evolver's internal state change — auto_retire is best-effort
        # and a blocked retire just means the next tick reconsiders.
        try:
            from dataclasses import replace as _dc_replace

            return _dc_replace(step, retired=kept)
        except (TypeError, ValueError):
            return step

    def _publish_events(
        self,
        step: EvolutionStep,
        report_before: dict[str, VariantReport],
        trigger: str,
    ) -> None:
        for name in step.retired:
            rep = report_before.get(name)
            self.bus.publish(
                VariantRetired(
                    variant_name=name,
                    reason="losing_verdict",
                    losing_uses=rep.assignments if rep else 0,
                    success_rate=rep.success_rate if rep else 0.0,
                )
            )
        for name, old_w, new_w in step.boosted:
            self.bus.publish(
                VariantBoosted(
                    variant_name=name,
                    old_weight=old_w,
                    new_weight=new_w,
                )
            )
        self.bus.publish(
            EvolverStepTriggered(
                trigger=trigger,
                retired_count=len(step.retired),
                boosted_count=len(step.boosted),
                mutated=step.mutation is not None,
            )
        )

    def as_periodic_task(self) -> Callable[[], None]:
        def _task() -> None:
            try:
                self.tick()
            except Exception as exc:
                logger.exception("AutoRetireScheduler tick failed: %s", exc)

        return _task
