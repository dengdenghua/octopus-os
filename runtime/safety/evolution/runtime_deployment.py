"""Runtime selection for governed evolution candidates.

The candidate registry is the control-plane source of truth. This module is
the data-plane adapter that makes a CANARY/PROMOTED candidate observable by
real turns without mutating baseline prompts, role files, or the skill
catalog. Canary assignment is stable per thread/conversation so one task does
not flip between control and treatment halfway through a run.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from runtime.platform.io import TransactionalFileError
from runtime.safety.evolution.canary import (
    CanaryConfig,
    CanaryManager,
    CanaryPersistenceError,
    CanaryPhase,
)
from runtime.safety.evolution.candidate_registry import (
    CandidateRegistry,
    CandidateRegistryError,
    CandidateStatus,
    EvolutionCandidate,
    GeneType,
)

_TRAFFIC_PERCENT = {
    CanaryPhase.CANARY_5: 0.05,
    CanaryPhase.CANARY_25: 0.25,
    CanaryPhase.CANARY_50: 0.50,
    CanaryPhase.FULL: 1.0,
    CanaryPhase.SHADOW: 0.0,
    CanaryPhase.ROLLED_BACK: 0.0,
}


def _candidate_canary_key(candidate: EvolutionCandidate) -> str:
    digest = hashlib.sha256(candidate.deployment_key.encode("utf-8")).hexdigest()[:16]
    return f"candidate.{candidate.candidate_id}.{digest}"


def _ambient_routing_key() -> str | None:
    try:
        from runtime.platform.process.session import current_session

        session = current_session()
        if session is None:
            return None
        value = session.thread_id or session.conversation_id or session.actor or session.turn_id
        return str(value).strip() if value else None
    except (ImportError, AttributeError, TypeError):
        return None


def _bucket(candidate: EvolutionCandidate, routing_key: str) -> float:
    digest = hashlib.sha256(f"{candidate.deployment_key}|{routing_key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / float(2**64)


class CandidateRuntimeSelector:
    """Resolve the newest active candidate for one runtime scope."""

    def __init__(
        self,
        registry: CandidateRegistry,
        canary_state_dir: str | Path,
        *,
        outcome_inbox_path: str | Path | None = None,
    ) -> None:
        self.registry = registry
        self.canary_state_dir = Path(canary_state_dir)
        self.outcome_inbox_path = (
            Path(outcome_inbox_path) if outcome_inbox_path is not None else None
        )

    def is_active(
        self,
        candidate: EvolutionCandidate | str,
        *,
        routing_key: str | None = None,
    ) -> bool:
        candidate_id = candidate if isinstance(candidate, str) else candidate.candidate_id
        try:
            resolved = self.registry.get(candidate_id)
        except (CandidateRegistryError, OSError, ValueError):
            return False
        if resolved is None:
            return False
        if resolved.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
            return False
        try:
            state = CanaryManager(CanaryConfig(state_dir=str(self.canary_state_dir))).refresh(
                _candidate_canary_key(resolved)
            )
        except (CanaryPersistenceError, OSError, TransactionalFileError, ValueError):
            return False
        if state is None:
            return False
        metadata = state.metadata
        if (
            metadata.get("candidate_id") != resolved.candidate_id
            or metadata.get("deployment_key") != resolved.deployment_key
            or metadata.get("runtime_materialized") is not True
            or metadata.get("registry_sync_pending") is not None
        ):
            return False
        if resolved.status == CandidateStatus.PROMOTED:
            return state.phase == CanaryPhase.FULL
        if state.phase not in {
            CanaryPhase.CANARY_5,
            CanaryPhase.CANARY_25,
            CanaryPhase.CANARY_50,
        }:
            return False
        key = routing_key or _ambient_routing_key()
        if not key:
            return False
        traffic = _TRAFFIC_PERCENT.get(state.phase, 0.0)
        return traffic >= 1.0 or _bucket(resolved, key) < traffic

    def select(
        self,
        *,
        gene_type: GeneType | str,
        scope: str,
        routing_key: str | None = None,
    ) -> EvolutionCandidate | None:
        resolved_type = GeneType(str(gene_type))
        rows = [
            row
            for row in self.registry.list(gene_type=resolved_type, limit=10_000)
            if row.scope == scope
            and row.status in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}
        ]
        for row in reversed(rows):
            if self.is_active(row, routing_key=routing_key):
                if row.status == CandidateStatus.CANARY:
                    from runtime.safety.evolution.runtime_outcomes import (
                        _current_turn_id,
                        record_runtime_candidate_activation,
                    )

                    turn_id = _current_turn_id()
                    if not turn_id or not record_runtime_candidate_activation(
                        row.candidate_id,
                        turn_id=turn_id,
                        inbox_path=self.outcome_inbox_path,
                    ):
                        # A canary must not alter a real turn unless its outcome
                        # remains recoverable after a worker crash.
                        return None
                return row
        return None

    def prompt_addendum(
        self,
        scope: str,
        *,
        routing_key: str | None = None,
    ) -> tuple[str | None, str]:
        candidate = self.select(
            gene_type=GeneType.PROMPT,
            scope=scope,
            routing_key=routing_key,
        )
        if candidate is None:
            return None, ""
        patch = candidate.patch
        if patch.get("op") != "replace" or not isinstance(patch.get("value"), str):
            return None, ""
        return candidate.candidate_id, str(patch["value"]).strip()

    def apply_role(
        self,
        soul: str,
        agent_id: str,
        *,
        routing_key: str | None = None,
    ) -> str:
        candidate = self.select(
            gene_type=GeneType.ROLE,
            scope=f"agent.{agent_id}.soul",
            routing_key=routing_key,
        )
        if candidate is None:
            return soul
        patch = candidate.patch
        if patch.get("op") == "append_lesson" and str(patch.get("value") or "").strip():
            tag = str(patch.get("tag") or "governed evolution").strip()
            lesson = str(patch["value"]).strip()
            return f"{soul.rstrip()}\n\n## Candidate lesson · {tag}\n\n{lesson}".strip()
        if patch.get("op") == "replace" and isinstance(patch.get("value"), str):
            return str(patch["value"]).strip()
        return soul

    @staticmethod
    def validate_materializable(candidate: EvolutionCandidate) -> None:
        patch = candidate.patch
        if candidate.gene_type == GeneType.PROMPT:
            if patch.get("op") == "replace" and str(patch.get("value") or "").strip():
                return
        elif candidate.gene_type == GeneType.ROLE:
            if (
                patch.get("op") in {"append_lesson", "replace"}
                and str(patch.get("value") or "").strip()
            ):
                return
        elif candidate.gene_type == GeneType.SKILL:
            sequence = patch.get("underlying_sequence")
            if (
                patch.get("op") == "register_forged_skill"
                and str(patch.get("name") or "").strip()
                and isinstance(sequence, list)
                and sequence
            ):
                return
        raise ValueError(
            f"candidate patch has no runtime consumer: {candidate.gene_type.value} {patch.get('op')}"
        )

    def ensure_skill_registered(
        self,
        candidate: EvolutionCandidate,
        runtime_registry: Any,
    ) -> None:
        if candidate.gene_type != GeneType.SKILL:
            return
        self.validate_materializable(candidate)
        if runtime_registry is None:
            raise ValueError("skill candidate rollout requires a live skill registry")
        patch = candidate.patch
        name = str(patch["name"]).strip()
        if runtime_registry.has(name):
            existing = runtime_registry.get(name)
            if getattr(existing, "rollout_candidate_id", None) == candidate.candidate_id:
                return
            raise ValueError(f"skill candidate conflicts with existing skill: {name}")

        from runtime.execution.suckers.forged_persistence import (
            _build_composite_handler_with_templates,
        )
        from runtime.execution.suckers.registry import Skill

        sequence = [str(item) for item in patch["underlying_sequence"]]
        missing = [item for item in sequence if not runtime_registry.has(item)]
        if missing:
            raise ValueError(f"skill candidate dependencies are unavailable: {', '.join(missing)}")
        handler = _build_composite_handler_with_templates(
            sequence,
            runtime_registry,
            step_templates=[
                dict(item) for item in patch.get("step_templates") or [] if isinstance(item, dict)
            ],
        )

        def _governed_handler(**kwargs: Any) -> Any:
            from runtime.safety.evolution.runtime_outcomes import (
                _current_turn_id,
                record_runtime_candidate_activation,
            )

            try:
                current = self.registry.get(candidate.candidate_id)
            except (CandidateRegistryError, OSError, ValueError) as exc:
                raise RuntimeError("governed candidate state could not be verified") from exc
            if current is None:
                raise RuntimeError("governed candidate state could not be verified")
            if current.status == CandidateStatus.CANARY:
                turn_id = _current_turn_id()
                if not turn_id or not record_runtime_candidate_activation(
                    candidate.candidate_id,
                    turn_id=turn_id,
                    inbox_path=self.outcome_inbox_path,
                ):
                    raise RuntimeError("governed candidate activation could not be persisted")
            elif current.status != CandidateStatus.PROMOTED:
                raise RuntimeError("governed candidate is not active")
            return handler(**kwargs)

        runtime_registry.register(
            Skill(
                name=name,
                description=str(patch.get("description") or ""),
                trusted_source=f"skill://evolution/{candidate.candidate_id}",
                affinity=["forged", "governed"],
                handler=_governed_handler,
                rollout_candidate_id=candidate.candidate_id,
            ),
            verify_tests=False,
        )


def default_runtime_selector() -> CandidateRuntimeSelector:
    from runtime.platform.process.paths import app_paths

    paths = app_paths()
    return CandidateRuntimeSelector(
        CandidateRegistry(paths.evolution_candidates_path),
        paths.candidate_canary_state_dir,
        outcome_inbox_path=paths.candidate_runtime_outcomes_path,
    )


def is_rollout_candidate_visible(candidate_id: str) -> bool:
    return default_runtime_selector().is_active(candidate_id)


def load_governed_candidate_skills(runtime_registry: Any) -> list[str]:
    selector = default_runtime_selector()
    loaded: list[str] = []
    for candidate in selector.registry.list(gene_type=GeneType.SKILL, limit=10_000):
        if candidate.status not in {CandidateStatus.CANARY, CandidateStatus.PROMOTED}:
            continue
        selector.ensure_skill_registered(candidate, runtime_registry)
        loaded.append(str(candidate.patch.get("name") or candidate.scope))
    return loaded


__all__ = [
    "CandidateRuntimeSelector",
    "default_runtime_selector",
    "is_rollout_candidate_visible",
    "load_governed_candidate_skills",
]
