from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

_LOG = logging.getLogger("echo.regeneration.scheduler")


class GovernedSkillForgeUnavailable(RuntimeError):
    """The installed SkillForge cannot guarantee candidate-only rollout."""


@dataclass
class SchedulerConfig:
    interval_sec: int = 600
    initial_delay_sec: int = 30
    output_dir: str = "data"
    enabled: bool = True


def _to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except (AttributeError, TypeError, ValueError):  # noqa: BLE001 — model_dump unsupported; try dataclass next
            pass
    if is_dataclass(obj) and not isinstance(obj, type):
        try:
            return asdict(obj)
        except (TypeError, ValueError):  # noqa: BLE001 — dataclass dump fallthrough
            pass
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    return str(obj)


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Thin shim around ``runtime.platform.io.atomic_write_json``.

    Kept as a private name because several call sites in this module
    pass non-JSON-native values (dataclasses, sets) that we coerce to
    strings via ``default=str``.
    """
    from runtime.platform.io import atomic_write_json

    atomic_write_json(path, payload, default=str)


def _fitness_entry(agent_id: str, report: Any) -> dict[str, Any]:
    governance = getattr(report, "governance", None)
    l2 = getattr(report, "l2", None)
    return {
        "agent_id": agent_id,
        "l1_score": report.l1.score,
        "l1_trend": report.l1.trend,
        "l2_score": l2.score if l2 else None,
        "governance_score": governance.score if governance else None,
        "governance_penalty": governance.penalty if governance else None,
        "governance_reasons": governance.reasons if governance else [],
        "combined": report.combined,
        "verdict": report.verdict,
    }


def _drift_entry(agent_id: str, report: Any) -> dict[str, Any]:
    return {
        "agent_id": agent_id,
        "has_drift": report.has_drift,
        "max_severity": report.max_severity,
        "events": [
            {"kind": event.kind, "severity": event.severity, "detail": event.detail}
            for event in report.events
        ],
    }


_DRIFT_SEVERITY_RANK = {"none": 0, "info": 1, "warning": 2, "critical": 3}

# A process-global background worker has no authoritative request principal.
# Learning readers therefore intentionally see legacy, ownership-free rows
# only. Tenant trajectories enter the governed request paths with an explicit
# TenantScope; automatically iterating tenants would require a tenant-aware
# deployment/registry, which the process-global planner does not yet provide.
_BACKGROUND_LEARNING_SCOPE = {
    "mode": "legacy_unscoped_only",
    "tenant_auto_learning": False,
    "tenant_request_learning": "governed_candidate_only",
}


class RegenerationScheduler:
    _instance: RegenerationScheduler | None = None
    _instance_lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> RegenerationScheduler:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Stop the worker thread (if running) and drop the singleton.

        For test isolation only — production keeps a single
        RegenerationScheduler alive for the process lifetime.
        """
        with cls._instance_lock:
            inst = cls._instance
            cls._instance = None
        if inst is not None:
            try:  # noqa: SIM105
                inst.stop(timeout=2.0)
            except Exception:  # noqa: BLE001 — reset must never raise
                pass

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stack: Any = None
        self._agent_registry: Any = None
        self._config = SchedulerConfig()
        self._tick_count = 0
        self._last_summary: dict[str, Any] = {}
        self._drift_monitors: dict[str, Any] = {}
        self._lock = threading.RLock()

    def start(
        self,
        stack: Any,
        config: SchedulerConfig | None = None,
        *,
        agent_registry: Any = None,
    ) -> None:
        with self._lock:
            # Preserve a late mount_agents() binding across an idempotent
            # start() call that omits the optional registry.
            if agent_registry is not None or self._agent_registry is None:
                self.bind_agent_registry(agent_registry)
            if self._thread is not None and self._thread.is_alive():
                _LOG.info("regeneration scheduler already running · skip start")
                return
            if stack is None or getattr(stack, "journal", None) is None:
                _LOG.warning(
                    "regeneration scheduler: stack/journal missing · skipping",
                )
                return
            self._stack = stack
            if config is not None:
                self._config = config
            if not self._config.enabled:
                _LOG.info("regeneration scheduler disabled by config · skip")
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="regeneration-scheduler",
                daemon=True,
            )
            self._thread.start()
            _LOG.info(
                "🔁 regeneration scheduler started · interval=%ds initial_delay=%ds",
                self._config.interval_sec,
                self._config.initial_delay_sec,
            )

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=timeout)
        with self._lock:
            self._drift_monitors.clear()

    def bind_agent_registry(self, agent_registry: Any) -> None:
        """Update the authoritative AgentRegistry after compatibility loading."""

        from runtime.safety.evolution.auto_trigger import registered_evolution_agent_ids

        allowed = set(registered_evolution_agent_ids(agent_registry))
        with self._lock:
            self._agent_registry = agent_registry
            for agent_id in tuple(self._drift_monitors):
                if agent_id not in allowed:
                    self._drift_monitors.pop(agent_id, None)

    def _resolve_evolution_agent_ids(self) -> tuple[str, ...]:
        with self._lock:
            stack = self._stack
            agent_registry = self._agent_registry
        if agent_registry is None:
            # Unlike AutoTrigger's legacy embedding fallback, regeneration
            # must never treat stack.config.name (the app instance name) as an
            # agent identity. A registry is the authorization boundary.
            return ()

        from runtime.safety.evolution.auto_trigger import resolve_evolution_agent_ids

        return resolve_evolution_agent_ids(
            stack,
            agent_registry=agent_registry,
        )

    def _drift_monitor_for(self, agent_id: str) -> Any:
        """Return the agent's long-lived, scheduler-local drift monitor."""

        with self._lock:
            monitor = self._drift_monitors.get(agent_id)
            if monitor is None:
                from runtime.safety.evolution.drift_monitor import DriftMonitor

                monitor = DriftMonitor(agent_id)
                self._drift_monitors[agent_id] = monitor
            return monitor

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": bool(self._thread and self._thread.is_alive()),
                "tick_count": self._tick_count,
                "last_summary": dict(self._last_summary),
                "interval_sec": self._config.interval_sec,
                "learning_scope": dict(_BACKGROUND_LEARNING_SCOPE),
            }

    def _run_loop(self) -> None:
        if self._stop_event.wait(timeout=self._config.initial_delay_sec):
            return
        while not self._stop_event.is_set():
            try:
                self._tick_once()
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("regeneration tick failed: %s", exc)
            if self._stop_event.wait(timeout=self._config.interval_sec):
                return

    def _tick_once(self) -> None:
        with self._lock:
            self._tick_count += 1
            n = self._tick_count
        summary: dict[str, Any] = {
            "tick": n,
            "ts": time.time(),
            "learning_scope": dict(_BACKGROUND_LEARNING_SCOPE),
        }
        out_dir = Path(self._config.output_dir)
        journal = self._stack.journal

        # ─── 1. RuleExtractor ────────────────────
        try:
            from runtime.safety.recovery.rule_extractor import RuleExtractor

            rule_report = RuleExtractor(journal=journal).extract()
            rules_obj = getattr(rule_report, "rules_produced", []) or []
            payload = {
                "tick": n,
                "ts": time.time(),
                "trajectories_scanned": rule_report.trajectories_scanned,
                "failure_count": rule_report.failure_count,
                "clusters_formed": rule_report.clusters_formed,
                "rules": _to_jsonable(rules_obj),
            }
            _atomic_write_json(out_dir / "learned_rules.json", payload)
            summary["rules"] = len(payload["rules"])
            try:
                planner = getattr(self._stack, "planner", None)
                if planner is not None and hasattr(planner, "update_learned_rules"):
                    planner.update_learned_rules(rules_obj)
                    summary["rules_to_planner"] = len(rules_obj)
            except (AttributeError, TypeError) as exc:
                _LOG.warning("inject rules into planner failed: %s", exc)
        except Exception as exc:
            _LOG.warning("RuleExtractor tick failed: %s", exc)
            summary["rules"] = "err"

        # ─── 2. MemoryConsolidator ──────────────
        try:
            from runtime.safety.recovery.memory_consolidator import (
                MemoryConsolidator,
            )

            memory_report = MemoryConsolidator(journal=journal).consolidate()
            # ``ConsolidationReport`` exposes the durable memories through
            # ``memories_produced``.  Older duck-typed test reports happened
            # to provide ``memories`` and masked this mismatch, leaving the
            # real scheduler to persist and inject an empty list forever.
            mem_obj = list(memory_report.memories_produced)
            payload = {
                "tick": n,
                "ts": time.time(),
                "scanned": memory_report.trajectories_scanned,
                "clusters_formed": memory_report.clusters_formed,
                "produced": len(mem_obj),
                "memories": _to_jsonable(mem_obj),
            }
            _atomic_write_json(out_dir / "learned_memories.json", payload)
            summary["memories"] = (
                len(payload["memories"]) if isinstance(payload["memories"], list) else 0
            )
            try:
                planner = getattr(self._stack, "planner", None)
                if planner is not None and hasattr(planner, "update_learned_memories"):
                    planner.update_learned_memories(mem_obj)
                    summary["memories_to_planner"] = (
                        len(mem_obj) if isinstance(mem_obj, list) else 0
                    )
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("inject memories into planner failed: %s", exc)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("MemoryConsolidator tick failed: %s", exc)
            summary["memories"] = "err"

        try:
            from runtime.safety.recovery.workflow_rewriter import (
                WorkflowRewriter,
            )

            try:
                with open(out_dir / "learned_rules.json", encoding="utf-8") as fh:
                    _rl = json.load(fh).get("rules", []) or []
            except (OSError, json.JSONDecodeError):
                _rl = []
            workflow_report = WorkflowRewriter(journal=journal).analyze(rules=_rl)
            payload = {
                "tick": n,
                "ts": time.time(),
                "proposals": _to_jsonable(
                    getattr(workflow_report, "proposals", None) or [],
                ),
                "summary": _to_jsonable(
                    {k: v for k, v in vars(workflow_report).items() if k != "proposals"}
                    if hasattr(workflow_report, "__dict__")
                    else {},
                ),
            }
            _atomic_write_json(out_dir / "workflow_proposals.json", payload)
            summary["proposals"] = (
                len(payload["proposals"]) if isinstance(payload["proposals"], list) else 0
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("WorkflowRewriter tick failed: %s", exc)
            summary["proposals"] = "err"

        # ─── 4. RecipeEvaluator ─────────────────
        try:
            from runtime.safety.recovery.recipe_evaluator import (
                RecipeEvaluator,
            )

            recipe_report = RecipeEvaluator(journal=journal).evaluate()
            payload = {
                "tick": n,
                "ts": time.time(),
                "scores": _to_jsonable(getattr(recipe_report, "scores", None) or []),
            }
            _atomic_write_json(out_dir / "recipe_scores.json", payload)
            summary["recipe_scores"] = (
                len(payload["scores"]) if isinstance(payload["scores"], list) else 0
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("RecipeEvaluator tick failed: %s", exc)
            summary["recipe_scores"] = "err"

        try:
            from runtime.platform import feature_flags as _ff
            from runtime.safety.recovery import forge_auto_tick as _fat

            _fat.bind_stack(self._stack)
            # Source of truth is the feature-flag system (env → legacy_env →
            # file → default); reading os.environ directly ignored runtime
            # flag changes via feature_flags.json. Default stays disabled.
            _apply = _ff.is_on("regeneration.gepa_auto_apply")
            _tr = _fat.run_tick(apply=_apply, journal=journal)
            payload = {
                "tick": n,
                "ts": time.time(),
                "auto_apply": _apply,
                "elapsed_s": _tr.elapsed_s,
                "recipes_scanned": _tr.recipes_scanned,
                "recipes_promoted": _tr.recipes_promoted,
                "results": _tr.results,
            }
            _atomic_write_json(out_dir / "gepa_proposals.json", payload)
            summary["gepa_proposals"] = len(_tr.results or [])
            summary["gepa_promoted"] = _tr.recipes_promoted
        except Exception as exc:
            _LOG.warning("GEPA dry-run tick failed: %s", exc)
            summary["gepa_proposals"] = "err"

        # ─── 6. SkillForge (need registry) ─────────
        try:
            registry = getattr(
                getattr(self._stack, "executor", None),
                "registry",
                None,
            )
            if registry is not None:
                from runtime.safety.recovery.skill_forge import SkillForge

                governed_factory = getattr(SkillForge, "for_governed_rollout", None)
                if not callable(governed_factory):
                    # Older SkillForge implementations promoted directly into
                    # the live registry.  Never fall back to that constructor:
                    # a mixed-version deployment must skip the stage rather
                    # than silently bypass candidate governance.
                    raise GovernedSkillForgeUnavailable(
                        "SkillForge does not expose for_governed_rollout()",
                    )
                forge_report = governed_factory(
                    journal=journal,
                    registry=registry,
                ).run()
                candidates = getattr(forge_report, "evolution_candidates", None)
                if not isinstance(candidates, list):
                    raise GovernedSkillForgeUnavailable(
                        "governed SkillForge returned no evolution_candidates list",
                    )
                if getattr(forge_report, "promoted", None):
                    raise GovernedSkillForgeUnavailable(
                        "governed SkillForge reported a direct live promotion",
                    )
                payload = {
                    "tick": n,
                    "ts": time.time(),
                    "candidates": _to_jsonable(candidates),
                }
                _atomic_write_json(out_dir / "forged_skills.json", payload)
                summary["forged"] = (
                    len(payload["candidates"]) if isinstance(payload["candidates"], list) else 0
                )
            else:
                summary["forged"] = "skip(no_registry)"
        except Exception as exc:  # noqa: BLE001
            _LOG.warning(
                "SkillForge tick failed (%s): %s",
                type(exc).__name__,
                exc,
            )
            # Keep the exception type in the summary so recurring patterns
            # (e.g. a duplicate-name crash loop) are visible without grepping
            # logs — a bare "err" hid the type.
            summary["forged"] = f"err:{type(exc).__name__}"

        # ─── 7. TopologyEvolver ─────────────────────
        # Organization-level reflection: read team-topology
        # performance history and emit ``swap_agent`` /
        # ``switch_protocol`` / ``adjust_quality_threshold``
        # proposals to ``topology_proposals.json``. Gated by
        # ``MutationKind.EVOLVE_TOPOLOGY`` inside ``tick()`` so a
        # PANIC freeze halts organization evolution alongside the
        # individual-agent paths above.
        try:
            from runtime.safety.organization.evolver import TopologyEvolver
            from runtime.safety.organization.forge import load_registry

            topology_registry = load_registry()
            evolver = TopologyEvolver(
                proposals_path=out_dir / "topology_proposals.json",
                registry=topology_registry,
            )
            report = evolver.tick()
            summary["topology_proposals"] = len(report.proposals)
            summary["topology_buckets"] = report.buckets_analysed
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("TopologyEvolver tick failed: %s", exc)
            summary["topology_proposals"] = "err"

        # ─── 8. Evolution Fitness ─────────────────
        try:
            from runtime.safety.evolution.fitness import compute_fitness

            agent_ids = self._resolve_evolution_agent_ids()
            fitness_entries: list[dict[str, Any]] = []
            for agent_id in agent_ids:
                try:
                    fitness_report = compute_fitness(agent_id, publish_event=False)
                    fitness_entries.append(_fitness_entry(agent_id, fitness_report))
                except Exception as exc:  # noqa: BLE001 — isolate agents within the tick
                    _LOG.warning("Evolution fitness failed for %s: %s", agent_id, exc)
                    fitness_entries.append(
                        {"agent_id": agent_id, "error": type(exc).__name__},
                    )

            successful_fitness = [entry for entry in fitness_entries if "error" not in entry]
            worst_fitness = (
                min(
                    successful_fitness,
                    key=lambda entry: (entry["combined"], entry["agent_id"]),
                )
                if successful_fitness
                else None
            )
            legacy_fitness = worst_fitness or {
                "agent_id": "",
                "l1_score": None,
                "l1_trend": "",
                "l2_score": None,
                "governance_score": None,
                "governance_penalty": None,
                "governance_reasons": [],
                "combined": None,
                "verdict": "unavailable",
            }
            payload = {
                "tick": n,
                "ts": time.time(),
                **legacy_fitness,
                "agent_ids": list(agent_ids),
                "agents": fitness_entries,
            }
            _atomic_write_json(out_dir / "evolution_fitness.json", payload)
            summary["evolution_fitness_agents"] = {
                entry["agent_id"]: entry.get("verdict", f"err:{entry.get('error', 'unknown')}")
                for entry in fitness_entries
            }
            if worst_fitness is not None:
                summary["evolution_fitness"] = worst_fitness["verdict"]
                summary["evolution_combined"] = worst_fitness["combined"]
            elif agent_ids:
                summary["evolution_fitness"] = "err"
                summary["evolution_combined"] = None
            else:
                summary["evolution_fitness"] = "skip(no_scored_agents)"
                summary["evolution_combined"] = None
        except Exception as exc:
            _LOG.warning("Evolution fitness tick failed: %s", exc)
            summary["evolution_fitness"] = "err"

        # ─── 9. Drift Monitor ───────────────────
        try:
            agent_ids = self._resolve_evolution_agent_ids()
            drift_entries: list[dict[str, Any]] = []
            for agent_id in agent_ids:
                try:
                    drift_report = self._drift_monitor_for(agent_id).check(
                        publish_events=False,
                    )
                    drift_entries.append(_drift_entry(agent_id, drift_report))
                except Exception as exc:  # noqa: BLE001 — isolate agents within the tick
                    _LOG.warning("Drift monitor failed for %s: %s", agent_id, exc)
                    drift_entries.append(
                        {"agent_id": agent_id, "error": type(exc).__name__},
                    )

            successful_drift = [entry for entry in drift_entries if "error" not in entry]
            has_drift = any(entry["has_drift"] for entry in successful_drift)
            max_severity = (
                max(
                    successful_drift,
                    key=lambda entry: _DRIFT_SEVERITY_RANK.get(entry["max_severity"], -1),
                )["max_severity"]
                if successful_drift
                else "none"
            )
            flattened_events = [
                {"agent_id": entry["agent_id"], **event}
                for entry in successful_drift
                for event in entry["events"]
            ]
            payload = {
                "tick": n,
                "ts": time.time(),
                "has_drift": has_drift,
                "max_severity": max_severity,
                "events": flattened_events,
                "agent_ids": list(agent_ids),
                "agents": drift_entries,
            }
            _atomic_write_json(out_dir / "evolution_drift.json", payload)
            summary["evolution_drift_agents"] = {
                entry["agent_id"]: entry.get(
                    "max_severity",
                    f"err:{entry.get('error', 'unknown')}",
                )
                for entry in drift_entries
            }
            if successful_drift:
                summary["evolution_drift"] = max_severity
            elif agent_ids:
                summary["evolution_drift"] = "err"
            else:
                summary["evolution_drift"] = "skip(no_scored_agents)"
        except Exception as exc:
            _LOG.warning("Drift monitor tick failed: %s", exc)
            summary["evolution_drift"] = "err"

        # ─── 10. Canary Check ───────────────────
        try:
            from runtime.safety.evolution.canary import CanaryManager

            cm = CanaryManager()
            active = cm.list_active()
            payload = {
                "tick": n,
                "ts": time.time(),
                "active_canaries": len(active),
                "skills": [
                    {"name": s.skill_name, "phase": s.phase.value, "rate": s.current_rate}
                    for s in active
                ],
            }
            _atomic_write_json(out_dir / "evolution_canary.json", payload)
            summary["evolution_canaries"] = len(active)
        except Exception as exc:
            _LOG.warning("Canary check tick failed: %s", exc)
            summary["evolution_canaries"] = "err"

        with self._lock:
            self._last_summary = summary
        _LOG.info(
            "🔁 regeneration tick #%d done · %s",
            n,
            " ".join(f"{k}={v}" for k, v in summary.items() if k not in ("tick", "ts")),
        )


def get_scheduler() -> RegenerationScheduler:
    return RegenerationScheduler.get()
