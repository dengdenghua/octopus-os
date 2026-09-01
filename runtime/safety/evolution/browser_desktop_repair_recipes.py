"""Browser/desktop repair recipe orchestration.

This module is the public entry point for discovering, queueing, verifying and
rerunning deterministic browser/desktop repair recipes. The heavy lifting lives
in sibling submodules that were extracted from this file:

- ``_recipes_common``: schema constants and shared primitives.
- ``_recipes_cluster``: cluster-key derivation and recipe body builders.
- ``_recipes_api``: local API check/mutation helpers.
- ``_recipes_evidence``: fresh evidence producers for recipe reruns.

The public API surface (constants and ``*_browser_desktop_repair_*`` functions)
is unchanged; helpers are imported from the submodules and re-exported here.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.memory.learning.review_queue import ReviewQueue
from runtime.platform.process.paths import app_paths

from ._recipes_api import (
    _provided_evidence_from_api_check,
    _provided_evidence_from_artifact,
    _run_api_check,
)
from ._recipes_cluster import (
    _candidate_kind,
    _cluster_key,
    _next_actions,
    _recipe_from_cluster,
    _recipe_text,
    _stale_computer_activity_replay,
    _stale_source_artifact,
    _verification_next_actions,
)
from ._recipes_common import (
    EVIDENCE_SCHEMA,
    QUEUE_SCHEMA,
    RECIPE_SCHEMA,
    SCHEMA,
    STALE_REJECTION_SCHEMA,
    VERIFICATION_SCHEMA,
    _dict,
    _priority_rank,
    _unique_strings,
)
from ._recipes_evidence import _produce_fresh_recipe_evidence


def compute_browser_desktop_repair_recipes(
    *,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
    min_occurrences: int = 1,
) -> dict[str, Any]:
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    rows = queue.items(
        status="pending",
        target_bucket="browser_desktop_replay",
        limit=max(1, int(limit)),
    )["items"]
    clusters: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = _cluster_key(row)
        clusters.setdefault(key, []).append(row)

    recipes = [
        _recipe_from_cluster(key, items)
        for key, items in clusters.items()
        if len(items) >= max(1, int(min_occurrences))
    ]
    recipes = sorted(
        recipes,
        key=lambda item: (
            _priority_rank(str(item.get("priority") or "P2")),
            -int(item.get("occurrences") or 0),
            str(item.get("cluster_key") or ""),
        ),
    )
    return {
        "schema": SCHEMA,
        "total_pending_cases": len(rows),
        "recipe_count": len(recipes),
        "recipes": recipes,
        "ready": len(rows) == 0,
        "next_actions": _next_actions(recipes, len(rows)),
    }


def queue_browser_desktop_repair_recipes(
    *,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
    min_occurrences: int = 1,
) -> dict[str, Any]:
    report = compute_browser_desktop_repair_recipes(
        review_queue_path=review_queue_path,
        limit=limit,
        min_occurrences=min_occurrences,
    )
    recipes = [row for row in report.get("recipes") or [] if isinstance(row, dict)]
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    created = 0
    updated = 0
    items: list[dict[str, Any]] = []
    for recipe in recipes:
        result = queue.upsert_item(
            source="browser_desktop_repair_recipe",
            source_kind="browser_desktop_repair_recipe",
            candidate_kind=_candidate_kind(recipe),
            priority=str(recipe.get("priority") or "P1"),
            target_bucket="browser_desktop_repair_recipe",
            title=str(recipe.get("title") or "Browser/Desktop repair recipe"),
            text=_recipe_text(recipe),
            metadata={
                "schema": RECIPE_SCHEMA,
                "recipe": recipe,
                "source_report": {
                    "schema": SCHEMA,
                    "total_pending_cases": report.get("total_pending_cases", 0),
                    "recipe_count": report.get("recipe_count", 0),
                },
            },
            tags=[
                "browser",
                "desktop",
                "repair_recipe",
                "replay_case",
                str(recipe.get("candidate_kind") or "unknown"),
            ],
        )
        created += int(result.get("created") or 0)
        updated += int(result.get("updated") or 0)
        items.extend(result.get("items") or [])
    return {
        "schema": QUEUE_SCHEMA,
        "created": created,
        "updated": updated,
        "recipes": recipes,
        "items": items,
        "summary": {
            "total_pending_cases": report.get("total_pending_cases", 0),
            "recipe_count": len(recipes),
        },
    }


def reject_stale_browser_desktop_replay_artifacts(
    *,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    rows = queue.items(
        status="pending",
        target_bucket="browser_desktop_replay",
        limit=max(1, int(limit)),
    )["items"]
    rejected: list[dict[str, Any]] = []
    archived_recipes: list[dict[str, Any]] = []
    skipped = 0
    for row in rows:
        kind = str(row.get("candidate_kind") or "")
        metadata = _dict(row.get("metadata"))
        if kind == "browser_pixel_replay_gate_case":
            stale = _stale_source_artifact(metadata)
        elif kind == "computer_activity_replay_case":
            stale = _stale_computer_activity_replay(metadata)
        else:
            stale = ""
        if not stale:
            skipped += 1
            continue
        item_id = str(row.get("id") or "")
        if not item_id:
            skipped += 1
            continue
        result = queue.decide(
            item_id,
            action="rejected",
            reason=(
                f"Rejected stale browser/desktop replay case: {stale}; regenerate replay evidence."
            ),
        )
        rejected.append(
            {
                "id": item_id,
                "title": row.get("title"),
                "artifact_path": stale,
                "status": _dict(result.get("item")).get("status"),
            }
        )
    replay_status = {
        str(item.get("id") or ""): str(item.get("status") or "")
        for item in queue.items(
            target_bucket="browser_desktop_replay",
            limit=max(1, int(limit)),
        )["items"]
    }
    recipe_items = queue.items(
        status="pending",
        target_bucket="browser_desktop_repair_recipe",
        limit=max(1, int(limit)),
    )["items"]
    for row in recipe_items:
        recipe = _dict(_dict(row.get("metadata")).get("recipe"))
        source_ids = [
            str(item_id) for item_id in recipe.get("source_item_ids") or [] if str(item_id or "")
        ]
        if not source_ids:
            continue
        if not all(
            replay_status.get(item_id) in {"rejected", "archived"} for item_id in source_ids
        ):
            continue
        item_id = str(row.get("id") or "")
        if not item_id:
            continue
        result = queue.decide(
            item_id,
            action="archived",
            reason=(
                "Archived stale browser/desktop repair recipe because all source "
                "replay cases were rejected or archived."
            ),
        )
        archived_recipes.append(
            {
                "id": item_id,
                "title": row.get("title"),
                "status": _dict(result.get("item")).get("status"),
            }
        )
    return {
        "schema": STALE_REJECTION_SCHEMA,
        "inspected": len(rows),
        "rejected_count": len(rejected),
        "archived_recipe_count": len(archived_recipes),
        "skipped_count": skipped,
        "rejected": rejected,
        "archived_recipes": archived_recipes,
    }


def compute_browser_desktop_repair_recipe_verifications(
    *,
    review_queue_path: str | Path | None = None,
    limit: int = 1000,
) -> dict[str, Any]:
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    recipe_items = queue.items(
        status="pending",
        target_bucket="browser_desktop_repair_recipe",
        limit=max(1, int(limit)),
    )["items"]
    replay_items = queue.items(
        target_bucket="browser_desktop_replay",
        limit=max(1, int(limit)),
    )["items"]
    replay_status = {
        str(item.get("id") or ""): str(item.get("status") or "pending") for item in replay_items
    }
    verifications = [_verification_from_recipe_item(item, replay_status) for item in recipe_items]
    verified = sum(1 for row in verifications if row["status"] == "verified")
    blocked = sum(1 for row in verifications if row["status"] != "verified")
    return {
        "schema": VERIFICATION_SCHEMA,
        "total": len(verifications),
        "verified_count": verified,
        "blocked_count": blocked,
        "ready": blocked == 0,
        "verifications": verifications,
        "next_actions": _verification_next_actions(verifications),
    }


def attach_browser_desktop_repair_recipe_evidence(
    *,
    item_id: str,
    passed: bool,
    provided: list[str] | None = None,
    artifacts: list[dict[str, Any]] | None = None,
    notes: str = "",
    actor: str = "operator_panel",
    review_queue_path: str | Path | None = None,
) -> dict[str, Any]:
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    evidence = {
        "schema": EVIDENCE_SCHEMA,
        "attached_at": datetime.now(UTC).isoformat(),
        "actor": str(actor or "operator_panel")[:120],
        "passed": bool(passed),
        "provided": _unique_strings(provided or []),
        "artifacts": [artifact for artifact in artifacts or [] if isinstance(artifact, dict)][:20],
        "notes": str(notes or "")[:1200],
    }
    result = queue.update_metadata(
        item_id,
        metadata_patch={"verification_evidence": evidence},
        tags=["verification_evidence"],
    )
    verification = compute_browser_desktop_repair_recipe_verifications(
        review_queue_path=review_queue_path,
    )
    current = next(
        (
            row
            for row in verification.get("verifications") or []
            if isinstance(row, dict) and row.get("item_id") == item_id
        ),
        None,
    )
    return {
        "schema": "echo.browser_desktop_repair_recipe_evidence_attachment.v1",
        "item": result["item"],
        "evidence": evidence,
        "verification": current,
    }


def rerun_browser_desktop_repair_recipe_evidence(
    *,
    item_id: str,
    review_queue_path: str | Path | None = None,
    api_base_url: str = "http://127.0.0.1:8000",
    promote_source_cases: bool = False,
    actor: str = "auto_rerun",
    api_get: Callable[[str], dict[str, Any]] | None = None,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    queue = ReviewQueue(
        Path(review_queue_path) if review_queue_path is not None else app_paths().review_queue_path,
    )
    item = _recipe_item(queue, item_id)
    recipe = _dict(_dict(item.get("metadata")).get("recipe"))
    plan = _dict(recipe.get("verification_plan"))
    artifacts: list[dict[str, Any]] = []
    artifacts.extend(
        _produce_fresh_recipe_evidence(
            recipe,
            api_base_url=api_base_url,
            api_get=api_get,
            api_request=api_request,
        )
    )
    api_checks = [
        str(check) for check in plan.get("api_checks") or [] if str(check or "").startswith("/api/")
    ]
    provided: list[str] = []
    for check in api_checks:
        result = _run_api_check(
            check,
            api_base_url=api_base_url,
            api_get=api_get,
        )
        artifacts.append(result)
        if result.get("ok") is True:
            provided.extend(_provided_evidence_from_api_check(check, result))
    for artifact in artifacts:
        provided.extend(_provided_evidence_from_artifact(artifact))
    provided = _unique_strings(provided)
    required = [str(item) for item in plan.get("evidence_required") or [] if str(item or "")]
    missing = [item for item in required if item not in set(provided)]
    passed = not missing and all(artifact.get("ok") is True for artifact in artifacts)
    promoted_sources = 0
    if passed and promote_source_cases:
        for source_item_id in recipe.get("source_item_ids") or []:
            source_id = str(source_item_id or "")
            if not source_id:
                continue
            try:
                queue.decide(
                    source_id,
                    action="promoted",
                    promoted_to="browser_desktop_replay",
                    reason=(
                        "Auto-promoted source replay case after browser/desktop "
                        "repair recipe rerun evidence passed."
                    ),
                )
                promoted_sources += 1
            except KeyError:
                continue
    attachment = attach_browser_desktop_repair_recipe_evidence(
        item_id=item_id,
        passed=passed,
        provided=provided,
        artifacts=artifacts,
        notes=(
            "Auto rerun evidence passed."
            if passed
            else f"Auto rerun evidence missing: {', '.join(missing) or 'api_check_failed'}."
        ),
        actor=actor,
        review_queue_path=review_queue_path,
    )
    return {
        "schema": "echo.browser_desktop_repair_recipe_rerun.v1",
        "item_id": item_id,
        "passed": passed,
        "provided": provided,
        "missing": missing,
        "promoted_source_count": promoted_sources,
        "artifacts": artifacts,
        "attachment": attachment,
    }


def rerun_browser_desktop_repair_recipe_batch(
    *,
    review_queue_path: str | Path | None = None,
    api_base_url: str = "http://127.0.0.1:8000",
    promote_source_cases: bool = False,
    actor: str = "auto_rerun",
    limit: int = 20,
    api_get: Callable[[str], dict[str, Any]] | None = None,
    api_request: Callable[[str, str, dict[str, Any] | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    report = compute_browser_desktop_repair_recipe_verifications(
        review_queue_path=review_queue_path,
        limit=max(1, int(limit)),
    )
    targets = [
        row
        for row in report.get("verifications") or []
        if isinstance(row, dict) and row.get("status") != "verified"
    ][: max(1, int(limit))]
    results = [
        rerun_browser_desktop_repair_recipe_evidence(
            item_id=str(row.get("item_id") or ""),
            review_queue_path=review_queue_path,
            api_base_url=api_base_url,
            promote_source_cases=promote_source_cases,
            actor=actor,
            api_get=api_get,
            api_request=api_request,
        )
        for row in targets
        if row.get("item_id")
    ]
    return {
        "schema": "echo.browser_desktop_repair_recipe_rerun_batch.v1",
        "attempted": len(results),
        "passed": sum(1 for row in results if row.get("passed") is True),
        "failed": sum(1 for row in results if row.get("passed") is not True),
        "results": results,
    }


def _verification_from_recipe_item(
    item: dict[str, Any],
    replay_status: dict[str, str],
) -> dict[str, Any]:
    metadata = _dict(item.get("metadata"))
    recipe = _dict(metadata.get("recipe"))
    evidence = _dict(metadata.get("verification_evidence"))
    source_item_ids = [
        str(item_id) for item_id in recipe.get("source_item_ids") or [] if str(item_id or "")
    ]
    source_status_counts: dict[str, int] = {}
    for item_id in source_item_ids:
        status = replay_status.get(item_id, "missing")
        source_status_counts[status] = source_status_counts.get(status, 0) + 1
    missing_evidence = [
        str(name)
        for name in _dict(recipe.get("verification_plan")).get("evidence_required") or []
        if str(name or "")
    ]
    provided_evidence = (
        evidence.get("provided") if isinstance(evidence.get("provided"), list) else []
    )
    missing_evidence = [
        name for name in missing_evidence if name not in {str(item) for item in provided_evidence}
    ]
    blockers: list[str] = []
    if source_status_counts.get("pending", 0):
        blockers.append("source_replay_cases_pending")
    if not evidence:
        blockers.append("missing_verification_evidence")
    if missing_evidence:
        blockers.append("missing_required_evidence")
    if evidence and evidence.get("passed") is not True:
        blockers.append("verification_evidence_failed")
    status = "verified" if not blockers else "needs_rerun_evidence"
    return {
        "schema": "echo.browser_desktop_repair_recipe_verification.v1",
        "item_id": item.get("id"),
        "recipe_id": recipe.get("recipe_id"),
        "title": item.get("title"),
        "priority": item.get("priority"),
        "status": status,
        "blockers": blockers,
        "source_status_counts": dict(sorted(source_status_counts.items())),
        "missing_evidence": missing_evidence,
        "verification_evidence": evidence,
    }


def _recipe_item(queue: ReviewQueue, item_id: str) -> dict[str, Any]:
    wanted = str(item_id or "")
    for item in queue.items(
        target_bucket="browser_desktop_repair_recipe",
        limit=10000,
    )["items"]:
        if str(item.get("id") or "") == wanted:
            return item
    raise KeyError(wanted)


__all__ = [
    "QUEUE_SCHEMA",
    "RECIPE_SCHEMA",
    "SCHEMA",
    "VERIFICATION_SCHEMA",
    "attach_browser_desktop_repair_recipe_evidence",
    "compute_browser_desktop_repair_recipes",
    "compute_browser_desktop_repair_recipe_verifications",
    "queue_browser_desktop_repair_recipes",
    "reject_stale_browser_desktop_replay_artifacts",
    "rerun_browser_desktop_repair_recipe_batch",
    "rerun_browser_desktop_repair_recipe_evidence",
]
