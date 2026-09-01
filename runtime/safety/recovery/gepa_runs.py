from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any

_LOG = logging.getLogger("echo.gepa.runs")

DEFAULT_MAX_RUNS = 20


@dataclass
class GepaRunRecord:
    """One persisted GEPA run · summarised so the store stays small."""

    ts: float  # unix seconds
    trigger: str  # "manual" | "auto_propose" | other
    recipe_id: str | None
    iterations_run: int
    elapsed_s: float
    front_size: int
    best_candidate_id: str | None
    best_avg_score: float | None
    best_rationale: str
    # Trim history → just the iter / improved / rationale tuples
    # so a 30-iter run takes ~3 KB instead of 30 KB.
    history_summary: list[dict[str, Any]] = field(default_factory=list)
    # Whether the operator subsequently applied this run's winner.
    applied: bool = False
    applied_at: float | None = None
    # Optional · the prompt of the best candidate so the operator
    # can re-apply from history without re-running. Capped so a
    # single record never exceeds ~2 KB.
    best_prompt: str = ""
    # Native, model-free evaluation artifacts. These are compact
    # summaries used by the UI to explain why a mutation was kept or
    # blocked without recalculating the replay report.
    native_evaluation: list[dict[str, Any]] = field(default_factory=list)
    native_replay: dict[str, Any] = field(default_factory=dict)
    native_sandbox_replay: dict[str, Any] = field(default_factory=dict)
    native_turn_replay: dict[str, Any] = field(default_factory=dict)
    native_llm_replay: dict[str, Any] = field(default_factory=dict)
    winner_proposal: dict[str, Any] | None = None
    optimizer_backend: str = "native_gepa"

    def mark_applied(self) -> None:
        self.applied = True
        self.applied_at = time.time()


class GepaRunStore:
    """Bounded ring buffer of recent runs · accessible from anywhere
    via ``get_default_store``. Records are immutable except for
    ``mark_applied`` which the apply endpoint flips."""

    def __init__(
        self,
        *,
        max_runs: int = DEFAULT_MAX_RUNS,
        mirror_path: str | None = None,
    ) -> None:
        self._max = max_runs
        self._lock = threading.RLock()
        self._runs: deque[GepaRunRecord] = deque(maxlen=max_runs)
        self._mirror_path = mirror_path
        if mirror_path:
            try:
                # Ensure parent dir exists · cheap, idempotent.
                os.makedirs(os.path.dirname(mirror_path), exist_ok=True)
            except OSError as exc:
                _LOG.warning("gepa_runs mirror dir create failed: %s", exc)

    def add(self, rec: GepaRunRecord) -> None:
        with self._lock:
            self._runs.append(rec)
        if self._mirror_path:
            self._mirror_append(rec)

    def list_recent(self, *, limit: int = 20) -> list[GepaRunRecord]:
        """Newest first."""
        with self._lock:
            # deque iteration order is oldest→newest; reverse for UI.
            return list(reversed(self._runs))[: max(1, int(limit))]

    def find(self, *, ts: float) -> GepaRunRecord | None:
        """Find a run by timestamp · used by the apply endpoint to
        flip ``applied=True`` when the operator picks a candidate
        from history. Float equality is fine since timestamps are
        captured at exactly one point per run."""
        with self._lock:
            for r in self._runs:
                if abs(r.ts - ts) < 1e-3:
                    return r
        return None

    def mark_applied(self, *, ts: float) -> bool:
        rec = self.find(ts=ts)
        if rec is None:
            return False
        rec.mark_applied()
        return True

    def _mirror_append(self, rec: GepaRunRecord) -> None:
        try:
            with open(self._mirror_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        except OSError as exc:
            _LOG.warning("gepa_runs mirror append failed: %s", exc)


# ═══════════════════════════════════════════════════════════
# Module singleton · one store per process
# ═══════════════════════════════════════════════════════════


_DEFAULT_STORE: GepaRunStore | None = None
_LOCK = threading.RLock()


def get_default_store() -> GepaRunStore:
    """Return the process-wide store · lazy-init from env."""
    global _DEFAULT_STORE
    with _LOCK:
        if _DEFAULT_STORE is None:
            mirror = os.environ.get("ECHO_GEPA_HISTORY_PATH") or None
            _DEFAULT_STORE = GepaRunStore(mirror_path=mirror)
        return _DEFAULT_STORE


def record_from_result(
    result: Any,
    *,
    trigger: str = "manual",
    recipe_id: str | None = None,
) -> GepaRunRecord:
    """Convert a ``GepaResult`` (loose-typed via Any to avoid an
    import cycle) into a compact record. Trims history to 30
    entries so a long run doesn't bloat the store."""
    best = getattr(result, "best_avg", None)
    history = getattr(result, "history", []) or []
    summary: list[dict[str, Any]] = []
    for h in history[:30]:
        if not isinstance(h, dict):
            continue
        summary.append(
            {
                k: v
                for k, v in h.items()
                if k
                in (
                    "iter",
                    "improved",
                    "child_avg",
                    "front_size",
                    "rationale",
                    "skipped",
                    "reason",
                    "early_stop",
                )
            }
        )
    native_evaluation = _compact_native_evaluation(
        getattr(result, "native_evaluation", None),
    )
    native_replay = _compact_native_replay(
        getattr(result, "native_replay", None),
    )
    native_sandbox_replay = _compact_native_sandbox_replay(
        getattr(result, "native_sandbox_replay", None),
    )
    native_turn_replay = _compact_native_turn_replay(
        getattr(result, "native_turn_replay", None),
    )
    native_llm_replay = _compact_native_turn_replay(
        getattr(result, "native_llm_replay", None),
    )
    winner_proposal = _compact_winner_proposal(
        getattr(result, "winner_proposal", None),
    )
    return GepaRunRecord(
        ts=time.time(),
        trigger=trigger,
        recipe_id=recipe_id,
        iterations_run=int(getattr(result, "iterations_run", 0) or 0),
        elapsed_s=float(getattr(result, "elapsed_s", 0.0) or 0.0),
        front_size=len(getattr(result, "final_front", []) or []),
        best_candidate_id=(getattr(best, "candidate_id", None) if best else None),
        best_avg_score=(float(getattr(best, "avg_score", 0.0)) if best else None),
        best_rationale=(str(getattr(best, "rationale", ""))[:300] if best else ""),
        best_prompt=(str(getattr(best, "prompt", ""))[:2000] if best else ""),
        history_summary=summary,
        native_evaluation=native_evaluation,
        native_replay=native_replay,
        native_sandbox_replay=native_sandbox_replay,
        native_turn_replay=native_turn_replay,
        native_llm_replay=native_llm_replay,
        winner_proposal=winner_proposal,
        optimizer_backend=str(getattr(result, "optimizer_backend", "") or "native_gepa"),
    )


def _compact_native_evaluation(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for item in value[:5]:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "candidate_id": item.get("candidate_id"),
                "total": item.get("total"),
                "verdict": item.get("verdict"),
                "task_score": item.get("task_score"),
                "constraint_score": item.get("constraint_score"),
                "failure_coverage": item.get("failure_coverage"),
                "positive_preservation": item.get("positive_preservation"),
                "efficiency": item.get("efficiency"),
                "reasons": list(item.get("reasons") or [])[:5],
            }
        )
    return rows


def _compact_native_replay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = value.get("candidates")
    cases = value.get("cases")
    compact_candidates: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            weak_cases: list[dict[str, Any]] = []
            case_results = candidate.get("case_results")
            if isinstance(case_results, list):
                for result in case_results:
                    if not isinstance(result, dict):
                        continue
                    try:
                        score = float(result.get("score") or 0.0)
                    except (TypeError, ValueError):
                        score = 0.0
                    if score >= 0.55:
                        continue
                    weak_cases.append(
                        {
                            "case_id": result.get("case_id"),
                            "kind": result.get("kind"),
                            "score": result.get("score"),
                            "reason": result.get("reason"),
                            "missing_signals": list(result.get("missing_signals") or [])[:5],
                        }
                    )
                    if len(weak_cases) >= 5:
                        break
            compact_candidates.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "total": candidate.get("total"),
                    "reasons": list(candidate.get("reasons") or [])[:5],
                    "weak_cases": weak_cases,
                }
            )
    return {
        "case_count": len(cases) if isinstance(cases, list) else 0,
        "candidates": compact_candidates,
    }


def _compact_winner_proposal(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "ok",
        "skipped",
        "reason",
        "proposal_id",
        "proposal_status",
        "canary_key",
        "canary_phase",
        "candidate_id",
        "avg_score",
    )
    return {key: value.get(key) for key in keys if key in value}


def _compact_native_sandbox_replay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = value.get("candidates")
    compact_candidates: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            weak_cases: list[dict[str, Any]] = []
            case_results = candidate.get("case_results")
            if isinstance(case_results, list):
                for result in case_results:
                    if not isinstance(result, dict):
                        continue
                    try:
                        score = float(result.get("score") or 0.0)
                    except (TypeError, ValueError):
                        score = 0.0
                    if score >= 0.55 and result.get("sandbox_passed") is not False:
                        continue
                    weak_cases.append(
                        {
                            "case_id": result.get("case_id"),
                            "kind": result.get("kind"),
                            "score": result.get("score"),
                            "sandbox_passed": result.get("sandbox_passed"),
                            "reason": result.get("reason"),
                        }
                    )
                    if len(weak_cases) >= 5:
                        break
            compact_candidates.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "total": candidate.get("total"),
                    "passed": candidate.get("passed"),
                    "reasons": list(candidate.get("reasons") or [])[:5],
                    "weak_cases": weak_cases,
                }
            )
    return {
        "case_count": value.get("case_count") or 0,
        "candidates": compact_candidates,
    }


def _compact_native_turn_replay(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    candidates = value.get("candidates")
    cases = value.get("cases")
    compact_candidates: list[dict[str, Any]] = []
    if isinstance(candidates, list):
        for candidate in candidates[:5]:
            if not isinstance(candidate, dict):
                continue
            weak_cases: list[dict[str, Any]] = []
            case_results = candidate.get("case_results")
            if isinstance(case_results, list):
                for result in case_results:
                    if not isinstance(result, dict):
                        continue
                    if result.get("passed") is True:
                        continue
                    weak_cases.append(
                        {
                            "case_id": result.get("case_id"),
                            "kind": result.get("kind"),
                            "score": result.get("score"),
                            "passed": result.get("passed"),
                            "reason": result.get("reason"),
                            "missing_signals": list(result.get("missing_signals") or [])[:5],
                        }
                    )
                    if len(weak_cases) >= 5:
                        break
            compact_candidates.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "total": candidate.get("total"),
                    "passed": candidate.get("passed"),
                    "reasons": list(candidate.get("reasons") or [])[:5],
                    "weak_cases": weak_cases,
                }
            )
    return {
        "case_count": len(cases) if isinstance(cases, list) else 0,
        "candidates": compact_candidates,
    }


def enrich_run_records(
    runs: list[GepaRunRecord],
    *,
    ledger_path: str | None = "data/proposal_ledger.jsonl",
    canary_config: Any = None,
) -> list[dict[str, Any]]:
    """Attach current proposal/canary lifecycle state to run rows."""
    canary_by_key: dict[tuple[str | None, str | None], Any] = {}
    proposal_by_key: dict[tuple[str | None, str | None], Any] = {}

    try:
        from runtime.safety.evolution.canary import CanaryManager

        for state in reversed(CanaryManager(canary_config).list_all()):
            metadata = state.metadata if isinstance(state.metadata, dict) else {}
            candidate_id = str(metadata.get("candidate_id") or "").strip() or None
            recipe = metadata.get("recipe_id")
            recipe_id = str(recipe).strip() if recipe is not None else None
            key = (recipe_id or None, candidate_id)
            if key not in canary_by_key:
                canary_by_key[key] = state
    except (OSError, ImportError, AttributeError, TypeError):
        canary_by_key = {}

    try:
        if ledger_path:
            from runtime.safety.evolution.proposal_ledger import ProposalLedger, ProposalStatus

            for record in reversed(
                ProposalLedger(ledger_path).query(
                    kind="prompt_optimizer_winner",
                    limit=5_000,
                )
            ):
                if record.status not in (
                    ProposalStatus.PROPOSED,
                    ProposalStatus.ACCEPTED,
                    ProposalStatus.APPLIED,
                    ProposalStatus.ROLLED_BACK,
                ):
                    continue
                metadata = record.metadata if isinstance(record.metadata, dict) else {}
                candidate_id = str(metadata.get("candidate_id") or "").strip() or None
                recipe = metadata.get("recipe_id")
                recipe_id = str(recipe).strip() if recipe is not None else None
                key = (recipe_id or None, candidate_id)
                if key not in proposal_by_key:
                    proposal_by_key[key] = record
    except (OSError, ImportError, AttributeError, TypeError):
        proposal_by_key = {}

    enriched: list[dict[str, Any]] = []
    for run in runs:
        row = asdict(run)
        key = (run.recipe_id or None, run.best_candidate_id or None)
        canary = canary_by_key.get(key)
        proposal = proposal_by_key.get(key)
        canary_metadata = (
            canary.metadata if canary is not None and isinstance(canary.metadata, dict) else {}
        )
        proposal_metadata = (
            proposal.metadata
            if proposal is not None and isinstance(proposal.metadata, dict)
            else {}
        )
        winner_canary_phase = canary.phase.value if canary is not None else None
        winner_proposal_status = proposal.status.value if proposal is not None else None
        lifecycle_state = (
            winner_canary_phase or winner_proposal_status or ("applied" if run.applied else None)
        )
        row.update(
            {
                "winner_proposal_id": proposal.proposal_id if proposal is not None else None,
                "winner_proposal_status": winner_proposal_status,
                "winner_proposal_kind": proposal.kind if proposal is not None else None,
                "winner_canary_key": canary.skill_name if canary is not None else None,
                "winner_canary_phase": winner_canary_phase,
                "winner_rollback_reason": (
                    canary_metadata.get("last_rollback_reason")
                    or proposal_metadata.get("last_rollback_reason")
                    or None
                ),
                "winner_lifecycle_state": lifecycle_state,
            }
        )
        enriched.append(row)
    return enriched


__all__ = [
    "enrich_run_records",
    "GepaRunRecord",
    "GepaRunStore",
    "get_default_store",
    "record_from_result",
]
