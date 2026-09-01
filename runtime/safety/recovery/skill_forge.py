from __future__ import annotations

import hashlib
import logging

_LOG = logging.getLogger(__name__)
from collections import defaultdict  # noqa: E402
from collections.abc import Callable  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Any  # noqa: E402

from pydantic import BaseModel, ConfigDict, Field  # noqa: E402

from runtime.adapters.instrumentation import trace_stage  # noqa: E402
from runtime.execution.suckers import (  # noqa: E402
    Skill,
    SkillExpect,
    SkillRegistry,
    SkillTestCase,
    SkillTester,
    SkillTestReport,
    SkillTestResult,
    SkillTestsFailed,
)
from runtime.memory.journal import Journal, TrajectoryEvent  # noqa: E402
from runtime.platform.models import Trajectory  # noqa: E402
from runtime.platform.process.paths import app_paths  # noqa: E402
from runtime.safety.approval.approval_gate import is_dangerous_tool  # noqa: E402
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path  # noqa: E402
from runtime.safety.evolution.candidate_registry import (  # noqa: E402
    CandidateRegistry,
    CandidateStatus,
    EvolutionCandidate,
)
from runtime.safety.recovery.tenant_scope import read_learning_events  # noqa: E402

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _composite_ran_successfully(result: Any) -> bool:
    """Golden-test predicate · True iff the meta-handler's returned
    dict reports every step succeeded.

    The meta-handler returns ``{"success": True, ...}`` on a clean
    run and ``{"success": False, "failed_at": N, ...}`` when a
    sub-skill raises. Checking the ``success`` key lets shadow
    validation distinguish the two without re-raising internally.
    """
    if not isinstance(result, dict):
        return False
    return bool(result.get("success"))


def pattern_signature(traj: Trajectory) -> str:
    seq = "→".join(s.action.sucker_id for s in traj.steps)
    return hashlib.blake2b(seq.encode("utf-8"), digest_size=8).hexdigest()


def path_of(traj: Trajectory) -> list[str]:
    return [s.action.sucker_id for s in traj.steps]


def _is_clean_success(traj: Trajectory) -> bool:
    """Return whether a trajectory is safe to use as positive evidence."""

    return traj.outcome.success and not traj.outcome.degraded


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


class ForgedSkillCandidate(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    candidate_id: str = Field(..., min_length=8)
    name: str
    description: str = ""
    path_signature: str
    underlying_sequence: list[str]  # Implementation note.
    source_trajectory_ids: list[str]
    source_sample_count: int
    source_success_rate: float
    generated_tests: list[SkillTestCase] = Field(default_factory=list)
    status: str = "proposed"  # proposed | shadow_pass | shadow_fail | public | retired
    # Per-step args_template captured from the cluster sample.
    # ``step_templates[i]`` is the unresolved template that step i ran
    # with in the original trajectory (may reference prior steps via
    # ``{nX.key}``). Used by ``_build_meta_handler`` to rebuild the
    # data flow between sub-skills. Empty list means "no templates
    # available" — the handler falls back to the MVP ``_prev`` hint.
    step_templates: list[dict[str, Any]] = Field(default_factory=list)


class SkillForgeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidates_total: int
    promoted: list[str]
    retired: list[str]
    shadow_failed: list[str]
    # Candidates refused auto-promotion because they wrap dangerous
    # primitives (e.g. forged GUI macros over mouse_click/keyboard_type).
    # Not dropped — routed to governed human approval. Also appear in
    # ``retired`` so they are not re-promoted next tick.
    quarantined: list[str] = Field(default_factory=list)
    governed: list[str] = Field(default_factory=list)
    evolution_candidates: list[dict[str, Any]] = Field(default_factory=list)
    reports: dict[str, SkillTestReport] = Field(default_factory=dict)


class UnsafeSkillPromotionError(ValueError):
    """Raised when a forged public skill would wrap dangerous tools."""


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


@dataclass
class ForgeConfig:
    min_hits: int = 3
    min_success_rate: float = 0.70
    shadow_runs: int = 5
    shadow_success_threshold: float = 0.80
    max_candidates_per_run: int = 10
    governed_rollout: bool = False
    candidate_registry_path: Any = None


class SkillForge:
    def __init__(
        self,
        journal: Journal,
        registry: SkillRegistry,
        config: ForgeConfig | None = None,
        name_generator: Callable[[list[str]], str] | None = None,
        *,
        auto_persist_dir: Any = None,  # Path | str | None
        scope: TenantScope | None = None,
    ) -> None:
        self.journal = journal
        self.registry = registry
        self.config = config or ForgeConfig()
        self._name_generator = name_generator or _default_name_for
        self.scope = scope
        self.auto_persist_dir = (
            tenant_scoped_path(auto_persist_dir, scope)
            if auto_persist_dir is not None and scope is not None
            else auto_persist_dir
        )

    @classmethod
    def for_governed_rollout(
        cls,
        *,
        journal: Journal,
        registry: SkillRegistry,
        candidate_registry_path: Any = None,
        name_generator: Callable[[list[str]], str] | None = None,
        scope: TenantScope | None = None,
    ) -> SkillForge:
        """Build a forge that can only stage typed evolution candidates.

        The scheduler uses this capability-based constructor instead of
        importing and assembling :class:`ForgeConfig` itself.  That keeps the
        scheduler compatible with mixed-version modules while making the safe
        behaviour explicit: if this factory is absent, the scheduler refuses
        to run SkillForge rather than falling back to direct live promotion.
        """
        return cls(
            journal=journal,
            registry=registry,
            config=ForgeConfig(
                governed_rollout=True,
                candidate_registry_path=candidate_registry_path,
            ),
            name_generator=name_generator,
            scope=scope,
        )

    # ─── propose ────────────────────────────────────

    def propose(self) -> list[ForgedSkillCandidate]:
        with trace_stage("regeneration.skill_forge.propose"):
            trajs = self._collect_successful_trajectories()

            clusters: dict[str, list[Trajectory]] = {}
            for t in trajs:
                if t.step_count < 2:
                    continue  # Implementation note.
                sig = pattern_signature(t)
                clusters.setdefault(sig, []).append(t)

            candidates: list[ForgedSkillCandidate] = []
            for sig, cluster in clusters.items():
                hits = len(cluster)
                if hits < self.config.min_hits:
                    continue
                clean_cluster = [t for t in cluster if _is_clean_success(t)]
                success_rate = len(clean_cluster) / hits
                if not clean_cluster or success_rate < self.config.min_success_rate:
                    continue
                candidates.append(self._make_candidate(sig, clean_cluster, success_rate))

            candidates.sort(key=lambda c: -(c.source_sample_count * c.source_success_rate))
            return candidates[: self.config.max_candidates_per_run]

    # ─── shadow ─────────────────────────────────────

    def shadow_validate(self, candidate: ForgedSkillCandidate) -> tuple[bool, SkillTestReport]:
        with trace_stage("regeneration.skill_forge.shadow_validate"):
            handler = self._build_meta_handler(candidate)
            test_skill = Skill(
                name=candidate.name,
                description=candidate.description,
                trusted_source=f"skill://forged/{candidate.candidate_id}",
                handler=handler,
                tests=candidate.generated_tests,
                affinity=["forged"],
            )
            tester = SkillTester()
            runs = max(1, self.config.shadow_runs)
            threshold = self.config.shadow_success_threshold
            reports: list[SkillTestReport] = []
            passed_runs = 0
            for _ in range(runs):
                report = tester.run(test_skill)
                reports.append(report)
                if report.overall_passed:
                    passed_runs += 1
            passed = (passed_runs / runs) >= threshold
            merged = self._merge_shadow_reports(
                candidate.name,
                reports,
                overall_passed=passed,
            )
            return passed, merged

    # ─── promote ────────────────────────────────────

    def promote_to_public(self, candidate: ForgedSkillCandidate) -> SkillTestReport:
        dangerous = self._dangerous_underlying_skills(candidate)
        if dangerous:
            names = ", ".join(dangerous)
            raise UnsafeSkillPromotionError(
                f"refusing to promote forged skill {candidate.name!r}; "
                f"underlying sequence includes dangerous skill(s): {names}"
            )
        handler = self._build_meta_handler(candidate)
        skill = Skill(
            name=candidate.name,
            description=candidate.description,
            trusted_source=f"skill://forged/{candidate.candidate_id}",
            handler=handler,
            tests=candidate.generated_tests,
            affinity=["forged"],
        )
        return self.registry.register(skill)  # type: ignore[return-value]

    def run(self) -> SkillForgeResult:
        # Autonomous path: cluster repeated journal trajectories, then forge.
        return self._forge_candidates(self.propose())

    def forge_selected(
        self,
        trajectories: list[Trajectory],
        *,
        min_steps: int = 2,
    ) -> SkillForgeResult:
        """Active single-demo forge: distil human-chosen successful trajectories
        into skills *now*, bypassing the ``min_hits``/``min_success_rate``
        statistical gate.

        Rationale: when a person actively demonstrates a process to teach the
        agent, requiring them to repeat it ``min_hits`` (3) times is absurd —
        one good demo should mint a (provisional) skill, and the *existing*
        evolution machinery (turn scoring, success-rate tracking, the
        autonomous :meth:`run` re-clustering as the skill gets re-used) refines
        it over the next few self-runs.

        Safety is NOT bypassed: each candidate still goes through shadow
        validation and the immune gate, so a macro over dangerous primitives is
        routed to governed quarantine for human approval — never auto-granted.
        """
        clusters: dict[str, list[Trajectory]] = {}
        for t in trajectories:
            if not _is_clean_success(t) or t.step_count < min_steps:
                continue  # a <2-step "macro" is not a reusable skill
            clusters.setdefault(pattern_signature(t), []).append(t)

        candidates: list[ForgedSkillCandidate] = []
        for sig, cluster in clusters.items():
            candidates.append(self._make_candidate(sig, cluster, 1.0))
        return self._forge_candidates(candidates)

    def _forge_candidates(self, candidates: list[ForgedSkillCandidate]) -> SkillForgeResult:
        """Shared promote/quarantine pipeline for both the autonomous
        (:meth:`run`) and active (:meth:`forge_selected`) forge paths."""
        promoted: list[str] = []
        retired: list[str] = []
        shadow_failed: list[str] = []
        quarantined: list[str] = []
        governed: list[str] = []
        evolution_candidates: list[dict[str, Any]] = []
        reports: dict[str, SkillTestReport] = {}

        for cand in candidates:
            if self._has_existing_quarantine_decision(cand):
                # The same deterministic candidate was already routed to
                # human review on an earlier tick. Re-running shadow tests and
                # appending another quarantine decision only pollutes the
                # review queue; keep the tick idempotent.
                retired.append(cand.name)
                continue
            passed, shadow_report = self.shadow_validate(cand)
            reports[cand.name] = shadow_report
            if not passed:
                shadow_failed.append(cand.name)
                retired.append(cand.name)
                continue
            dangerous = self._dangerous_underlying_skills(cand)
            if dangerous:
                exc = UnsafeSkillPromotionError(
                    f"refusing to promote forged skill {cand.name!r}; "
                    f"underlying sequence includes dangerous skill(s): {', '.join(dangerous)}"
                )
                quarantined.append(cand.name)
                retired.append(cand.name)
                self._record_quarantine_decision(cand, dangerous, exc)
                continue
            # A tenant-scoped request cannot safely register executable code
            # in the process-global SkillRegistry.  Until registries are
            # tenant-partitioned, stage an auditable candidate instead.  An
            # explicitly cross-tenant operator retains the legacy global
            # promotion capability; local scope=None behaviour is unchanged.
            if self.config.governed_rollout or (
                self.scope is not None and not self.scope.allow_cross_tenant
            ):
                evolution_candidate = self._record_evolution_candidate(cand, shadow_report)
                governed.append(cand.name)
                evolution_candidates.append(evolution_candidate.to_wire())
                continue
            if self.registry.has(cand.name):
                # Already promoted on an earlier tick. The forge re-proposes
                # identical auto-generated skills every tick (deterministic
                # clustering by signature), so without this skip
                # promote_to_public() → registry.register() raises a bare
                # ``ValueError("duplicate skill name")`` every tick.
                retired.append(cand.name)
                continue
            try:
                self.promote_to_public(cand)
                promoted.append(cand.name)
                self._maybe_persist(cand)
                try:
                    from runtime.safety.evolution.canary import CanaryManager

                    CanaryManager().register(cand.name)
                except Exception as _exc:
                    _LOG.debug("canary register after forge failed: %s", _exc)
            except UnsafeSkillPromotionError as exc:
                # The forged macro wraps dangerous primitives — e.g. a
                # recorded GUI sequence over mouse_click / keyboard_type, all
                # of which are ``is_dangerous_tool``. Auto-granting it would
                # let a replayed click-sequence run unattended; silently
                # dropping it (the old generic-ValueError path) loses a real
                # learned capability. Instead route it to governed quarantine
                # for human approval. Must precede the generic ``ValueError``
                # clause below — UnsafeSkillPromotionError subclasses it.
                dangerous = self._dangerous_underlying_skills(cand)
                _LOG.info(
                    "forge candidate %s quarantined; dangerous underlying: %s",
                    cand.name,
                    ", ".join(dangerous) or "(affinity)",
                )
                quarantined.append(cand.name)
                retired.append(cand.name)
                self._record_quarantine_decision(cand, dangerous, exc)
            except (SkillTestsFailed, ValueError) as exc:
                # ValueError subsumes UnsafeSkillPromotionError (a subclass)
                # AND the bare duplicate-name ValueError from
                # registry.register(). The latter is NOT a subclass match for
                # UnsafeSkillPromotionError, so it used to escape this except
                # and crash the whole regeneration tick.
                _LOG.debug(
                    "forge promote skipped (%s): %s",
                    type(exc).__name__,
                    exc,
                )
                shadow_failed.append(cand.name)
                retired.append(cand.name)

        return SkillForgeResult(
            candidates_total=len(candidates),
            promoted=promoted,
            retired=retired,
            shadow_failed=shadow_failed,
            quarantined=quarantined,
            governed=governed,
            evolution_candidates=evolution_candidates,
            reports=reports,
        )

    def _record_evolution_candidate(
        self,
        candidate: ForgedSkillCandidate,
        shadow_report: SkillTestReport,
    ) -> EvolutionCandidate:
        registry_path = self.config.candidate_registry_path or app_paths().evolution_candidates_path
        if self.scope is not None:
            registry_path = tenant_scoped_path(registry_path, self.scope)
        registry = CandidateRegistry(registry_path, tenant_scope=self.scope)
        evolution_candidate = registry.propose(
            gene_type="skill",
            scope=f"skill.{candidate.name}",
            patch={
                "op": "register_forged_skill",
                "name": candidate.name,
                "description": candidate.description,
                "underlying_sequence": list(candidate.underlying_sequence),
                "step_templates": list(candidate.step_templates),
                "path_signature": candidate.path_signature,
            },
            proposer="skill_forge",
            lineage_id=f"skill_forge:{candidate.path_signature}",
            role_id="general",
            task_domain="workflow_automation",
            risk_level="medium",
            source_failures=[],
            metadata={
                "forge_candidate_id": candidate.candidate_id,
                "source_trajectory_ids": list(candidate.source_trajectory_ids),
                "source_sample_count": candidate.source_sample_count,
                "source_success_rate": candidate.source_success_rate,
                "shadow_report": shadow_report.model_dump(mode="json"),
            },
            tenant_id=self.scope.tenant_id if self.scope is not None else None,
            owner_actor_id=self.scope.actor_id if self.scope is not None else None,
        )
        gates = {
            "source_evidence": candidate.source_sample_count >= self.config.min_hits,
            "source_success_rate": candidate.source_success_rate >= self.config.min_success_rate,
            "deterministic_shadow": shadow_report.overall_passed,
            "dangerous_primitive_free": True,
        }
        evolution_candidate = registry.record_evidence(
            evolution_candidate.candidate_id,
            hard_gate_results=gates,
            metric_vector={
                "source_success_rate": candidate.source_success_rate,
                "source_sample_count": float(candidate.source_sample_count),
            },
            metadata={
                "awaiting_gates": [name for name, passed in gates.items() if not passed],
                "next_stage": "structured_shadow" if all(gates.values()) else "validation",
            },
        )
        if evolution_candidate.status == CandidateStatus.PROPOSED and all(gates.values()):
            evolution_candidate = registry.transition(
                evolution_candidate.candidate_id,
                CandidateStatus.VALIDATED,
                metadata={"next_stage": "structured_shadow"},
            )
        return evolution_candidate

    def _has_existing_quarantine_decision(self, candidate: ForgedSkillCandidate) -> bool:
        try:
            events = read_learning_events(
                self.journal,
                "skill_proposal_decision",
                scope=self.scope,
            )
        except Exception as exc:  # noqa: BLE001 — journal lookup must not break forge.
            _LOG.debug("forge quarantine dedupe skipped: %s", exc)
            return False
        for event in events:
            if getattr(event, "proposal_kind", "") != "skill_forge":
                continue
            if getattr(event, "candidate_id", "") != candidate.candidate_id:
                continue
            if str(getattr(event, "decision", "")).lower() == "quarantined":
                return True
        return False

    def _record_quarantine_decision(
        self,
        candidate: ForgedSkillCandidate,
        dangerous: list[str],
        exc: Exception,
    ) -> None:
        """Surface a refused forged macro to the governed approval queue.

        Best-effort: a journal that lacks the method (or a write failure)
        must not break the regeneration tick.
        """
        try:
            from runtime.memory.journal import journal_context

            context = (
                journal_context(
                    tenant_id=self.scope.tenant_id,
                    owner_actor_id=self.scope.actor_id,
                )
                if self.scope is not None
                else journal_context()
            )
            with context:
                self.journal.write_skill_proposal_decision(
                    proposal_name=candidate.name,
                    decision="quarantined",
                    candidate_id=candidate.candidate_id,
                    proposal_kind="skill_forge",
                    reason=str(exc),
                    details={
                        "dangerous_underlying": dangerous,
                        "underlying_sequence": list(candidate.underlying_sequence),
                        "source_sample_count": candidate.source_sample_count,
                        "source_success_rate": candidate.source_success_rate,
                    },
                    actor="skill_forge",
                )
        except Exception as _exc:  # noqa: BLE001 — recording must not break tick
            _LOG.debug("forge quarantine decision not recorded: %s", _exc)

    def _dangerous_underlying_skills(self, candidate: ForgedSkillCandidate) -> list[str]:
        dangerous: list[str] = []
        for name in candidate.underlying_sequence:
            if is_dangerous_tool(name):
                dangerous.append(name)
                continue
            try:
                skill = self.registry.get(name)
            except Exception:  # noqa: BLE001 - missing/invalid skills fail elsewhere.
                continue
            affinity = set(skill.affinity or [])
            if "dangerous" in affinity:
                dangerous.append(name)
        return dangerous

    def _collect_successful_trajectories(self) -> list[Trajectory]:
        events = read_learning_events(
            self.journal,
            "trajectory",
            scope=self.scope,
        )
        trajs = [e.trajectory for e in events if isinstance(e, TrajectoryEvent)]

        # Swarm tasks can emit both per-arm GraphRuntime trajectories
        # and one aggregated ``strategy_id="swarm"`` trajectory for
        # the same task_id. When the aggregate exists, it is the
        # learnable whole-task sample; keeping the per-arm rows too
        # double-counts a single real execution.
        by_task: dict[str, list[Trajectory]] = defaultdict(list)
        order: list[str] = []
        for traj in trajs:
            key = str(traj.task_id)
            if key not in by_task:
                order.append(key)
            by_task[key].append(traj)

        selected: list[Trajectory] = []
        for key in order:
            bucket = by_task[key]
            swarm_bucket = [t for t in bucket if t.strategy_id == "swarm"]
            if swarm_bucket:
                selected.append(
                    max(
                        swarm_bucket,
                        key=lambda t: (
                            t.step_count,
                            int(t.outcome.success),
                            t.completed_at,
                        ),
                    )
                )
                continue
            selected.extend(bucket)
        return selected

    @staticmethod
    def _merge_shadow_reports(
        skill_name: str,
        reports: list[SkillTestReport],
        *,
        overall_passed: bool,
    ) -> SkillTestReport:
        merged_results: list[SkillTestResult] = []
        for run_idx, report in enumerate(reports, start=1):
            for result in report.results:
                case_name = result.case_name
                if len(reports) > 1:
                    case_name = f"run{run_idx}:{case_name}"
                merged_results.append(result.model_copy(update={"case_name": case_name}))

        per_tier: dict[str, list[bool]] = defaultdict(list)
        for result in merged_results:
            per_tier[result.tier].append(result.passed)

        tier_rates = {tier: sum(outcomes) / len(outcomes) for tier, outcomes in per_tier.items()}
        tier_counts = {tier: len(outcomes) for tier, outcomes in per_tier.items()}

        return SkillTestReport(
            skill_name=skill_name,
            results=merged_results,
            tier_pass_rates=tier_rates,
            tier_counts=tier_counts,
            overall_passed=overall_passed,
        )

    def _make_candidate(
        self,
        signature: str,
        cluster: list[Trajectory],
        success_rate: float,
    ) -> ForgedSkillCandidate:
        sample = cluster[0]
        sequence = path_of(sample)
        name = self._name_generator(sequence)
        tests = self._generate_golden_tests(cluster)

        # Capture the sample's per-step templates so the meta-handler
        # can replay inter-step data flow. Older Step rows (pre-fix
        # trajectories, direct-executor constructions) have
        # ``args_template == {}``; we record them as-is and let the
        # handler's fallback logic handle the "all empty" case.
        step_templates = [dict(getattr(s, "args_template", {}) or {}) for s in sample.steps]

        return ForgedSkillCandidate(
            candidate_id=signature,
            name=name,
            description=f"Composite skill: {' → '.join(sequence)}",
            path_signature=signature,
            underlying_sequence=sequence,
            source_trajectory_ids=[str(t.trajectory_id) for t in cluster],
            source_sample_count=len(cluster),
            source_success_rate=success_rate,
            generated_tests=tests,
            step_templates=step_templates,
        )

    def _generate_golden_tests(self, cluster: list[Trajectory]) -> list[SkillTestCase]:
        if not cluster:
            return []
        sample = cluster[0]
        first_step_args = dict(sample.steps[0].action.args) if sample.steps else {}

        # Post-2026-05 meta-handler catches sub-skill exceptions
        # internally and returns ``{"success": False, ...}`` rather
        # than propagating · so the golden smoke can't rely on
        # "no exception raised" alone to distinguish a healthy forge
        # from a broken one. Use ``custom_predicate`` to assert the
        # composite actually ran to completion. Matches
        # ``test_shadow_fails_when_underlying_skill_raises`` which
        # expects shadow-validate to REJECT forged skills whose
        # underlying handlers blow up.
        return [
            SkillTestCase(
                name="basic_smoke",
                tier="golden",
                args=first_step_args,
                expect=SkillExpect(no_exception=True),
                custom_predicate=_composite_ran_successfully,
            )
        ]

    def _maybe_persist(self, candidate: ForgedSkillCandidate) -> None:
        if self.auto_persist_dir is None:
            return
        try:
            from runtime.execution.suckers.forged_persistence import dump_forged_skill_to_md

            dump_forged_skill_to_md(candidate, self.auto_persist_dir)
        except (ImportError, AttributeError, OSError, ValueError, TypeError) as exc:
            _LOG.debug("Forged skill persistence skipped: %s", exc)

    def _build_meta_handler(self, candidate: ForgedSkillCandidate) -> Callable[..., Any]:
        """Build the composite skill's handler.

        Two call-time strategies · chosen per candidate:

        1. **Template replay** (preferred) — when the candidate
           captured ``step_templates`` from the sample trajectory,
           the handler replays them step-by-step against the live
           ``outputs`` dict using ``resolve_templates``. This
           preserves the exact inter-step data flow the original
           trajectory had: if step 2's arg was ``{n0.content}`` in
           the original, it stays ``{n0.content}`` here · resolved
           at replay time against the forged skill's own n0 output.
           User kwargs only seed step 0 (to parameterize the whole
           composite); later steps ignore them because the original
           trajectory's steps 1..N didn't take them either.

        2. **MVP fallback** — when the candidate has no templates
           (old trajectory format, Step rows written without the
           ``args_template`` field), revert to the pre-fix behavior:
           pass user kwargs to every step plus an ``_prev`` hint.
           Lossy but backward-compatible · sees limited use post-2026-04.

        Why not just always use strategy 2:
          LLM-planned trajectories with multi-step data dependencies
          (``{n0.content}`` → step 1) simply don't replay correctly
          without the templates. The MVP handler would call step 1
          with the user's original kwargs (probably missing the
          ``content`` field that step 1 actually needs) plus ``_prev``
          (a raw dict the skill likely doesn't know what to do with).
        """
        registry = self.registry
        sequence = list(candidate.underlying_sequence)
        templates = list(candidate.step_templates or [])

        # Normalize: a template list shorter than the sequence, or
        # all-empty, means "not available" — use fallback.
        has_templates = len(templates) == len(sequence) and any(t for t in templates)

        def _meta(**kwargs: Any) -> dict[str, Any]:
            # Import locally to avoid pulling ganglia at module load
            # time (skill_forge is imported in serialization paths
            # where we don't want the runtime dependency).
            from runtime.core.graph_runtime.runtime import (
                TemplateResolutionError,
                resolve_templates,
            )

            outputs: dict[str, Any] = {}

            # Error-halt fidelity with GraphRuntime:
            #   * original execution caught handler exceptions inside
            #     ``executor.execute_step`` → tagged as failed Step →
            #     GraphRuntime broke out of the loop, never propagated
            #   * the pre-fix meta propagated any handler exception
            #     straight to the caller, meaning the forged
            #     composite raised where the source trajectory would
            #     have returned a graceful partial result
            #
            # Post-fix behavior: catch handler + template exceptions,
            # record the failed step index + error kind in the
            # returned dict, stop further steps. Caller gets a
            # well-formed dict with ``success=False`` rather than an
            # unhandled exception. Matches the "always returns a
            # dict" contract the golden smoke test checks for and
            # matches what the original GraphRuntime would have done.
            success = True
            failed_at: int | None = None
            error_type: str | None = None
            error_msg: str | None = None

            for i, skill_name in enumerate(sequence):
                skill = registry.get(skill_name)

                try:
                    if has_templates:
                        template = templates[i]
                        if i == 0:
                            # Step 0 has no prior outputs — user
                            # kwargs override the sample's literal
                            # values to parameterize the composite
                            # (e.g., sample was
                            # read_file(path="/sample"), user calls
                            # with path="/their/file").
                            call_args = {**template, **kwargs}
                        else:
                            # Steps 1..N: templates reference prior
                            # outputs. User kwargs are deliberately
                            # NOT mixed in — the original
                            # trajectory's step i didn't take them,
                            # so passing them now risks TypeError
                            # or worse: silently overriding a
                            # template-driven arg.
                            call_args = resolve_templates(template, outputs)
                    else:
                        # MVP fallback: kwargs + _prev hint.
                        call_args = dict(kwargs)
                        if i > 0 and f"n{i - 1}" in outputs:
                            call_args.setdefault(
                                "_prev",
                                outputs[f"n{i - 1}"],
                            )

                    # Safety chokepoint. This in-process composite handler
                    # calls each sub-skill's handler DIRECTLY (not via
                    # executor.execute_step), so every pre-execution gate —
                    # capability-permission, injection-taint, immunity,
                    # file-safety — is bypassed. Identical to the persisted
                    # twin in suckers/forged_persistence; re-apply the shared
                    # gate sequence per sub-skill, fail-closed.
                    from runtime.execution.tool_engine.skill_gate import (
                        gate_inner_dispatch,
                    )

                    _block = gate_inner_dispatch(
                        skill,
                        call_args,
                        caller="forged_composite",
                    )
                    if _block is not None:
                        success = False
                        failed_at = i
                        error_type = _block.error_type
                        error_msg = _block.message
                        break
                    outputs[f"n{i}"] = skill.handler(**call_args)
                except TemplateResolutionError as e:
                    # Previous step's output shape didn't match the
                    # template · most likely a sub-skill changed its
                    # output schema after this composite was forged.
                    # Halt and report which step ran off the rails.
                    success = False
                    failed_at = i
                    error_type = "TemplateResolutionError"
                    error_msg = str(e)
                    break
                except Exception as e:  # noqa: BLE001 · user-skill
                    # Sub-skill handlers are arbitrary · any
                    # exception halts the composite. We preserve
                    # partial outputs in ``outputs`` so caller /
                    # shadow tester can see how far the composite
                    # got before the break.
                    success = False
                    failed_at = i
                    error_type = type(e).__name__
                    error_msg = str(e)
                    break

            result: dict[str, Any] = {
                "composite_output": outputs,
                "steps": len(outputs),
                "total_steps": len(sequence),
                "success": success,
            }
            if not success:
                result["failed_at"] = failed_at
                result["error_type"] = error_type
                result["error"] = error_msg
            return result

        return _meta


# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════


def _default_name_for(sequence: list[str]) -> str:
    base = "_then_".join(sequence)
    h = hashlib.blake2b("→".join(sequence).encode("utf-8"), digest_size=4).hexdigest()
    return f"forged_{base}_{h}"
