"""Fail-closed rollback of governed candidates after a score regression.

Score drift is aggregate evidence: it can tell us that an agent became worse,
but it cannot reliably attribute the regression to one of several concurrent
candidates.  The conservative recovery action is therefore to roll back every
active governed candidate for that exact agent and ownership partition.

The helper deliberately does not mutate SOUL.md, prompts, or skills directly.
It drives the existing candidate/canary state machine so lineage, rollout
state, and runtime routing all converge on ``ROLLED_BACK``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.candidate_canary import CandidateCanaryManager
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateStatus,
    EvolutionCandidate,
)


class RegressionRollbackError(RuntimeError):
    """The rollback could not be proven safe or durable."""


@dataclass(frozen=True)
class RegressionRollbackResult:
    """Outcome of one exact-partition recovery decision."""

    agent_id: str
    active_candidate_ids: tuple[str, ...]
    rolled_back_candidate_ids: tuple[str, ...]
    scope_mode: str

    @property
    def changed(self) -> bool:
        return bool(self.rolled_back_candidate_ids)


def _paths_for_scope(scope: TenantScope | None) -> tuple[Path, Path, bool]:
    paths = app_paths()
    if scope is None:
        return (
            paths.evolution_candidates_path,
            paths.candidate_canary_state_dir,
            True,
        )
    if scope.allow_cross_tenant:
        raise RegressionRollbackError(
            "cross-tenant score regression cannot be attributed to one ownership partition"
        )
    registry_path = tenant_scoped_path(paths.evolution_candidates_path, scope)
    return (
        registry_path,
        registry_path.parent / paths.candidate_canary_state_dir.name,
        False,
    )


def _active_candidates(
    registry: CandidateRegistry,
    *,
    agent_id: str,
    scope: TenantScope | None,
) -> list[EvolutionCandidate]:
    rows = [
        row
        for row in registry.list(limit=10_000)
        if row.role_id == agent_id
        and row.status in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}
    ]
    if scope is None:
        # Legacy/global recovery never adopts an owned row that happened to
        # land in an old shared registry before tenant partitioning existed.
        rows = [row for row in rows if row.tenant_id is None and row.owner_actor_id is None]
    return list(reversed(rows))


def rollback_active_candidates_for_regression(
    agent_id: str,
    *,
    scope: TenantScope | None = None,
    reason: str = "critical score regression",
) -> RegressionRollbackResult:
    """Roll back active candidates for one agent and exact ownership scope.

    Every candidate is preflighted before the first rollback.  A missing,
    corrupt, pending, or incorrectly bound canary state therefore stops the
    operation before a healthy candidate is changed.  The underlying state
    machine supplies cross-process locking, durable writes, idempotency, and
    registry/canary reconciliation.
    """

    resolved_agent_id = str(agent_id or "").strip()
    if not resolved_agent_id:
        raise RegressionRollbackError("agent id is required for regression rollback")
    resolved_reason = str(reason or "").strip() or "critical score regression"

    registry_path, state_dir, materialize_runtime = _paths_for_scope(scope)
    registry = CandidateRegistry(registry_path, tenant_scope=scope)
    manager = CandidateCanaryManager(
        registry,
        state_dir,
        materialize_runtime=materialize_runtime,
    )

    try:
        active = _active_candidates(registry, agent_id=resolved_agent_id, scope=scope)
        # Preflight the complete rollback set. ``status`` validates durable
        # candidate/state binding and completes any interrupted two-file sync.
        for candidate in active:
            wire = manager.status(candidate.candidate_id)
            canary = wire.get("canary")
            current = wire.get("candidate")
            if not isinstance(canary, dict) or not isinstance(current, dict):
                raise RegressionRollbackError(
                    f"candidate {candidate.candidate_id} has no durable canary state"
                )
            if current.get("status") not in {
                CandidateStatus.CANARY.value,
                CandidateStatus.PROMOTED.value,
            }:
                raise RegressionRollbackError(
                    f"candidate {candidate.candidate_id} changed during rollback preflight"
                )

        rolled_back: list[str] = []
        for candidate in active:
            wire = manager.force_rollback(candidate.candidate_id, reason=resolved_reason)
            if wire.get("candidate", {}).get("status") != CandidateStatus.ROLLED_BACK.value:
                raise RegressionRollbackError(
                    f"candidate {candidate.candidate_id} rollback was not durable"
                )
            rolled_back.append(candidate.candidate_id)
    except RegressionRollbackError:
        raise
    except Exception as exc:  # noqa: BLE001 - storage/control-plane failures fail closed
        raise RegressionRollbackError("governed candidate rollback failed closed") from exc

    return RegressionRollbackResult(
        agent_id=resolved_agent_id,
        active_candidate_ids=tuple(candidate.candidate_id for candidate in active),
        rolled_back_candidate_ids=tuple(rolled_back),
        scope_mode="tenant" if scope is not None else "legacy",
    )


__all__ = [
    "RegressionRollbackError",
    "RegressionRollbackResult",
    "rollback_active_candidates_for_regression",
]
