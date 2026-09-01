"""State factories and promotion helpers for the agent-trace API."""

from __future__ import annotations

import contextlib
import threading
from pathlib import Path
from typing import Any

from runtime.memory.diagnostics.trace_store import AgentTraceStore
from runtime.memory.learning.experience_ledger import ExperienceLedger
from runtime.memory.learning.promotion_applier import PromotionApplier
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths
from runtime.safety.auth.scope import TenantScope, tenant_scoped_path
from runtime.safety.evolution.proposal_ledger import ProposalLedger

_STORE_LOCK = threading.Lock()
_STORE_INSTANCE: AgentTraceStore | None = None
_STORE_DB_PATH: Path | None = None
_EXPERIENCE_LOCK = threading.Lock()
_EXPERIENCE_INSTANCE: ExperienceLedger | None = None
_EXPERIENCE_PATH: Path | None = None
_REVIEW_QUEUE_LOCK = threading.Lock()
_REVIEW_QUEUE_INSTANCE: ReviewQueue | None = None
_REVIEW_QUEUE_PATH: Path | None = None


def _get_store(
    *, store: AgentTraceStore | None = None, db_path: Path | None = None
) -> AgentTraceStore:
    if store is not None:
        return store
    target = Path(db_path) if db_path is not None else app_paths().agent_trace_path
    global _STORE_INSTANCE, _STORE_DB_PATH
    with _STORE_LOCK:
        if _STORE_INSTANCE is None or target != _STORE_DB_PATH:
            if _STORE_INSTANCE is not None:
                with contextlib.suppress(Exception):
                    _STORE_INSTANCE.close()
            _STORE_INSTANCE = AgentTraceStore(target)
            _STORE_DB_PATH = target
        return _STORE_INSTANCE


def _get_experience_ledger(
    *,
    experience_ledger: ExperienceLedger | None = None,
    experience_ledger_path: Path | None = None,
    scope: TenantScope | None = None,
) -> ExperienceLedger:
    if experience_ledger is not None:
        return experience_ledger
    target = tenant_scoped_path(
        Path(experience_ledger_path or app_paths().experience_ledger_path), scope
    )
    global _EXPERIENCE_INSTANCE, _EXPERIENCE_PATH
    with _EXPERIENCE_LOCK:
        if _EXPERIENCE_INSTANCE is None or target != _EXPERIENCE_PATH:
            _EXPERIENCE_INSTANCE = ExperienceLedger(target)
            _EXPERIENCE_PATH = target
        return _EXPERIENCE_INSTANCE


def _get_review_queue(
    *,
    review_queue: ReviewQueue | None = None,
    review_queue_path: Path | None = None,
    scope: TenantScope | None = None,
) -> ReviewQueue:
    if review_queue is not None:
        return review_queue
    target = tenant_scoped_path(Path(review_queue_path or app_paths().review_queue_path), scope)
    global _REVIEW_QUEUE_INSTANCE, _REVIEW_QUEUE_PATH
    with _REVIEW_QUEUE_LOCK:
        if _REVIEW_QUEUE_INSTANCE is None or target != _REVIEW_QUEUE_PATH:
            _REVIEW_QUEUE_INSTANCE = ReviewQueue(target)
            _REVIEW_QUEUE_PATH = target
        return _REVIEW_QUEUE_INSTANCE


def _get_promotion_applier(
    *,
    experience_ledger: ExperienceLedger | None = None,
    experience_ledger_path: Path | None = None,
    review_queue: ReviewQueue | None = None,
    review_queue_path: Path | None = None,
    promotion_audit_path: Path | None = None,
    proposal_ledger_path: Path | None = None,
    scope: TenantScope | None = None,
) -> PromotionApplier:
    return PromotionApplier(
        review_queue=_get_review_queue(
            review_queue=review_queue,
            review_queue_path=review_queue_path,
            scope=scope,
        ),
        experience_ledger=_get_experience_ledger(
            experience_ledger=experience_ledger,
            experience_ledger_path=experience_ledger_path,
            scope=scope,
        ),
        proposal_ledger=ProposalLedger(
            tenant_scoped_path(proposal_ledger_path or app_paths().proposal_ledger_path, scope)
        ),
        audit_path=promotion_audit_path or app_paths().promotion_audit_path,
        scope=scope,
    )


def _source_task_ids_from_promotion_plan(plan: dict[str, Any]) -> list[str]:
    out: list[str] = []
    raw_actions = plan.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    for action in actions:
        if not isinstance(action, dict):
            continue
        raw_item = action.get("item")
        item = raw_item if isinstance(raw_item, dict) else {}
        source_task_ids = item.get("source_task_ids")
        if not isinstance(source_task_ids, list):
            continue
        for task_id in source_task_ids:
            text = str(task_id or "").strip()
            if text and text not in out:
                out.append(text)
    return out


def _promotion_plan_has_target(plan: dict[str, Any], target: str) -> bool:
    raw_actions = plan.get("actions")
    actions = raw_actions if isinstance(raw_actions, list) else []
    return any(
        isinstance(action, dict)
        and action.get("action") == "apply"
        and str(action.get("target") or "") == target
        for action in actions
    )


def _queue_repeated_trust_denials(
    summary: dict[str, Any], *, review_queue: ReviewQueue, min_occurrences: int
) -> dict[str, Any]:
    raw_by_tool = summary.get("by_tool")
    by_tool = raw_by_tool if isinstance(raw_by_tool, dict) else {}
    raw_recent = summary.get("recent")
    recent = raw_recent if isinstance(raw_recent, list) else []
    touched: list[dict[str, Any]] = []
    created = updated = 0
    for tool_name, count in sorted(by_tool.items(), key=lambda item: str(item[0])):
        occurrences = int(count or 0)
        if occurrences < min_occurrences:
            continue
        examples = [
            item
            for item in recent
            if isinstance(item, dict) and str(item.get("tool_name") or "") == str(tool_name)
        ]
        latest = examples[-1] if examples else {}
        result = review_queue.upsert_item(
            source="trust_denials",
            source_kind="tool_policy_denial",
            candidate_kind="policy_review",
            priority="P1" if occurrences < 5 else "P0",
            target_bucket="policy_review",
            title=f"Review repeated denials for {tool_name}",
            text=(
                f"Tool {tool_name} was denied {occurrences} time(s). "
                f"Latest reason: {latest.get('reason') or 'not recorded'}."
            ),
            metadata={
                "summary_schema": summary.get("schema"),
                "tool_name": tool_name,
                "occurrences": occurrences,
                "latest_denial": latest,
            },
            source_task_ids=[str(item["task_id"]) for item in examples if item.get("task_id")],
            thread_ids=[str(item["thread_id"]) for item in examples if item.get("thread_id")],
            turn_ids=[str(item["turn_id"]) for item in examples if item.get("turn_id")],
            agent_ids=[str(item["agent_id"]) for item in examples if item.get("agent_id")],
            tags=["trust_denial", "policy_review", str(tool_name)],
        )
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        touched.extend(result.get("items") or [])
    return {
        "schema": "echo.trust_denial_review_queue.v1",
        "min_occurrences": min_occurrences,
        "created": created,
        "updated": updated,
        "items": touched,
    }


__all__ = [
    "_get_experience_ledger",
    "_get_promotion_applier",
    "_get_review_queue",
    "_get_store",
    "_promotion_plan_has_target",
    "_queue_repeated_trust_denials",
    "_source_task_ids_from_promotion_plan",
]
