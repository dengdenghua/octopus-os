from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import project_root as default_project_root
from runtime.safety.evolution.agent_benchmark import compute_agent_benchmark
from runtime.safety.evolution.kimi_swarm_load_test import (
    best_kimi_swarm_load_test_proof,
    build_kimi_swarm_resume_plan,
    export_kimi_swarm_proof_bundle,
    latest_kimi_swarm_load_test,
    recommend_kimi_swarm_next_stage,
)

KIMI_REFERENCE = {
    "schema": "echo.kimi_swarm_reference.v1",
    "product": "Kimi Agent / K2.6 Agent Swarm",
    "public_claims": {
        "subagents": 300,
        "coordinated_tool_calls": 4000,
        "speedup": "4.5x",
        "visibility": [
            "agent progress",
            "tool-call trace",
            "agent result aggregation",
        ],
    },
    "source_urls": [
        "https://www.kimi.com/blog/agent-swarm",
        "https://www.kimi.com/blog/kimi-k2-6",
    ],
}


@dataclass(frozen=True)
class KimiSwarmEvidenceCheck:
    id: str
    title: str
    capability: str
    paths: tuple[str, ...]
    required_terms: tuple[str, ...]
    proves: str
    remaining_risk: str = ""


CHECKS: tuple[KimiSwarmEvidenceCheck, ...] = (
    KimiSwarmEvidenceCheck(
        id="safe_default_cost_boundary",
        title="Safe mode keeps ordinary turns cost-bounded",
        capability="scale_control",
        paths=(
            "runtime/execution/agents/group_fanout.py",
            "tests/test_group_fanout.py",
        ),
        required_terms=(
            'scale_mode: str = "safe"',
            "_MAX_FANOUT = 6",
            "test_capacity_marks_kimi_scale_rosters_without_hiding_dispatch_limit",
            '"scale_mode": "safe"',
            '"capacity_tier": "kimi_scale"',
        ),
        proves=(
            "Kimi-scale rosters are visible without silently turning every chat "
            "turn into a 300-call spend."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="full_scale_320_dispatch",
        title="Explicit full-scale mode dispatches beyond Kimi's 300-agent claim",
        capability="agent_scale",
        paths=(
            "runtime/execution/agents/group_fanout.py",
            "tests/test_group_fanout.py",
        ),
        required_terms=(
            "_MAX_SCALE_FANOUT = 512",
            'scale_mode="full"',
            "test_full_scale_mode_dispatches_kimi_scale_roster_with_bounded_workers",
            'assert out["count"] == 320',
            'assert out["spoke"] == 320',
            '"requested_members": 320',
            '"dispatched_members": 320',
        ),
        proves=(
            "The local orchestrator can deterministically fan out to 320 roster "
            "members, exceeding the 300-agent public Kimi Swarm reference point."
        ),
        remaining_risk=(
            "This is deterministic local dispatch proof, not a paid-provider "
            "300+ real model-call load test."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="bounded_worker_concurrency",
        title="Full-scale dispatch is separated from worker concurrency",
        capability="runtime_control",
        paths=(
            "runtime/execution/agents/group_fanout.py",
            "runtime/sensing/gateway/realtime_team_stream.py",
            "runtime/sensing/gateway/_team_stream_group_fanout.py",
            "tests/test_group_fanout.py",
        ),
        required_terms=(
            "max_concurrency",
            "max_concurrency=32",
            '"max_concurrency": 32',
            '"concurrency": 32',
            "swarm_max_concurrency",
        ),
        proves=(
            "Total swarm size and actual worker concurrency are independently "
            "controlled, so full-scale mode does not require uncontrolled thread "
            "or provider fan-out."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="realtime_context_switch",
        title="Realtime turns can opt into full-scale swarm mode explicitly",
        capability="runtime_integration",
        paths=(
            "runtime/sensing/gateway/realtime_team_stream.py",
            "runtime/sensing/gateway/_team_stream_group_fanout.py",
            "tests/test_realtime_cerebrum.py",
        ),
        required_terms=(
            "swarm_scale_mode",
            "fanout_scale_mode",
            "swarm_max_members",
            "swarm_max_concurrency",
            "team_swarm",
            "echo.group_fanout_capacity.v1",
        ),
        proves=(
            "The Kimi-scale path is wired into realtime team stream context, not "
            "left as an isolated unit-test helper."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="swarm_synthesis_and_delivery",
        title="Swarm output includes synthesis, primary answer, retry targets",
        capability="result_quality",
        paths=(
            "runtime/execution/agents/group_fanout.py",
            "runtime/sensing/gateway/realtime_team_stream.py",
            "frontend/src/components/workspace/swarm-run-overview.tsx",
            "frontend/src/core/threads/use-thread-stream-realtime.ts",
            "frontend/src/components/workspace/swarm-run-overview.test.tsx",
        ),
        required_terms=(
            "echo.group_fanout_synthesis.v1",
            "synthesize_group_fanout",
            "primary_agent_id",
            "retry_agent_ids",
            "SynthesisStrip",
            "deliveryReady",
            "deliveryCoverage",
        ),
        proves=(
            "The swarm does not only produce parallel noise; it exposes a "
            "structured delivery envelope."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="control_session_replay_300_pages",
        title="ControlSession timeline paginates 300 swarm evidence items",
        capability="replay_audit",
        paths=(
            "runtime/memory/control_sessions.py",
            "runtime/sensing/gateway/control_sessions_router.py",
            "tests/test_control_sessions.py",
        ),
        required_terms=(
            "echo.control_session_replay_timeline.v1",
            "after_cursor",
            "next_cursor",
            "has_more",
            "test_control_session_timeline_pages_kimi_scale_swarm_replay",
            "range(300)",
            "limit=75",
            "assert len(seen) == 300",
        ),
        proves=(
            "Kimi-scale swarm traces can be reviewed in stable replay pages "
            "instead of being hidden in one oversized blob."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="large_replay_blob_export",
        title="Large swarm replay packages spill to detail blobs safely",
        capability="replay_audit",
        paths=(
            "runtime/memory/control_sessions.py",
            "runtime/sensing/gateway/control_sessions_router.py",
            "tests/test_control_sessions.py",
        ),
        required_terms=(
            "echo.control_evidence_blob_ref.v1",
            "echo.control_evidence_detail.v1",
            "test_control_session_stores_large_swarm_replay_evidence_as_blob_ref",
            "swarm_replay_export",
            "sha256",
        ),
        proves=(
            "Large swarm replay exports stay inspectable and integrity-addressed "
            "without bloating the default timeline response."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="ui_capacity_and_replay_export",
        title="Frontend exposes Kimi-scale capacity and replay export",
        capability="operator_visibility",
        paths=(
            "frontend/src/components/workspace/swarm-run-overview.tsx",
            "frontend/src/components/workspace/swarm-run-overview.test.tsx",
            "frontend/src/components/workspace/agent-workbench-utils.ts",
        ),
        required_terms=(
            "preserves large swarm capacity in overview and replay export",
            "requestedMembers: 300",
            'capacityTier: "kimi_scale"',
            "buildSwarmReplayPackage",
            "echo.swarm_replay_package.v1",
            "buildSwarmReplayTimeline",
        ),
        proves=(
            "The operator can see swarm scale, capacity drops, and replay export "
            "state in the product surface."
        ),
    ),
    KimiSwarmEvidenceCheck(
        id="benchmark_gate_includes_kimi_swarm",
        title="Benchmark gate tracks Kimi-style swarm capability",
        capability="release_gate",
        paths=(
            "runtime/safety/evolution/kimi_swarm_load_test.py",
            "runtime/safety/evolution/agent_benchmark.py",
            "runtime/sensing/gateway/evolution_router.py",
            "tests/test_kimi_swarm_load_test.py",
            "tests/test_evolution_router.py",
            "tests/test_agent_benchmark.py",
        ),
        required_terms=(
            "kimi_style_swarm_pipeline_visibility",
            "scale_mode",
            "max_concurrency",
            "test_full_scale_mode_dispatches_kimi_scale_roster_with_bounded_workers",
            "echo.kimi_swarm_load_test.v1",
            "provider_backed",
            "confirm_real_provider",
            "max_provider_calls",
            "estimated_max_tokens",
            "echo.kimi_swarm_load_test_preflight.v1",
            "echo.kimi_swarm_load_stage_plan.v1",
            "provider_full_reference",
            "best_kimi_swarm_load_test_proof",
            "provider_load_test_proof",
            "recorded_step_evidence_count",
            "actual_recorded_step_evidence_count",
            "replay_evidence_verified",
            "record_every_step",
            "echo.kimi_swarm_proof_bundle.v1",
            "export_kimi_swarm_proof_bundle",
            "timeline_digest",
            "step_evidence_digest",
            "previous_stage_ready",
            "latest_successful_stage_proof",
            "echo.kimi_swarm_next_stage.v1",
            "recommend_kimi_swarm_next_stage",
            "provider_load_test_next_stage",
            "recommended_payload",
            "recommended_preflight",
            "can_run_recommended_payload",
            "kimi-k3",
            "volcengine_ark",
            "attempt_count",
            "retryable",
            "_is_retryable_provider_error",
            "echo.kimi_swarm_failure_summary.v1",
            "provider_quota_limit",
            "provider_rate_limit",
            "provider_rate_limited",
            "latest_blocking_failure",
            "echo.kimi_swarm_stage_blocking_failure.v1",
            "echo.kimi_swarm_resume_plan.v1",
            "build_kimi_swarm_resume_plan",
            "provider_load_test_resume_plan",
            "provider_full_reference_resume",
            "resume_step_ranges",
            "partial_resume_session_ids",
            "covered_step_count",
            "recommended_chunk_payload",
            "total_remaining_step_count",
            "selected_step_count",
            "throttled resume chunk",
            "test_kimi_swarm_resume_plan_accumulates_partial_resume_successes",
            "test_kimi_swarm_composite_proof_accumulates_partial_resume_sessions",
            "echo.kimi_swarm_quota_probe.v1",
            "KimiSwarmQuotaProbeConfig",
            "run_kimi_swarm_quota_probe",
            "quota_probe_payload",
            "can_resume_provider_load_test",
            "/kimi-swarm-certification/quota-probe",
            "test_kimi_swarm_quota_probe_success_allows_resume",
            "test_kimi_swarm_quota_probe_reports_provider_quota_limit",
            "test_kimi_swarm_quota_probe_reports_provider_rate_limit",
            "test_kimi_swarm_quota_probe_endpoint_runs_guarded_provider_probe",
            "echo.kimi_swarm_composite_proof.v1",
            "echo.kimi_swarm_composite_coverage.v1",
            "raw_step_evidence_count",
            "resume_session_ids",
            "score",
            "ready",
        ),
        proves=(
            "Kimi-style swarm visibility remains part of the repeatable release "
            "benchmark instead of a one-off comparison claim."
        ),
    ),
)


def compute_kimi_swarm_certification(
    *,
    root: str | Path | None = None,
    data_dir: str | Path | None = None,
    provider_configured: bool | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else default_project_root(Path(__file__))
    checks = [_check_row(base, check) for check in CHECKS]
    passed = sum(1 for row in checks if row["passed"])
    benchmark = compute_agent_benchmark(root=base)
    latest_load_test = latest_kimi_swarm_load_test(data_dir=data_dir)
    best_load_test = best_kimi_swarm_load_test_proof(data_dir=data_dir)
    proof_bundle = export_kimi_swarm_proof_bundle(data_dir=data_dir)
    resume_plan = build_kimi_swarm_resume_plan(data_dir=data_dir)
    next_stage = recommend_kimi_swarm_next_stage(
        data_dir=data_dir,
        provider_configured=provider_configured,
    )
    load_test_ready = _load_test_proves_real_provider_reference(best_load_test)
    benchmark_case = next(
        (
            row
            for row in benchmark.get("cases") or []
            if row.get("id") == "kimi_style_swarm_pipeline_visibility"
        ),
        None,
    )
    proven_capabilities = sorted(
        {
            str(row["capability"])
            for row in checks
            if row.get("passed") and str(row.get("capability") or "")
        }
    )
    remaining_proof = [] if load_test_ready else [_provider_load_test_missing(latest_load_test)]
    ready = passed == len(checks) and bool(benchmark_case and benchmark_case.get("passed"))
    if ready and load_test_ready:
        verdict = "fully_surpassed"
    elif ready:
        verdict = "deterministic_orchestration_surpassed"
    else:
        verdict = "needs_work"
    return {
        "schema": "echo.kimi_swarm_certification.v1",
        "target": "kimi_agent_swarm",
        "ready": ready,
        "verdict": verdict,
        "score": round(passed / max(1, len(checks)), 3),
        "passed": passed,
        "total": len(checks),
        "summary": {
            "echo_deterministic_max_members": 320,
            "echo_worker_concurrency_proven": 32,
            "kimi_reference_subagents": int(
                KIMI_REFERENCE["public_claims"]["subagents"],
            ),
            "kimi_reference_tool_calls": int(
                KIMI_REFERENCE["public_claims"]["coordinated_tool_calls"],
            ),
            "benchmark_ready": bool(benchmark.get("ready")),
            "benchmark_score": float(benchmark.get("score") or 0.0),
            "benchmark_case_ready": bool(benchmark_case and benchmark_case.get("passed")),
            "provider_load_test_ready": load_test_ready,
            "proven_capabilities": proven_capabilities,
            "remaining_proof_count": len(remaining_proof),
        },
        "kimi_reference": KIMI_REFERENCE,
        "provider_load_test": latest_load_test,
        "provider_load_test_proof": best_load_test,
        "provider_load_test_proof_bundle": {
            "schema": proof_bundle.get("schema"),
            "ready": bool(proof_bundle.get("ready")),
            "sha256": proof_bundle.get("sha256"),
            "step_evidence_count": proof_bundle.get("step_evidence_count"),
            "raw_step_evidence_count": proof_bundle.get("raw_step_evidence_count"),
            "timeline_count": proof_bundle.get("timeline_count"),
            "replay_href": proof_bundle.get("replay_href"),
            "replay_hrefs": proof_bundle.get("replay_hrefs"),
        },
        "provider_load_test_resume_plan": resume_plan,
        "provider_load_test_next_stage": next_stage,
        "checks": checks,
        "benchmark_case": benchmark_case,
        "remaining_proof": remaining_proof,
        "next_actions": [
            str(row["next_action"])
            for row in checks
            if not row.get("passed") and row.get("next_action")
        ]
        + [
            "Run the provider-backed 300-agent / 4000-step production load test "
            "before claiming full real-world Kimi Swarm superiority."
        ],
    }


def _load_test_proves_real_provider_reference(report: dict[str, Any] | None) -> bool:
    if not isinstance(report, dict):
        return False
    return (
        bool(report.get("provider_backed"))
        and str(report.get("stage_id") or "") == "provider_full_reference"
        and int(report.get("agent_count") or 0) >= 300
        and int(report.get("step_count") or 0) >= 4000
        and int(report.get("successful_steps") or 0) >= 4000
        and int(report.get("actual_recorded_step_evidence_count") or 0)
        >= int(report.get("step_count") or 0)
        and bool(report.get("replay_evidence_verified"))
        and int(report.get("failed_steps") or 0) == 0
        and bool(report.get("meets_kimi_reference"))
    )


def _provider_load_test_missing(report: dict[str, Any] | None) -> dict[str, Any]:
    status = "missing"
    if isinstance(report, dict):
        if not bool(report.get("provider_backed")):
            status = "dry_run_only"
        elif bool(report.get("quota_limited")):
            status = "provider_quota_limited"
        else:
            status = "insufficient"
    return {
        "id": "provider_backed_300_agent_load_test",
        "title": "Provider-backed 300+ real model-call swarm load test",
        "status": status,
        "why": (
            "Current evidence proves deterministic orchestration, bounded "
            "workers, replay, and UI visibility. It does not prove a paid "
            "provider can sustain 300+ live model calls and 4000+ tool "
            "steps under production latency/quota."
        ),
        "acceptance": [
            "Run >=300 real provider-backed agent calls in full mode",
            "Record >=4000 coordinated tool/action steps",
            "Persist every step to ControlSession replay",
            "Export latency, failure, retry, and cost metrics",
        ],
        "failure_summary": (report.get("failure_summary") if isinstance(report, dict) else None),
        "latest_load_test": report,
    }


def _check_row(base: Path, check: KimiSwarmEvidenceCheck) -> dict[str, Any]:
    path_rows = [{"path": path, "exists": (base / path).exists()} for path in check.paths]
    haystack = "\n".join(
        _read_text(base / str(row["path"])) for row in path_rows if row["exists"]
    ).lower()
    missing_paths = [str(row["path"]) for row in path_rows if not row["exists"]]
    missing_terms = [term for term in check.required_terms if term.lower() not in haystack]
    passed = not missing_paths and not missing_terms
    return {
        "id": check.id,
        "title": check.title,
        "capability": check.capability,
        "passed": passed,
        "paths": path_rows,
        "required_terms": list(check.required_terms),
        "missing_paths": missing_paths,
        "missing_terms": missing_terms,
        "proves": check.proves,
        "remaining_risk": check.remaining_risk,
        "next_action": ("Restore the missing Kimi Swarm evidence terms." if not passed else ""),
    }


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


__all__ = [
    "CHECKS",
    "KIMI_REFERENCE",
    "KimiSwarmEvidenceCheck",
    "compute_kimi_swarm_certification",
]
