"""Kimi Swarm reference load-test / certification pipeline — orchestrator.

Proves the product can reach Kimi Agent Swarm's public reference scale
(300 agents / 4000 coordinated tool calls) with real, replayable evidence
rather than a marketing claim: a dry-run mode exercises the full
control-session replay/metrics/pagination/export path for free, and a
``real_provider=True`` mode drives the same path against a live model
provider, staged (canary → ramp → full reference) with resumable partial
runs so a quota/rate-limit failure never forces a full 4000-step restart.

This module is the public entry point / orchestrator. The implementation is
split across focused siblings (each independently importable and tested):

  kimi_swarm_types.py             configs, schema constants, stage resolution
  kimi_swarm_failure_taxonomy.py  provider-error classification
  kimi_swarm_proof_lookup.py      control-session history / proof ranking
  kimi_swarm_resume_planner.py    "what's left to run" resume-plan math
  kimi_swarm_load_run.py          the actual concurrent step-execution engine

Kept here: preflight (readiness checks before spending tokens), the quota
probe, proof-bundle export, and next-stage recommendation — the functions
that compose the siblings into the operator-facing workflow.

NOTE for anyone editing this file's public surface: kimi_swarm_certification.py
and agent_benchmark.py both certify features by grepping this file (and its
siblings) for literal strings (schema constants, function/test names) —
see their ``KimiSwarmEvidenceCheck``/``AgentBenchmarkCase`` ``paths``/
``required_terms`` lists. If you move a term-bearing symbol to a new
sibling file, add that file to the relevant check's ``paths`` tuple, or the
certification will report a false regression despite nothing breaking.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from runtime.memory.control_sessions import ControlSessionStore
from runtime.platform.models.llm import Message, ModelRequest
from runtime.platform.process.paths import app_paths

from .kimi_swarm_failure_taxonomy import _failure_category
from .kimi_swarm_load_run import run_kimi_swarm_load_test
from .kimi_swarm_proof_lookup import (
    _digest_json,
    _proof_without_session,
    best_kimi_swarm_load_test_proof,
    kimi_swarm_load_test_history,
    latest_kimi_swarm_load_test,
    latest_successful_stage_proof,
)
from .kimi_swarm_resume_planner import (
    _indices_from_ranges,
    _resume_preflight_from_plan,
    build_kimi_swarm_resume_plan,
)
from .kimi_swarm_types import (
    _DEFAULT_AGENT_COUNT,
    _DEFAULT_MAX_CONCURRENCY,
    _DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP,
    _DEFAULT_REFERENCE_MODEL,
    _DEFAULT_REFERENCE_PROVIDER_ID,
    _DEFAULT_STEP_COUNT,
    _NEXT_STAGE_SCHEMA,
    _PREFLIGHT_SCHEMA,
    _PROOF_BUNDLE_SCHEMA,
    _QUOTA_PROBE_SCHEMA,
    KimiSwarmLoadTestConfig,
    KimiSwarmQuotaProbeConfig,
    ProviderCaller,
    _normalize_counts,
    _previous_stage_id,
    _resolve_stage,
    _stage_plan,
)

__all__ = [
    "KimiSwarmLoadTestConfig",
    "KimiSwarmQuotaProbeConfig",
    "best_kimi_swarm_load_test_proof",
    "build_kimi_swarm_load_test_preflight",
    "build_kimi_swarm_resume_plan",
    "export_kimi_swarm_proof_bundle",
    "kimi_swarm_load_test_history",
    "latest_successful_stage_proof",
    "latest_kimi_swarm_load_test",
    "recommend_kimi_swarm_next_stage",
    "run_kimi_swarm_load_test",
    "run_kimi_swarm_quota_probe",
]


def build_kimi_swarm_load_test_preflight(
    *,
    config: KimiSwarmLoadTestConfig | None = None,
    provider_configured: bool | None = None,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a no-side-effect readiness report for a Kimi-scale load test."""
    cfg = config or KimiSwarmLoadTestConfig()
    agent_count, step_count, max_concurrency = _normalize_counts(cfg)
    provider_needed = bool(cfg.real_provider)
    configured = (not provider_needed) if provider_configured is None else bool(provider_configured)
    selected_stage = _resolve_stage(
        config=cfg,
        requested_agent_count=agent_count,
        requested_step_count=step_count,
        requested_max_concurrency=max_concurrency,
    )
    selected_step_count = int(selected_stage["step_count"])
    if selected_stage.get("id") == "provider_full_reference_resume":
        selected_step_count = len(
            _indices_from_ranges(
                list(cfg.resume_step_ranges),
                upper_bound=step_count,
            )
        )
        if selected_step_count <= 0 and cfg.resume_from_session_id:
            selected_step_count = 1
    previous_stage = _previous_stage_id(str(selected_stage["id"]))
    previous_stage_ready = True
    previous_stage_proof: dict[str, Any] | None = None
    if provider_needed and previous_stage:
        previous_stage_proof = latest_successful_stage_proof(
            provider_id=cfg.provider_id,
            model=cfg.model,
            requested_agent_count=agent_count,
            requested_step_count=step_count,
            stage_id=previous_stage,
            store=store,
            data_dir=data_dir,
        )
        previous_stage_ready = previous_stage_proof is not None
    checks = [
        {
            "id": "agent_count_positive",
            "title": "Agent count is positive",
            "passed": agent_count >= 1,
            "value": agent_count,
            "blocking": True,
        },
        {
            "id": "step_count_positive",
            "title": "Step count is positive",
            "passed": step_count >= 1,
            "value": step_count,
            "blocking": True,
        },
        {
            "id": "bounded_concurrency",
            "title": "Concurrency is bounded for provider safety",
            "passed": max_concurrency <= (64 if provider_needed else step_count),
            "value": max_concurrency,
            "limit": 64 if provider_needed else step_count,
            "blocking": True,
        },
        {
            "id": "real_provider_confirmed",
            "title": "Real provider run is explicitly confirmed",
            "passed": (not provider_needed) or bool(cfg.confirm_real_provider),
            "value": bool(cfg.confirm_real_provider),
            "blocking": provider_needed,
        },
        {
            "id": "provider_configured",
            "title": "A custom model router is configured",
            "passed": (not provider_needed) or configured,
            "value": configured,
            "blocking": provider_needed,
        },
        {
            "id": "provider_call_budget",
            "title": "Provider call budget covers every step",
            "passed": (not provider_needed)
            or int(cfg.max_provider_calls or 0) >= selected_step_count,
            "value": int(cfg.max_provider_calls or 0),
            "required": selected_step_count,
            "blocking": provider_needed,
        },
        {
            "id": "token_budget",
            "title": "Token budget is explicitly declared",
            "passed": (not provider_needed)
            or int(cfg.estimated_max_tokens or 0) >= selected_step_count,
            "value": int(cfg.estimated_max_tokens or 0),
            "required": selected_step_count,
            "blocking": provider_needed,
        },
        {
            "id": "kimi_reference_size",
            "title": "Requested run reaches Kimi reference scale",
            "passed": agent_count >= 300 and step_count >= 4000,
            "value": {"agent_count": agent_count, "step_count": step_count},
            "required": {"agent_count": 300, "step_count": 4000},
            "blocking": False,
        },
        {
            "id": "previous_stage_ready",
            "title": "Previous provider stage succeeded",
            "passed": previous_stage_ready,
            "value": previous_stage,
            "blocking": provider_needed and bool(previous_stage),
        },
    ]
    blocking_failures = [
        check for check in checks if check["blocking"] and not bool(check["passed"])
    ]
    stage_plan = _stage_plan(
        agent_count=agent_count,
        step_count=step_count,
        max_concurrency=max_concurrency,
        real_provider=provider_needed,
    )
    return {
        "schema": _PREFLIGHT_SCHEMA,
        "ready": not blocking_failures,
        "provider_ready": (not provider_needed)
        or (
            bool(cfg.confirm_real_provider)
            and configured
            and int(cfg.max_provider_calls or 0) >= selected_step_count
            and int(cfg.estimated_max_tokens or 0) >= selected_step_count
            and max_concurrency <= 64
            and previous_stage_ready
        ),
        "reference_ready": agent_count >= 300 and step_count >= 4000,
        "mode": "real_provider" if provider_needed else "dry_run",
        "model": cfg.model,
        "provider_id": cfg.provider_id,
        "agent_count": agent_count,
        "step_count": step_count,
        "max_concurrency": max_concurrency,
        "max_provider_calls": int(cfg.max_provider_calls or 0),
        "estimated_max_tokens": int(cfg.estimated_max_tokens or 0),
        "selected_stage": selected_stage,
        "previous_stage": previous_stage,
        "previous_stage_ready": previous_stage_ready,
        "previous_stage_proof": previous_stage_proof,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "stage_plan": stage_plan,
        "next_action": (
            "Run the load test."
            if not blocking_failures
            else "Resolve blocking preflight checks before running the load test."
        ),
    }


def run_kimi_swarm_quota_probe(
    *,
    config: KimiSwarmQuotaProbeConfig | None = None,
    store: ControlSessionStore | None = None,
    provider_caller: ProviderCaller | None = None,
) -> dict[str, Any]:
    cfg = config or KimiSwarmQuotaProbeConfig()
    if not cfg.confirm_real_provider:
        raise ValueError("confirm_real_provider=True is required for quota probes")
    if provider_caller is None:
        raise ValueError("quota probe requires provider_caller")
    control_store = store or ControlSessionStore(app_paths().data_dir / "control_sessions")
    session = control_store.upsert_session(
        session_id=cfg.session_id,
        owner_id="kimi_swarm_quota_probe",
        owner_label="Kimi Swarm Quota Probe",
        surface="backend_preview",
        target_id="agent-collaboration",
        status="running",
        metadata={
            "schema": _QUOTA_PROBE_SCHEMA,
            "provider_id": cfg.provider_id,
            "model": cfg.model,
            "confirm_real_provider": True,
            "max_tokens": max(1, int(cfg.max_tokens)),
        },
    )
    action = control_store.append_action(
        cfg.session_id,
        action_id=f"{cfg.session_id}:probe",
        action_type="kimi_swarm_quota_probe",
        status="running",
        descriptor={
            "schema": _QUOTA_PROBE_SCHEMA,
            "provider_id": cfg.provider_id,
            "model": cfg.model,
            "max_tokens": max(1, int(cfg.max_tokens)),
        },
    )
    started = time.perf_counter()
    ok = False
    error = ""
    input_tokens = 0
    output_tokens = 0
    output_preview = ""
    try:
        response = provider_caller(
            ModelRequest(
                model=cfg.model,
                system_provider=cfg.provider_id,
                max_tokens=max(1, int(cfg.max_tokens)),
                messages=[
                    Message(
                        role="user",
                        content='Quota probe. Reply with compact JSON: {"ok":true}.',
                    )
                ],
            )
        )
        ok = True
        output_preview = str(getattr(response, "text", "") or "")[:240]
        input_tokens = int(getattr(response, "input_tokens", 0) or 0)
        output_tokens = int(getattr(response, "output_tokens", 0) or 0)
    except Exception as exc:  # noqa: BLE001 - probe reports provider errors
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    category = "" if ok else _failure_category(error)
    result = {
        "schema": _QUOTA_PROBE_SCHEMA,
        "session_id": cfg.session_id,
        "provider_id": cfg.provider_id,
        "model": cfg.model,
        "ok": ok,
        "can_resume_provider_load_test": ok,
        "provider_quota_limited": category == "provider_quota_limit",
        "provider_rate_limited": category == "provider_rate_limit",
        "failure_category": category,
        "error": error,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "output_preview": output_preview,
        "elapsed_ms": elapsed_ms,
        "control_session_replay": {
            "session_id": cfg.session_id,
            "action_id": action["action_id"],
            "surface": session["surface"],
        },
    }
    control_store.append_evidence(
        cfg.session_id,
        action_id=str(action["action_id"]),
        evidence_id=f"{cfg.session_id}:result",
        kind="result",
        action="kimi_swarm_quota_probe",
        ok=ok,
        summary=(
            "Kimi quota probe succeeded"
            if ok
            else f"Kimi quota probe failed: {category or 'provider_error'}"
        ),
        detail=result,
    )
    control_store.update_action(
        cfg.session_id,
        str(action["action_id"]),
        status="done" if ok else "failed",
        result=result,
        error=error,
    )
    control_store.set_session_state(
        cfg.session_id,
        status="idle" if ok else "paused",
        metadata={"last_kimi_swarm_quota_probe": result},
    )
    return result


def export_kimi_swarm_proof_bundle(
    *,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Build a tamper-evident proof bundle for the best full-reference run."""
    control_store = store or ControlSessionStore(
        (Path(data_dir) / "control_sessions") if data_dir is not None else None
    )
    proof = best_kimi_swarm_load_test_proof(store=control_store)
    if proof is None:
        return {
            "schema": _PROOF_BUNDLE_SCHEMA,
            "ready": False,
            "reason": "no provider_full_reference proof with complete replay evidence",
            "proof": None,
            "step_evidence_count": 0,
            "timeline_count": 0,
            "sha256": "",
        }
    if bool(proof.get("composite")):
        return _export_composite_proof_bundle(control_store, proof)
    session_id = str(proof["session_id"])
    replay = control_store.replay(session_id, limit=5000)
    step_evidence = [
        item
        for item in replay.get("evidence") or []
        if isinstance(item, dict) and item.get("action") == "kimi_swarm_load_step"
    ]
    timeline_items = list((replay.get("timeline") or {}).get("items") or [])
    payload = {
        "schema": _PROOF_BUNDLE_SCHEMA,
        "ready": True,
        "proof": _proof_without_session(proof),
        "session": replay.get("session"),
        "action_count": len(replay.get("actions") or []),
        "step_evidence_count": len(step_evidence),
        "timeline_count": len(timeline_items),
        "timeline_digest": _digest_json(timeline_items),
        "step_evidence_digest": _digest_json(step_evidence),
        "replay_href": f"/api/control-sessions/{session_id}/replay",
        "timeline_href": f"/api/control-sessions/{session_id}/timeline",
    }
    return {
        **payload,
        "sha256": _digest_json(payload),
    }


def _export_composite_proof_bundle(
    control_store: ControlSessionStore,
    proof: dict[str, Any],
) -> dict[str, Any]:
    session_ids = [
        str(session_id)
        for session_id in proof.get("source_session_ids") or []
        if str(session_id or "")
    ]
    replays: list[dict[str, Any]] = []
    all_step_evidence: list[dict[str, Any]] = []
    all_timeline_items: list[dict[str, Any]] = []
    action_count = 0
    for session_id in session_ids:
        replay = control_store.replay(session_id, limit=5000)
        replays.append(replay)
        action_count += len(replay.get("actions") or [])
        all_step_evidence.extend(
            item
            for item in replay.get("evidence") or []
            if isinstance(item, dict) and item.get("action") == "kimi_swarm_load_step"
        )
        all_timeline_items.extend(list((replay.get("timeline") or {}).get("items") or []))
    payload = {
        "schema": _PROOF_BUNDLE_SCHEMA,
        "ready": True,
        "proof": _proof_without_session(proof),
        "composite": True,
        "sessions": [replay.get("session") for replay in replays],
        "action_count": action_count,
        "step_evidence_count": int(proof.get("actual_recorded_step_evidence_count") or 0),
        "raw_step_evidence_count": len(all_step_evidence),
        "timeline_count": len(all_timeline_items),
        "timeline_digest": _digest_json(all_timeline_items),
        "step_evidence_digest": _digest_json(all_step_evidence),
        "replay_hrefs": [
            f"/api/control-sessions/{session_id}/replay" for session_id in session_ids
        ],
        "timeline_hrefs": [
            f"/api/control-sessions/{session_id}/timeline" for session_id in session_ids
        ],
    }
    return {
        **payload,
        "sha256": _digest_json(payload),
    }


def recommend_kimi_swarm_next_stage(
    *,
    provider_id: str = _DEFAULT_REFERENCE_PROVIDER_ID,
    model: str = _DEFAULT_REFERENCE_MODEL,
    agent_count: int = _DEFAULT_AGENT_COUNT,
    step_count: int = _DEFAULT_STEP_COUNT,
    max_concurrency: int = _DEFAULT_MAX_CONCURRENCY,
    provider_configured: bool | None = None,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return the next safe stage payload needed to reach full proof."""
    requested_agent_count = max(1, int(agent_count))
    requested_step_count = max(1, int(step_count))
    requested_max_concurrency = max(
        1,
        min(int(max_concurrency), requested_agent_count, requested_step_count),
    )
    stages = ("provider_canary", "provider_ramp", "provider_full_reference")
    proofs = {
        stage: latest_successful_stage_proof(
            provider_id=provider_id,
            model=model,
            requested_agent_count=requested_agent_count,
            requested_step_count=requested_step_count,
            stage_id=stage,
            store=store,
            data_dir=data_dir,
        )
        for stage in stages
    }
    if proofs["provider_full_reference"] is None:
        best_full = best_kimi_swarm_load_test_proof(store=store, data_dir=data_dir)
        if (
            isinstance(best_full, dict)
            and str(best_full.get("provider_id") or "") == str(provider_id or "")
            and str(best_full.get("model") or "") == str(model or "")
            and int(best_full.get("requested_agent_count") or 0) == requested_agent_count
            and int(best_full.get("requested_step_count") or 0) == requested_step_count
        ):
            proofs["provider_full_reference"] = best_full
    if proofs["provider_full_reference"] is not None:
        next_stage = "complete"
    elif proofs["provider_ramp"] is not None:
        next_stage = "provider_full_reference"
    elif proofs["provider_canary"] is not None:
        next_stage = "provider_ramp"
    else:
        next_stage = "provider_canary"
    blocking_failure = (
        None
        if next_stage == "complete"
        else _latest_stage_blocking_failure(
            provider_id=provider_id,
            model=model,
            requested_agent_count=requested_agent_count,
            requested_step_count=requested_step_count,
            stage_id=next_stage,
            store=store,
            data_dir=data_dir,
        )
    )
    preflight: dict[str, Any] | None = None
    resume_plan: dict[str, Any] | None = None
    if next_stage == "complete":
        payload = None
    else:
        stage_config = KimiSwarmLoadTestConfig(
            provider_id=provider_id,
            model=model,
            agent_count=requested_agent_count,
            step_count=requested_step_count,
            max_concurrency=requested_max_concurrency,
            real_provider=True,
            confirm_real_provider=True,
            stage_id=next_stage,
        )
        selected = _resolve_stage(
            config=stage_config,
            requested_agent_count=requested_agent_count,
            requested_step_count=requested_step_count,
            requested_max_concurrency=requested_max_concurrency,
        )
        payload = {
            "provider_id": provider_id,
            "model": model,
            "agent_count": requested_agent_count,
            "step_count": requested_step_count,
            "max_concurrency": requested_max_concurrency,
            "real_provider": True,
            "confirm_real_provider": True,
            "record_every_step": True,
            "stage_id": next_stage,
            "max_provider_calls": int(selected["step_count"]),
            "estimated_max_tokens": (
                int(selected["step_count"]) * _DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP
            ),
        }
        if next_stage == "provider_full_reference":
            resume_plan = build_kimi_swarm_resume_plan(
                provider_id=provider_id,
                model=model,
                agent_count=requested_agent_count,
                step_count=requested_step_count,
                max_concurrency=requested_max_concurrency,
                store=store,
                data_dir=data_dir,
            )
            if resume_plan.get("ready") and isinstance(
                resume_plan.get("recommended_payload"),
                dict,
            ):
                payload = dict(resume_plan["recommended_payload"])
                if isinstance(resume_plan.get("recommended_chunk_payload"), dict):
                    payload = dict(resume_plan["recommended_chunk_payload"])
        preflight = build_kimi_swarm_load_test_preflight(
            config=KimiSwarmLoadTestConfig(
                provider_id=provider_id,
                model=model,
                agent_count=requested_agent_count,
                step_count=requested_step_count,
                max_concurrency=requested_max_concurrency,
                real_provider=True,
                confirm_real_provider=True,
                record_every_step=True,
                stage_id=next_stage,
                max_provider_calls=int(selected["step_count"]),
                estimated_max_tokens=(
                    int(selected["step_count"]) * _DEFAULT_PROVIDER_OUTPUT_TOKENS_PER_STEP
                ),
            ),
            provider_configured=(True if provider_configured is None else provider_configured),
            store=store,
            data_dir=data_dir,
        )
    if isinstance(resume_plan, dict) and resume_plan.get("ready"):
        preflight = _resume_preflight_from_plan(
            resume_plan,
            provider_configured=provider_configured,
        )
    quota_probe_payload = None
    if (
        isinstance(blocking_failure, dict)
        and str(blocking_failure.get("category") or "") == "provider_quota_limit"
    ):
        quota_probe_payload = {
            "provider_id": provider_id,
            "model": model,
            "confirm_real_provider": True,
            "max_tokens": 16,
        }
    proof_ready = next_stage == "complete"
    can_run = (
        not proof_ready
        and provider_configured is True
        and isinstance(preflight, dict)
        and bool(preflight.get("ready"))
        and blocking_failure is None
    )
    provider_state = (
        "unknown"
        if provider_configured is None
        else ("configured" if provider_configured else "missing")
    )
    return {
        "schema": _NEXT_STAGE_SCHEMA,
        "ready": proof_ready,
        "proof_ready": proof_ready,
        "next_stage": next_stage,
        "provider_id": provider_id,
        "model": model,
        "provider_configured": provider_configured,
        "provider_configuration_state": provider_state,
        "requested": {
            "agent_count": requested_agent_count,
            "step_count": requested_step_count,
            "max_concurrency": requested_max_concurrency,
        },
        "stage_proofs": {
            key: _proof_without_session(value) if value else None for key, value in proofs.items()
        },
        "recommended_payload": payload,
        "recommended_preflight": preflight,
        "resume_plan": resume_plan,
        "recommended_chunk_payload": (
            resume_plan.get("recommended_chunk_payload") if isinstance(resume_plan, dict) else None
        ),
        "quota_probe_payload": quota_probe_payload,
        "latest_blocking_failure": blocking_failure,
        "can_run_recommended_payload": can_run,
        "next_action": _next_stage_action(
            next_stage=next_stage,
            provider_state=provider_state,
            can_run=can_run,
            blocking_failure=blocking_failure,
            resume_plan=resume_plan,
        ),
    }


def _next_stage_action(
    *,
    next_stage: str,
    provider_state: str,
    can_run: bool,
    blocking_failure: dict[str, Any] | None = None,
    resume_plan: dict[str, Any] | None = None,
) -> str:
    if next_stage == "complete":
        return "Full provider-backed Kimi-reference proof is complete."
    if blocking_failure:
        category = str(blocking_failure.get("category") or "provider_failure")
        if category == "provider_quota_limit":
            if isinstance(resume_plan, dict) and resume_plan.get("ready"):
                return (
                    "Kimi provider quota is exhausted; run the quota probe after "
                    "refresh, then resume "
                    f"{resume_plan.get('remaining_step_count')} remaining full-reference steps "
                    "instead of rerunning all 4000."
                )
            return (
                "Kimi provider quota is exhausted for the latest "
                f"{next_stage} attempt; run the quota probe after refresh before rerun."
            )
        if category == "provider_rate_limit":
            chunk = (
                resume_plan.get("recommended_chunk_payload")
                if isinstance(resume_plan, dict)
                else None
            )
            if isinstance(chunk, dict):
                return (
                    "Kimi provider is rate-limiting; wait briefly, then run the "
                    f"throttled resume chunk for {chunk.get('resume_step_count')} "
                    f"of {resume_plan.get('remaining_step_count')} remaining steps."
                )
            return (
                "Kimi provider is rate-limiting; wait briefly and rerun with lower "
                "resume concurrency."
            )
        if category == "token_budget_exceeded":
            return f"Increase the recommended {next_stage} token budget before rerun."
        return f"Resolve the latest {next_stage} provider failure before rerun."
    if provider_state == "missing":
        return f"Configure the Kimi K3 custom model, then run the recommended {next_stage} payload."
    if provider_state == "unknown":
        return f"Check custom model configuration, then run the recommended {next_stage} payload."
    if can_run and isinstance(resume_plan, dict) and resume_plan.get("ready"):
        chunk = resume_plan.get("recommended_chunk_payload")
        if isinstance(chunk, dict) and chunk.get("chunked"):
            return (
                f"Run the throttled resume chunk for {chunk.get('resume_step_count')} "
                f"of {resume_plan.get('remaining_step_count')} remaining {next_stage} steps."
            )
        return (
            f"Run the resume payload for {resume_plan.get('remaining_step_count')} "
            f"remaining {next_stage} steps."
        )
    if can_run:
        return f"Run the recommended {next_stage} payload."
    return f"Resolve the recommended {next_stage} preflight blockers."


def _latest_stage_blocking_failure(
    *,
    provider_id: str,
    model: str,
    requested_agent_count: int,
    requested_step_count: int,
    stage_id: str,
    store: ControlSessionStore | None = None,
    data_dir: str | Path | None = None,
) -> dict[str, Any] | None:
    for row in kimi_swarm_load_test_history(store=store, data_dir=data_dir):
        if (
            str(row.get("provider_id") or "") != str(provider_id or "")
            or str(row.get("model") or "") != str(model or "")
            or str(row.get("stage_id") or "") != str(stage_id or "")
            or int(row.get("requested_agent_count") or 0) != int(requested_agent_count)
            or int(row.get("requested_step_count") or 0) != int(requested_step_count)
        ):
            continue
        if int(row.get("failed_steps") or 0) <= 0:
            return None
        failure_summary = row.get("failure_summary")
        if not isinstance(failure_summary, dict):
            return None
        category = str(failure_summary.get("primary_category") or "")
        if category not in {
            "provider_quota_limit",
            "provider_rate_limit",
            "token_budget_exceeded",
        }:
            return None
        return {
            "schema": "echo.kimi_swarm_stage_blocking_failure.v1",
            "category": category,
            "stage_id": stage_id,
            "session_id": row.get("session_id"),
            "failed_steps": row.get("failed_steps"),
            "successful_steps": row.get("successful_steps"),
            "failure_summary": failure_summary,
        }
    return None
