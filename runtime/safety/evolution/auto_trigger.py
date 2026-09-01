from __future__ import annotations

import contextlib
import hashlib
import logging
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from runtime.safety.auth.scope import TenantScope

_LOG = logging.getLogger("echo.evolution.auto_trigger")

# Agent ids become path components in turn-scoring / evolution storage.  Keep
# the accepted shape deliberately narrower than a generic filesystem name so
# an event or a malformed registry cannot turn the auto-trigger into a path
# traversal primitive.  Dots inside a slug remain compatible with existing
# agent naming conventions; a leading dot (including ``..``) is rejected.
_SAFE_AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass
class AutoTriggerConfig:
    enabled: bool = True
    check_interval_sec: int = 120
    fitness_threshold: float = 0.5
    fitness_window: int = 20
    evolve_dry_run: bool = True
    evolve_max_rounds: int = 1
    drift_critical_auto_rollback: bool = True
    feature_flag: str = "evolution.auto_trigger"
    # Optional allowlist.  Empty preserves the legacy auto-discovery contract:
    # use registered agents with score history, or (without a registry) the
    # historical ``stack.config.name`` when it is itself a real scored id.
    agent_ids: tuple[str, ...] = ()


def _normalise_agent_ids(
    values: Iterable[str] | str | None,
    *,
    strip: bool = True,
) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        values = (values,)
    try:
        raw_values = tuple(values)
    except TypeError:
        return ()

    normalised: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if not isinstance(raw, str):
            continue
        agent_id = raw.strip() if strip else raw
        if not _SAFE_AGENT_ID_RE.fullmatch(agent_id) or agent_id in seen:
            continue
        seen.add(agent_id)
        normalised.append(agent_id)
    return tuple(normalised)


def _registered_agent_ids(agent_registry: Any) -> tuple[str, ...]:
    """Return a safe snapshot from the runtime registry, never the filesystem."""

    if agent_registry is None:
        return ()
    get_ids = getattr(agent_registry, "all_ids", None)
    if not callable(get_ids):
        _LOG.warning("evolution auto-trigger registry has no all_ids(); failing closed")
        return ()
    try:
        raw_ids = get_ids()
    except Exception as exc:  # noqa: BLE001 — a broken registry must not start evolution
        _LOG.warning("evolution auto-trigger could not read agent registry: %s", exc)
        return ()
    # Registry identity is exact.  Never turn a registered ``" coder "``
    # into permission to operate on the distinct, unregistered ``"coder"``.
    return _normalise_agent_ids(raw_ids, strip=False)


def _has_score_history(
    agent_id: str,
    *,
    scope: TenantScope | None = None,
) -> bool:
    """Check one already-validated id without enumerating an agents directory."""

    try:
        from runtime.memory.learning.turn_scoring import read_recent_scores

        if scope is None:
            return bool(read_recent_scores(agent_id, limit=1))
        return bool(read_recent_scores(agent_id, limit=1, scope=scope))
    except Exception as exc:  # noqa: BLE001 — score storage is best-effort
        _LOG.debug("score history check failed for %s: %s", agent_id, exc)
        return False


def registered_evolution_agent_ids(
    agent_registry: Any,
    *,
    configured_agent_ids: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    """Return exact safe registry ids, optionally narrowed by an allowlist."""

    explicitly_configured = bool(configured_agent_ids)
    configured = _normalise_agent_ids(configured_agent_ids)
    registered = _registered_agent_ids(agent_registry)
    if not explicitly_configured:
        return registered
    registered_set = set(registered)
    return tuple(agent_id for agent_id in configured if agent_id in registered_set)


def resolve_evolution_agent_ids(
    stack: Any,
    *,
    agent_registry: Any = None,
    configured_agent_ids: Iterable[str] | str | None = None,
    scope: TenantScope | None = None,
) -> tuple[str, ...]:
    """Resolve the exact agents eligible for a periodic evolution check.

    The in-memory :class:`AgentRegistry` is authoritative when supplied.  We
    deliberately do not glob or walk ``agents/``: the registry both captures
    the agents this process may execute and bounds which score paths may be
    read.  Agents without any turn scores are skipped because there is no
    fitness evidence on which to base an automatic mutation.

    Older embedding callers may not have an AgentRegistry.  For them only, the
    historical ``stack.config.name`` remains a fallback, and only when it is a
    safe id with existing score history.  Thus an application name such as
    ``my-echo`` no longer shadows real ``coder`` / ``researcher`` scores.
    """

    explicitly_configured = bool(configured_agent_ids)
    if agent_registry is not None:
        candidates = registered_evolution_agent_ids(
            agent_registry,
            configured_agent_ids=configured_agent_ids,
        )
    elif explicitly_configured:
        candidates = _normalise_agent_ids(configured_agent_ids)
    else:
        legacy_name = getattr(getattr(stack, "config", None), "name", None)
        candidates = _normalise_agent_ids(legacy_name)

    if scope is None:
        return tuple(agent_id for agent_id in candidates if _has_score_history(agent_id))
    return tuple(agent_id for agent_id in candidates if _has_score_history(agent_id, scope=scope))


def _scope_from_event(event: Any) -> tuple[bool, TenantScope | None]:
    """Decode only the typed internal event ownership envelope.

    Older events have no scope fields and remain in the legacy namespace.
    Cross-tenant aggregate observations cannot identify one mutation owner and
    are intentionally non-actionable. Incomplete, oversized, or contradictory
    envelopes fail closed instead of selecting a storage partition.
    """

    mode = str(getattr(event, "scope_mode", "legacy") or "legacy").strip()
    tenant_id = str(getattr(event, "tenant_id", "") or "").strip()
    owner_actor_id = str(getattr(event, "owner_actor_id", "") or "").strip()
    if len(tenant_id) > 512 or len(owner_actor_id) > 512:
        return False, None
    if mode == "legacy":
        return (not tenant_id and not owner_actor_id), None
    if mode == "tenant":
        if not tenant_id or not owner_actor_id:
            return False, None
        return True, TenantScope(tenant_id=tenant_id, actor_id=owner_actor_id)
    # An aggregate can be observed and displayed, but never used as authority
    # for a mutation in one guessed tenant partition.
    return False, None


def _monitor_key(agent_id: str, scope: TenantScope | None) -> str:
    if scope is None:
        return agent_id
    digest = hashlib.sha256(f"{scope.tenant_id}\0{scope.actor_id}".encode()).hexdigest()[:24]
    return f"{agent_id}:tenant:{digest}"


def _monitor_agent_id(key: str, monitor: Any) -> str:
    value = getattr(monitor, "agent_id", None)
    if isinstance(value, str) and value:
        return value
    return key.split(":tenant:", 1)[0]


class EvolutionAutoTrigger:
    _instance: EvolutionAutoTrigger | None = None
    _lock: threading.Lock = threading.Lock()

    @classmethod
    def get(cls) -> EvolutionAutoTrigger:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Stop the worker thread (if running) and drop the singleton.

        For test isolation only — production code keeps a single
        EvolutionAutoTrigger alive for the process lifetime.
        """
        with cls._lock:
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
        self._config = AutoTriggerConfig()
        self._stack: Any = None
        self._agent_registry: Any = None
        self._tick_count = 0
        self._tick_context = threading.local()
        self._drift_monitor_lock = threading.Lock()
        self._drift_monitors: dict[str, Any] = {}
        self._active = False
        self._event_wire_lock = threading.Lock()
        self._event_bus: Any = None
        self._event_subscription_ids: tuple[int, ...] = ()

    def start(
        self,
        stack: Any,
        config: AutoTriggerConfig | None = None,
        *,
        agent_registry: Any = None,
    ) -> None:
        if config is not None:
            self._config = config
        self._stack = stack
        # An idempotent start without a registry must not erase a registry
        # attached later by mount_agents(). Explicit clearing remains
        # available through bind_agent_registry(None).
        if agent_registry is not None or self._agent_registry is None:
            self.bind_agent_registry(agent_registry)
        if not self._config.enabled:
            self.stop()
            _LOG.info("evolution auto-trigger disabled by config")
            return
        try:
            from runtime.platform import feature_flags as _ff

            if not _ff.is_on(self._config.feature_flag):
                self.stop()
                _LOG.info("evolution auto-trigger disabled by feature flag")
                return
        except Exception:  # noqa: BLE001 — feature-flag check best-effort
            pass
        self._active = True
        self._wire_events()
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="evolution-auto-trigger",
            daemon=True,
        )
        self._thread.start()
        _LOG.info(
            "evolution auto-trigger started · interval=%ds fitness_threshold=%.2f",
            self._config.check_interval_sec,
            self._config.fitness_threshold,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._active = False
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._unwire_events()
        with self._drift_monitor_lock:
            self._drift_monitors.clear()

    def bind_agent_registry(self, agent_registry: Any) -> None:
        """Bind the authoritative registry, including one loaded after startup."""

        allowed = set(
            registered_evolution_agent_ids(
                agent_registry,
                configured_agent_ids=self._config.agent_ids,
            )
        )
        with self._drift_monitor_lock:
            self._agent_registry = agent_registry
            for key, monitor in tuple(self._drift_monitors.items()):
                if _monitor_agent_id(key, monitor) not in allowed:
                    self._drift_monitors.pop(key, None)

    def status(self) -> dict[str, Any]:
        return {
            "running": bool(self._thread and self._thread.is_alive()),
            "tick_count": self._tick_count,
            "config": {
                "enabled": self._config.enabled,
                "check_interval_sec": self._config.check_interval_sec,
                "fitness_threshold": self._config.fitness_threshold,
                "agent_ids": list(self._config.agent_ids),
            },
        }

    def _resolve_agent_ids(self, scope: TenantScope | None = None) -> tuple[str, ...]:
        kwargs: dict[str, Any] = {
            "agent_registry": self._agent_registry,
            "configured_agent_ids": self._config.agent_ids,
        }
        if scope is not None:
            kwargs["scope"] = scope
        return resolve_evolution_agent_ids(self._stack, **kwargs)

    def _drift_monitor_for(
        self,
        agent_id: str,
        scope: TenantScope | None = None,
    ) -> Any:
        """Keep drift baselines alive across periodic ticks for one agent."""

        key = _monitor_key(agent_id, scope)
        with self._drift_monitor_lock:
            monitor = self._drift_monitors.get(key)
            if monitor is None:
                from runtime.safety.evolution.drift_monitor import DriftMonitor

                monitor = (
                    DriftMonitor(agent_id) if scope is None else DriftMonitor(agent_id, scope=scope)
                )
                self._drift_monitors[key] = monitor
            return monitor

    def _is_allowed_event_agent(
        self,
        agent_id: Any,
        scope: TenantScope | None = None,
    ) -> bool:
        normalised = _normalise_agent_ids(agent_id)
        if len(normalised) != 1:
            return False
        candidate = normalised[0]
        # Event handlers pass the original value downstream.  Reject values
        # that only become valid after trimming instead of authorising one id
        # and then evolving a different filesystem component.
        if not isinstance(agent_id, str) or candidate != agent_id:
            return False

        configured = _normalise_agent_ids(self._config.agent_ids)
        if self._config.agent_ids and candidate not in configured:
            return False

        if self._agent_registry is not None:
            return candidate in set(_registered_agent_ids(self._agent_registry))

        # Legacy embedders have no registry.  Reuse the score-backed resolver
        # so untrusted events cannot select an arbitrary path or phantom id.
        return candidate in set(self._resolve_agent_ids(scope))

    def _run_loop(self) -> None:
        if self._stop_event.wait(timeout=30):
            return
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                _LOG.warning("evolution auto-trigger tick failed: %s", exc)
            if self._stop_event.wait(timeout=self._config.check_interval_sec):
                return

    def _tick(self) -> None:
        self._tick_count += 1
        agent_ids = self._resolve_agent_ids()
        if not agent_ids:
            _LOG.debug("evolution auto-trigger tick skipped: no scored agent ids resolved")
            return

        from runtime.safety.evolution.fitness import FitnessConfig, compute_fitness

        for agent_id in agent_ids:
            try:
                self._tick_context.fitness_agent_id = agent_id
                try:
                    report = compute_fitness(
                        agent_id,
                        FitnessConfig(window=self._config.fitness_window),
                        publish_event=False,
                    )
                finally:
                    self._tick_context.fitness_agent_id = None

                if report.combined < self._config.fitness_threshold:
                    _LOG.warning(
                        "fitness below threshold: %.2f < %.2f · triggering deep_evolve for %s",
                        report.combined,
                        self._config.fitness_threshold,
                        agent_id,
                    )
                    self._trigger_evolve(agent_id)

                if not self._config.drift_critical_auto_rollback:
                    continue
                drift = self._drift_monitor_for(agent_id).check(publish_events=False)
                if not (drift.has_drift and drift.max_severity == "critical"):
                    continue
                _LOG.warning(
                    "critical drift detected for %s · triggering auto-rollback",
                    agent_id,
                )
                self._trigger_rollback(agent_id, drift)
            except Exception as exc:  # noqa: BLE001 — isolate agents in a multi-agent tick
                _LOG.warning("evolution auto-trigger check failed for %s: %s", agent_id, exc)

    def _wire_events(self) -> None:
        with self._event_wire_lock:
            if self._event_subscription_ids:
                return
            subscribed: list[int] = []
            bus: Any = None
            try:
                from runtime.platform.process.eventbus import get_eventbus

                bus = get_eventbus()
                subscribed.append(bus.subscribe("fitness.computed", self._on_fitness_event))
                subscribed.append(bus.subscribe("drift.detected", self._on_drift_event))
            except Exception as exc:  # noqa: BLE001 — event bus is optional
                if bus is not None:
                    for subscription_id in subscribed:
                        with contextlib.suppress(Exception):
                            bus.unsubscribe(subscription_id)
                _LOG.debug("event wire failed: %s", exc)
                return
            self._event_bus = bus
            self._event_subscription_ids = tuple(subscribed)

    def _unwire_events(self) -> None:
        with self._event_wire_lock:
            bus = self._event_bus
            subscription_ids = self._event_subscription_ids
            self._event_bus = None
            self._event_subscription_ids = ()
        if bus is None:
            return
        for subscription_id in subscription_ids:
            with contextlib.suppress(Exception):
                bus.unsubscribe(subscription_id)

    def _on_fitness_event(self, event: Any) -> None:
        if not self._active or not self._config.enabled:
            return
        score = getattr(event, "combined_score", 1.0)
        agent_id = getattr(event, "agent_id", "")
        valid_scope, scope = _scope_from_event(event)
        if not valid_scope:
            _LOG.warning("ignored fitness event with non-actionable ownership scope")
            return
        if not self._is_allowed_event_agent(agent_id, scope):
            _LOG.warning("ignored fitness event for unknown or unsafe agent id")
            return
        if agent_id == getattr(self._tick_context, "fitness_agent_id", None):
            # compute_fitness publishes synchronously.  The tick owns the
            # returned report and acts on it below; processing that event too
            # would run deep_evolve twice for the same observation.  The flag
            # is thread-local so independent external events remain visible.
            return
        if score < self._config.fitness_threshold:
            _LOG.warning(
                "fitness event below threshold: %.2f < %.2f for %s",
                score,
                self._config.fitness_threshold,
                agent_id,
            )
            if scope is None:
                self._trigger_evolve(agent_id)
            else:
                self._trigger_evolve(agent_id, scope=scope)

    def _on_drift_event(self, event: Any) -> None:
        if not self._active or not self._config.enabled:
            return
        severity = getattr(event, "severity", "")
        agent_id = getattr(event, "agent_id", "")
        valid_scope, scope = _scope_from_event(event)
        if not valid_scope:
            _LOG.warning("ignored drift event with non-actionable ownership scope")
            return
        if not self._is_allowed_event_agent(agent_id, scope):
            _LOG.warning("ignored drift event for unknown or unsafe agent id")
            return
        if self._config.drift_critical_auto_rollback and severity == "critical":
            _LOG.warning("critical drift event for %s", agent_id)
            if scope is None:
                self._trigger_rollback_from_event(event)
            else:
                self._trigger_rollback_from_event(event, scope=scope)

    def _trigger_rollback_from_event(
        self,
        event: Any,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        drift_kind = getattr(event, "drift_kind", "")
        agent_id = getattr(event, "agent_id", "")
        detail = str(getattr(event, "detail", "") or "").strip()
        if drift_kind == "score_regression":
            self._rollback_regressed_candidates(
                agent_id,
                scope=scope,
                reason=detail or "auto-rollback: critical score regression",
            )
            return

        # SOUL and genome are process-global assets. A tenant-scoped signal
        # must never mutate them; tenant evolution recovers through candidates.
        if scope is not None:
            _LOG.warning("ignored tenant-scoped direct %s rollback", drift_kind)
            return
        if drift_kind == "genome_change":
            try:
                from runtime.safety.recovery.genome_registry import GenomeRegistry

                registry = GenomeRegistry()
                latest = registry.latest_version()
                if latest and latest > 1:
                    registry.rollback(latest - 1)
                    _LOG.info("auto-rolled back genome to v%d", latest - 1)
                    try:
                        from runtime.platform.process.eventbus import GenomeRolledBack, get_eventbus

                        get_eventbus().publish(
                            GenomeRolledBack(
                                event_type="genome.rolled_back",
                                from_version=latest,
                                to_version=latest - 1,
                                agent_id=agent_id,
                            )
                        )
                    except Exception as _exc:
                        _LOG.debug("genome rollback event publish failed: %s", _exc)
            except Exception as exc:
                _LOG.warning("genome rollback failed: %s", exc)

        if drift_kind == "soul_change":
            try:
                from runtime.execution.suckers.memory_skills import _revert_soul

                _revert_soul(steps_back=1, reason="auto-rollback: critical soul drift")
                _LOG.info("auto-reverted SOUL.md by 1 step")
            except Exception as exc:
                _LOG.warning("SOUL revert failed: %s", exc)

    def _trigger_evolve(
        self,
        agent_id: str,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        try:
            from runtime.memory.learning.deep_evolution import deep_evolve

            kwargs: dict[str, Any] = {
                "agent_id": agent_id,
                "window": self._config.fitness_window,
                "max_rounds": self._config.evolve_max_rounds,
                "dry_run": self._config.evolve_dry_run,
            }
            if scope is not None:
                kwargs["scope"] = scope
            result = deep_evolve(**kwargs)
            try:
                from runtime.platform.process.eventbus import EvolutionCompleted, get_eventbus

                get_eventbus().publish(
                    EvolutionCompleted(
                        event_type="evolution.completed",
                        rounds_run=result.get("rounds_run", 0),
                        applied_count=len(result.get("applied", [])),
                        ok=result.get("ok", False),
                        agent_id=agent_id,
                    )
                )
            except Exception:  # noqa: BLE001 — event publish best-effort
                pass
            applied_n = len(result.get("applied", []))
            candidate_n = len(result.get("candidates", []))
            if result.get("ok") and candidate_n:
                _LOG.info(
                    "auto-evolve routed %d governed candidate(s) for %s (rounds=%s)",
                    candidate_n,
                    agent_id,
                    result.get("rounds_run"),
                )
            elif result.get("ok") and applied_n == 0:
                # Dry-run previews and rejected proposals both leave baseline
                # state untouched. Do not claim every proposal was rejected:
                # a dry-run winner can be valid but intentionally unmaterialized.
                _LOG.info(
                    "auto-evolve completed without baseline mutation for %s "
                    "(rounds=%s · dry_run=%s)",
                    agent_id,
                    result.get("rounds_run"),
                    result.get("dry_run"),
                )
            else:
                _LOG.info(
                    "auto-evolve result: ok=%s rounds=%s applied=%d",
                    result.get("ok"),
                    result.get("rounds_run"),
                    applied_n,
                )
        except Exception as exc:
            _LOG.warning("auto-evolve failed: %s", exc)

    def _rollback_regressed_candidates(
        self,
        agent_id: str,
        *,
        scope: TenantScope | None,
        reason: str,
    ) -> None:
        try:
            from runtime.safety.evolution.regression_rollback import (
                rollback_active_candidates_for_regression,
            )

            result = rollback_active_candidates_for_regression(
                agent_id,
                scope=scope,
                reason=reason[:500],
            )
            if result.changed:
                _LOG.warning(
                    "auto-rolled back %d governed candidate(s) for %s after score regression",
                    len(result.rolled_back_candidate_ids),
                    agent_id,
                )
            else:
                _LOG.info(
                    "critical score regression for %s had no active governed candidate",
                    agent_id,
                )
        except Exception as exc:  # noqa: BLE001 - control plane must fail closed
            _LOG.warning("governed candidate rollback failed closed: %s", type(exc).__name__)

    def _trigger_rollback(self, agent_id: str, drift_report: Any) -> None:
        valid_scope, scope = _scope_from_event(drift_report)
        if not valid_scope:
            _LOG.warning("ignored drift report with non-actionable ownership scope")
            return
        for event in drift_report.events:
            if event.kind == "score_regression" and event.severity == "critical":
                self._rollback_regressed_candidates(
                    agent_id,
                    scope=scope,
                    reason=str(getattr(event, "detail", "") or "")
                    or "auto-rollback: critical score regression",
                )
                continue

            if scope is not None:
                # Tenant reports cannot directly mutate global assets.
                continue
            if event.kind == "genome_change" and event.severity == "critical":
                try:
                    from runtime.safety.recovery.genome_registry import GenomeRegistry

                    registry = GenomeRegistry()
                    latest = registry.latest_version()
                    if latest and latest > 1:
                        registry.rollback(latest - 1)
                        _LOG.info("auto-rolled back genome to v%d", latest - 1)
                except Exception as exc:
                    _LOG.warning("genome rollback failed: %s", exc)

            if event.kind == "soul_change" and event.severity == "critical":
                try:
                    from runtime.execution.suckers.memory_skills import _revert_soul

                    _revert_soul(steps_back=1, reason="auto-rollback: critical soul drift")
                    _LOG.info("auto-reverted SOUL.md by 1 step")
                except Exception as exc:
                    _LOG.warning("SOUL revert failed: %s", exc)


def get_auto_trigger() -> EvolutionAutoTrigger:
    return EvolutionAutoTrigger.get()


__all__ = [
    "AutoTriggerConfig",
    "EvolutionAutoTrigger",
    "get_auto_trigger",
    "registered_evolution_agent_ids",
    "resolve_evolution_agent_ids",
]
