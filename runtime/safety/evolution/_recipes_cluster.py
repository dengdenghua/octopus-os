"""Cluster key derivation and recipe body builders for browser/desktop repair recipes.

Extracted from ``browser_desktop_repair_recipes.py``. These pure functions
turn raw review-queue rows into deterministic recipe dictionaries (cluster
keys, titles, evidence summaries, recommended steps, verification plans and
stale-artifact probes). They depend only on the standard library and the
``_recipes_common`` primitives.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from ._recipes_common import RECIPE_SCHEMA, _dict, _unique_strings


def _cluster_key(row: dict[str, Any]) -> str:
    kind = str(row.get("candidate_kind") or "unknown")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    if kind == "browser_pixel_replay_gate_case":
        return "|".join(("pixel", _pixel_reason(metadata), _artifact_shape(metadata)))
    if kind == "browser_session_replay_case":
        last_action = _dict(metadata.get("last_action"))
        health = _dict(metadata.get("health"))
        issues = health.get("issues") if isinstance(health.get("issues"), list) else []
        issue_text = ",".join(sorted(str(issue) for issue in issues)) or "healthy"
        return "|".join(
            (
                "browser_session",
                str(last_action.get("status") or "unknown"),
                str(last_action.get("action") or "unknown"),
                issue_text,
            )
        )
    if kind == "computer_activity_replay_case":
        last_activity = _dict(metadata.get("last_activity"))
        action = _dict(last_activity.get("action"))
        return "|".join(
            (
                "computer_activity",
                str(last_activity.get("event") or "unknown"),
                str(action.get("action") or "unknown"),
                f"pending:{int(metadata.get('pending_count') or 0)}",
            )
        )
    return "|".join(("unknown", kind))


def _recipe_from_cluster(key: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = rows[0]
    kind = str(first.get("candidate_kind") or "unknown")
    metadata = _dict(first.get("metadata"))
    case_ids = _unique_strings(_case_id(_dict(row.get("metadata"))) for row in rows)
    fingerprints = _unique_strings(
        str(_dict(row.get("metadata")).get("fingerprint") or "") for row in rows
    )
    priority = "P0" if any(str(row.get("priority") or "") == "P0" for row in rows) else "P1"
    if len(rows) >= 3:
        priority = "P0"
    recipe_id = "browser-desktop-recipe:" + hashlib.sha256(key.encode()).hexdigest()[:16]
    return {
        "schema": RECIPE_SCHEMA,
        "recipe_id": recipe_id,
        "cluster_key": key,
        "candidate_kind": kind,
        "title": _title(kind, metadata, len(rows)),
        "priority": priority,
        "occurrences": len(rows),
        "source_item_ids": _unique_strings(str(row.get("id") or "") for row in rows),
        "case_ids": case_ids,
        "fingerprints": fingerprints,
        "evidence_summary": _evidence_summary(kind, metadata, len(rows)),
        "recommended_steps": _recommended_steps(kind, metadata),
        "verification_plan": _verification_plan(kind, metadata),
        "promotion_gate": {
            "schema": "echo.browser_desktop_repair_recipe_gate.v1",
            "requires_operator_review": True,
            "requires_replay_rerun": True,
            "requires_fresh_visual_or_activity_evidence": True,
            "blocks_auto_promotion": True,
        },
    }


def _title(kind: str, metadata: dict[str, Any], occurrences: int) -> str:
    if kind == "browser_pixel_replay_gate_case":
        return f"Stabilize browser pixel replay gate ({occurrences} case(s))"
    if kind == "browser_session_replay_case":
        action = str(_dict(metadata.get("last_action")).get("action") or "session")
        return f"Stabilize browser session replay: {action}"
    if kind == "computer_activity_replay_case":
        action = str(
            _dict(_dict(metadata.get("last_activity")).get("action")).get("action") or "activity"
        )
        return f"Stabilize desktop activity replay: {action}"
    return f"Stabilize browser/desktop replay cluster ({occurrences} case(s))"


def _evidence_summary(kind: str, metadata: dict[str, Any], occurrences: int) -> dict[str, Any]:
    if kind == "browser_pixel_replay_gate_case":
        replay_case = _dict(metadata.get("replay_gate_case"))
        failures = (
            replay_case.get("failures") if isinstance(replay_case.get("failures"), list) else []
        )
        return {
            "occurrences": occurrences,
            "failure_reason": _pixel_reason(metadata),
            "failure_count": len(failures) or int(metadata.get("failure_count") or 0),
            "artifact": metadata.get("artifact")
            if isinstance(metadata.get("artifact"), dict)
            else {},
        }
    if kind == "browser_session_replay_case":
        return {
            "occurrences": occurrences,
            "health": _dict(metadata.get("health")),
            "last_action": _dict(metadata.get("last_action")),
            "action_count": metadata.get("action_count"),
        }
    if kind == "computer_activity_replay_case":
        return {
            "occurrences": occurrences,
            "last_activity": _dict(metadata.get("last_activity")),
            "activity_count": metadata.get("activity_count"),
            "pending_count": metadata.get("pending_count"),
            "reason": metadata.get("reason"),
        }
    return {"occurrences": occurrences}


def _recommended_steps(kind: str, metadata: dict[str, Any]) -> list[str]:
    if kind == "browser_pixel_replay_gate_case":
        return [
            "Replay the browser action that produced the failing screenshot.",
            "Capture a fresh before/after screenshot pair for the same viewport.",
            "Compare pixel metrics against the recorded threshold before promotion.",
        ]
    if kind == "browser_session_replay_case":
        last_action = _dict(metadata.get("last_action"))
        action = str(last_action.get("action") or "the last browser action")
        return [
            f"Re-run the browser session replay through `{action}`.",
            "Verify `/api/browser/session/health` reports a healthy session.",
            "Attach the replay case and session health evidence before promotion.",
        ]
    if kind == "computer_activity_replay_case":
        reason = str(metadata.get("reason") or "")
        if "screenshot read failed" in reason.lower():
            return [
                "Re-run the production desktop loop with a capture path resolved inside the sandbox.",
                "Verify the vision planner reads the capture result path instead of the original relative request.",
                "Attach fresh screenshot-path contract evidence before promotion.",
            ]
        return [
            "Re-run the desktop preview/execute sequence under the same lease owner.",
            "Verify the computer activity replay case is replay_ready.",
            "Attach UIA grounding or activity replay evidence before promotion.",
        ]
    return ["Re-run the replay cluster and attach fresh evidence before promotion."]


def _verification_plan(kind: str, metadata: dict[str, Any]) -> dict[str, Any]:
    if kind == "browser_pixel_replay_gate_case":
        return {
            "schema": "echo.browser_desktop_recipe_verification.v1",
            "commands": [],
            "api_checks": [
                "/api/evolution/browser-desktop-quality",
                "/api/evolution/browser-desktop-repair-recipes",
            ],
            "evidence_required": ["fresh_screenshot", "pixel_comparison"],
        }
    if kind == "browser_session_replay_case":
        session_id = str(metadata.get("session_id") or "workspace")
        return {
            "schema": "echo.browser_desktop_recipe_verification.v1",
            "commands": [],
            "api_checks": [
                f"/api/browser/session/replay-case?session_id={session_id}",
                f"/api/browser/session/health?session_id={session_id}",
            ],
            "evidence_required": ["browser_session_replay_case", "session_health"],
        }
    if (
        kind == "computer_activity_replay_case"
        and "screenshot read failed" in str(metadata.get("reason") or "").lower()
    ):
        return {
            "schema": "echo.browser_desktop_recipe_verification.v1",
            "commands": [],
            "api_checks": [],
            "evidence_required": ["computer_screenshot_path_contract"],
        }
    return {
        "schema": "echo.browser_desktop_recipe_verification.v1",
        "commands": [],
        "api_checks": ["/api/computer/activity/replay-case"],
        "evidence_required": ["computer_activity_replay_case"],
    }


def _pixel_reason(metadata: dict[str, Any]) -> str:
    replay_case = _dict(metadata.get("replay_gate_case"))
    failures = replay_case.get("failures") if isinstance(replay_case.get("failures"), list) else []
    reasons = [
        str(item.get("reason") or "")
        for item in failures
        if isinstance(item, dict) and item.get("reason")
    ]
    return "; ".join(reasons) or str(
        _dict(metadata.get("replay_gate")).get("reason") or "browser_pixel_evidence_failed"
    )


def _artifact_shape(metadata: dict[str, Any]) -> str:
    artifact = _dict(metadata.get("artifact"))
    return f"{artifact.get('width') or 'w'}x{artifact.get('height') or 'h'}"


def _case_id(metadata: dict[str, Any]) -> str:
    replay = metadata.get("replay") if isinstance(metadata.get("replay"), dict) else {}
    return str(metadata.get("case_id") or replay.get("case_id") or "")


def _recipe_text(recipe: dict[str, Any]) -> str:
    steps = recipe.get("recommended_steps")
    step_text = (
        "\n".join(f"- {step}" for step in steps if isinstance(step, str))
        if isinstance(steps, list)
        else ""
    )
    cases = ", ".join(str(case_id) for case_id in recipe.get("case_ids") or [])
    return (
        f"{recipe.get('title')}\n"
        f"Occurrences: {recipe.get('occurrences')}; cases: {cases or 'none'}.\n"
        "Recommended steps:\n"
        f"{step_text}"
    ).strip()


def _candidate_kind(recipe: dict[str, Any]) -> str:
    digest = hashlib.sha256(str(recipe.get("cluster_key") or "").encode()).hexdigest()[:10]
    return f"browser_desktop_repair_recipe:{digest}"


def _next_actions(recipes: list[dict[str, Any]], pending_count: int) -> list[str]:
    if recipes:
        return [
            f"Queue {len(recipes)} deterministic browser/desktop repair recipe(s) for operator review.",
        ]
    if pending_count:
        return ["Inspect pending replay cases; no deterministic recipe cluster was found yet."]
    return ["No pending browser/desktop replay cases need repair recipes."]


def _verification_next_actions(verifications: list[dict[str, Any]]) -> list[str]:
    blocked = [row for row in verifications if row.get("status") != "verified"]
    if blocked:
        return [
            f"Attach rerun evidence for {len(blocked)} browser/desktop repair recipe(s).",
        ]
    if verifications:
        return ["All browser/desktop repair recipes have rerun evidence."]
    return ["Queue browser/desktop repair recipes before verification."]


def _stale_source_artifact(metadata: dict[str, Any]) -> str:
    artifact = metadata.get("artifact") if isinstance(metadata.get("artifact"), dict) else {}
    local_path = str(artifact.get("local_path") or "").strip()
    if local_path and not Path(local_path).is_file():
        return local_path
    return ""


def _stale_computer_activity_replay(
    metadata: dict[str, Any],
    *,
    max_age_seconds: int = 300,
) -> str:
    last_activity = _dict(metadata.get("last_activity"))
    created_at = last_activity.get("created_at")
    try:
        age_seconds = time.time() - float(created_at)
    except (TypeError, ValueError):
        return "computer activity replay has no durable created_at timestamp"
    if age_seconds > max_age_seconds:
        return (
            "computer activity replay is ephemeral and "
            f"{int(age_seconds)}s old (ttl {max_age_seconds}s)"
        )
    return ""
