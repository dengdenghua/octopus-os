from __future__ import annotations

from pathlib import Path

from runtime.memory.control_sessions import ControlSessionStore
from runtime.safety.evolution.kimi_swarm_certification import (
    compute_kimi_swarm_certification,
)
from runtime.safety.evolution.kimi_swarm_load_test import (
    KimiSwarmLoadTestConfig,
    run_kimi_swarm_load_test,
)
from runtime.sensing.model_router.models import ModelRequest, ModelResponse


def test_kimi_swarm_certification_is_evidence_backed(tmp_path) -> None:
    report = compute_kimi_swarm_certification(data_dir=tmp_path)

    assert report["schema"] == "echo.kimi_swarm_certification.v1"
    assert report["ready"] is True
    assert report["score"] == 1.0
    assert report["passed"] == report["total"] == 9
    assert report["verdict"] == "deterministic_orchestration_surpassed"
    assert report["summary"]["echo_deterministic_max_members"] == 320
    assert report["summary"]["echo_worker_concurrency_proven"] == 32
    assert report["summary"]["kimi_reference_subagents"] == 300
    assert report["summary"]["kimi_reference_tool_calls"] == 4000
    assert report["summary"]["benchmark_ready"] is True
    assert report["summary"]["benchmark_case_ready"] is True
    assert report["provider_load_test_next_stage"]["schema"] == ("echo.kimi_swarm_next_stage.v1")
    assert report["provider_load_test_next_stage"]["next_stage"] == "provider_canary"
    assert report["provider_load_test_next_stage"]["provider_id"] == "volcengine_ark"
    assert report["provider_load_test_next_stage"]["model"] == "kimi-k3"
    assert report["provider_load_test_next_stage"]["recommended_payload"]["real_provider"] is True
    assert report["provider_load_test_resume_plan"]["schema"] == (
        "echo.kimi_swarm_resume_plan.v1"
    )
    assert {
        "agent_scale",
        "operator_visibility",
        "replay_audit",
        "result_quality",
        "runtime_control",
        "runtime_integration",
        "scale_control",
        "release_gate",
    } <= set(report["summary"]["proven_capabilities"])
    assert report["remaining_proof"][0]["id"] == "provider_backed_300_agent_load_test"
    assert report["next_actions"] == [
        "Run the provider-backed 300-agent / 4000-step production load test "
        "before claiming full real-world Kimi Swarm superiority."
    ]


def test_kimi_swarm_certification_explains_missing_evidence(tmp_path) -> None:
    report = compute_kimi_swarm_certification(root=tmp_path, data_dir=tmp_path)

    assert report["ready"] is False
    assert report["score"] == 0.0
    assert report["verdict"] == "needs_work"
    assert report["checks"][0]["missing_paths"]
    assert report["remaining_proof"][0]["id"] == "provider_backed_300_agent_load_test"


def test_kimi_swarm_certification_reports_provider_quota_limit(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="cert-quota-limit",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=10,
            estimated_max_tokens=5120,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    report = compute_kimi_swarm_certification(data_dir=tmp_path)

    assert report["remaining_proof"][0]["status"] == "provider_quota_limited"
    assert report["remaining_proof"][0]["failure_summary"]["primary_category"] == (
        "provider_quota_limit"
    )
    assert (
        report["provider_load_test_next_stage"]["latest_blocking_failure"]["category"]
        == "provider_quota_limit"
    )

