"""Fitness scoring for delegated subagent roles.

This is intentionally downstream of the review queue: a subagent role earns
trust when its trace-linked outputs survive operator review, and loses trust
when candidates are rejected or archived. The score is not a model-quality
claim; it is an operational signal for routing, retirement, and promotion.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths, project_root

_SCHEMA = "echo.subagent_fitness.v1"
_STATUS_VALUE = {
    "promoted": 1.0,
    "pending": 0.68,
    "archived": 0.42,
    "rejected": 0.1,
}


def compute_subagent_fitness(
    *,
    review_queue_path: str | Path | None = None,
    deep_research_job_store_path: str | Path | None = None,
    include_deep_research_routes: bool = True,
    role: str | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    path = (
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path
    )
    queue = ReviewQueue(path)
    rows = queue.items(limit=max(1, min(int(limit), 5000)))["items"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    wanted = _clean(role, limit=80)

    for row in rows:
        if str(row.get("candidate_kind") or "") != "subagent_output":
            continue
        role_id = _role_from_item(row)
        if not role_id:
            continue
        if wanted and role_id != wanted:
            continue
        grouped[role_id].append(row)

    if include_deep_research_routes:
        route_path = _deep_research_route_path(
            review_queue_path=review_queue_path,
            deep_research_job_store_path=deep_research_job_store_path,
        )
        if route_path is not None:
            for row in _deep_research_route_rows(route_path, limit=max(1, min(int(limit), 5000))):
                role_id = _role_from_route_item(row)
                if not role_id:
                    continue
                if wanted and role_id != wanted:
                    continue
                grouped[role_id].append(row)

    reports = [_role_report(role_id, items) for role_id, items in sorted(grouped.items())]
    return {
        "schema": _SCHEMA,
        "role": wanted or None,
        "roles": reports,
        "role_count": len(reports),
        "top_risks": [
            item
            for item in sorted(
                reports,
                key=lambda row: (row["score"], -row["sample_count"], row["role"]),
            )
            if item["verdict"] in {"retire_candidate", "watch"}
        ][:5],
        "next_actions": _next_actions(reports),
    }


def _role_report(role: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(row.get("status") or "pending") for row in rows)
    evidence_sources = Counter(_evidence_source(row) for row in rows)
    values = [_item_value(row) for row in rows]
    score = round(sum(values) / len(values), 3) if values else 1.0
    sample_count = len(rows)
    confidence = round(min(1.0, sample_count / 5.0), 3)
    rejected = statuses.get("rejected", 0)
    promoted = statuses.get("promoted", 0)
    verdict = _verdict(score=score, sample_count=sample_count, rejected=rejected, promoted=promoted)
    return {
        "role": role,
        "score": score,
        "confidence": confidence,
        "sample_count": sample_count,
        "by_status": dict(sorted(statuses.items())),
        "promoted_count": promoted,
        "rejected_count": rejected,
        "pending_count": statuses.get("pending", 0),
        "routing_evidence_count": evidence_sources.get("deep_research_route_decision", 0),
        "by_evidence_source": dict(sorted(evidence_sources.items())),
        "verdict": verdict,
        "recommendation": _recommendation(verdict, role),
        "evidence_item_ids": [
            str(row.get("id") or "")
            for row in sorted(rows, key=_evidence_sort_key)[:8]
            if row.get("id")
        ],
    }


def _item_value(row: dict[str, Any]) -> float:
    if _evidence_source(row) == "deep_research_route_decision":
        action = str(row.get("status") or "")
        if action == "rejected":
            return 0.12
        if action == "archived":
            return 0.42
        return 0.58
    value = _STATUS_VALUE.get(str(row.get("status") or "pending"), 0.5)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    candidate = metadata.get("candidate") if isinstance(metadata.get("candidate"), dict) else {}
    subagent = candidate.get("subagent") if isinstance(candidate.get("subagent"), dict) else {}
    if subagent.get("files_touched"):
        value += 0.05
    return round(min(1.0, value), 3)


def _role_from_item(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    candidate = metadata.get("candidate") if isinstance(metadata.get("candidate"), dict) else {}
    subagent = candidate.get("subagent") if isinstance(candidate.get("subagent"), dict) else {}
    role = _clean(subagent.get("role"), limit=80)
    if role:
        return role
    agent_ids = row.get("agent_ids") if isinstance(row.get("agent_ids"), list) else []
    return _clean(agent_ids[0] if agent_ids else "", limit=80)


def _role_from_route_item(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    route = (
        metadata.get("route_decision") if isinstance(metadata.get("route_decision"), dict) else {}
    )
    return _clean(route.get("role") or row.get("agent_id"), limit=80)


def _verdict(
    *,
    score: float,
    sample_count: int,
    rejected: int,
    promoted: int,
) -> str:
    if sample_count >= 3 and (score < 0.4 or rejected / sample_count >= 0.5):
        return "retire_candidate"
    if sample_count >= 3 and score >= 0.8 and promoted >= 1:
        return "strong"
    if score >= 0.7:
        return "developing"
    return "watch"


def _recommendation(verdict: str, role: str) -> str:
    if verdict == "retire_candidate":
        return f"Review or retire the {role} subagent before assigning more critical work."
    if verdict == "strong":
        return f"Prefer the {role} subagent for similar delegated work."
    if verdict == "developing":
        return f"Keep collecting reviewed outcomes for the {role} subagent."
    return f"Route low-risk work to the {role} subagent until quality improves."


def _next_actions(reports: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for report in reports:
        verdict = str(report.get("verdict") or "")
        if verdict not in {"retire_candidate", "watch"}:
            continue
        actions.append(
            {
                "role": str(report.get("role") or ""),
                "verdict": verdict,
                "action": str(report.get("recommendation") or ""),
            }
        )
    return actions[:5]


def _evidence_sort_key(row: dict[str, Any]) -> tuple[float, str]:
    return (_item_value(row), str(row.get("updated_at") or row.get("created_at") or ""))


def _evidence_source(row: dict[str, Any]) -> str:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    source = _clean(metadata.get("fitness_source"), limit=80)
    return source or "review_queue"


def _deep_research_route_rows(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if len(rows) >= limit:
            break
        try:
            job = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(job, dict):
            continue
        job_id = _clean(job.get("job_id"), limit=120)
        thread_id = _clean(job.get("thread_id"), limit=120)
        for decision in job.get("route_decisions") or []:
            if len(rows) >= limit:
                break
            if not isinstance(decision, dict):
                continue
            row = _route_decision_row(
                decision,
                job_id=job_id,
                thread_id=thread_id,
            )
            if row:
                rows.append(row)
    return rows


def _route_decision_row(
    decision: dict[str, Any],
    *,
    job_id: str,
    thread_id: str,
) -> dict[str, Any] | None:
    role = _clean(decision.get("role"), limit=80)
    action = _clean(decision.get("action"), limit=40)
    if not role or action not in {"block", "allow_with_warning"}:
        return None
    step_id = _clean(decision.get("step_id") or decision.get("task_id"), limit=120)
    created_at = _clean(decision.get("created_at"), limit=80)
    status = "rejected" if action == "block" else "archived"
    item_id = "route-" + "-".join(part for part in (job_id, step_id, role, action) if part)
    return {
        "id": item_id[:80],
        "status": status,
        "created_at": created_at,
        "updated_at": created_at,
        "candidate_kind": "subagent_route_decision",
        "source_kind": "subagent_route_decision",
        "priority": "P1" if action == "block" else "P2",
        "title": f"Deep research route {action}: {role}",
        "text": _clean(decision.get("reason"), limit=1200),
        "agent_id": role,
        "agent_ids": [role],
        "source_task_ids": [step_id] if step_id else [],
        "thread_ids": [thread_id] if thread_id else [],
        "turn_ids": [],
        "metadata": {
            "fitness_source": "deep_research_route_decision",
            "route_decision": {
                "role": role,
                "action": action,
                "risk_level": _clean(decision.get("risk_level"), limit=40),
                "verdict": _clean(decision.get("verdict"), limit=80),
                "score": decision.get("score"),
                "confidence": decision.get("confidence"),
                "job_id": job_id,
                "step_id": step_id,
            },
        },
    }


def _default_deep_research_job_store_path() -> Path:
    root = project_root(Path(__file__))
    current = root / ".echo" / "research" / "deep-research-jobs.jsonl"
    legacy = root / ".echo-research" / "deep-research-jobs.jsonl"
    if not current.exists() and legacy.exists():
        return legacy
    return current


def _deep_research_route_path(
    *,
    review_queue_path: str | Path | None,
    deep_research_job_store_path: str | Path | None,
) -> Path | None:
    if deep_research_job_store_path is not None:
        return Path(deep_research_job_store_path)
    if review_queue_path is not None:
        return None
    return _default_deep_research_job_store_path()


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


__all__ = ["compute_subagent_fitness"]
