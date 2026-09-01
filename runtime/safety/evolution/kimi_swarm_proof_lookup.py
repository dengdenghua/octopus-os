"""Control-session history / proof lookup for the Kimi Swarm load-test family.

Split out of the former ~1960-line kimi_swarm_load_test.py — reads recorded
load-test sessions back out of the ``ControlSessionStore`` and ranks/combines
them into "proof" the reference scale was reached. Depends on
kimi_swarm_types.py (schema constants) and kimi_swarm_failure_taxonomy.py
(replay-derived failure summaries); used by kimi_swarm_resume_planner.py and
by kimi_swarm_load_test.py's preflight/export/next-stage orchestration.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from runtime.memory.control_sessions import ControlSessionStore

from .kimi_swarm_failure_taxonomy import _failure_summary_from_replay
from .kimi_swarm_types import _COMPOSITE_PROOF_SCHEMA, _SCHEMA, _SUMMARY_EVIDENCE_SCHEMA


def latest_kimi_swarm_load_test(
    *,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    history = kimi_swarm_load_test_history(store=store, data_dir=data_dir)
    return history[0] if history else None


def kimi_swarm_load_test_history(
    *,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    control_store = store or ControlSessionStore(
        (Path(data_dir) / "control_sessions") if data_dir is not None else None
    )
    sessions = control_store.list_sessions(surface="backend_preview", limit=limit)
    candidates = [
        session
        for session in sessions
        if isinstance(session.get("metadata"), dict)
        and (session["metadata"].get("schema") == _SCHEMA)
    ]
    summaries: list[dict[str, Any]] = []
    for session in candidates:
        summary = _summary_for_session(control_store, session)
        if summary is not None:
            summaries.append(summary)
    return sorted(
        summaries,
        key=lambda row: float((row.get("session") or {}).get("updated_at") or 0.0),
        reverse=True,
    )


def best_kimi_swarm_load_test_proof(
    *,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    control_store = store or ControlSessionStore(
        (Path(data_dir) / "control_sessions") if data_dir is not None else None
    )
    history = kimi_swarm_load_test_history(store=control_store)
    eligible = [
        row
        for row in history
        if _real_provider_stage_proof_eligible(row)
        and str(row.get("stage_id") or "") == "provider_full_reference"
        and int(row.get("agent_count") or 0) >= 300
        and int(row.get("step_count") or 0) >= 4000
        and int(row.get("successful_steps") or 0) >= 4000
        and int(row.get("actual_recorded_step_evidence_count") or 0)
        >= int(row.get("step_count") or 0)
        and bool(row.get("replay_evidence_verified"))
        and int(row.get("failed_steps") or 0) == 0
        and bool(row.get("meets_kimi_reference"))
    ]
    composite = _best_composite_full_reference_proof(
        history=history,
        control_store=control_store,
    )
    candidates = eligible + ([composite] if composite is not None else [])
    if not candidates:
        return None
    return max(candidates, key=_proof_rank)


def latest_successful_stage_proof(
    *,
    provider_id: str,
    model: str,
    requested_agent_count: int,
    requested_step_count: int,
    stage_id: str,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    history = kimi_swarm_load_test_history(store=store, data_dir=data_dir)
    matches = [
        row
        for row in history
        if _real_provider_stage_proof_eligible(row)
        and str(row.get("provider_id") or "") == str(provider_id or "")
        and str(row.get("model") or "") == str(model or "")
        and str(row.get("stage_id") or "") == str(stage_id or "")
        and int(row.get("requested_agent_count") or 0) == int(requested_agent_count)
        and int(row.get("requested_step_count") or 0) == int(requested_step_count)
        and int(row.get("failed_steps") or 0) == 0
        and int(row.get("successful_steps") or 0) == int(row.get("step_count") or 0)
        and int(row.get("actual_recorded_step_evidence_count") or 0)
        >= int(row.get("step_count") or 0)
        and bool(row.get("replay_evidence_verified"))
    ]
    if not matches:
        return None
    return max(matches, key=lambda row: float((row.get("session") or {}).get("updated_at") or 0.0))


def _best_composite_full_reference_proof(
    *,
    history: list[dict[str, Any]],
    control_store: ControlSessionStore,
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for source in history:
        if (
            not _real_provider_stage_proof_eligible(source)
            or str(source.get("stage_id") or "") != "provider_full_reference"
            or int(source.get("agent_count") or 0) < 300
            or int(source.get("step_count") or 0) < 4000
            or not bool(source.get("meets_kimi_reference"))
        ):
            continue
        composite = _composite_proof_for_source(
            source,
            history=history,
            control_store=control_store,
        )
        if composite is not None:
            candidates.append(composite)
    if not candidates:
        return None
    return max(candidates, key=_proof_rank)


def _composite_proof_for_source(
    source: dict[str, Any],
    *,
    history: list[dict[str, Any]],
    control_store: ControlSessionStore,
) -> dict[str, Any] | None:
    source_session_id = str(source.get("session_id") or "")
    requested_step_count = int(source.get("requested_step_count") or source.get("step_count") or 0)
    if not source_session_id or requested_step_count <= 0:
        return None
    session_ids = [source_session_id]
    resume_rows = _resume_rows_for_source(history=history, source=source)
    session_ids.extend(str(row.get("session_id") or "") for row in resume_rows)
    success_by_step, failed_by_step, total_step_evidence = _step_coverage_for_sessions(
        control_store,
        session_ids,
        requested_step_count=requested_step_count,
    )
    missing = sorted(set(range(requested_step_count)) - set(success_by_step))
    if missing:
        return None
    resume_session_ids = [
        str(row.get("session_id") or "") for row in resume_rows if str(row.get("session_id") or "")
    ]
    updated_at = max(
        [float((source.get("session") or {}).get("updated_at") or 0.0)]
        + [float((row.get("session") or {}).get("updated_at") or 0.0) for row in resume_rows],
    )
    return {
        "schema": _COMPOSITE_PROOF_SCHEMA,
        "composite": True,
        "provider_backed": True,
        "provider_id": source.get("provider_id"),
        "model": source.get("model"),
        "session_id": source_session_id,
        "source_session_id": source_session_id,
        "resume_session_ids": resume_session_ids,
        "source_session_ids": [source_session_id, *resume_session_ids],
        "stage_id": "provider_full_reference",
        "agent_count": int(source.get("agent_count") or 0),
        "step_count": requested_step_count,
        "requested_agent_count": int(source.get("requested_agent_count") or 0),
        "requested_step_count": requested_step_count,
        "successful_steps": requested_step_count,
        "failed_steps": 0,
        "actual_recorded_step_evidence_count": len(success_by_step),
        "raw_recorded_step_evidence_count": total_step_evidence,
        "replay_evidence_verified": True,
        "meets_kimi_reference": (
            int(source.get("agent_count") or 0) >= 300 and requested_step_count >= 4000
        ),
        "total_input_tokens": sum(
            int(row.get("total_input_tokens") or 0) for row in [source, *resume_rows]
        ),
        "total_output_tokens": sum(
            int(row.get("total_output_tokens") or 0) for row in [source, *resume_rows]
        ),
        "estimated_max_tokens": sum(
            int(row.get("estimated_max_tokens") or 0) for row in [source, *resume_rows]
        ),
        "updated_at": updated_at,
        "coverage": {
            "schema": "echo.kimi_swarm_composite_coverage.v1",
            "covered_step_count": len(success_by_step),
            "missing_step_count": 0,
            "source_successful_steps": int(source.get("successful_steps") or 0),
            "resume_successful_steps": sum(
                int(row.get("successful_steps") or 0) for row in resume_rows
            ),
            "resume_failed_step_count": len(failed_by_step),
        },
    }


def _resume_rows_for_source(
    *,
    history: list[dict[str, Any]],
    source: dict[str, Any],
) -> list[dict[str, Any]]:
    source_session_id = str(source.get("session_id") or "")
    rows = [
        row
        for row in history
        if _real_provider_stage_proof_eligible(row)
        and str(row.get("stage_id") or "") == "provider_full_reference_resume"
        and str(row.get("resume_from_session_id") or "") == source_session_id
        and str(row.get("provider_id") or "") == str(source.get("provider_id") or "")
        and str(row.get("model") or "") == str(source.get("model") or "")
    ]
    rows.sort(key=lambda row: float((row.get("session") or {}).get("updated_at") or 0.0))
    return rows


def _step_coverage_for_sessions(
    control_store: ControlSessionStore,
    session_ids: list[str],
    *,
    requested_step_count: int,
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], int]:
    success_by_step: dict[int, dict[str, Any]] = {}
    failed_by_step: dict[int, dict[str, Any]] = {}
    total_step_evidence = 0
    for session_id in session_ids:
        if not session_id:
            continue
        try:
            replay = control_store.replay(session_id, limit=max(5000, requested_step_count + 10))
        except (KeyError, ValueError):
            continue
        for item in replay.get("evidence") or []:
            if not isinstance(item, dict) or item.get("action") != "kimi_swarm_load_step":
                continue
            detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
            try:
                index = int(detail.get("step_index"))
            except (TypeError, ValueError):
                continue
            if index < 0 or index >= requested_step_count:
                continue
            total_step_evidence += 1
            if bool(detail.get("ok")):
                success_by_step[index] = {
                    "session_id": session_id,
                    "evidence_id": item.get("evidence_id"),
                    "step_index": index,
                }
                failed_by_step.pop(index, None)
            elif index not in success_by_step:
                failed_by_step[index] = {
                    "session_id": session_id,
                    "evidence_id": item.get("evidence_id"),
                    "step_index": index,
                    "error": detail.get("error"),
                }
    return success_by_step, failed_by_step, total_step_evidence


def _proof_rank(row: dict[str, Any]) -> tuple[int, int, float]:
    return (
        int(row.get("successful_steps") or 0),
        int(row.get("agent_count") or 0),
        float(row.get("updated_at") or (row.get("session") or {}).get("updated_at") or 0.0),
    )


def _real_provider_stage_proof_eligible(row: dict[str, Any]) -> bool:
    if not bool(row.get("provider_backed")):
        return False
    estimated = int(row.get("estimated_max_tokens") or 0)
    total_output = int(row.get("total_output_tokens") or 0)
    return (
        int(row.get("per_step_output_budget") or 0) > 0
        and estimated > 0
        and total_output <= estimated
    )


def _summary_for_session(
    control_store: ControlSessionStore,
    session: dict[str, Any],
) -> dict[str, Any] | None:
    metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
    summary = metadata.get("last_kimi_swarm_load_test")
    replay: dict[str, Any] | None = None
    if not isinstance(summary, dict):
        try:
            replay = control_store.replay(str(session["session_id"]), limit=5000)
        except (KeyError, ValueError):
            return None
        for evidence in reversed(replay.get("evidence") or []):
            detail = evidence.get("detail") if isinstance(evidence, dict) else {}
            if isinstance(detail, dict) and detail.get("schema") == _SUMMARY_EVIDENCE_SCHEMA:
                summary = {k: v for k, v in detail.items() if k != "schema"}
                break
    if not isinstance(summary, dict):
        return None
    if replay is None:
        try:
            replay = control_store.replay(str(session["session_id"]), limit=5000)
        except (KeyError, ValueError):
            replay = {}
    actual_step_evidence_count = _actual_step_evidence_count(replay)
    failure_summary = summary.get("failure_summary")
    if not isinstance(failure_summary, dict):
        failure_summary = _failure_summary_from_replay(replay)
    return {
        "schema": _SUMMARY_EVIDENCE_SCHEMA,
        **summary,
        "failure_summary": failure_summary,
        "quota_limited": bool(failure_summary.get("provider_quota_limited")),
        "actual_recorded_step_evidence_count": actual_step_evidence_count,
        "replay_evidence_verified": (
            actual_step_evidence_count >= int(summary.get("step_count") or 0)
        ),
        "session": session,
    }


def _actual_step_evidence_count(replay: dict[str, Any] | None) -> int:
    if not isinstance(replay, dict):
        return 0
    return sum(
        1
        for item in replay.get("evidence") or []
        if isinstance(item, dict) and item.get("action") == "kimi_swarm_load_step"
    )


def _proof_without_session(proof: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in proof.items() if key not in {"session"}}


def _digest_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


__all__ = [
    "best_kimi_swarm_load_test_proof",
    "kimi_swarm_load_test_history",
    "latest_kimi_swarm_load_test",
    "latest_successful_stage_proof",
]
