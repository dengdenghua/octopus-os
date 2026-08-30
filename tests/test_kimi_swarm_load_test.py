from __future__ import annotations

from pathlib import Path

from runtime.memory.control_sessions import ControlSessionStore
from runtime.safety.evolution.kimi_swarm_certification import (
    compute_kimi_swarm_certification,
)
from runtime.safety.evolution.kimi_swarm_load_test import (
    KimiSwarmLoadTestConfig,
    KimiSwarmQuotaProbeConfig,
    best_kimi_swarm_load_test_proof,
    build_kimi_swarm_load_test_preflight,
    build_kimi_swarm_resume_plan,
    export_kimi_swarm_proof_bundle,
    kimi_swarm_load_test_history,
    latest_kimi_swarm_load_test,
    recommend_kimi_swarm_next_stage,
    run_kimi_swarm_load_test,
    run_kimi_swarm_quota_probe,
)
from runtime.sensing.model_router.models import ModelRequest, ModelResponse


def test_kimi_swarm_load_test_writes_control_session_replay(tmp_path: Path) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-dry-1",
            agent_count=5,
            step_count=12,
            max_concurrency=3,
        ),
        store=store,
    )

    assert summary["schema"] == "echo.kimi_swarm_load_test.v1"
    assert summary["agent_count"] == 5
    assert summary["step_count"] == 12
    assert summary["successful_steps"] == 12
    assert summary["failed_steps"] == 0
    assert summary["provider_backed"] is False

    replay = store.replay("load-dry-1", limit=50)
    assert replay["session"]["metadata"]["schema"] == "echo.kimi_swarm_load_test.v1"
    assert replay["actions"][0]["action_type"] == "kimi_swarm_load_test"
    step_evidence = [
        item for item in replay["evidence"] if item["action"] == "kimi_swarm_load_step"
    ]
    assert len(step_evidence) == 12
    assert replay["evidence"][-1]["detail"]["schema"] == ("echo.kimi_swarm_load_test_summary.v1")
    assert replay["timeline"]["count"] >= 14

    latest = latest_kimi_swarm_load_test(store=store)
    assert latest is not None
    assert latest["session_id"] == "load-dry-1"
    assert latest["provider_backed"] is False


def test_kimi_swarm_certification_uses_provider_backed_load_test(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls: list[ModelRequest] = []

    def provider_caller(request: ModelRequest) -> ModelResponse:
        calls.append(request)
        return ModelResponse(
            text='{"ok":true}',
            input_tokens=2,
            output_tokens=1,
            model=request.model,
            provider=request.system_provider,
        )

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-proof",
            provider_id="test_provider",
            model="test-swarm-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert len(calls) == 4000
    report = compute_kimi_swarm_certification(data_dir=tmp_path)

    assert report["summary"]["provider_load_test_ready"] is True
    assert report["verdict"] == "fully_surpassed"
    assert report["remaining_proof"] == []
    assert report["provider_load_test"]["provider_backed"] is True
    assert report["provider_load_test"]["agent_count"] == 300
    assert report["provider_load_test"]["step_count"] == 4000
    assert report["provider_load_test"]["stage_id"] == "provider_full_reference"
    assert report["provider_load_test"]["recorded_step_evidence_count"] == 4000
    assert report["provider_load_test"]["actual_recorded_step_evidence_count"] == 4000
    assert report["provider_load_test"]["replay_evidence_verified"] is True
    assert report["provider_load_test_proof_bundle"]["ready"] is True
    assert report["provider_load_test_proof_bundle"]["step_evidence_count"] == 4000

    bundle = export_kimi_swarm_proof_bundle(store=store)
    assert bundle["schema"] == "echo.kimi_swarm_proof_bundle.v1"
    assert bundle["ready"] is True
    assert len(bundle["sha256"]) == 64
    assert bundle["step_evidence_count"] == 4000
    assert bundle["proof"]["stage_id"] == "provider_full_reference"
    assert bundle["replay_href"] == "/api/control-sessions/load-real-proof/replay"
    assert bundle["timeline_href"] == "/api/control-sessions/load-real-proof/timeline"


def test_best_kimi_swarm_proof_survives_later_canary_run(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-full",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-canary-after-full",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=10,
            estimated_max_tokens=5120,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    history = kimi_swarm_load_test_history(store=store)
    latest = latest_kimi_swarm_load_test(store=store)
    best = best_kimi_swarm_load_test_proof(store=store)
    report = compute_kimi_swarm_certification(data_dir=tmp_path)

    assert len(history) == 2
    assert latest is not None and latest["stage_id"] == "provider_canary"
    assert best is not None and best["stage_id"] == "provider_full_reference"
    assert report["verdict"] == "fully_surpassed"
    assert report["provider_load_test"]["stage_id"] == "provider_canary"
    assert report["provider_load_test_proof"]["stage_id"] == "provider_full_reference"


def test_full_reference_summary_without_step_replay_is_not_best_proof(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-summary-only",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert summary["recorded_step_evidence_count"] == 0
    assert best_kimi_swarm_load_test_proof(store=store) is None
    report = compute_kimi_swarm_certification(data_dir=tmp_path)
    assert report["verdict"] == "deterministic_orchestration_surpassed"
    assert report["provider_load_test_proof"] is None

    bundle = export_kimi_swarm_proof_bundle(store=store)
    assert bundle["ready"] is False
    assert bundle["sha256"] == ""


def test_best_proof_recounts_replay_evidence_instead_of_trusting_summary(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-tampered-summary",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    session = store.get_session("load-real-tampered-summary")
    assert session is not None
    tampered = dict(session["metadata"]["last_kimi_swarm_load_test"])
    tampered["recorded_step_evidence_count"] = 4000
    store.upsert_session(
        session_id="load-real-tampered-summary",
        owner_id=session["owner_id"],
        owner_label=session["owner_label"],
        surface=session["surface"],
        target_id=session["target_id"],
        status=session["status"],
        metadata={
            **session["metadata"],
            "last_kimi_swarm_load_test": tampered,
        },
    )

    latest = latest_kimi_swarm_load_test(store=store)
    assert latest is not None
    assert latest["recorded_step_evidence_count"] == 4000
    assert latest["actual_recorded_step_evidence_count"] == 0
    assert latest["replay_evidence_verified"] is False
    assert best_kimi_swarm_load_test_proof(store=store) is None


def test_kimi_swarm_load_test_requires_real_provider_budget(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    try:
        run_kimi_swarm_load_test(
            config=KimiSwarmLoadTestConfig(
                session_id="load-real-no-budget",
                agent_count=2,
                step_count=3,
                max_concurrency=1,
                real_provider=True,
                confirm_real_provider=True,
                max_provider_calls=2,
                estimated_max_tokens=10,
                stage_id="provider_canary",
            ),
            store=store,
            provider_caller=provider_caller,
        )
    except ValueError as exc:
        assert "max_provider_calls" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("real provider run should require explicit call budget")


def test_kimi_swarm_load_test_enforces_per_step_token_budget(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    max_tokens_seen: list[int | None] = []

    def provider_caller(request: ModelRequest) -> ModelResponse:
        max_tokens_seen.append(request.max_tokens)
        return ModelResponse(
            text="too many tokens",
            input_tokens=1,
            output_tokens=5,
            model=request.model,
            provider=request.system_provider,
        )

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-token-budget",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=10,
            estimated_max_tokens=20,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert max_tokens_seen == [2] * 10
    assert summary["per_step_output_budget"] == 2
    assert summary["successful_steps"] == 0
    assert summary["failed_steps"] == 10

    replay = store.replay("load-real-token-budget", limit=100)
    errors = [
        item["detail"]["error"]
        for item in replay["evidence"]
        if item.get("action") == "kimi_swarm_load_step"
    ]
    assert all("exceeded per-step output budget" in error for error in errors)


def test_kimi_swarm_load_test_retries_transient_provider_errors(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls = 0

    def provider_caller(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("ConnectError: [SSL: UNEXPECTED_EOF_WHILE_READING]")
        return ModelResponse(
            text="ok",
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            provider=request.system_provider,
        )

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-transient-retry",
            provider_id="test_provider",
            model="test-model",
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

    assert calls == 11
    assert summary["successful_steps"] == 10
    assert summary["failed_steps"] == 0

    replay = store.replay("load-real-transient-retry", limit=100)
    retried = [
        item["detail"]
        for item in replay["evidence"]
        if item.get("action") == "kimi_swarm_load_step" and item["detail"]["attempt_count"] == 2
    ]
    assert len(retried) == 1
    assert retried[0]["attempts"][0]["retryable"] is True
    assert retried[0]["attempts"][1]["ok"] is True


def test_kimi_swarm_quota_probe_success_allows_resume(tmp_path: Path) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls: list[ModelRequest] = []

    def provider_caller(request: ModelRequest) -> ModelResponse:
        calls.append(request)
        return ModelResponse(
            text='{"ok":true}',
            input_tokens=1,
            output_tokens=1,
            model=request.model,
            provider=request.system_provider,
        )

    result = run_kimi_swarm_quota_probe(
        config=KimiSwarmQuotaProbeConfig(
            session_id="quota-probe-ok",
            provider_id="volcengine_ark",
            model="kimi-k3",
            confirm_real_provider=True,
            max_tokens=7,
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert result["schema"] == "echo.kimi_swarm_quota_probe.v1"
    assert result["ok"] is True
    assert result["can_resume_provider_load_test"] is True
    assert result["provider_quota_limited"] is False
    assert result["failure_category"] == ""
    assert calls[0].model == "kimi-k3"
    assert calls[0].system_provider == "volcengine_ark"
    assert calls[0].max_tokens == 7

    replay = store.replay("quota-probe-ok", limit=20)
    assert replay["session"]["status"] == "idle"
    assert replay["actions"][0]["action_type"] == "kimi_swarm_quota_probe"
    assert replay["actions"][0]["status"] == "done"
    assert replay["evidence"][0]["detail"]["can_resume_provider_load_test"] is True


def test_kimi_swarm_quota_probe_reports_provider_quota_limit(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls = 0

    def provider_caller(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")

    result = run_kimi_swarm_quota_probe(
        config=KimiSwarmQuotaProbeConfig(
            session_id="quota-probe-blocked",
            provider_id="kimi_coding",
            model="kimi-for-coding",
            confirm_real_provider=True,
            max_tokens=7,
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert calls == 1
    assert result["schema"] == "echo.kimi_swarm_quota_probe.v1"
    assert result["ok"] is False
    assert result["can_resume_provider_load_test"] is False
    assert result["provider_quota_limited"] is True
    assert result["failure_category"] == "provider_quota_limit"

    replay = store.replay("quota-probe-blocked", limit=20)
    assert replay["session"]["status"] == "paused"
    assert replay["actions"][0]["status"] == "failed"
    assert replay["evidence"][0]["detail"]["provider_quota_limited"] is True


def test_kimi_swarm_quota_probe_reports_provider_rate_limit(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        raise RuntimeError("OpenAIRouterError: http_429: too many requests")

    result = run_kimi_swarm_quota_probe(
        config=KimiSwarmQuotaProbeConfig(
            session_id="quota-probe-rate-limited",
            provider_id="kimi_coding",
            model="kimi-for-coding",
            confirm_real_provider=True,
            max_tokens=7,
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert result["ok"] is False
    assert result["can_resume_provider_load_test"] is False
    assert result["provider_quota_limited"] is False
    assert result["provider_rate_limited"] is True
    assert result["failure_category"] == "provider_rate_limit"

    replay = store.replay("quota-probe-rate-limited", limit=20)
    assert replay["evidence"][0]["detail"]["provider_rate_limited"] is True


def test_kimi_swarm_next_stage_blocks_after_provider_quota_limit(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls = 0

    def provider_caller(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-quota-block",
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

    assert calls == 10
    assert summary["failed_steps"] == 10
    assert summary["failure_summary"]["primary_category"] == "provider_quota_limit"
    assert summary["failure_summary"]["provider_quota_limited"] is True

    recommendation = recommend_kimi_swarm_next_stage(
        store=store,
        provider_configured=True,
    )
    assert recommendation["next_stage"] == "provider_canary"
    assert recommendation["can_run_recommended_payload"] is False
    assert recommendation["latest_blocking_failure"]["category"] == ("provider_quota_limit")
    assert recommendation["quota_probe_payload"] == {
        "provider_id": "volcengine_ark",
        "model": "kimi-k3",
        "confirm_real_provider": True,
        "max_tokens": 16,
    }
    assert "quota" in recommendation["next_action"].lower()


def test_kimi_swarm_resume_plan_uses_failed_full_reference_steps_only(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def ok_provider(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="resume-ramp-proof",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=3,
            step_count=12,
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=12,
            estimated_max_tokens=6144,
            stage_id="provider_ramp",
        ),
        store=store,
        provider_caller=ok_provider,
    )

    calls = 0

    def quota_after_seven(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 7:
            raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="resume-full-quota",
            provider_id="volcengine_ark",
            model="kimi-k3",
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

    plan = build_kimi_swarm_resume_plan(
        provider_id="volcengine_ark",
        model="kimi-k3",
        agent_count=3,
        step_count=12,
        max_concurrency=1,
        store=store,
    )
    recommendation = recommend_kimi_swarm_next_stage(
        provider_id="volcengine_ark",
        model="kimi-k3",
        agent_count=3,
        step_count=12,
        max_concurrency=1,
        store=store,
        provider_configured=True,
    )

    assert plan["schema"] == "echo.kimi_swarm_resume_plan.v1"
    assert plan["ready"] is True
    assert plan["source_session_id"] == "resume-full-quota"
    assert plan["successful_step_count"] == 7
    assert plan["remaining_step_count"] == 5
    assert plan["remaining_step_ranges"] == [{"start": 7, "end": 11, "count": 5}]
    assert plan["recommended_payload"]["stage_id"] == "provider_full_reference_resume"
    assert plan["recommended_payload"]["max_provider_calls"] == 5

    assert recommendation["next_stage"] == "provider_full_reference"
    assert recommendation["resume_plan"]["remaining_step_count"] == 5
    assert recommendation["recommended_payload"]["stage_id"] == ("provider_full_reference_resume")
    assert recommendation["recommended_preflight"]["mode"] == "real_provider_resume"
    assert recommendation["recommended_preflight"]["selected_stage"]["step_count"] == 5
    assert recommendation["can_run_recommended_payload"] is False
    assert recommendation["quota_probe_payload"]["model"] == "kimi-k3"
    assert "resume 5 remaining" in recommendation["next_action"]

    resumed_indices: list[int] = []

    def resume_provider(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    resume_payload = plan["recommended_payload"]
    resume_summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="resume-full-repair",
            provider_id=resume_payload["provider_id"],
            model=resume_payload["model"],
            agent_count=resume_payload["agent_count"],
            step_count=resume_payload["step_count"],
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=resume_payload["max_provider_calls"],
            estimated_max_tokens=resume_payload["estimated_max_tokens"],
            stage_id=resume_payload["stage_id"],
            resume_from_session_id=resume_payload["resume_from_session_id"],
            resume_step_ranges=tuple(resume_payload["resume_step_ranges"]),
        ),
        store=store,
        provider_caller=resume_provider,
    )
    replay = store.replay("resume-full-repair", limit=50)
    for item in replay["evidence"]:
        if item.get("action") == "kimi_swarm_load_step":
            resumed_indices.append(item["detail"]["step_index"])

    assert resume_summary["stage_id"] == "provider_full_reference_resume"
    assert resume_summary["step_count"] == 5
    assert resume_summary["reference_step_count"] == 12
    assert resume_summary["resume_from_session_id"] == "resume-full-quota"
    assert resume_summary["successful_steps"] == 5
    assert resumed_indices == [7, 8, 9, 10, 11]


def test_kimi_swarm_resume_plan_accumulates_partial_resume_successes(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    calls = 0

    def quota_after_seven(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 7:
            raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="partial-resume-source",
            provider_id="volcengine_ark",
            model="kimi-k3",
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

    plan = build_kimi_swarm_resume_plan(
        provider_id="volcengine_ark",
        model="kimi-k3",
        agent_count=3,
        step_count=12,
        max_concurrency=1,
        store=store,
    )
    assert plan["remaining_step_ranges"] == [{"start": 7, "end": 11, "count": 5}]

    resume_calls = 0

    def quota_after_two_resume_steps(request: ModelRequest) -> ModelResponse:
        nonlocal resume_calls
        resume_calls += 1
        if resume_calls > 2:
            raise RuntimeError("OpenAIRouterError: http_429: too many requests")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    payload = plan["recommended_payload"]
    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="partial-resume-first",
            provider_id=payload["provider_id"],
            model=payload["model"],
            agent_count=payload["agent_count"],
            step_count=payload["step_count"],
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=payload["max_provider_calls"],
            estimated_max_tokens=payload["estimated_max_tokens"],
            stage_id=payload["stage_id"],
            resume_from_session_id=payload["resume_from_session_id"],
            resume_step_ranges=tuple(payload["resume_step_ranges"]),
        ),
        store=store,
        provider_caller=quota_after_two_resume_steps,
    )

    updated = build_kimi_swarm_resume_plan(
        provider_id="volcengine_ark",
        model="kimi-k3",
        agent_count=3,
        step_count=12,
        max_concurrency=1,
        store=store,
    )

    assert updated["partial_resume_session_ids"] == ["partial-resume-first"]
    assert updated["covered_step_count"] == 9
    assert updated["successful_step_count"] == 9
    assert updated["failed_step_count"] == 3
    assert updated["remaining_step_count"] == 3
    assert updated["remaining_step_ranges"] == [{"start": 9, "end": 11, "count": 3}]
    assert updated["recommended_payload"]["max_provider_calls"] == 3


def test_kimi_swarm_composite_proof_accepts_full_reference_resume(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def ok_provider(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="composite-ramp-proof",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=300,
            estimated_max_tokens=153600,
            stage_id="provider_ramp",
        ),
        store=store,
        provider_caller=ok_provider,
    )

    calls = 0

    def quota_after_2538(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 2538:
            raise RuntimeError("OpenAIRouterError: http_429: usage limit quota")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="composite-full-quota",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=4000,
            estimated_max_tokens=2_048_000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=quota_after_2538,
    )
    plan = build_kimi_swarm_resume_plan(store=store)
    assert plan["remaining_step_count"] == 1462

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="composite-full-resume",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=16,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=plan["recommended_payload"]["max_provider_calls"],
            estimated_max_tokens=plan["recommended_payload"]["estimated_max_tokens"],
            stage_id=plan["recommended_payload"]["stage_id"],
            resume_from_session_id=plan["recommended_payload"]["resume_from_session_id"],
            resume_step_ranges=tuple(plan["recommended_payload"]["resume_step_ranges"]),
        ),
        store=store,
        provider_caller=ok_provider,
    )

    proof = best_kimi_swarm_load_test_proof(store=store)
    bundle = export_kimi_swarm_proof_bundle(store=store)
    report = compute_kimi_swarm_certification(data_dir=tmp_path)
    recommendation = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)

    assert proof is not None
    assert proof["schema"] == "echo.kimi_swarm_composite_proof.v1"
    assert proof["composite"] is True
    assert proof["successful_steps"] == 4000
    assert proof["failed_steps"] == 0
    assert proof["actual_recorded_step_evidence_count"] == 4000
    assert proof["resume_session_ids"] == ["composite-full-resume"]

    assert bundle["ready"] is True
    assert bundle["composite"] is True
    assert bundle["step_evidence_count"] == 4000
    assert bundle["raw_step_evidence_count"] == 5462
    assert len(bundle["sha256"]) == 64

    assert report["verdict"] == "fully_surpassed"
    assert report["summary"]["provider_load_test_ready"] is True
    assert recommendation["next_stage"] == "complete"
    assert recommendation["ready"] is True


def test_kimi_swarm_composite_proof_accumulates_partial_resume_sessions(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    calls = 0

    def quota_after_3998(request: ModelRequest) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls > 3998:
            raise RuntimeError("OpenAIRouterError: http_429: too many requests")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="partial-composite-source",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=4000,
            estimated_max_tokens=2_048_000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=quota_after_3998,
    )
    plan = build_kimi_swarm_resume_plan(store=store)
    assert plan["remaining_step_ranges"] == [{"start": 3998, "end": 3999, "count": 2}]

    resume_calls = 0

    def quota_after_one_resume_step(request: ModelRequest) -> ModelResponse:
        nonlocal resume_calls
        resume_calls += 1
        if resume_calls > 1:
            raise RuntimeError("OpenAIRouterError: http_429: too many requests")
        return ModelResponse(text="ok", input_tokens=1, output_tokens=1)

    payload = plan["recommended_payload"]
    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="partial-composite-resume-one",
            provider_id=payload["provider_id"],
            model=payload["model"],
            agent_count=payload["agent_count"],
            step_count=payload["step_count"],
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=payload["max_provider_calls"],
            estimated_max_tokens=payload["estimated_max_tokens"],
            stage_id=payload["stage_id"],
            resume_from_session_id=payload["resume_from_session_id"],
            resume_step_ranges=tuple(payload["resume_step_ranges"]),
        ),
        store=store,
        provider_caller=quota_after_one_resume_step,
    )

    updated = build_kimi_swarm_resume_plan(store=store)
    assert updated["partial_resume_session_ids"] == ["partial-composite-resume-one"]
    assert updated["remaining_step_ranges"] == [{"start": 3999, "end": 3999, "count": 1}]

    payload = updated["recommended_payload"]
    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="partial-composite-resume-two",
            provider_id=payload["provider_id"],
            model=payload["model"],
            agent_count=payload["agent_count"],
            step_count=payload["step_count"],
            max_concurrency=1,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=payload["max_provider_calls"],
            estimated_max_tokens=payload["estimated_max_tokens"],
            stage_id=payload["stage_id"],
            resume_from_session_id=payload["resume_from_session_id"],
            resume_step_ranges=tuple(payload["resume_step_ranges"]),
        ),
        store=store,
        provider_caller=lambda request: ModelResponse(text="ok", input_tokens=1, output_tokens=1),
    )

    proof = best_kimi_swarm_load_test_proof(store=store)
    bundle = export_kimi_swarm_proof_bundle(store=store)

    assert proof is not None
    assert proof["schema"] == "echo.kimi_swarm_composite_proof.v1"
    assert proof["successful_steps"] == 4000
    assert proof["failed_steps"] == 0
    assert proof["resume_session_ids"] == [
        "partial-composite-resume-one",
        "partial-composite-resume-two",
    ]
    assert proof["coverage"]["resume_failed_step_count"] == 0

    assert bundle["ready"] is True
    assert bundle["step_evidence_count"] == 4000
    assert bundle["raw_step_evidence_count"] == 4003


def test_kimi_swarm_load_test_canary_stage_does_not_claim_full_reference(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    calls: list[ModelRequest] = []

    def provider_caller(request: ModelRequest) -> ModelResponse:
        calls.append(request)
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    summary = run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-real-canary",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )

    assert summary["stage_id"] == "provider_canary"
    assert summary["agent_count"] == 10
    assert summary["step_count"] == 10
    assert summary["requested_agent_count"] == 300
    assert summary["requested_step_count"] == 4000
    assert summary["meets_kimi_reference"] is False
    assert len(calls) == 10

    report = compute_kimi_swarm_certification(data_dir=tmp_path)
    assert report["verdict"] == "deterministic_orchestration_surpassed"
    assert report["remaining_proof"][0]["status"] == "insufficient"


def test_kimi_swarm_load_test_preflight_explains_stage_plan() -> None:
    dry = build_kimi_swarm_load_test_preflight(
        config=KimiSwarmLoadTestConfig(agent_count=300, step_count=4000)
    )
    assert dry["schema"] == "echo.kimi_swarm_load_test_preflight.v1"
    assert dry["ready"] is True
    assert dry["reference_ready"] is True
    assert dry["stage_plan"]["schema"] == "echo.kimi_swarm_load_stage_plan.v1"
    assert [stage["id"] for stage in dry["stage_plan"]["stages"]] == ["dry_replay"]

    real_full = build_kimi_swarm_load_test_preflight(
        config=KimiSwarmLoadTestConfig(
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
        ),
        provider_configured=True,
    )
    assert real_full["ready"] is False
    assert real_full["previous_stage"] == "provider_ramp"
    assert real_full["previous_stage_ready"] is False
    assert [stage["id"] for stage in real_full["stage_plan"]["stages"]] == [
        "provider_canary",
        "provider_ramp",
        "provider_full_reference",
    ]

    real_canary = build_kimi_swarm_load_test_preflight(
        config=KimiSwarmLoadTestConfig(
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            max_provider_calls=10,
            estimated_max_tokens=5120,
            stage_id="provider_canary",
        ),
        provider_configured=True,
    )
    assert real_canary["ready"] is True
    assert real_canary["provider_ready"] is True
    assert real_canary["previous_stage"] == ""


def test_kimi_swarm_load_test_preflight_blocks_unconfirmed_real_provider() -> None:
    report = build_kimi_swarm_load_test_preflight(
        config=KimiSwarmLoadTestConfig(
            agent_count=300,
            step_count=4000,
            real_provider=True,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
        ),
        provider_configured=False,
    )

    assert report["ready"] is False
    assert report["provider_ready"] is False
    assert {check["id"] for check in report["blocking_failures"]} >= {
        "real_provider_confirmed",
        "provider_configured",
    }


def test_kimi_swarm_load_test_preflight_enforces_provider_stage_order(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    ramp_cfg = KimiSwarmLoadTestConfig(
        session_id="stage-ramp",
        provider_id="test_provider",
        model="test-model",
        agent_count=300,
        step_count=4000,
        max_concurrency=32,
        real_provider=True,
        confirm_real_provider=True,
        record_every_step=False,
        max_provider_calls=300,
        estimated_max_tokens=600,
        stage_id="provider_ramp",
    )
    ramp_before = build_kimi_swarm_load_test_preflight(
        config=ramp_cfg,
        provider_configured=True,
        store=store,
    )
    assert ramp_before["ready"] is False
    assert ramp_before["previous_stage"] == "provider_canary"
    assert ramp_before["previous_stage_ready"] is False

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-canary-summary-only",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=10,
            estimated_max_tokens=5120,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    ramp_after_summary_only = build_kimi_swarm_load_test_preflight(
        config=ramp_cfg,
        provider_configured=True,
        store=store,
    )
    assert ramp_after_summary_only["ready"] is False
    assert ramp_after_summary_only["previous_stage_ready"] is False

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-canary",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
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
    ramp_after = build_kimi_swarm_load_test_preflight(
        config=ramp_cfg,
        provider_configured=True,
        store=store,
    )
    assert ramp_after["ready"] is True
    assert ramp_after["previous_stage_ready"] is True

    full_cfg = KimiSwarmLoadTestConfig(
        session_id="stage-full",
        provider_id="test_provider",
        model="test-model",
        agent_count=300,
        step_count=4000,
        max_concurrency=32,
        real_provider=True,
        confirm_real_provider=True,
        record_every_step=False,
        max_provider_calls=4000,
        estimated_max_tokens=8000,
        stage_id="provider_full_reference",
    )
    full_before = build_kimi_swarm_load_test_preflight(
        config=full_cfg,
        provider_configured=True,
        store=store,
    )
    assert full_before["ready"] is False
    assert full_before["previous_stage"] == "provider_ramp"
    assert full_before["previous_stage_ready"] is False

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-ramp",
            provider_id="test_provider",
            model="test-model",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=300,
            estimated_max_tokens=600,
            stage_id="provider_ramp",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    full_after = build_kimi_swarm_load_test_preflight(
        config=full_cfg,
        provider_configured=True,
        store=store,
    )
    assert full_after["ready"] is True
    assert full_after["previous_stage_ready"] is True


def test_kimi_swarm_certification_keeps_dry_run_as_remaining_proof(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")
    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="load-dry-proof",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=False,
            record_every_step=False,
        ),
        store=store,
    )

    report = compute_kimi_swarm_certification(data_dir=tmp_path)

    assert report["summary"]["provider_load_test_ready"] is False
    assert report["verdict"] == "deterministic_orchestration_surpassed"
    assert report["remaining_proof"][0]["status"] == "dry_run_only"


def test_kimi_swarm_next_stage_defaults_to_kimi_k3_reference(
    tmp_path: Path,
) -> None:
    recommendation = recommend_kimi_swarm_next_stage(
        provider_configured=False,
        data_dir=tmp_path,
    )

    assert recommendation["schema"] == "echo.kimi_swarm_next_stage.v1"
    assert recommendation["ready"] is False
    assert recommendation["proof_ready"] is False
    assert recommendation["next_stage"] == "provider_canary"
    assert recommendation["provider_id"] == "volcengine_ark"
    assert recommendation["model"] == "kimi-k3"
    assert recommendation["provider_configured"] is False
    assert recommendation["provider_configuration_state"] == "missing"
    assert recommendation["can_run_recommended_payload"] is False
    assert recommendation["recommended_payload"]["real_provider"] is True
    assert recommendation["recommended_payload"]["stage_id"] == "provider_canary"
    assert recommendation["recommended_payload"]["max_provider_calls"] == 10
    assert recommendation["recommended_preflight"]["ready"] is False
    assert recommendation["recommended_preflight"]["blocking_failures"][0]["id"] == (
        "provider_configured"
    )
    assert "Configure the Kimi K3 custom model" in recommendation["next_action"]


def test_kimi_swarm_next_stage_advances_only_with_replay_backed_stage_proofs(
    tmp_path: Path,
) -> None:
    store = ControlSessionStore(tmp_path / "control_sessions")

    def provider_caller(request: ModelRequest) -> ModelResponse:
        return ModelResponse(text="ok", model=request.model, provider=request.system_provider)

    first = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)
    assert first["next_stage"] == "provider_canary"
    assert first["can_run_recommended_payload"] is True

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-canary-summary-only-next",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=False,
            max_provider_calls=10,
            estimated_max_tokens=5120,
            stage_id="provider_canary",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    summary_only = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)
    assert summary_only["next_stage"] == "provider_canary"
    assert summary_only["stage_proofs"]["provider_canary"] is None

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-canary-next",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
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
    after_canary = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)
    assert after_canary["next_stage"] == "provider_ramp"
    assert after_canary["recommended_payload"]["max_provider_calls"] == 300
    assert after_canary["stage_proofs"]["provider_canary"]["stage_id"] == ("provider_canary")

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-ramp-next",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=300,
            estimated_max_tokens=600,
            stage_id="provider_ramp",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    after_ramp = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)
    assert after_ramp["next_stage"] == "provider_full_reference"
    assert after_ramp["recommended_payload"]["max_provider_calls"] == 4000

    run_kimi_swarm_load_test(
        config=KimiSwarmLoadTestConfig(
            session_id="stage-full-next",
            provider_id="volcengine_ark",
            model="kimi-k3",
            agent_count=300,
            step_count=4000,
            max_concurrency=32,
            real_provider=True,
            confirm_real_provider=True,
            record_every_step=True,
            max_provider_calls=4000,
            estimated_max_tokens=8000,
            stage_id="provider_full_reference",
        ),
        store=store,
        provider_caller=provider_caller,
    )
    complete = recommend_kimi_swarm_next_stage(store=store, provider_configured=True)
    assert complete["ready"] is True
    assert complete["proof_ready"] is True
    assert complete["next_stage"] == "complete"
    assert complete["recommended_payload"] is None
    assert complete["recommended_preflight"] is None
    assert complete["can_run_recommended_payload"] is False

