"""Resume-plan computation for the Kimi Swarm load-test family.

Split out of the former ~1960-line kimi_swarm_load_test.py — given a failed
``provider_full_reference`` run, figures out which steps are still missing
and builds the payload to resume just those. Depends on kimi_swarm_types.py
and kimi_swarm_proof_lookup.py (history/step-coverage); used by
kimi_swarm_load_run.py (turning a resume config into execution indices) and
kimi_swarm_load_test.py's preflight/next-stage orchestration.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.memory.control_sessions import ControlSessionStore

from .kimi_swarm_proof_lookup import (
    _resume_rows_for_source,
    _step_coverage_for_sessions,
    kimi_swarm_load_test_history,
)
from .kimi_swarm_types import (
    _DEFAULT_AGENT_COUNT,
    _DEFAULT_MAX_CONCURRENCY,
    _DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP,
    _DEFAULT_REFERENCE_MODEL,
    _DEFAULT_REFERENCE_PROVIDER_ID,
    _DEFAULT_RESUME_CHUNK_CONCURRENCY,
    _DEFAULT_RESUME_CHUNK_STEP_COUNT,
    _DEFAULT_STEP_COUNT,
    _PREFLIGHT_SCHEMA,
    _RESUME_PLAN_SCHEMA,
    KimiSwarmLoadTestConfig,
)


def build_kimi_swarm_resume_plan(
    *,
    provider_id: str = _DEFAULT_REFERENCE_PROVIDER_ID,
    model: str = _DEFAULT_REFERENCE_MODEL,
    agent_count: int = _DEFAULT_AGENT_COUNT,
    step_count: int = _DEFAULT_STEP_COUNT,
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a replay-backed plan to resume a failed full-reference run."""
    requested_agent_count = max(1, int(agent_count))
    requested_step_count = max(1, int(step_count))
    requested_max_concurrency = max(
        1,
        min(int(max_concurrency), requested_agent_count, requested_step_count),
    )
    control_store = store or ControlSessionStore(
        (Path(data_dir) / "control_sessions") if data_dir is not None else None
    )
    candidate = _latest_failed_full_reference_run(
        provider_id=provider_id,
        model=model,
        requested_agent_count=requested_agent_count,
        requested_step_count=requested_step_count,
        store=control_store,
    )
    if candidate is None:
        return {
            "schema": _RESUME_PLAN_SCHEMA,
            "ready": False,
            "reason": "no failed provider_full_reference run to resume",
            "provider_id": provider_id,
            "model": model,
            "source_session_id": "",
            "successful_step_count": 0,
            "remaining_step_count": 0,
            "remaining_step_ranges": [],
            "recommended_payload": None,
        }
    session_id = str(candidate.get("session_id") or "")
    history = kimi_swarm_load_test_history(store=control_store)
    resume_rows = _resume_rows_for_source(history=history, source=candidate)
    resume_session_ids = [
        str(row.get("session_id") or "") for row in resume_rows if str(row.get("session_id") or "")
    ]
    success_by_step, failed_by_step, raw_step_evidence_count = _step_coverage_for_sessions(
        control_store,
        [session_id, *resume_session_ids],
        requested_step_count=requested_step_count,
    )
    successful_indices = set(success_by_step)
    failed_indices = set(failed_by_step)
    all_indices = set(range(requested_step_count))
    remaining_indices = sorted((all_indices - successful_indices) | failed_indices)
    per_step_budget = _DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP
    payload = None
    chunk_payload = None
    if remaining_indices:
        payload = {
            "provider_id": provider_id,
            "model": model,
            "agent_count": requested_agent_count,
            "step_count": requested_step_count,
            "max_concurrency": requested_max_concurrency,
            "real_provider": True,
            "confirm_real_provider": True,
            "record_every_step": True,
            "stage_id": "provider_full_reference_resume",
            "resume_from_session_id": session_id,
            "resume_step_count": len(remaining_indices),
            "resume_step_ranges": _compact_ranges(remaining_indices),
            "max_provider_calls": len(remaining_indices),
            "estimated_max_tokens": len(remaining_indices) * per_step_budget,
        }
        chunk_indices = remaining_indices[
            : min(len(remaining_indices), _DEFAULT_RESUME_CHUNK_STEP_COUNT)
        ]
        chunk_payload = {
            **payload,
            "max_concurrency": min(
                requested_max_concurrency,
                _DEFAULT_RESUME_CHUNK_CONCURRENCY,
                len(chunk_indices),
            ),
            "resume_step_count": len(chunk_indices),
            "resume_step_ranges": _compact_ranges(chunk_indices),
            "max_provider_calls": len(chunk_indices),
            "estimated_max_tokens": len(chunk_indices) * per_step_budget,
            "chunked": len(chunk_indices) < len(remaining_indices),
            "total_remaining_step_count": len(remaining_indices),
        }
    failure_summary = candidate.get("failure_summary")
    return {
        "schema": _RESUME_PLAN_SCHEMA,
        "ready": bool(remaining_indices),
        "reason": "" if remaining_indices else "source run already has full success coverage",
        "provider_id": provider_id,
        "model": model,
        "source_session_id": session_id,
        "source_stage_id": candidate.get("stage_id"),
        "partial_resume_session_ids": resume_session_ids,
        "requested": {
            "agent_count": requested_agent_count,
            "step_count": requested_step_count,
            "max_concurrency": requested_max_concurrency,
        },
        "successful_step_count": len(successful_indices),
        "failed_step_count": len(failed_indices),
        "covered_step_count": len(successful_indices),
        "remaining_step_count": len(remaining_indices),
        "remaining_step_ranges": _compact_ranges(remaining_indices),
        "raw_step_evidence_count": raw_step_evidence_count,
        "failure_summary": failure_summary if isinstance(failure_summary, dict) else None,
        "recommended_payload": payload,
        "recommended_chunk_payload": chunk_payload,
    }


def _latest_failed_full_reference_run(
    *,
    provider_id: str,
    model: str,
    requested_agent_count: int,
    requested_step_count: int,
    store: ControlSessionStore,
) -> dict[str, Any] | None:
    for row in kimi_swarm_load_test_history(store=store):
        if (
            bool(row.get("provider_backed"))
            and str(row.get("provider_id") or "") == str(provider_id or "")
            and str(row.get("model") or "") == str(model or "")
            and str(row.get("stage_id") or "") == "provider_full_reference"
            and int(row.get("requested_agent_count") or 0) == int(requested_agent_count)
            and int(row.get("requested_step_count") or 0) == int(requested_step_count)
            and int(row.get("failed_steps") or 0) > 0
        ):
            return row
    return None


def _compact_ranges(indices: list[int]) -> list[dict[str, int]]:
    if not indices:
        return []
    ranges: list[dict[str, int]] = []
    start = prev = int(indices[0])
    for raw in indices[1:]:
        current = int(raw)
        if current == prev + 1:
            prev = current
            continue
        ranges.append({"start": start, "end": prev, "count": prev - start + 1})
        start = prev = current
    ranges.append({"start": start, "end": prev, "count": prev - start + 1})
    return ranges


def _resume_execution_indices(
    config: KimiSwarmLoadTestConfig,
    *,
    control_store: ControlSessionStore,
    requested_step_count: int,
) -> list[int]:
    indices = _indices_from_ranges(
        list(config.resume_step_ranges),
        upper_bound=requested_step_count,
    )
    if indices:
        return indices
    source_session_id = str(config.resume_from_session_id or "").strip()
    if not source_session_id:
        raise ValueError("resume_from_session_id is required for provider_full_reference_resume")
    try:
        replay = control_store.replay(source_session_id, limit=max(5000, requested_step_count + 10))
    except (KeyError, ValueError) as exc:
        raise ValueError(f"cannot load resume source session: {source_session_id}") from exc
    successful: set[int] = set()
    failed: set[int] = set()
    for item in replay.get("evidence") or []:
        if not isinstance(item, dict) or item.get("action") != "kimi_swarm_load_step":
            continue
        detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
        try:
            index = int(detail.get("step_index"))
        except (TypeError, ValueError):
            continue
        if bool(detail.get("ok")):
            successful.add(index)
            failed.discard(index)
        elif index not in successful:
            failed.add(index)
    return sorted((set(range(requested_step_count)) - successful) | failed)


def _indices_from_ranges(
    ranges: list[dict[str, int]],
    *,
    upper_bound: int,
) -> list[int]:
    out: set[int] = set()
    for item in ranges:
        if not isinstance(item, dict):
            continue
        try:
            start = int(item.get("start"))
            end = int(item.get("end"))
        except (TypeError, ValueError):
            continue
        start = max(0, start)
        end = min(max(0, upper_bound - 1), end)
        if end < start:
            continue
        out.update(range(start, end + 1))
    return sorted(out)


def _resume_preflight_from_plan(
    plan: dict[str, Any],
    *,
    provider_configured: bool | None,
) -> dict[str, Any]:
    payload = plan.get("recommended_chunk_payload") or plan.get("recommended_payload")
    payload = payload if isinstance(payload, dict) else {}
    selected_steps = int(payload.get("resume_step_count") or plan.get("remaining_step_count") or 0)
    configured = True if provider_configured is None else bool(provider_configured)
    checks = [
        {
            "id": "provider_configured",
            "title": "A custom model router is configured",
            "passed": configured,
            "value": configured,
            "blocking": True,
        },
        {
            "id": "provider_call_budget",
            "title": "Provider call budget covers selected resume steps",
            "passed": int(payload.get("max_provider_calls") or 0) >= selected_steps,
            "value": int(payload.get("max_provider_calls") or 0),
            "required": selected_steps,
            "blocking": True,
        },
        {
            "id": "token_budget",
            "title": "Token budget covers selected resume steps",
            "passed": int(payload.get("estimated_max_tokens") or 0) >= selected_steps,
            "value": int(payload.get("estimated_max_tokens") or 0),
            "required": selected_steps,
            "blocking": True,
        },
    ]
    blocking = [check for check in checks if check["blocking"] and not check["passed"]]
    return {
        "schema": _PREFLIGHT_SCHEMA,
        "ready": not blocking,
        "provider_ready": not blocking,
        "reference_ready": True,
        "mode": "real_provider_resume",
        "model": plan.get("model"),
        "provider_id": plan.get("provider_id"),
        "agent_count": (plan.get("requested") or {}).get("agent_count"),
        "step_count": (plan.get("requested") or {}).get("step_count"),
        "max_concurrency": (plan.get("requested") or {}).get("max_concurrency"),
        "selected_stage": {
            "id": "provider_full_reference_resume",
            "title": "Provider full Kimi-reference resume",
            "step_count": selected_steps,
            "requires_confirmation": True,
        },
        "resume_plan": {
            "schema": plan.get("schema"),
            "source_session_id": plan.get("source_session_id"),
            "remaining_step_count": plan.get("remaining_step_count"),
            "selected_step_count": selected_steps,
            "remaining_step_ranges": plan.get("remaining_step_ranges"),
            "selected_step_ranges": payload.get("resume_step_ranges"),
            "chunked": payload.get("chunked", False),
        },
        "checks": checks,
        "blocking_failures": blocking,
        "next_action": (
            "Run the resume payload."
            if not blocking
            else "Resolve blocking resume preflight checks before running."
        ),
    }


__all__ = ["build_kimi_swarm_resume_plan"]
