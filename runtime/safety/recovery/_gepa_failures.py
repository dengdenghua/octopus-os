"""Failure-sample collectors extracted from ``gepa_bridge.py``.

Pure structural split · no logic changes. Pulls real losing
trajectories from the reflection journal and the realtime
turn-failure ledger so the GEPA optimizer has grounded,
non-synthetic signal to mutate against.
"""

from __future__ import annotations

import logging
from typing import Any

from runtime.safety.auth.scope import TenantScope
from runtime.safety.evolution.proposal_ledger import ProposalLedger
from runtime.safety.recovery.tenant_scope import (
    is_legacy_unscoped_event,
    read_learning_events,
)

_LOG = logging.getLogger("echo.gepa.bridge")


def collect_failures_from_journal(
    journal: Any,
    *,
    recipe_id: str | None = None,
    limit: int = 10,
    scope: TenantScope | None = None,
) -> list[dict[str, Any]]:
    """Pull failed trajectories · optionally filter by recipe.

    Returns descriptors the LLM mutator can read · keeps each
    entry small (< 400 chars) so the mutation prompt stays
    tractable even with 5 failures bundled in.

    SKIPS empty-goal entries · these slip in from reflex-only
    turns and synthetic test data, and feeding them to the
    LLM-as-judge produces all-zero scores (no goal text means
    nothing to score). Empty-goal failures still WERE failures,
    but they aren't actionable without re-running them with
    the goal preserved · cleaner to skip than to noise the
    optimizer's signal.
    """
    try:
        evs = read_learning_events(journal, "trajectory", scope=scope)
    except (OSError, TypeError, ValueError, AttributeError):  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    skipped_empty = 0
    for ev in reversed(evs):  # newest first
        outcome = getattr(ev, "outcome", None)
        success = bool(getattr(outcome, "success", False)) if outcome else False
        if success:
            continue
        rid = getattr(ev, "recipe_id", None)
        if recipe_id and rid != recipe_id:
            continue
        # Pull goal text + step count + last error if any.
        goal = (
            getattr(ev, "goal", "")
            or getattr(getattr(ev, "intent", None), "normalized_goal", "")
            or ""
        )[:200]
        # Drop entries with no goal text · the judge can't score them
        # and the mutator can't reason about them (would just see
        # "{}"). These typically come from short-circuit paths
        # (reflex hits, error envelopes) that don't preserve the
        # original prompt. Keep a counter for the caller to surface
        # in admin UI ("we skipped N entries · check what's
        # writing trajectory events without goals").
        if not goal.strip():
            skipped_empty += 1
            continue
        steps = getattr(ev, "steps", None) or []
        last_err = ""
        if steps:
            last_step = steps[-1]
            last_err = str(
                getattr(getattr(last_step, "result", None), "error", "")
                or getattr(last_step, "error", "")
                or ""
            )[:150]
        out.append(
            {
                "goal": goal,
                "step_count": len(steps),
                "last_error": last_err,
                "recipe_id": rid,
            }
        )
        if len(out) >= limit:
            break
    if skipped_empty:
        _LOG.info(
            "gepa: skipped %d empty-goal failure(s) · returned %d usable",
            skipped_empty,
            len(out),
        )
    return out


def collect_failures_from_ledger(
    *,
    ledger_path: Any = "data/proposal_ledger.jsonl",
    recipe_id: str | None = None,
    limit: int = 10,
    scope: TenantScope | None = None,
) -> list[dict[str, Any]]:
    """Pull realtime failed-turn records from ProposalLedger."""

    try:
        records = ProposalLedger(ledger_path).query(
            kind="turn_failure",
            limit=max(limit * 4, limit),
            scope=scope,
        )
    except (OSError, TypeError, ValueError, AttributeError):  # noqa: BLE001
        return []

    out: list[dict[str, Any]] = []
    skipped_empty = 0
    for record in reversed(records):
        if scope is None and not is_legacy_unscoped_event(record):
            continue
        metadata = record.metadata if isinstance(record.metadata, dict) else {}
        rid = metadata.get("recipe_id")
        if recipe_id and isinstance(rid, str) and rid.strip() and rid != recipe_id:
            continue
        goal = str(metadata.get("goal") or "").strip()[:200]
        if not goal:
            skipped_empty += 1
            continue
        item_counts = metadata.get("item_counts")
        step_count = 0
        if isinstance(item_counts, dict):
            for value in item_counts.values():
                try:
                    step_count += int(value or 0)
                except (TypeError, ValueError):
                    continue
        out.append(
            {
                "goal": goal,
                "step_count": step_count,
                "last_error": str(metadata.get("error") or record.description or "")[:150],
                "recipe_id": rid if isinstance(rid, str) else None,
                "source": "proposal_ledger",
                "proposal_id": record.proposal_id,
                "turn_id": metadata.get("turn_id"),
                "thread_id": metadata.get("thread_id"),
                "failure_source": metadata.get("failure_source"),
                "code_change_paths": metadata.get("code_change_paths") or [],
            }
        )
        if len(out) >= limit:
            break
    if skipped_empty:
        _LOG.info(
            "gepa: skipped %d empty-goal ledger failure(s) · returned %d usable",
            skipped_empty,
            len(out),
        )
    return out
