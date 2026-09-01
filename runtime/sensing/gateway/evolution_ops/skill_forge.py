"""SkillForge subsystem for evolution operators."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.recovery.tenant_scope import read_learning_events

from .utils import (
    _as_dt,
    _journal_events,
    _registry_has_skill,
    _utcnow,
)


def _skill_forge_candidates(
    journal: Any,
    registry: Any,
    *,
    suppressed_names: set[str] | None = None,
    scope: TenantScope | None = None,
) -> list[Any]:
    if journal is None or registry is None:
        return []
    try:
        from runtime.safety.recovery.skill_forge import SkillForge

        candidates = SkillForge(
            journal=journal,
            registry=registry,
            scope=scope,
        ).propose()
    except (ImportError, AttributeError, TypeError, OSError):
        return []

    suppressed = set(suppressed_names or set())
    decisions = _skill_proposal_decision_map(journal, scope=scope)
    return [
        candidate
        for candidate in candidates
        if candidate.name not in suppressed
        and decisions.get(candidate.name)
        not in {
            "promoted",
            "governed",
            "rejected",
            "shadow_failed",
            "promote_failed",
        }
        and not _registry_has_skill(registry, candidate.name)
    ]


def _skill_candidate_to_proposal(candidate: Any) -> dict[str, Any]:
    sequence = list(getattr(candidate, "underlying_sequence", []) or [])
    created_at = _utcnow().isoformat()
    return {
        "name": getattr(candidate, "name", ""),
        "created_at": created_at,
        "topic": "SkillForge",
        "source_url": None,
        "status": "pending",
        "candidate_id": getattr(candidate, "candidate_id", ""),
        "description": getattr(candidate, "description", ""),
        "underlying_sequence": sequence,
        "source_sample_count": int(getattr(candidate, "source_sample_count", 0) or 0),
        "source_success_rate": float(getattr(candidate, "source_success_rate", 0.0) or 0.0),
    }


def _skill_proposal_decision_map(
    journal: Any,
    *,
    scope: TenantScope | None = None,
) -> dict[str, str]:
    decisions: dict[str, tuple[datetime, str]] = {}
    if journal is None:
        return {}
    try:
        events = read_learning_events(
            journal,
            "skill_proposal_decision",
            scope=scope,
        )
    except (AttributeError, TypeError, OSError):
        fallback = [
            event
            for event in _journal_events(journal)
            if getattr(event, "event_type", "") == "skill_proposal_decision"
        ]
        # A storage adapter without scoped reads cannot safely serve an
        # authenticated tenant.  Legacy local mode may still consume only
        # ownership-free rows.
        events = (
            []
            if scope is not None
            else [
                event
                for event in fallback
                if not str(getattr(event, "tenant_id", None) or "").strip()
                and not str(getattr(event, "owner_actor_id", None) or "").strip()
            ]
        )
    for event in events:
        name = str(getattr(event, "proposal_name", "") or "").strip()
        decision = str(getattr(event, "decision", "") or "").strip()
        if not name or not decision:
            continue
        ts = _as_dt(getattr(event, "ts", None)) or _utcnow()
        existing = decisions.get(name)
        if existing is None or ts >= existing[0]:
            decisions[name] = (ts, decision)
    return {name: decision for name, (_ts, decision) in decisions.items()}


def _write_skill_proposal_decision(
    journal: Any,
    *,
    proposal_name: str,
    decision: str,
    candidate_id: str = "",
    reason: str = "",
    details: dict[str, Any] | None = None,
    scope: TenantScope | None = None,
) -> bool:
    if journal is None:
        return False
    try:
        from runtime.memory.journal import journal_context

        context = (
            journal_context(
                tenant_id=scope.tenant_id,
                owner_actor_id=scope.actor_id,
            )
            if scope is not None
            else journal_context()
        )
        with context:
            if hasattr(journal, "write_skill_proposal_decision"):
                journal.write_skill_proposal_decision(
                    proposal_name=proposal_name,
                    candidate_id=candidate_id,
                    decision=decision,
                    reason=reason,
                    details=details or {},
                )
            else:
                from runtime.memory.journal import SkillProposalDecisionEvent

                journal.write(
                    SkillProposalDecisionEvent(
                        proposal_name=proposal_name,
                        candidate_id=candidate_id,
                        decision=decision,
                        reason=reason,
                        details=details or {},
                    )
                )
        return True
    except (AttributeError, TypeError, OSError):
        return False
