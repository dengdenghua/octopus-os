"""Candidate-scoped canary rollout, promotion, and rollback coordination."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from runtime.platform.io import path_transaction
from runtime.safety.evolution.canary import (
    CanaryConfig,
    CanaryManager,
    CanaryPhase,
    CanaryState,
)
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateStatus,
    EvolutionCandidate,
)


def _canary_key(candidate: EvolutionCandidate) -> str:
    digest = hashlib.sha256(candidate.deployment_key.encode("utf-8")).hexdigest()[:16]
    return f"candidate.{candidate.candidate_id}.{digest}"


_PENDING_SYNC_KEY = "registry_sync_pending"


def _sync_token(candidate_id: str, operation_id: str) -> str:
    payload = f"{candidate_id}|{operation_id}".encode()
    return hashlib.sha256(payload).hexdigest()


class CandidateCanaryManager:
    """Bind generic traffic phases to one typed candidate deployment key."""

    def __init__(
        self,
        registry: CandidateRegistry,
        state_dir: str | Path,
        *,
        config: CanaryConfig | None = None,
        runtime_registry: Any = None,
        materialize_runtime: bool = True,
    ) -> None:
        self.registry = registry
        self.runtime_registry = runtime_registry
        self.materialize_runtime = bool(materialize_runtime)
        self.state_dir = Path(state_dir)
        base = config or CanaryConfig()
        # Generic canaries historically defaulted to a 20-sample rolling
        # window, while later stages require 40/60 observations. Candidate
        # rollout must be able to reach FULL, so retain at least one complete
        # phase window.
        resolved = replace(
            base,
            state_dir=str(state_dir),
            sample_window=max(60, int(base.sample_window or 1)),
        )
        self.canary = CanaryManager(resolved)

    def _state_path(self, key: str) -> Path:
        return self.state_dir / f"{key}.json"

    def register(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        if candidate.status not in {CandidateStatus.SHADOW, CandidateStatus.CANARY}:
            raise ValueError("candidate must pass structured shadow review before canary")
        if not candidate.hard_gate_passed:
            raise ValueError("candidate canary requires passing hard-gate evidence")

        # Fail closed before changing rollout state: a candidate that has no
        # runtime consumer must never be shown as "in canary" in the UI.
        from runtime.safety.evolution.runtime_deployment import CandidateRuntimeSelector

        runtime_selector = CandidateRuntimeSelector(self.registry, self.state_dir)
        runtime_selector.validate_materializable(candidate)
        if self.materialize_runtime:
            runtime_selector.ensure_skill_registered(candidate, self.runtime_registry)

        key = _canary_key(candidate)
        sync_token = _sync_token(candidate.candidate_id, "register")
        with path_transaction(self._state_path(key)):
            existing_state = self.canary.refresh(key)
            candidate = self._require_candidate(candidate_id)
            if candidate.status not in {CandidateStatus.SHADOW, CandidateStatus.CANARY}:
                raise ValueError("candidate must pass structured shadow review before canary")
            if existing_state is not None:
                self._validate_state_binding(candidate, existing_state)
            state = self.canary.register(
                key,
                initial_phase=CanaryPhase.CANARY_5,
                metadata={
                    "candidate_id": candidate.candidate_id,
                    "deployment_key": candidate.deployment_key,
                    "role_id": candidate.role_id,
                    "task_domain": candidate.task_domain,
                    "environment_digest": candidate.environment_digest,
                    "runtime_materialized": self.materialize_runtime,
                    _PENDING_SYNC_KEY: {
                        "token": sync_token,
                        "operation": "register",
                    },
                },
            )
            if candidate.status == CandidateStatus.SHADOW:
                candidate = self.registry.transition(
                    candidate.candidate_id,
                    CandidateStatus.CANARY,
                    metadata={"canary_key": key, "canary_phase": state.phase.value},
                )
            candidate, state = self._finish_sync(candidate, state, key=key)
        return self._to_wire(candidate, state)

    def record_outcome(
        self,
        candidate_id: str,
        success: bool,
        *,
        outcome_id: str | None = None,
    ) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        key = _canary_key(candidate)
        resolved_outcome_id = str(outcome_id or "").strip() or f"manual:{uuid.uuid4().hex}"
        sync_token = _sync_token(candidate.candidate_id, resolved_outcome_id)
        with path_transaction(self._state_path(key)):
            candidate = self._require_candidate(candidate_id)
            if candidate.status in {
                CandidateStatus.PROMOTED,
                CandidateStatus.ROLLED_BACK,
            }:
                # A previous attempt can commit the registry transition and
                # then fail while clearing the state-side pending marker.  A
                # retry must reconcile that marker without counting again.
                state = self.canary.refresh(key)
                if state is None:
                    raise KeyError(f"candidate canary is not registered: {candidate_id}")
                self._validate_state_binding(candidate, state)
                if self._needs_registry_reconciliation(candidate, state):
                    candidate = self._reconcile_registry(candidate, state, key=key)
                candidate, state = self._finish_sync(candidate, state, key=key)
                return self._to_wire(candidate, state)
            if candidate.status != CandidateStatus.CANARY:
                raise ValueError("candidate is not in canary")
            before = self.canary.refresh(key)
            if before is None:
                raise KeyError(f"candidate canary is not registered: {candidate_id}")
            self._validate_state_binding(candidate, before)
            before_phase = before.phase
            state = self.canary.record_outcome(
                key,
                bool(success),
                outcome_id=resolved_outcome_id,
                metadata_updates={
                    _PENDING_SYNC_KEY: {
                        "token": sync_token,
                        "operation": "outcome",
                    }
                },
            )
            if state is None:  # defensive: key was validated above
                raise KeyError(f"candidate canary is not registered: {candidate_id}")

            if state.phase in {CanaryPhase.ROLLED_BACK, CanaryPhase.FULL} or (
                state.phase != before_phase
            ):
                candidate = self._reconcile_registry(candidate, state, key=key)
            elif candidate.metadata.get("canary_phase") != state.phase.value:
                # Recover an earlier state write whose registry evidence did
                # not reach durable storage.
                candidate = self._reconcile_registry(candidate, state, key=key)
            candidate, state = self._finish_sync(candidate, state, key=key)
        return self._to_wire(candidate, state)

    def force_rollback(self, candidate_id: str, *, reason: str) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        if candidate.status == CandidateStatus.ROLLED_BACK:
            return self.status(candidate_id)
        if candidate.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
            raise ValueError("only canary or promoted candidates can be rolled back")
        key = _canary_key(candidate)
        sync_token = _sync_token(candidate.candidate_id, f"rollback:{reason}")
        with path_transaction(self._state_path(key)):
            candidate = self._require_candidate(candidate_id)
            if candidate.status == CandidateStatus.ROLLED_BACK:
                state = self.canary.refresh(key)
                if state is None:
                    raise KeyError(f"candidate canary is not registered: {candidate_id}")
                candidate, state = self._finish_sync(candidate, state, key=key)
                return self._to_wire(candidate, state)
            if candidate.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
                raise ValueError("only canary or promoted candidates can be rolled back")
            before = self.canary.refresh(key)
            if before is None:
                raise KeyError(f"candidate canary is not registered: {candidate_id}")
            self._validate_state_binding(candidate, before)
            state = self.canary.force_rollback(
                key,
                reason=reason,
                metadata={
                    _PENDING_SYNC_KEY: {
                        "token": sync_token,
                        "operation": "rollback",
                        "phase": CanaryPhase.ROLLED_BACK.value,
                    }
                },
            )
            if state is None:
                raise KeyError(f"candidate canary is not registered: {candidate_id}")
            candidate = self._reconcile_registry(candidate, state, key=key)
            candidate, state = self._finish_sync(candidate, state, key=key)
        return self._to_wire(candidate, state)

    def status(self, candidate_id: str) -> dict[str, Any]:
        candidate = self._require_candidate(candidate_id)
        key = _canary_key(candidate)
        with path_transaction(self._state_path(key)):
            state = self.canary.refresh(key)
            if state is not None:
                self._validate_state_binding(candidate, state)
                if self._needs_registry_reconciliation(candidate, state):
                    candidate = self._reconcile_registry(candidate, state, key=key)
                candidate, state = self._finish_sync(candidate, state, key=key)
        return self._to_wire(candidate, state)

    def should_route(self, candidate_id: str) -> bool:
        if not self.materialize_runtime:
            return False
        candidate = self._require_candidate(candidate_id)
        if candidate.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
            return False
        key = _canary_key(candidate)
        with path_transaction(self._state_path(key)):
            try:
                state = self.canary.refresh(key)
                if state is not None:
                    self._validate_state_binding(candidate, state)
            except (OSError, RuntimeError, ValueError):
                return False
            if state is None:
                return False
            if state.metadata.get(_PENDING_SYNC_KEY) is not None:
                return False
            if candidate.status == CandidateStatus.PROMOTED:
                return state.phase == CanaryPhase.FULL
            if state.phase not in {
                CanaryPhase.CANARY_5,
                CanaryPhase.CANARY_25,
                CanaryPhase.CANARY_50,
            }:
                return False
            return self.canary.should_route_to_skill(key)

    @staticmethod
    def _needs_registry_reconciliation(
        candidate: EvolutionCandidate,
        state: CanaryState,
    ) -> bool:
        if state.metadata.get(_PENDING_SYNC_KEY) is not None:
            return True
        if candidate.status == CandidateStatus.SHADOW:
            return True
        if state.phase == CanaryPhase.ROLLED_BACK:
            return candidate.status != CandidateStatus.ROLLED_BACK
        if state.phase == CanaryPhase.FULL:
            return candidate.status == CandidateStatus.CANARY
        return candidate.metadata.get("canary_phase") != state.phase.value

    def _reconcile_registry(
        self,
        candidate: EvolutionCandidate,
        state: CanaryState,
        *,
        key: str,
    ) -> EvolutionCandidate:
        """Bring the append-only registry up to the durable canary state."""

        self._validate_state_binding(candidate, state)
        metadata = {
            "canary_key": key,
            "canary_phase": state.phase.value,
            "canary_sample_count": state.sample_count,
            "canary_success_rate": state.current_rate,
        }
        pending = state.metadata.get(_PENDING_SYNC_KEY)
        if isinstance(pending, dict) and pending.get("token"):
            metadata["canary_sync_token"] = str(pending["token"])

        if state.phase == CanaryPhase.ROLLED_BACK:
            if candidate.status == CandidateStatus.ROLLED_BACK:
                return candidate
            if candidate.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
                raise ValueError("rolled-back canary state conflicts with candidate lifecycle")
            return self.registry.transition(
                candidate.candidate_id,
                CandidateStatus.ROLLED_BACK,
                rollback_target=candidate.parent_id or "baseline",
                metadata={
                    **metadata,
                    "rollback_reason": state.metadata.get("last_rollback_reason")
                    or "canary threshold breached",
                },
            )

        if state.phase == CanaryPhase.FULL:
            if candidate.status == CandidateStatus.ROLLED_BACK:
                raise ValueError("full canary state conflicts with rolled-back candidate")
            if self.materialize_runtime:
                if candidate.status == CandidateStatus.PROMOTED:
                    return candidate
                if candidate.status != CandidateStatus.CANARY:
                    raise ValueError("full canary state requires a canary candidate")
                return self.registry.transition(
                    candidate.candidate_id,
                    CandidateStatus.PROMOTED,
                    metadata=metadata,
                )
            if candidate.status != CandidateStatus.CANARY:
                raise ValueError("non-materialized full state requires a canary candidate")
            blocked_metadata = {
                **metadata,
                "promotion_blocked": "tenant_runtime_registry_not_partitioned",
            }
            if all(
                candidate.metadata.get(name) == value for name, value in blocked_metadata.items()
            ):
                return candidate
            return self.registry.record_evidence(
                candidate.candidate_id,
                metadata=blocked_metadata,
            )

        if candidate.status == CandidateStatus.SHADOW:
            return self.registry.transition(
                candidate.candidate_id,
                CandidateStatus.CANARY,
                metadata=metadata,
            )
        if candidate.status != CandidateStatus.CANARY:
            raise ValueError("active canary state conflicts with candidate lifecycle")
        if all(candidate.metadata.get(name) == value for name, value in metadata.items()):
            return candidate
        return self.registry.record_evidence(candidate.candidate_id, metadata=metadata)

    def _finish_sync(
        self,
        candidate: EvolutionCandidate,
        state: CanaryState,
        *,
        key: str,
    ) -> tuple[EvolutionCandidate, CanaryState]:
        """Clear a pending marker only after registry durability is proven."""

        pending = state.metadata.get(_PENDING_SYNC_KEY)
        if pending is not None:
            updated_state = self.canary.update_metadata(key, remove=(_PENDING_SYNC_KEY,))
            if updated_state is None:
                raise KeyError(f"candidate canary is not registered: {candidate.candidate_id}")
            state = updated_state
        refreshed = self._require_candidate(candidate.candidate_id)
        return refreshed, state

    def _validate_state_binding(
        self,
        candidate: EvolutionCandidate,
        state: CanaryState,
    ) -> None:
        metadata = state.metadata
        if metadata.get("candidate_id") != candidate.candidate_id:
            raise ValueError("canary state candidate binding is invalid")
        if metadata.get("deployment_key") != candidate.deployment_key:
            raise ValueError("canary state deployment binding is stale")
        if metadata.get("runtime_materialized") is not self.materialize_runtime:
            raise ValueError("canary state runtime materialization mode conflicts with manager")

    def _require_candidate(self, candidate_id: str) -> EvolutionCandidate:
        candidate = self.registry.get(candidate_id)
        if candidate is None:
            raise KeyError(f"unknown evolution candidate: {candidate_id}")
        return candidate

    @staticmethod
    def _to_wire(
        candidate: EvolutionCandidate,
        state: CanaryState | None,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "schema": "echo.evolution.candidate_canary.v1",
            "candidate": candidate.to_wire(),
            "canary": (
                {
                    **asdict(state),
                    "phase": state.phase.value,
                }
                if state is not None
                else None
            ),
        }


__all__ = ["CandidateCanaryManager"]
