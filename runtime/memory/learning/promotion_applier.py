"""Safe applier for promoted Review Queue items."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.memory.learning.experience_ledger import ExperienceLedger
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.safety.auth.scope import TenantScope, row_visible, tenant_scoped_path
from runtime.safety.evolution.governance_audit import (
    promotion_audit_signals,
    verify_governance_audit_chain,
)
from runtime.safety.evolution.proposal_ledger import ProposalLedger

_SCHEMA = "echo.promotion_applier.v1"
_AUDIT_SCHEMA = "echo.promotion_audit.v1"
_VALID_TARGETS = {
    "experience",
    "project_knowledge",
    "experiment_backlog",
    "policy_review",
    "rule_candidate",
    "browser_desktop_repair_recipe",
    # Active single-demo forge: a human picks one successful run in review and
    # mints a skill now (bypasses SkillForge's min_hits≥3 statistical gate;
    # the immune gate still quarantines dangerous macros).
    "forged_skill",
}


class PromotionApplier:
    """Apply human-promoted review items with dry-run and audit records."""

    def __init__(
        self,
        *,
        review_queue: ReviewQueue,
        experience_ledger: ExperienceLedger,
        proposal_ledger: ProposalLedger,
        audit_path: str | Path,
        journal: Any = None,
        registry: Any = None,
        auto_persist_dir: str | Path | None = None,
        scope: TenantScope | None = None,
    ) -> None:
        self.review_queue = review_queue
        self.experience_ledger = experience_ledger
        self.proposal_ledger = proposal_ledger
        self.scope = scope
        self.audit_path = tenant_scoped_path(audit_path, scope)
        # Optional: required only for the "forged_skill" target (active forge).
        self.journal = journal
        self.registry = registry
        # SkillForge owns the tenant partition for this directory; retain the
        # base here so it is not hashed twice when passed downstream.
        self.auto_persist_dir = Path(auto_persist_dir) if auto_persist_dir is not None else None

    def plan(
        self,
        *,
        item_id: str | None = None,
        target: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        items = self._candidate_items(item_id=item_id, target=target, limit=limit)
        records = self._audit_records()
        applied_keys = {
            _audit_key(record.get("review_queue_item_id"), record.get("target"))
            for record in records
            if record.get("status") == "applied"
        }
        actions = [_planned_action(item, applied_keys=applied_keys) for item in items]
        return {
            "schema": _SCHEMA,
            "dry_run": True,
            "total": len(actions),
            "applicable": sum(1 for action in actions if action["action"] == "apply"),
            "skipped": sum(1 for action in actions if action["action"] == "skip"),
            "actions": actions,
        }

    def apply(
        self,
        *,
        item_id: str | None = None,
        target: str | None = None,
        limit: int = 50,
        decision_context: dict[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        now_text = _iso(now)
        payload = self._read_audit_payload()
        records = list(payload.get("records") or [])
        applied_keys = {
            _audit_key(record.get("review_queue_item_id"), record.get("target"))
            for record in records
            if record.get("status") == "applied"
        }
        results: list[dict[str, Any]] = []

        for item in self._candidate_items(item_id=item_id, target=target, limit=limit):
            planned = _planned_action(item, applied_keys=applied_keys)
            if planned["action"] != "apply":
                results.append(planned)
                continue
            target_name = planned["target"]
            try:
                artifact = self._apply_one(
                    item,
                    target=target_name,
                    decision_context=decision_context,
                )
                audit = _audit_record(
                    item=item,
                    target=target_name,
                    status="applied",
                    artifact=artifact,
                    decision_context=decision_context,
                    now_text=now_text,
                )
                records.append(audit)
                applied_keys.add(_audit_key(item.get("id"), target_name))
                results.append({**planned, "status": "applied", "artifact": artifact})
            except Exception as exc:
                audit = _audit_record(
                    item=item,
                    target=target_name,
                    status="failed",
                    artifact={"error": str(exc)},
                    decision_context=decision_context,
                    now_text=now_text,
                )
                records.append(audit)
                results.append({**planned, "status": "failed", "error": str(exc)})

        payload["records"] = records
        payload["lastUpdated"] = now_text
        self._write_audit_payload(payload)
        return {
            "schema": _SCHEMA,
            "dry_run": False,
            "applied": sum(1 for result in results if result.get("status") == "applied"),
            "failed": sum(1 for result in results if result.get("status") == "failed"),
            "skipped": sum(1 for result in results if result.get("action") == "skip"),
            "results": results,
        }

    def audit(
        self,
        *,
        item_id: str | None = None,
        target: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        rows = self._audit_records()
        if item_id:
            rows = [row for row in rows if str(row.get("review_queue_item_id") or "") == item_id]
        if target:
            rows = [row for row in rows if str(row.get("target") or "") == target]
        total = len(rows)
        return {
            "schema": _AUDIT_SCHEMA,
            "records": rows[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def audit_summary(self) -> dict[str, Any]:
        rows = self._audit_records()
        by_status: dict[str, int] = {}
        by_target: dict[str, int] = {}
        by_event_type: dict[str, int] = {}
        override_count = 0
        gate_blocked_override_count = 0
        gate_failed_count = 0
        topology_policy_block_count = 0
        for row in rows:
            status = str(row.get("status") or "unknown")
            target = str(row.get("target") or "unknown")
            event_type = str(row.get("event_type") or "promotion_apply")
            by_status[status] = by_status.get(status, 0) + 1
            by_target[target] = by_target.get(target, 0) + 1
            by_event_type[event_type] = by_event_type.get(event_type, 0) + 1
            if event_type == "topology_policy_block":
                topology_policy_block_count += 1
            signals = promotion_audit_signals(row)
            if signals["gate_failed"]:
                gate_failed_count += 1
            if signals["override"]:
                override_count += 1
                if signals["gate_blocked_override"]:
                    gate_blocked_override_count += 1
        return {
            "schema": "echo.promotion_audit_summary.v1",
            "total": len(rows),
            "by_status": dict(sorted(by_status.items())),
            "by_target": dict(sorted(by_target.items())),
            "by_event_type": dict(sorted(by_event_type.items())),
            "integrity": verify_governance_audit_chain(audit_path=self.audit_path),
            "override_count": override_count,
            "gate_failed_count": gate_failed_count,
            "gate_blocked_override_count": gate_blocked_override_count,
            "topology_policy_block_count": topology_policy_block_count,
            "latest": rows[-5:],
        }

    def _candidate_items(
        self,
        *,
        item_id: str | None,
        target: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if item_id:
            rows = self.review_queue.items(status="promoted", limit=10000, scope=self.scope)[
                "items"
            ]
            rows = [row for row in rows if str(row.get("id") or "") == item_id]
        else:
            rows = self.review_queue.items(status="promoted", limit=limit, scope=self.scope)[
                "items"
            ]
        if target:
            wanted = _target_name({"promoted_to": target, "target_bucket": target})
            rows = [row for row in rows if _target_name(row) == wanted]
        return rows[:limit]

    def _apply_one(
        self,
        item: dict[str, Any],
        *,
        target: str,
        decision_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if target in {"experience", "project_knowledge", "experiment_backlog"}:
            review = _review_from_queue_item(item, target=target)
            result = self.experience_ledger.add_from_task_run_review(review, scope=self.scope)
            return {
                "type": "experience_ledger",
                "target_bucket": target,
                "created": result.get("created", 0),
                "updated": result.get("updated", 0),
                "record_ids": [
                    str(record.get("id") or "")
                    for record in result.get("records") or []
                    if isinstance(record, dict)
                ],
            }
        if target == "policy_review":
            if not _has_policy_review_evidence(item, decision_context=decision_context):
                raise ValueError("policy_review promotion requires replay evidence")
            proposal = self.proposal_ledger.propose(
                kind="review_queue_policy_review",
                description=_description(item),
                proposer="review_queue_applier",
                metadata={
                    "source": "review_queue",
                    "review_queue_item_id": item.get("id"),
                    "priority": item.get("priority"),
                    "candidate_kind": item.get("candidate_kind"),
                    "source_task_ids": item.get("source_task_ids") or [],
                    "item": item,
                    "evidence": _policy_review_evidence(
                        item,
                        decision_context=decision_context,
                    ),
                },
                scope=self.scope,
            )
            return {
                "type": "proposal_ledger",
                "proposal_id": proposal.proposal_id,
                "kind": proposal.kind,
                "status": proposal.status.value,
            }
        if target == "rule_candidate":
            proposal = self.proposal_ledger.propose(
                kind="review_queue_rule_candidate",
                description=_description(item),
                proposer="review_queue_applier",
                metadata={
                    "source": "review_queue",
                    "review_queue_item_id": item.get("id"),
                    "priority": item.get("priority"),
                    "candidate_kind": item.get("candidate_kind"),
                    "source_task_ids": item.get("source_task_ids") or [],
                    "item": item,
                },
                scope=self.scope,
            )
            return {
                "type": "proposal_ledger",
                "proposal_id": proposal.proposal_id,
                "kind": proposal.kind,
                "status": proposal.status.value,
            }
        if target == "browser_desktop_repair_recipe":
            proposal = self.proposal_ledger.propose(
                kind="review_queue_browser_desktop_repair_recipe",
                description=_description(item),
                proposer="review_queue_applier",
                metadata={
                    "source": "review_queue",
                    "review_queue_item_id": item.get("id"),
                    "priority": item.get("priority"),
                    "candidate_kind": item.get("candidate_kind"),
                    "source_task_ids": item.get("source_task_ids") or [],
                    "item": item,
                    "evidence": _browser_desktop_recipe_evidence(item),
                },
                scope=self.scope,
            )
            return {
                "type": "proposal_ledger",
                "proposal_id": proposal.proposal_id,
                "kind": proposal.kind,
                "status": proposal.status.value,
            }
        if target == "forged_skill":
            return self._forge_from_review_item(item)
        raise ValueError(f"unsupported promotion target: {target}")

    def _forge_from_review_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Active forge: distil the review item's successful trajectory into a
        reusable skill *now* (single demo, no ``min_hits`` wait). Reuses
        :class:`SkillForge` end-to-end, so shadow validation + the immune gate
        (dangerous-macro quarantine for human approval) still apply."""
        missing = [
            name
            for name, value in (
                ("journal", self.journal),
                ("registry", self.registry),
                ("auto_persist_dir", self.auto_persist_dir),
            )
            if value is None
        ]
        if missing:
            raise ValueError(
                "forged_skill promotion requires durable PromotionApplier wiring; "
                f"missing: {', '.join(missing)}"
            )
        task_ids = {str(t) for t in (item.get("source_task_ids") or []) if t}
        if not task_ids:
            raise ValueError(
                "forged_skill promotion requires source_task_ids (the trajectory to forge)"
            )
        from runtime.memory.journal.journal import TrajectoryEvent
        from runtime.safety.recovery.skill_forge import SkillForge
        from runtime.safety.recovery.tenant_scope import read_learning_events

        trajs = [
            e.trajectory
            for e in read_learning_events(
                self.journal,
                "trajectory",
                scope=self.scope,
            )
            if isinstance(e, TrajectoryEvent)
            and str(e.trajectory.task_id) in task_ids
            and e.trajectory.outcome.success
            and not e.trajectory.outcome.degraded
        ]
        if not trajs:
            raise ValueError(
                f"no successful trajectory found for source_task_ids={sorted(task_ids)}"
            )
        result = SkillForge(
            journal=self.journal,
            registry=self.registry,
            auto_persist_dir=self.auto_persist_dir,
            scope=self.scope,
        ).forge_selected(trajs)
        return {
            "type": "skill_forge",
            "promoted": list(result.promoted),
            "governed": list(result.governed),
            "evolution_candidates": list(result.evolution_candidates),
            "quarantined": list(result.quarantined),
            "shadow_failed": list(result.shadow_failed),
            "retired": list(result.retired),
            "candidates_total": result.candidates_total,
            "source_task_ids": sorted(task_ids),
        }

    def _audit_records(self) -> list[dict[str, Any]]:
        return [
            row
            for row in self._read_audit_payload().get("records") or []
            if row_visible(row, self.scope)
        ]

    def _read_audit_payload(self) -> dict[str, Any]:
        raw = read_json_with_backup(self.audit_path, default=None)
        if not isinstance(raw, dict):
            return _empty_audit_payload()
        return _normalize_audit_payload(raw)

    def _write_audit_payload(self, payload: dict[str, Any]) -> None:
        atomic_write_json(self.audit_path, _normalize_audit_payload(payload))


def _empty_audit_payload() -> dict[str, Any]:
    return {
        "schema": _AUDIT_SCHEMA,
        "version": 1,
        "lastUpdated": "",
        "records": [],
    }


def _normalize_audit_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = _empty_audit_payload()
    records: list[dict[str, Any]] = []
    for record in raw.get("records") or []:
        if not isinstance(record, dict):
            continue
        item_id = _clean_text(record.get("review_queue_item_id"), limit=120)
        target = _target_text(record.get("target"))
        if not item_id or not target:
            continue
        records.append(
            {
                "id": _clean_text(record.get("id"), limit=100)
                or _promotion_id(
                    item_id=item_id,
                    target=target,
                ),
                "event_type": _clean_text(record.get("event_type"), limit=80),
                "review_queue_item_id": item_id,
                "tenant_id": _clean_text(record.get("tenant_id"), limit=160),
                "owner_actor_id": _clean_text(record.get("owner_actor_id"), limit=160),
                "agent_id": _clean_text(record.get("agent_id"), limit=120),
                "target": target,
                "status": _clean_text(record.get("status"), limit=40) or "applied",
                "applied_at": _clean_text(record.get("applied_at"), limit=80),
                "artifact": record.get("artifact")
                if isinstance(record.get("artifact"), dict)
                else {},
                "decision_context": (
                    record.get("decision_context")
                    if isinstance(record.get("decision_context"), dict)
                    else {}
                ),
                "item_snapshot": (
                    record.get("item_snapshot")
                    if isinstance(record.get("item_snapshot"), dict)
                    else {}
                ),
            }
        )
    payload.update(
        {
            "schema": _AUDIT_SCHEMA,
            "version": 1,
            "lastUpdated": _clean_text(raw.get("lastUpdated"), limit=80),
            "records": sorted(records, key=lambda row: str(row.get("applied_at") or "")),
        }
    )
    return payload


def _planned_action(
    item: dict[str, Any],
    *,
    applied_keys: set[str],
) -> dict[str, Any]:
    target = _target_name(item)
    item_id = str(item.get("id") or "")
    if target not in _VALID_TARGETS:
        return {
            "action": "skip",
            "reason": f"unsupported target: {target}",
            "item_id": item_id,
            "target": target,
            "item": item,
        }
    if _audit_key(item_id, target) in applied_keys:
        return {
            "action": "skip",
            "reason": "already applied",
            "item_id": item_id,
            "target": target,
            "item": item,
        }
    return {
        "action": "apply",
        "reason": "promoted review item is ready to apply",
        "item_id": item_id,
        "target": target,
        "item": item,
    }


def _review_from_queue_item(item: dict[str, Any], *, target: str) -> dict[str, Any]:
    source_task_ids = (
        item.get("source_task_ids") if isinstance(item.get("source_task_ids"), list) else []
    )
    thread_ids = item.get("thread_ids") if isinstance(item.get("thread_ids"), list) else []
    turn_ids = item.get("turn_ids") if isinstance(item.get("turn_ids"), list) else []
    agent_ids = item.get("agent_ids") if isinstance(item.get("agent_ids"), list) else []
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    review = {
        "schema": "echo.task_run_review.v1",
        "task_id": str(source_task_ids[0]) if source_task_ids else "",
        "thread_id": str(thread_ids[0]) if thread_ids else "",
        "turn_id": str(turn_ids[0]) if turn_ids else "",
        "agent_id": str(agent_ids[0]) if agent_ids else "",
        "status": "promoted",
        "learning_candidates": [],
        "backlog_candidates": [],
    }
    if isinstance(metadata.get("replay"), dict):
        review["replay"] = metadata["replay"]
    if isinstance(metadata.get("resume"), dict):
        review["resume"] = metadata["resume"]
    title = _clean_text(item.get("title"), limit=180)
    text = _clean_text(item.get("text"), limit=1200)
    priority = _clean_text(item.get("priority"), limit=20) or "P2"
    if target == "experiment_backlog":
        review["backlog_candidates"] = [
            {
                "priority": priority,
                "experiment": title,
                "hypothesis": text,
                "minimal_implementation": _metadata_text(item, "minimal_implementation"),
                "validation_metric": _metadata_text(item, "validation_metric"),
            }
        ]
    else:
        review["learning_candidates"] = [
            {
                "kind": _clean_text(item.get("candidate_kind"), limit=80) or "learning_candidate",
                "priority": priority,
                "memory_bucket": target,
                "title": title,
                "text": text,
            }
        ]
    return review


def _audit_record(
    *,
    item: dict[str, Any],
    target: str,
    status: str,
    artifact: dict[str, Any],
    decision_context: dict[str, Any] | None,
    now_text: str,
) -> dict[str, Any]:
    item_id = str(item.get("id") or "")
    agent_ids = item.get("agent_ids") if isinstance(item.get("agent_ids"), list) else []
    return {
        "id": _promotion_id(item_id=item_id, target=target),
        "review_queue_item_id": item_id,
        "tenant_id": str(item.get("tenant_id") or ""),
        "owner_actor_id": str(item.get("owner_actor_id") or ""),
        "agent_id": str(agent_ids[0]) if agent_ids else "",
        "target": target,
        "status": status,
        "applied_at": now_text,
        "artifact": artifact,
        "decision_context": decision_context or {},
        "item_snapshot": item,
    }


def _promotion_id(*, item_id: str, target: str) -> str:
    digest = hashlib.blake2b(
        f"{item_id}|{target}".encode(),
        digest_size=10,
    ).hexdigest()
    return f"pa_{digest}"


def _audit_key(item_id: Any, target: Any) -> str:
    return f"{str(item_id or '')}|{_target_text(target)}"


def _target_name(item: dict[str, Any]) -> str:
    promoted_to = _target_text(item.get("promoted_to"))
    if promoted_to and promoted_to != "none":
        return promoted_to
    return _target_text(item.get("target_bucket")) or "experience"


def _target_text(value: Any) -> str:
    return _clean_text(value, limit=80)


def _description(item: dict[str, Any]) -> str:
    title = _clean_text(item.get("title"), limit=180)
    text = _clean_text(item.get("text"), limit=1200)
    return f"{title}\n\n{text}".strip()


def _has_policy_review_evidence(
    item: dict[str, Any],
    *,
    decision_context: dict[str, Any] | None,
) -> bool:
    evidence = _policy_review_evidence(item, decision_context=decision_context)
    replay = evidence.get("replay") if isinstance(evidence.get("replay"), dict) else {}
    gate = evidence.get("replay_gate") if isinstance(evidence.get("replay_gate"), dict) else {}
    return bool(
        (replay.get("case_id") and replay.get("replayable") is True) or gate.get("passed") is True
    )


def _policy_review_evidence(
    item: dict[str, Any],
    *,
    decision_context: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
    context = decision_context if isinstance(decision_context, dict) else {}
    replay_gate = context.get("replay_gate") if isinstance(context.get("replay_gate"), dict) else {}
    if not replay_gate and isinstance(metadata.get("replay_gate"), dict):
        replay_gate = metadata["replay_gate"]
    return {
        "schema": "echo.policy_review_promotion_evidence.v1",
        "replay": {
            "schema": replay.get("schema"),
            "case_id": replay.get("case_id"),
            "fingerprint": replay.get("fingerprint"),
            "replayable": bool(replay.get("replayable")),
            "step_count": int(replay.get("step_count") or 0),
        },
        "replay_gate": replay_gate,
    }


def _browser_desktop_recipe_evidence(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    recipe = metadata.get("recipe") if isinstance(metadata.get("recipe"), dict) else {}
    gate = recipe.get("promotion_gate") if isinstance(recipe.get("promotion_gate"), dict) else {}
    verification_plan = (
        recipe.get("verification_plan") if isinstance(recipe.get("verification_plan"), dict) else {}
    )
    return {
        "schema": "echo.browser_desktop_repair_recipe_promotion_evidence.v1",
        "recipe_id": recipe.get("recipe_id"),
        "candidate_kind": recipe.get("candidate_kind"),
        "occurrences": int(recipe.get("occurrences") or 0),
        "case_ids": [
            str(case_id) for case_id in recipe.get("case_ids") or [] if str(case_id or "")
        ],
        "fingerprints": [
            str(fingerprint)
            for fingerprint in recipe.get("fingerprints") or []
            if str(fingerprint or "")
        ],
        "recommended_steps": [
            str(step) for step in recipe.get("recommended_steps") or [] if str(step or "")
        ],
        "verification_plan": verification_plan,
        "promotion_gate": gate,
    }


def _metadata_text(item: dict[str, Any], key: str) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return _clean_text(metadata.get(key), limit=1200)


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _clean_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


__all__ = ["PromotionApplier"]
