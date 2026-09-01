from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.control_sessions import ControlSessionStore
from runtime.memory.learning.review_queue import ReviewQueue
from runtime.safety.approval.approval_policy_store import load_policy
from runtime.safety.evolution.kimi_swarm_load_test import (
    KimiSwarmLoadTestConfig,
    build_kimi_swarm_resume_plan,
    run_kimi_swarm_load_test,
)
from runtime.sensing.gateway.evolution_router import create_evolution_router
from runtime.sensing.model_router.models import ModelRequest, ModelResponse


def test_auto_verifier_metrics_endpoint(monkeypatch) -> None:
    def fake_summary(*, limit: int = 1000):
        return {
            "schema": "echo.auto_verifier_metrics.v1",
            "total": limit,
            "families": [],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.auto_verifier_metrics.summarize_auto_verifier_metrics",
        fake_summary,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/auto-verifier-metrics?limit=7")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["total"] == 7


def test_auto_verifier_drift_queue_endpoint(monkeypatch) -> None:
    def fake_queue(*, limit: int = 1000):
        return {
            "schema": "echo.verifier_drift_repair_queue.v1",
            "created": 1,
            "updated": 0,
            "alerts": [{"family": "ruff"}],
            "items": [{"candidate_kind": "verifier_drift:ruff"}],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.auto_verifier_metrics.queue_verifier_drift_backlog",
        fake_queue,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/auto-verifier-metrics/drift/queue",
        json={"limit": 7},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["schema"] == "echo.verifier_drift_repair_queue.v1"
    assert response.json()["created"] == 1


def test_agent_scorecard_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/agent-scorecard?target_score=90")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.agent_competitor_scorecard.v1"
    assert data["target_score"] == 90
    assert data["overall"]["echo"] == 98
    assert data["overall"]["codex"] == 97
    assert data["overall"]["openclaw"] == 84
    assert data["overall"]["hermes"] == 85
    assert data["verdict"] == "leading"
    assert data["evidence_adjusted_overall"]["echo"] == 98
    assert data["evidence_adjusted_verdict"] == "leading"
    assert data["baseline_context"]["as_of"] == "2026-08-04"
    assert data["baseline_context"]["source_revision"] == (
        "a41dc160a4056563891cc069fcbcf6b961cf56d9"
    )
    assert data["baseline_context"]["max_age_days"] == 90
    assert data["baseline_context"]["score_kind"] == "architecture_capability_estimate"
    assert data["baseline_context"]["excludes"] == "legacy CLI-only comparisons"
    assert data["evidence_layers"]["architecture"] == {
        "status": "estimated",
        "echo_score": 98,
        "codex_score": 97,
        "source": "version_controlled_architecture_calibration",
        "source_revision": "a41dc160a4056563891cc069fcbcf6b961cf56d9",
        "as_of": "2026-08-04",
    }
    assert data["evidence_layers"]["static_certification"]["status"] == "certified"
    assert data["evidence_layers"]["behavioral_head_to_head"]["status"] == ("not_certified")
    assert data["scorecard_policy"]["certification_floors_do_not_change_overall"] is True
    assert data["scorecard_policy"]["explicit_objective"] == (
        "surpass_best_external_on_every_dimension"
    )
    assert data["surpass_summary"] == {
        "schema": "echo.agent_surpass_summary.v1",
        "total_dimensions": 15,
        "surpassed_dimensions": 15,
        "gap_dimensions": 0,
        "target_gap_dimensions": 0,
        "focus_gap_dimensions": 0,
        "all_dimensions_surpassed": True,
        "largest_gap": 0,
        "largest_effective_gap": 0,
    }
    assert data["echo_external_gap_dimensions"] == []
    assert data["echo_focus_gaps"] == []
    assert data["ecosystem_readiness"]["score"] == 1.0
    assert data["parity_certification"]["ready"] is True
    assert data["parity_certification"]["passed"] == 17
    assert data["parity_certification"]["by_kind"]["operational_excellence"]["passed"] == 4
    assert data["parity_certification"]["by_kind"]["advantage"]["passed"] == 7


def test_agent_scorecard_endpoint_defaults_to_e2e_target() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/agent-scorecard")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["target_score"] == 95
    assert data["echo_below_target"] == []
    assert data["echo_focus_gaps"] == []


def test_agent_benchmark_endpoint_and_scorecards_are_evidence_backed() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    benchmark_response = client.get("/api/evolution/agent-benchmark")
    benchmark = benchmark_response.json()

    assert benchmark_response.status_code == 200
    assert benchmark["ok"] is True
    assert benchmark["schema"] == "echo.agent_benchmark.v1"
    assert benchmark["ready"] is True
    assert benchmark["score"] == 1.0

    scorecard = client.get("/api/evolution/agent-scorecard").json()
    radar = client.get("/api/evolution/automation-radar").json()

    assert scorecard["agent_benchmark"]["schema"] == "echo.agent_benchmark.v1"
    assert scorecard["agent_benchmark"]["ready"] is True
    assert radar["agent_benchmark"]["schema"] == "echo.agent_benchmark.v1"
    assert radar["agent_benchmark"]["ready"] is True


def test_kimi_swarm_certification_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/kimi-swarm-certification")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_certification.v1"
    assert data["ready"] is True
    assert data["verdict"] == "deterministic_orchestration_surpassed"
    assert data["summary"]["echo_deterministic_max_members"] == 320
    assert data["summary"]["kimi_reference_subagents"] == 300
    assert data["summary"]["benchmark_case_ready"] is True
    assert data["remaining_proof"][0]["id"] == "provider_backed_300_agent_load_test"
    assert "provider_load_test_proof" in data
    assert data["provider_load_test_next_stage"]["schema"] == ("echo.kimi_swarm_next_stage.v1")
    assert data["provider_load_test_next_stage"]["next_stage"] == "provider_canary"
    assert data["provider_load_test_next_stage"]["provider_id"] == "volcengine_ark"
    assert data["provider_load_test_next_stage"]["model"] == "kimi-k3"


def test_kimi_swarm_next_stage_endpoint_reports_default_kimi_k3_path(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: None,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/kimi-swarm-certification/next-stage")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_next_stage.v1"
    assert data["ready"] is False
    assert data["proof_ready"] is False
    assert data["next_stage"] == "provider_canary"
    assert data["provider_id"] == "volcengine_ark"
    assert data["model"] == "kimi-k3"
    assert data["provider_configured"] is False
    assert data["can_run_recommended_payload"] is False
    assert data["recommended_payload"]["stage_id"] == "provider_canary"
    assert data["recommended_preflight"]["blocking_failures"][0]["id"] == ("provider_configured")


def test_kimi_swarm_next_stage_endpoint_marks_configured_payload_runnable(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: object(),
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/kimi-swarm-certification/next-stage")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["next_stage"] == "provider_canary"
    assert data["provider_configured"] is True
    assert data["can_run_recommended_payload"] is True
    assert data["recommended_preflight"]["ready"] is True


def test_kimi_swarm_resume_plan_endpoint_reports_no_source(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/kimi-swarm-certification/resume-plan")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_resume_plan.v1"
    assert data["ready"] is False
    assert data["recommended_payload"] is None


def test_kimi_swarm_proof_bundle_endpoint_reports_missing_proof(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/kimi-swarm-certification/proof-bundle")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_proof_bundle.v1"
    assert data["ready"] is False
    assert data["proof"] is None


def test_kimi_swarm_load_test_endpoint_runs_dry_replay(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={
            "session_id": "router-load-dry-1",
            "agent_count": 4,
            "step_count": 9,
            "max_concurrency": 2,
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_load_test.v1"
    assert data["successful_steps"] == 9
    assert data["provider_backed"] is False

    certification = client.get("/api/evolution/kimi-swarm-certification").json()
    assert certification["ok"] is True
    assert certification["provider_load_test"]["session_id"] == "router-load-dry-1"
    assert certification["remaining_proof"][0]["status"] == "dry_run_only"


def test_kimi_swarm_load_test_preflight_endpoint_reports_stage_plan(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: object(),
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test/preflight",
        json={
            "provider_id": "test_provider",
            "model": "test-model",
            "agent_count": 300,
            "step_count": 4000,
            "max_concurrency": 32,
            "real_provider": True,
            "confirm_real_provider": True,
            "max_provider_calls": 10,
            "estimated_max_tokens": 5120,
            "stage_id": "provider_canary",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_load_test_preflight.v1"
    assert data["ready"] is True
    assert data["provider_ready"] is True
    assert data["selected_stage"]["id"] == "provider_canary"
    assert data["stage_plan"]["schema"] == "echo.kimi_swarm_load_stage_plan.v1"
    assert data["stage_plan"]["stages"][-1]["id"] == "provider_full_reference"
    assert data["stage_plan"]["stages"][0]["id"] == "provider_canary"


def test_kimi_swarm_quota_probe_endpoint_rejects_without_confirmation() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/quota-probe",
        json={"model": "test-model"},
    )

    assert response.status_code == 400
    assert "confirm_real_provider=true" in response.json()["detail"]


def test_kimi_swarm_quota_probe_endpoint_rejects_without_model_config(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: None,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/quota-probe",
        json={"model": "test-model", "confirm_real_provider": True},
    )

    assert response.status_code == 400
    assert "custom model router" in response.json()["detail"]


def test_kimi_swarm_quota_probe_endpoint_runs_guarded_provider_probe(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))

    class FakeRouter:
        def __init__(self) -> None:
            self.calls: list[ModelRequest] = []

        def call(self, request):
            self.calls.append(request)
            return ModelResponse(
                text='{"ok":true}',
                input_tokens=1,
                output_tokens=1,
                model=request.model,
                provider=request.system_provider,
            )

    fake_router = FakeRouter()
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: fake_router,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/quota-probe",
        json={
            "session_id": "router-quota-probe",
            "provider_id": "test_provider",
            "model": "test-model",
            "confirm_real_provider": True,
            "max_tokens": 9,
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.kimi_swarm_quota_probe.v1"
    assert data["can_resume_provider_load_test"] is True
    assert data["provider_quota_limited"] is False
    assert len(fake_router.calls) == 1
    assert fake_router.calls[0].max_tokens == 9
    assert fake_router.calls[0].model == "test-model"
    assert fake_router.calls[0].system_provider == "test_provider"


def test_kimi_swarm_load_test_endpoint_rejects_real_provider_without_confirmation() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={"real_provider": True},
    )

    assert response.status_code == 400
    assert "confirm_real_provider=true" in response.json()["detail"]


def test_kimi_swarm_load_test_endpoint_rejects_real_provider_without_model_config(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: None,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={
            "real_provider": True,
            "confirm_real_provider": True,
            "step_count": 3,
            "agent_count": 2,
            "max_provider_calls": 3,
            "estimated_max_tokens": 12,
            "stage_id": "provider_canary",
        },
    )

    assert response.status_code == 400
    assert "custom model router" in response.json()["detail"]


def test_kimi_swarm_load_test_endpoint_rejects_full_before_ramp(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: object(),
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={
            "session_id": "router-full-before-ramp",
            "provider_id": "test_provider",
            "model": "test-model",
            "agent_count": 300,
            "step_count": 4000,
            "max_concurrency": 32,
            "real_provider": True,
            "confirm_real_provider": True,
            "max_provider_calls": 4000,
            "estimated_max_tokens": 8000,
            "stage_id": "provider_full_reference",
        },
    )

    assert response.status_code == 400
    assert "previous_stage_ready" in response.json()["detail"]


def test_kimi_swarm_load_test_endpoint_runs_guarded_real_provider(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))

    class FakeRouter:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, request):
            self.calls += 1
            from runtime.sensing.model_router.models import ModelResponse

            return ModelResponse(
                text='{"ok":true}',
                input_tokens=2,
                output_tokens=1,
                model=request.model,
                provider=request.system_provider,
            )

    fake_router = FakeRouter()
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: fake_router,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={
            "session_id": "router-load-real-1",
            "provider_id": "test_provider",
            "model": "test-model",
            "agent_count": 3,
            "step_count": 7,
            "max_concurrency": 2,
            "real_provider": True,
            "confirm_real_provider": True,
            "max_provider_calls": 7,
            "estimated_max_tokens": 21,
            "record_every_step": False,
            "stage_id": "provider_canary",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["provider_backed"] is True
    assert data["stage_id"] == "provider_canary"
    assert data["successful_steps"] == 7
    assert data["recorded_step_evidence_count"] == 0
    assert data["max_provider_calls"] == 7
    assert fake_router.calls == 7


def test_kimi_swarm_load_test_endpoint_runs_resume_payload(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    store = ControlSessionStore(tmp_path / "data" / "control_sessions")
    calls = 0

    def quota_after_seven(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 7:
            raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="router-resume-full-source",
            provider_id="test_provider",
            model="test-model",
            agent_count=3,
            step_count=12,
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=12,
            estimated_max_tokens=6144,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=quota_after_seven,
    )
    payload = build_kimi_swarm_resume_plan(
        provider_id="test_provider",
        model="test-model",
        agent_count=3,
        step_count=12,
        max_concurrency=1,
        store=store,
    )["recommended_payload"]

    class FakeRouter:
        def __init__(self) -> None:
            self.calls = 0

        def call(self, request):
            self.calls += 1
            return ModelResponse(
                text="ok",
                input_tokens=1,
                output_tokens=1,
                model=request.model,
                provider=request.system_provider,
            )

    fake_router = FakeRouter()
    monkeypatch.setattr(
        "runtime.sensing.model_router.openai_router.build_fallback_router_from_custom_models",
        lambda _model: fake_router,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/kimi-swarm-certification/load-test",
        json={
            **payload,
            "session_id": "router-resume-full-repair",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["stage_id"] == "provider_full_reference_resume"
    assert data["step_count"] == 5
    assert data["reference_step_count"] == 12
    assert data["successful_steps"] == 5
    assert data["resume_from_session_id"] == "router-resume-full-source"
    assert fake_router.calls == 5


def test_e2e_surpass_certification_endpoint(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_INFRASTRUCTURE_STATUS",
        str(tmp_path / "no-infrastructure-receipt.json"),
    )
    monkeypatch.setenv(
        "ECHO_BEHAVIORAL_EVAL_BUNDLE",
        str(tmp_path / "no-behavioral-bundle.json"),
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/e2e-surpass-certification?target_score=95")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.e2e_surpass_certification.v1"
    assert data["ready"] is False
    assert data["verdict"] == "needs_behavioral_evidence"
    assert data["summary"]["scorecard_echo"] == 98
    assert data["summary"]["automation_echo"] == 96
    assert data["summary"]["automation_echo"] >= data["target_score"]
    assert data["summary"]["quality_ready"] == data["summary"]["quality_total"]
    assert data["summary"]["behavioral_ready"] is False
    assert data["behavioral"]["verdict"] == "missing_behavioral_evidence"
    assert any(
        check["id"] == "scorecard_all_dimensions_surpassed" and check["passed"] is True
        for check in data["checks"]
    )


def test_agent_scorecard_gaps_can_queue_real_baseline_backlog(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/agent-scorecard/gaps/queue",
        json={"target_score": 98, "limit": 3, "reason": "raise stretch ceiling"},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.agent_scorecard_gap_queue.v1"
    assert data["created"] == 3
    assert data["scorecard"]["overall"]["echo"] == 98
    assert data["scorecard"]["evidence_adjusted_overall"]["echo"] == 98
    assert data["scorecard"]["below_target_count"] == 5
    assert data["scorecard"]["external_gap_count"] == 0
    assert data["scorecard"]["focus_gap_count"] == 5
    assert [item["candidate_kind"] for item in data["items"]] == [
        "scorecard_gap:long_term_learning",
        "scorecard_gap:digital_employee_workflows",
        "scorecard_gap:differentiated_agent_os",
    ]
    first = data["items"][0]["metadata"]
    assert first["dimension_id"] == "long_term_learning"
    assert first["gap_to_effective_target"] == 1
    assert first["gap_to_surpass"] == 0
    assert first["best_external_competitor"] == "codex"
    assert first["best_external_score"] == 95

    summary = ReviewQueue(tmp_path / "data" / "review_queue.json").summary()
    assert summary["pending_count"] == 3


def test_agent_scorecard_gap_queue_tracks_recalibrated_default_gaps(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post("/api/evolution/agent-scorecard/gaps/queue")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["scorecard"]["target_score"] == 95
    assert data["created"] == 0
    assert data["items"] == []
    assert data["scorecard"]["external_gap_count"] == 0
    assert data["scorecard"]["focus_gap_count"] == 0

    summary = ReviewQueue(tmp_path / "data" / "review_queue.json").summary()
    assert summary["pending_count"] == 0


def test_repair_route_promotion_candidates_can_queue_from_router(
    monkeypatch,
) -> None:
    def fake_queue(*, limit: int = 1000):
        return {
            "schema": "echo.repair_route_promotion_queue.v1",
            "created": 1,
            "updated": 0,
            "candidates": [{"route": "test_driven_repair", "limit": limit}],
            "items": [{"candidate_kind": "repair_route_promotion:test_driven_repair"}],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.repair_route_quality.queue_repair_route_promotion_candidates",
        fake_queue,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/repair-route-quality/promotions/queue",
        json={"limit": 50},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.repair_route_promotion_queue.v1"
    assert data["created"] == 1
    assert data["candidates"][0]["route"] == "test_driven_repair"
    assert data["candidates"][0]["limit"] == 50


def test_agent_scorecard_gap_queue_can_target_single_dimension(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/agent-scorecard/gaps/queue",
        json={
            "target_score": 97,
            "limit": 10,
            "dimension_id": "ecosystem_maturity",
            "reason": "operator drilldown remediation",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["created"] == 0
    assert data["items"] == []

    summary = ReviewQueue(tmp_path / "data" / "review_queue.json").summary()
    assert summary["pending_count"] == 0


def test_browser_desktop_quality_endpoint() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/browser-desktop-quality")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_quality.v1"
    assert data["ready"] is True
    assert data["score"] == 1.0


def test_repo_context_quality_endpoint() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/repo-context-quality")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.repo_context_quality.v1"
    assert data["ready"] is True
    assert data["score"] == 1.0


def test_permission_sandbox_quality_endpoint() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/permission-sandbox-quality")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.permission_sandbox_quality.v1"
    assert data["ready"] is True
    assert data["score"] == 1.0
    assert data["automation_policy_coverage"]["ready"] is True


def test_product_experience_quality_endpoint() -> None:
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get("/api/evolution/product-experience-quality")
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.product_experience_quality.v1"
    assert data["ready"] is True
    assert data["score"] == 1.0


def test_automation_policy_rule_drafts_endpoint_and_install(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("ECHO_DATA_DIR", str(tmp_path / "data"))
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    drafts_response = client.get("/api/evolution/automation-policy-rule-drafts")
    drafts = drafts_response.json()
    draft = next(
        item
        for item in drafts["drafts"]
        if item["signed_payload"]["rule"]["tool"] == "computer_execute_token"
    )
    missing_confirm = client.post(
        "/api/evolution/automation-policy-rule-drafts/install",
        json={"draft_id": draft["draft_id"]},
    )
    installed = client.post(
        "/api/evolution/automation-policy-rule-drafts/install",
        json={"draft_id": draft["draft_id"], "confirm_install": True},
    )
    policy = load_policy(tmp_path / "data" / "permissions.json")

    assert drafts_response.status_code == 200
    assert drafts["schema"] == "echo.automation_policy_rule_drafts.v1"
    assert drafts["total"] >= 7
    assert drafts["verified"] == drafts["total"]
    assert missing_confirm.status_code == 400
    assert missing_confirm.json()["detail"] == "confirm_install=true is required"
    assert installed.status_code == 200
    assert installed.json()["ok"] is True
    assert installed.json()["installed"] is True
    assert installed.json()["source_kind"] == "automation_policy_review"
    assert policy.rules[0].effect == "deny"
    assert policy.rules[0].tool == "computer_execute_token"


def test_browser_desktop_repair_recipe_queue_endpoint(monkeypatch) -> None:
    def fake_queue(*, limit: int = 1000, min_occurrences: int = 1):
        return {
            "schema": "echo.browser_desktop_repair_recipe_queue.v1",
            "created": 1,
            "updated": 0,
            "recipes": [
                {
                    "candidate_kind": "browser_pixel_replay_gate_case",
                    "limit": limit,
                    "min_occurrences": min_occurrences,
                }
            ],
            "items": [
                {
                    "candidate_kind": "browser_desktop_repair_recipe:abcdef",
                    "target_bucket": "browser_desktop_repair_recipe",
                }
            ],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.queue_browser_desktop_repair_recipes",
        fake_queue,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/queue",
        json={"limit": 50, "min_occurrences": 2},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_repair_recipe_queue.v1"
    assert data["created"] == 1
    assert data["recipes"][0]["limit"] == 50
    assert data["recipes"][0]["min_occurrences"] == 2


def test_browser_desktop_stale_artifact_rejection_endpoint(monkeypatch) -> None:
    def fake_reject(*, limit: int = 1000):
        return {
            "schema": "echo.browser_desktop_stale_replay_artifact_rejection.v1",
            "inspected": limit,
            "rejected_count": 2,
            "archived_recipe_count": 1,
            "skipped_count": 1,
            "rejected": [{"id": "rq_stale"}],
            "archived_recipes": [{"id": "rq_recipe"}],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.reject_stale_browser_desktop_replay_artifacts",
        fake_reject,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/stale-artifacts/reject",
        json={"limit": 3},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_stale_replay_artifact_rejection.v1"
    assert data["inspected"] == 3
    assert data["rejected_count"] == 2
    assert data["archived_recipe_count"] == 1


def test_browser_desktop_repair_recipe_verifications_endpoint(monkeypatch) -> None:
    def fake_verifications(*, limit: int = 1000):
        return {
            "schema": "echo.browser_desktop_repair_recipe_verifications.v1",
            "total": limit,
            "verified_count": 0,
            "blocked_count": 1,
            "ready": False,
            "verifications": [{"status": "needs_rerun_evidence"}],
            "next_actions": ["Attach rerun evidence."],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.compute_browser_desktop_repair_recipe_verifications",
        fake_verifications,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.get(
        "/api/evolution/browser-desktop-repair-recipes/verifications?limit=7",
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_repair_recipe_verifications.v1"
    assert data["total"] == 7
    assert data["ready"] is False


def test_browser_desktop_repair_recipe_evidence_endpoint(monkeypatch) -> None:
    def fake_attach(
        *,
        item_id: str,
        passed: bool,
        provided: list[str],
        artifacts: list[dict],
        notes: str,
        actor: str,
    ):
        return {
            "schema": "echo.browser_desktop_repair_recipe_evidence_attachment.v1",
            "item": {"id": item_id},
            "evidence": {
                "passed": passed,
                "provided": provided,
                "artifacts": artifacts,
                "notes": notes,
                "actor": actor,
            },
            "verification": {"status": "verified"},
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.attach_browser_desktop_repair_recipe_evidence",
        fake_attach,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/verifications/evidence",
        json={
            "item_id": "rq_recipe",
            "passed": True,
            "provided": ["fresh_screenshot"],
            "artifacts": [{"type": "screenshot", "ok": True}],
            "notes": "rerun passed",
            "actor": "operator_test",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == ("echo.browser_desktop_repair_recipe_evidence_attachment.v1")
    assert data["item"]["id"] == "rq_recipe"
    assert data["evidence"]["provided"] == ["fresh_screenshot"]
    assert data["verification"]["status"] == "verified"


def test_browser_desktop_repair_recipe_rerun_endpoint(monkeypatch) -> None:
    def fake_rerun(
        *,
        item_id: str,
        api_base_url: str,
        promote_source_cases: bool,
        actor: str,
    ):
        return {
            "schema": "echo.browser_desktop_repair_recipe_rerun.v1",
            "item_id": item_id,
            "passed": True,
            "provided": ["browser_session_replay_case", "session_health"],
            "missing": [],
            "promoted_source_count": 1 if promote_source_cases else 0,
            "artifacts": [{"url": api_base_url, "ok": True}],
            "attachment": {"evidence": {"actor": actor}},
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.rerun_browser_desktop_repair_recipe_evidence",
        fake_rerun,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/verifications/rerun",
        json={
            "item_id": "rq_recipe",
            "api_base_url": "http://127.0.0.1:8000",
            "promote_source_cases": True,
            "actor": "operator_test",
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_repair_recipe_rerun.v1"
    assert data["passed"] is True
    assert data["promoted_source_count"] == 1
    assert data["attachment"]["evidence"]["actor"] == "operator_test"


def test_browser_desktop_repair_recipe_rerun_defaults_to_request_base_url(
    monkeypatch,
) -> None:
    def fake_rerun(
        *,
        item_id: str,
        api_base_url: str,
        promote_source_cases: bool,
        actor: str,
    ):
        return {
            "schema": "echo.browser_desktop_repair_recipe_rerun.v1",
            "item_id": item_id,
            "passed": True,
            "artifacts": [{"url": api_base_url}],
            "promote_source_cases": promote_source_cases,
            "actor": actor,
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.rerun_browser_desktop_repair_recipe_evidence",
        fake_rerun,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app, base_url="http://localhost:8123")

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/verifications/rerun",
        json={"item_id": "rq_recipe"},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["artifacts"][0]["url"] == "http://localhost:8123"


def test_browser_desktop_repair_recipe_rerun_batch_endpoint(monkeypatch) -> None:
    def fake_batch(
        *,
        api_base_url: str,
        promote_source_cases: bool,
        actor: str,
        limit: int,
    ):
        return {
            "schema": "echo.browser_desktop_repair_recipe_rerun_batch.v1",
            "attempted": limit,
            "passed": 1,
            "failed": limit - 1,
            "results": [
                {
                    "passed": True,
                    "promoted_source_cases": promote_source_cases,
                    "api_base_url": api_base_url,
                    "actor": actor,
                }
            ],
        }

    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.rerun_browser_desktop_repair_recipe_batch",
        fake_batch,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app)

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/verifications/rerun-batch",
        json={
            "api_base_url": "http://127.0.0.1:8000",
            "promote_source_cases": True,
            "actor": "operator_test",
            "limit": 3,
        },
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["schema"] == "echo.browser_desktop_repair_recipe_rerun_batch.v1"
    assert data["attempted"] == 3
    assert data["passed"] == 1
    assert data["failed"] == 2


def test_browser_desktop_repair_recipe_rerun_batch_uses_gateway_env(
    monkeypatch,
) -> None:
    def fake_batch(
        *,
        api_base_url: str,
        promote_source_cases: bool,
        actor: str,
        limit: int,
    ):
        return {
            "schema": "echo.browser_desktop_repair_recipe_rerun_batch.v1",
            "attempted": limit,
            "passed": 1,
            "failed": 0,
            "results": [
                {
                    "api_base_url": api_base_url,
                    "promoted_source_cases": promote_source_cases,
                    "actor": actor,
                }
            ],
        }

    monkeypatch.setenv(
        "ECHO_INTERNAL_GATEWAY_BASE_URL",
        "http://127.0.0.1:8777/",
    )
    monkeypatch.setattr(
        "runtime.safety.evolution.browser_desktop_repair_recipes.rerun_browser_desktop_repair_recipe_batch",
        fake_batch,
    )
    app = FastAPI()
    app.include_router(create_evolution_router())
    client = TestClient(app, base_url="http://localhost:8123")

    response = client.post(
        "/api/evolution/browser-desktop-repair-recipes/verifications/rerun-batch",
        json={"limit": 2},
    )
    data = response.json()

    assert response.status_code == 200
    assert data["ok"] is True
    assert data["results"][0]["api_base_url"] == "http://127.0.0.1:8777"

