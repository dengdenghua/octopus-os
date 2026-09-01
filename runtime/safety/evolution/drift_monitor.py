from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime.memory.learning.turn_scoring import is_safe_agent_id
from runtime.platform.io import (
    JsonMutation,
    TransactionalFileError,
    mutate_json_file,
    read_json_file,
)
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path

_LOG = logging.getLogger("echo.evolution.drift_monitor")
_DRIFT_STATE_SCHEMA = "echo.evolution.drift_state.v1"
_MISSING_DRIFT_STATE = object()


def _publish_drift_events(
    events: list[DriftEvent],
    *,
    agent_id: str,
    scope: TenantScope | None,
) -> None:
    from runtime.platform.process.eventbus import DriftDetected, publish_event

    for ev in events:
        publish_event(
            DriftDetected(
                event_type="drift.detected",
                agent_id=agent_id,
                drift_kind=ev.kind,
                severity=ev.severity,
                detail=ev.detail,
                tenant_id=(
                    scope.tenant_id if scope is not None and not scope.allow_cross_tenant else ""
                ),
                owner_actor_id=(
                    scope.actor_id if scope is not None and not scope.allow_cross_tenant else ""
                ),
                scope_mode=(
                    "cross_tenant"
                    if scope is not None and scope.allow_cross_tenant
                    else "tenant"
                    if scope is not None
                    else "legacy"
                ),
            ),
            logger=_LOG,
        )


@dataclass
class DriftConfig:
    check_interval_sec: int = 300
    score_window: int = 20
    score_drop_threshold: float = 0.15
    soul_change_cooldown_sec: int = 60
    genome_dir: str = "data/genome"
    state_dir: str | None = None


@dataclass
class DriftEvent:
    kind: str
    severity: str
    detail: str
    ts: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DriftReport:
    agent_id: str
    ts: str
    events: list[DriftEvent]
    has_drift: bool
    max_severity: str
    tenant_id: str = ""
    owner_actor_id: str = ""
    scope_mode: str = "legacy"


class DriftMonitor:
    def __init__(
        self,
        agent_id: str,
        config: DriftConfig | None = None,
        *,
        scope: TenantScope | None = None,
    ) -> None:
        if not is_safe_agent_id(agent_id):
            # Validate before resolving state or SOUL paths: an invalid event
            # must not create a lock/directory or inspect an arbitrary file.
            raise ValueError("unsafe agent id for drift monitoring")
        self.agent_id = agent_id
        self.config = config or DriftConfig()
        self.scope = scope
        self._last_soul_hash: str | None = None
        self._last_genome_version: int | None = None
        self._last_check_ts: float = 0.0
        self._baseline_score: float | None = None
        self._state_path = self._resolve_state_path()
        self._state_error = ""
        self._load_state()

    def check(self, *, publish_events: bool = True) -> DriftReport:
        events: list[DriftEvent] = []
        now = datetime.now().isoformat(timespec="seconds")

        if self._state_error:
            events.append(self._state_integrity_event(now, self._state_error))
        else:
            previous = (
                self._last_soul_hash,
                self._last_genome_version,
                self._baseline_score,
            )
            soul_event = self._check_soul_drift(now)
            if soul_event is not None:
                events.append(soul_event)

            genome_event = self._check_genome_drift(now)
            if genome_event is not None:
                events.append(genome_event)

            score_event = self._check_score_drift(now)
            if score_event is not None:
                events.append(score_event)

            try:
                self._persist_state(now)
            except (OSError, TransactionalFileError, TypeError, ValueError) as exc:
                # Do not advance the in-memory baseline if its durable commit
                # failed.  Otherwise this process would disagree with the next
                # worker/restart and potentially suppress a real regression.
                (
                    self._last_soul_hash,
                    self._last_genome_version,
                    self._baseline_score,
                ) = previous
                self._state_error = f"state persistence failed: {type(exc).__name__}"
                events.append(self._state_integrity_event(now, self._state_error))

        has_drift = len(events) > 0
        severities = [e.severity for e in events]
        if "critical" in severities:
            max_severity = "critical"
        elif "warning" in severities:
            max_severity = "warning"
        elif "info" in severities:
            max_severity = "info"
        else:
            max_severity = "none"

        if publish_events:
            _publish_drift_events(events, agent_id=self.agent_id, scope=self.scope)
        return DriftReport(
            agent_id=self.agent_id,
            ts=now,
            events=events,
            has_drift=has_drift,
            max_severity=max_severity,
            tenant_id=(
                self.scope.tenant_id
                if self.scope is not None and not self.scope.allow_cross_tenant
                else ""
            ),
            owner_actor_id=(
                self.scope.actor_id
                if self.scope is not None and not self.scope.allow_cross_tenant
                else ""
            ),
            scope_mode=(
                "cross_tenant"
                if self.scope is not None and self.scope.allow_cross_tenant
                else "tenant"
                if self.scope is not None
                else "legacy"
            ),
        )

    def _resolve_state_path(self) -> Path | None:
        if self.scope is not None and self.scope.allow_cross_tenant:
            # A cross-tenant aggregate has no single owner and must never
            # become a baseline that later drives one tenant's rollback.
            return None
        state_dir = (
            Path(self.config.state_dir).expanduser()
            if self.config.state_dir
            else Path(os.environ["ECHO_DRIFT_STATE_DIR"]).expanduser()
            if os.environ.get("ECHO_DRIFT_STATE_DIR")
            else app_paths().data_dir / "evolution_drift_state"
        )
        opaque_agent_id = hashlib.sha256(self.agent_id.encode("utf-8")).hexdigest()[:32]
        base = state_dir / f"{opaque_agent_id}.json"
        return tenant_scoped_path(base, self.scope) if self.scope is not None else base

    def _state_scope_fields(self) -> tuple[str, str, str]:
        if self.scope is None:
            return ("legacy", "", "")
        return ("tenant", self.scope.tenant_id, self.scope.actor_id)

    def _validate_state(self, raw: Any) -> None:
        if raw is _MISSING_DRIFT_STATE:
            return
        if not isinstance(raw, dict) or raw.get("schema") != _DRIFT_STATE_SCHEMA:
            raise ValueError("drift state schema is invalid")
        scope_mode, tenant_id, owner_actor_id = self._state_scope_fields()
        if str(raw.get("agent_id") or "") != self.agent_id:
            raise ValueError("drift state agent provenance is invalid")
        if str(raw.get("scope_mode") or "") != scope_mode:
            raise ValueError("drift state scope mode is invalid")
        if str(raw.get("tenant_id") or "") != tenant_id:
            raise ValueError("drift state tenant provenance is invalid")
        if str(raw.get("owner_actor_id") or "") != owner_actor_id:
            raise ValueError("drift state owner provenance is invalid")
        soul_hash = raw.get("last_soul_hash")
        genome_version = raw.get("last_genome_version")
        baseline_score = raw.get("baseline_score")
        if soul_hash is not None and not isinstance(soul_hash, str):
            raise ValueError("drift state soul hash is invalid")
        if genome_version is not None and (
            isinstance(genome_version, bool) or not isinstance(genome_version, int)
        ):
            raise ValueError("drift state genome version is invalid")
        if baseline_score is not None and (
            isinstance(baseline_score, bool) or not isinstance(baseline_score, (int, float))
        ):
            raise ValueError("drift state score baseline is invalid")
        if baseline_score is not None and not 0.0 <= float(baseline_score) <= 1.0:
            raise ValueError("drift state score baseline is out of range")

    def _load_state(self) -> None:
        if self._state_path is None:
            return
        try:
            raw = read_json_file(
                self._state_path,
                default_factory=lambda: _MISSING_DRIFT_STATE,
                validate=self._validate_state,
                mode=0o600,
            )
            if raw is _MISSING_DRIFT_STATE:
                return
            self._last_soul_hash = raw.get("last_soul_hash")
            self._last_genome_version = raw.get("last_genome_version")
            baseline = raw.get("baseline_score")
            self._baseline_score = float(baseline) if baseline is not None else None
        except (OSError, TransactionalFileError, TypeError, ValueError) as exc:
            self._state_error = f"state load failed: {type(exc).__name__}"

    def _persist_state(self, now: str) -> None:
        if self._state_path is None:
            return
        scope_mode, tenant_id, owner_actor_id = self._state_scope_fields()
        payload = {
            "schema": _DRIFT_STATE_SCHEMA,
            "agent_id": self.agent_id,
            "scope_mode": scope_mode,
            "tenant_id": tenant_id,
            "owner_actor_id": owner_actor_id,
            "last_soul_hash": self._last_soul_hash,
            "last_genome_version": self._last_genome_version,
            "baseline_score": self._baseline_score,
            "updated_at": now,
        }

        def _replace(current: Any) -> JsonMutation[None]:
            if not isinstance(current, dict):
                raise ValueError("drift state must be an object")
            current.clear()
            current.update(payload)
            return JsonMutation(None)

        mutate_json_file(
            self._state_path,
            default_factory=lambda: dict(payload),
            validate=self._validate_state,
            mutate=_replace,
            mode=0o600,
        )

    @staticmethod
    def _state_integrity_event(now: str, error: str) -> DriftEvent:
        return DriftEvent(
            kind="drift_state_integrity",
            severity="critical",
            detail="Drift baseline state is unavailable or invalid; checks are fail-closed",
            ts=now,
            metadata={"error": error},
        )

    def _check_soul_drift(self, now: str) -> DriftEvent | None:
        from runtime.execution.agents.loader import default_agents_root

        soul_path = default_agents_root() / self.agent_id / "agent-core" / "SOUL.md"
        if not soul_path.exists():
            return None

        try:
            current_hash = hashlib.md5(soul_path.read_bytes(), usedforsecurity=False).hexdigest()[
                :8
            ]
        except Exception as _exc:
            _LOG.debug("drift check failed: %s", _exc)
            return None

        if self._last_soul_hash is None:
            self._last_soul_hash = current_hash
            return None

        if current_hash != self._last_soul_hash:
            old_hash = self._last_soul_hash
            self._last_soul_hash = current_hash
            return DriftEvent(
                kind="soul_change",
                severity="info",
                detail=f"SOUL.md changed: {old_hash} -> {current_hash}",
                ts=now,
                metadata={"old_hash": old_hash, "new_hash": current_hash},
            )

        return None

    def _check_genome_drift(self, now: str) -> DriftEvent | None:
        try:
            from runtime.safety.recovery.genome_registry import GenomeRegistry

            registry = GenomeRegistry(
                config=__import__(
                    "runtime.safety.recovery.genome_registry",
                    fromlist=["GenomeRegistryConfig"],
                ).GenomeRegistryConfig(genome_dir=self.config.genome_dir),
            )
            current_version = registry.latest_version()
        except Exception as _exc:
            _LOG.debug("drift check failed: %s", _exc)
            return None

        if self._last_genome_version is None:
            self._last_genome_version = current_version
            return None

        if current_version != self._last_genome_version:
            old_v = self._last_genome_version
            self._last_genome_version = current_version
            return DriftEvent(
                kind="genome_change",
                severity="info",
                detail=f"Genome version changed: v{old_v} -> v{current_version}",
                ts=now,
                metadata={"old_version": old_v, "new_version": current_version},
            )

        return None

    def _check_score_drift(self, now: str) -> DriftEvent | None:
        from runtime.memory.learning.turn_scoring import read_recent_scores

        scores = read_recent_scores(
            self.agent_id,
            limit=self.config.score_window,
            scope=self.scope,
        )
        if len(scores) < 5:
            return None

        current_avg = sum(s.score for s in scores) / len(scores)

        if self._baseline_score is None:
            self._baseline_score = current_avg
            return None

        delta = current_avg - self._baseline_score
        if delta < -self.config.score_drop_threshold:
            severity = "critical" if delta < -0.3 else "warning"
            event = DriftEvent(
                kind="score_regression",
                severity=severity,
                detail=(
                    f"Score dropped {abs(delta):.2f} "
                    f"({self._baseline_score:.2f} -> {current_avg:.2f})"
                ),
                ts=now,
                metadata={
                    "baseline": round(self._baseline_score, 3),
                    "current": round(current_avg, 3),
                    "delta": round(delta, 3),
                },
            )
            self._baseline_score = current_avg
            return event

        if delta > self.config.score_drop_threshold:
            self._baseline_score = current_avg

        return None


__all__ = [
    "DriftConfig",
    "DriftEvent",
    "DriftMonitor",
    "DriftReport",
]
