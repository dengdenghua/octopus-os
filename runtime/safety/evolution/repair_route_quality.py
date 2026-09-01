from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalRecord

SCHEMA = "echo.repair_route_quality.v1"
PROMOTION_CANDIDATE_SCHEMA = "echo.repair_route_promotion_candidate.v1"
PROMOTION_QUEUE_SCHEMA = "echo.repair_route_promotion_queue.v1"


def compute_repair_route_quality(
    *,
    ledger_path: str | Path | None = None,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    ledger = ProposalLedger(ledger_path) if ledger_path is not None else ProposalLedger()
    records = ledger.query(kind="turn_failure", limit=max(1, int(limit)))
    failures = [_failure_row(record) for record in records]
    failures = [row for row in failures if row]
    governance = (
        _repair_route_governance(review_queue_path=review_queue_path)
        if ledger_path is None or review_queue_path is not None
        else {}
    )

    route_counts = Counter(str(row["route"]) for row in failures)
    source_counts = Counter(str(row["failure_source"]) for row in failures)
    total = len(failures)
    failed_verification_total = sum(int(row["failed_verification_count"]) for row in failures)
    code_change_failures = sum(1 for row in failures if row["has_code_changes"])
    unverified_code_changes = sum(
        1 for row in failures if row["has_code_changes"] and int(row["verification_count"]) <= 0
    )

    routes = [
        _route_summary(route, failures, total, governance=governance)
        for route, _count in route_counts.most_common()
    ]
    promotion_candidates = _promotion_candidates(routes)
    quality_gate = _quality_gate(
        total_failures=total,
        routes=routes,
        failed_verification_total=failed_verification_total,
        unverified_code_changes=unverified_code_changes,
        promotion_candidates=promotion_candidates,
        governance=governance,
    )
    return {
        "schema": SCHEMA,
        "score": quality_gate["score"],
        "ready": quality_gate["ready"],
        "quality_gate": quality_gate,
        "total_failures": total,
        "route_count": len(routes),
        "routes": routes,
        "promotion_candidates": promotion_candidates,
        "summary": {
            "failed_verification_total": failed_verification_total,
            "avg_failed_verifications": round(
                failed_verification_total / total,
                3,
            )
            if total
            else 0.0,
            "code_change_failures": code_change_failures,
            "unverified_code_changes": unverified_code_changes,
            "top_failure_sources": [
                {"source": source, "count": count} for source, count in source_counts.most_common(5)
            ],
            "governed_route_count": sum(
                1 for route in routes if route.get("governance", {}).get("covered")
            ),
        },
        "recommendations": _recommendations(routes, unverified_code_changes),
    }


def _quality_gate(
    *,
    total_failures: int,
    routes: list[dict[str, Any]],
    failed_verification_total: int,
    unverified_code_changes: int,
    promotion_candidates: list[dict[str, Any]],
    governance: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    governed_routes = {route for route, state in governance.items() if state.get("covered")}
    unresolved_routes = [
        route for route in routes if str(route.get("route") or "") not in governed_routes
    ]
    unresolved_failures = sum(int(route.get("count") or 0) for route in unresolved_routes)
    unresolved_failed_verification_total = sum(
        int(route.get("failed_verification_count") or 0) for route in unresolved_routes
    )
    unresolved_unverified_code_changes = sum(
        int(route.get("unverified_code_changes") or 0) for route in unresolved_routes
    )
    top_share = float(unresolved_routes[0].get("share") or 0.0) if unresolved_routes else 0.0
    failure_denominator = max(1, total_failures)
    failed_verification_rate = unresolved_failed_verification_total / failure_denominator
    unverified_code_change_rate = unresolved_unverified_code_changes / failure_denominator
    unresolved_candidates = [
        candidate
        for candidate in promotion_candidates
        if str(candidate.get("route") or "") not in governed_routes
    ]
    p0_candidate_count = sum(
        1 for candidate in unresolved_candidates if str(candidate.get("priority") or "") == "P0"
    )
    pending_governance_count = sum(
        1
        for state in governance.values()
        if state.get("covered") and str(state.get("status") or "") == "pending"
    )
    penalty = min(
        0.85,
        (top_share * 0.22)
        + (failed_verification_rate * 0.28)
        + (unverified_code_change_rate * 0.32)
        + (min(len(unresolved_candidates), 3) * 0.06)
        + (min(p0_candidate_count, 3) * 0.04),
    )
    if pending_governance_count:
        penalty = min(0.85, penalty + min(pending_governance_count, 3) * 0.025)
    score = round(max(0.0, 1.0 - penalty), 3)
    blockers: list[str] = []
    if unresolved_unverified_code_changes:
        blockers.append("unverified_code_changes")
    if unresolved_failed_verification_total:
        blockers.append("failed_verifications")
    if p0_candidate_count:
        blockers.append("p0_promotion_candidates")
    if top_share >= 0.6 and unresolved_failures >= 3:
        blockers.append("dominant_failure_route")
    if pending_governance_count:
        blockers.append("pending_repair_route_review")
    return {
        "schema": "echo.repair_route_quality_gate.v1",
        "score": score,
        "ready": score >= 0.85 and not blockers,
        "blockers": blockers,
        "signals": {
            "total_failures": total_failures,
            "top_route_share": round(top_share, 3),
            "failed_verification_rate": round(failed_verification_rate, 3),
            "unverified_code_change_rate": round(unverified_code_change_rate, 3),
            "promotion_candidate_count": len(unresolved_candidates),
            "p0_candidate_count": p0_candidate_count,
            "governed_route_count": len(governed_routes),
            "unresolved_route_count": len(unresolved_routes),
            "pending_governance_count": pending_governance_count,
        },
    }


def queue_repair_route_promotion_candidates(
    *,
    ledger_path: str | Path | None = None,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    report = compute_repair_route_quality(
        ledger_path=ledger_path,
        review_queue_path=review_queue_path,
        limit=limit,
    )
    candidates = [row for row in report.get("promotion_candidates") or [] if isinstance(row, dict)]
    if not candidates:
        return {
            "schema": PROMOTION_QUEUE_SCHEMA,
            "created": 0,
            "updated": 0,
            "candidates": [],
            "items": [],
            "summary": {
                "total_failures": report.get("total_failures", 0),
                "route_count": report.get("route_count", 0),
            },
        }

    from runtime.memory.learning.review_queue import ReviewQueue
    from runtime.platform.process.paths import app_paths

    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    created = 0
    updated = 0
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        route = str(candidate.get("route") or "unknown")
        result = queue.upsert_item(
            source="repair_route_quality",
            source_kind="repair_route_promotion",
            candidate_kind=f"repair_route_promotion:{route}",
            priority=str(candidate.get("priority") or "P1"),
            target_bucket="experiment_backlog",
            title=f"Promote repair route: {route}",
            text=_promotion_candidate_text(candidate),
            metadata={
                "schema": PROMOTION_CANDIDATE_SCHEMA,
                "promotion_candidate": candidate,
                "quality_report": {
                    "schema": SCHEMA,
                    "window_limit": limit,
                    "total_failures": report.get("total_failures", 0),
                    "route_count": report.get("route_count", 0),
                },
            },
            tags=[
                "repair_route",
                "promotion_candidate",
                route,
            ],
        )
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        items.extend(result.get("items") or [])
    return {
        "schema": PROMOTION_QUEUE_SCHEMA,
        "created": created,
        "updated": updated,
        "candidates": candidates,
        "items": items,
        "summary": {
            "total_failures": report.get("total_failures", 0),
            "route_count": report.get("route_count", 0),
        },
    }


def _failure_row(record: ProposalRecord) -> dict[str, Any] | None:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    route = _primary_route(metadata)
    if not route:
        return None
    failed_verifications = metadata.get("failed_verifications")
    if not isinstance(failed_verifications, list):
        failed_verifications = []
    return {
        "proposal_id": record.proposal_id,
        "ts": record.ts,
        "route": route,
        "failure_source": str(metadata.get("failure_source") or "turn_failure"),
        "goal": str(metadata.get("goal") or ""),
        "has_code_changes": bool(metadata.get("has_code_changes")),
        "verification_count": int(metadata.get("verification_count") or 0),
        "failed_verification_count": len(failed_verifications),
        "verification_plan_commands": _verification_plan_commands(metadata),
    }


def _primary_route(metadata: dict[str, Any]) -> str:
    explicit = str(metadata.get("primary_repair_route") or "").strip()
    if explicit:
        return explicit
    routes = metadata.get("repair_routes")
    if isinstance(routes, list):
        for route in routes:
            if isinstance(route, dict):
                route_id = str(route.get("route") or "").strip()
                if route_id:
                    return route_id
    source = str(metadata.get("failure_source") or "").strip()
    return source or "turn_failure"


def _route_summary(
    route: str,
    failures: list[dict[str, Any]],
    total: int,
    *,
    governance: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    rows = [row for row in failures if row["route"] == route]
    failed_verifications = sum(int(row["failed_verification_count"]) for row in rows)
    unverified = sum(
        1 for row in rows if row["has_code_changes"] and int(row["verification_count"]) <= 0
    )
    sources = Counter(str(row["failure_source"]) for row in rows)
    plan_commands = Counter(
        command for row in rows for command in row["verification_plan_commands"]
    )
    governed = governance.get(route) or {}
    return {
        "route": route,
        "count": len(rows),
        "share": round(len(rows) / total, 3) if total else 0.0,
        "failed_verification_count": failed_verifications,
        "unverified_code_changes": unverified,
        "latest_ts": rows[-1]["ts"] if rows else "",
        "recommended_commands": [
            {"command": command, "count": count} for command, count in plan_commands.most_common(5)
        ],
        "failure_sources": [
            {"source": source, "count": count} for source, count in sources.most_common(3)
        ],
        "examples": [
            {
                "proposal_id": row["proposal_id"],
                "goal": row["goal"][:160],
                "failure_source": row["failure_source"],
                "ts": row["ts"],
            }
            for row in rows[-3:]
        ],
        "governance": governed
        or {
            "covered": False,
            "status": "uncovered",
            "item_id": "",
        },
    }


def _recommendations(
    routes: list[dict[str, Any]],
    unverified_code_changes: int,
) -> list[str]:
    out: list[str] = []
    if unverified_code_changes:
        out.append("Require at least one verifier for code-change turns before finalizing.")
    if routes:
        top = routes[0]
        if float(top.get("share") or 0.0) >= 0.5:
            out.append(
                f"Prioritize repair-route `{top['route']}`; it owns {top['share']:.0%} of recent failures."
            )
    if not out:
        out.append("No dominant repair-route weakness detected in the sampled ledger window.")
    return out


def _promotion_candidates(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for route in routes:
        count = int(route.get("count") or 0)
        share = float(route.get("share") or 0.0)
        failed_verifications = int(route.get("failed_verification_count") or 0)
        unverified = int(route.get("unverified_code_changes") or 0)
        if count < 2 and failed_verifications < 2 and unverified < 2:
            continue
        if share < 0.4 and failed_verifications < 2 and unverified < 2:
            continue
        priority = "P0" if failed_verifications >= 2 or unverified >= 2 else "P1"
        recommended_commands = route.get("recommended_commands")
        if not isinstance(recommended_commands, list):
            recommended_commands = []
        examples = route.get("examples")
        if not isinstance(examples, list):
            examples = []
        governance = route.get("governance")
        if not isinstance(governance, dict):
            governance = {}
        status = (
            "operator_review_pending"
            if governance.get("covered") and governance.get("status") == "pending"
            else "operator_review_decided"
            if governance.get("covered")
            else "needs_operator_review"
        )
        candidates.append(
            {
                "schema": PROMOTION_CANDIDATE_SCHEMA,
                "route": str(route.get("route") or "unknown"),
                "priority": priority,
                "status": status,
                "governance": governance,
                "evidence": {
                    "count": count,
                    "share": share,
                    "failed_verification_count": failed_verifications,
                    "unverified_code_changes": unverified,
                    "failure_sources": route.get("failure_sources") or [],
                    "recommended_commands": recommended_commands,
                    "example_proposal_ids": [
                        str(example.get("proposal_id") or "")
                        for example in examples
                        if isinstance(example, dict) and example.get("proposal_id")
                    ],
                },
                "promotion_gate": {
                    "schema": "echo.repair_route_promotion_gate.v1",
                    "requires_operator_review": True,
                    "requires_passing_rerun": True,
                    "blocks_auto_promotion": failed_verifications > 0 or unverified > 0,
                },
            }
        )
    return candidates


def _repair_route_governance(
    *,
    review_queue_path: str | Path | None,
) -> dict[str, dict[str, Any]]:
    try:
        from runtime.memory.learning.review_queue import ReviewQueue
        from runtime.platform.process.paths import app_paths

        queue = ReviewQueue(
            Path(review_queue_path)
            if review_queue_path is not None
            else app_paths().review_queue_path,
        )
        rows = queue.items(limit=5000).get("items") or []
    except Exception:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_kind = str(row.get("candidate_kind") or "")
        prefix = "repair_route_promotion:"
        if not candidate_kind.startswith(prefix):
            continue
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        candidate = metadata.get("promotion_candidate")
        if not isinstance(candidate, dict):
            candidate = {}
        route = str(candidate.get("route") or candidate_kind[len(prefix) :]).strip()
        if not route:
            continue
        status = str(row.get("status") or "pending")
        existing = out.get(route)
        state = {
            "covered": True,
            "status": status,
            "item_id": str(row.get("id") or ""),
            "priority": str(row.get("priority") or ""),
            "target_bucket": str(row.get("target_bucket") or ""),
            "occurrences": int(row.get("occurrences") or 1),
            "decided_at": str(row.get("decided_at") or ""),
            "decision_reason": str(row.get("decision_reason") or ""),
        }
        if existing is None or _governance_rank(status) > _governance_rank(
            str(existing.get("status") or ""),
        ):
            out[route] = state
    return out


def _governance_rank(status: str) -> int:
    if status == "promoted":
        return 4
    if status in {"rejected", "archived"}:
        return 3
    if status == "pending":
        return 2
    return 1


def _promotion_candidate_text(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {}
    lines = [
        f"Repair route `{candidate.get('route') or 'unknown'}` has repeated quality evidence.",
        (
            "Recent evidence: "
            f"{evidence.get('count') or 0} failures, "
            f"{evidence.get('failed_verification_count') or 0} failed verifications, "
            f"{evidence.get('unverified_code_changes') or 0} unverified code-change turns."
        ),
        "Promote only after an operator reviews the evidence and attaches a passing rerun.",
    ]
    commands = evidence.get("recommended_commands")
    if isinstance(commands, list) and commands:
        first = commands[0]
        if isinstance(first, dict) and first.get("command"):
            lines.append(f"Suggested verifier: {first['command']}")
    return "\n".join(lines)


def _verification_plan_commands(metadata: dict[str, Any]) -> list[str]:
    plan = metadata.get("verification_plan")
    if not isinstance(plan, dict):
        return []
    commands = plan.get("commands")
    if not isinstance(commands, list):
        return []
    out: list[str] = []
    for item in commands:
        if not isinstance(item, dict):
            continue
        command = str(item.get("command") or "").strip()
        if command:
            out.append(command)
    return out


__all__ = [
    "PROMOTION_CANDIDATE_SCHEMA",
    "PROMOTION_QUEUE_SCHEMA",
    "SCHEMA",
    "compute_repair_route_quality",
    "queue_repair_route_promotion_candidates",
]
