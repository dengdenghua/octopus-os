from __future__ import annotations

import hashlib
import json
from typing import Any

from runtime.execution.loops.models import LoopRun
from runtime.execution.loops.recovery import build_loop_run_checkpoint
from runtime.execution.loops.replay import (
    build_loop_run_findings,
    build_loop_run_replay,
    build_loop_run_review_score,
)


def _loop_fingerprint(run: LoopRun) -> str:
    return str(run.run_id or "")[:16]


def _failing_check_names(run: LoopRun) -> list[str]:
    result = run.last_verifier_result
    if result is None:
        return []
    names: list[str] = []
    for finding in result.findings:
        if finding.passed:
            continue
        name = str(finding.name or "").strip()
        if name and name not in names:
            names.append(name)
    return names


def _failure_category(run: LoopRun) -> str:
    result = run.last_verifier_result
    if result is None:
        return ""
    category = str(result.failure_category or "").strip()
    if category:
        return category
    for finding in result.findings:
        if finding.passed:
            continue
        category = str(finding.category or "").strip()
        if category:
            return category
    return ""


def _repair_failure_categories(run: LoopRun) -> list[str]:
    categories: list[str] = []
    for attempt in run.attempts[:-1]:
        verifier = attempt.verifier_result
        if verifier is not None and not verifier.passed:
            category = str(verifier.failure_category or "").strip()
            if category and category not in categories:
                categories.append(category)
        reason = str(attempt.terminated_reason or "").strip()
        if reason.startswith("exception:"):
            category = reason.removeprefix("exception:").strip()
            if category and category not in categories:
                categories.append(category)
    return categories[:5]


def build_loop_repair_candidate_spec(run: LoopRun) -> dict[str, Any] | None:
    """Build a typed prompt candidate only from sealed, local repair evidence."""

    if run.status.value != "completed" or len(run.attempts) <= 1:
        return None
    latest = run.attempts[-1]
    verifier = latest.verifier_result or run.last_verifier_result
    if verifier is None or verifier.passed is not True or latest.success is not True:
        return None
    summary = latest.effect_summary if isinstance(latest.effect_summary, dict) else {}
    if summary.get("schema") != "echo.loop.attempt_effect_summary.v2":
        return None
    if summary.get("complete") is not True or summary.get("sealed") is not True:
        return None
    blocking_counts = (
        "external_effect_count",
        "indeterminate_effect_count",
        "unknown_effect_count",
        "unsealed_receipt_count",
    )
    if any(int(summary.get(key) or 0) != 0 for key in blocking_counts):
        return None
    if int(summary.get("workspace_write_effect_count") or 0) <= 0:
        return None

    failure_categories = _repair_failure_categories(run)
    if not failure_categories:
        return None
    verifier_profile = str(run.policy.verifier_profile or "auto")
    applicability = {
        "mode": run.mode.value,
        "verifier_profile": verifier_profile,
        "failure_categories": failure_categories,
    }
    environment_digest = hashlib.sha256(
        json.dumps(applicability, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "gene_type": "prompt",
        "scope": f"runtime.loop.{run.mode.value}.repair_prompt:{verifier_profile}",
        "patch": {
            "op": "append_guidance",
            "target": "loop.repair_prompt",
            "when": applicability,
            "value": (
                "Use only server-owned verifier and exception evidence. Inspect the current "
                "workspace before making the smallest exact repair, then verify it independently. "
                "Never replay an external, unknown, or indeterminate effect."
            ),
        },
        "proposer": "loop_controller",
        "lineage_id": f"loop_repair:{run.mode.value}:{verifier_profile}",
        "role_id": "loop_controller",
        "task_domain": f"loop_{run.mode.value}_repair",
        "environment_digest": environment_digest,
        "risk_level": "medium",
        "source_failures": failure_categories,
        "metadata": {
            "source_run_id": run.run_id,
            "attempt_count": len(run.attempts),
            "effect_summary_schema": summary.get("schema"),
            "effect_fingerprint": summary.get("effect_fingerprint"),
            "governance": "typed_candidate_registry",
            "automatic_activation": False,
            "next_stage": "operator_validation",
        },
    }


def build_loop_run_review(run: LoopRun) -> dict[str, Any]:
    attempts = len(run.attempts)
    failed_checks = _failing_check_names(run)
    candidates: list[dict[str, Any]] = []
    backlog: list[dict[str, Any]] = []
    verifier_profile = str(run.policy.verifier_profile or "auto")
    failure_category = _failure_category(run)
    findings = build_loop_run_findings(run)
    score, score_reasons = build_loop_run_review_score(run, findings)
    replay = build_loop_run_replay(run)
    resume_available = run.status.value in {"failed", "cancelled", "interrupted"}
    latest_checkpoint = build_loop_run_checkpoint(run) if resume_available else {}

    if run.status.value == "completed" and attempts > 1:
        repair_categories = _repair_failure_categories(run)
        candidates.append(
            {
                "kind": "success_pattern",
                "gene_type": "prompt",
                "candidate_stage": "pending_review",
                "governance": "review_queue",
                "automatic_activation": False,
                "priority": "P1",
                "memory_bucket": "experience",
                "title": "Verification-guided repair converged",
                "text": (
                    f"Code loop reached a verified passing state after {attempts} attempts "
                    f"using verifier profile {verifier_profile}. Keep repair prompts grounded "
                    "in server-owned failure evidence, inspect the current workspace state, "
                    "and never blindly repeat external effects. This is a pending candidate "
                    "and must not change runtime prompts before explicit review."
                ),
                "source_failure_categories": repair_categories,
            }
        )
    elif run.status.value == "failed":
        checks_text = (
            ", ".join(failed_checks[:5]) if failed_checks else "verification or execution failures"
        )
        candidates.append(
            {
                "kind": "failure_pattern",
                "priority": "P0",
                "memory_bucket": "experience",
                "title": "Code loop exhausted retries",
                "text": (
                    f"Loop run exhausted {attempts or 1} attempts and still failed. "
                    f"Most recent failing signals: {checks_text}. "
                    f"Failure category: {failure_category or 'unknown'}. "
                    "Review whether the repair prompt carried enough concrete evidence into the next attempt."
                ),
            }
        )
        backlog.append(
            {
                "priority": "P1",
                "experiment": "Add replay coverage for loop failure pattern",
                "hypothesis": (
                    "A deterministic replay or fixture for this loop failure would make "
                    "repair prompts and verifier-guided retries easier to validate."
                ),
                "minimal_implementation": (
                    "Capture the failing workspace diff, verifier output, and retry prompt "
                    "as a replay-style fixture for operator review."
                ),
                "validation_metric": "A replayed loop can reproduce the same failure signature.",
            }
        )

    return {
        "schema": "echo.task_run_review.v1",
        "task_id": run.run_id,
        "thread_id": run.thread_id or run.run_id,
        "turn_id": run.run_id,
        "agent_id": "loop_controller",
        "status": run.status.value,
        "score": score,
        "score_reasons": score_reasons,
        "summary": {
            "attempt_count": attempts,
            "verifier_profile": verifier_profile,
            "failure_category": failure_category,
            "final_status": run.status.value,
            "workspace_path": run.workspace_path,
            "parent_run_id": run.parent_run_id,
            "origin_run_id": run.origin_run_id,
            "resume_checkpoint_id": run.resume_checkpoint_id,
        },
        "findings": findings,
        "replay": replay,
        "resume": {
            "available": resume_available,
            "source": "loop_runs",
            "latest_checkpoint": latest_checkpoint,
            "resume_from_run_id": run.run_id if resume_available else None,
            "reuse_workspace": bool(run.workspace_path) if resume_available else False,
        },
        "learning_candidates": candidates,
        "backlog_candidates": backlog,
    }
