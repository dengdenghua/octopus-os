from __future__ import annotations

from benchmarks.eval_harness import Trajectory, TrajectoryStep, Verdict
from benchmarks.execution_metrics import aggregate_measurements, measurement_from_trial


def _trajectory(
    *,
    status: str = "completed",
    items: list[dict] | None = None,
    turn_fields: dict | None = None,
) -> Trajectory:
    trajectory = Trajectory(
        trial_id="case.one.0.abc123",
        case_id="case.one",
        started_at=100.0,
        ended_at=101.25,
    )
    turn = {"id": "turn-1", "status": status, "items": items or []}
    turn.update(turn_fields or {})
    trajectory.append("turn_result", turn=turn)
    return trajectory


def test_measurement_uses_real_terminal_verification_and_server_usage() -> None:
    trajectory = _trajectory(
        items=[
            {
                "id": "verify-1",
                "type": "verification",
                "status": "completed",
                "kind": "test",
                "command": "pytest -q",
                "exitCode": 0,
                "summary": "12 passed",
            }
        ]
    )
    trajectory.steps.insert(
        0,
        TrajectoryStep(
            kind="token_usage",
            payload={
                "usage": {
                    "total": {
                        "inputTokens": 120,
                        "outputTokens": 30,
                        "totalTokens": 150,
                    }
                }
            },
            ts=100.5,
        ),
    )

    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=True, reason="fixture verifier passed"),
        backend="codex",
        agent_id="coder",
        model=None,
    )

    assert measurement.execution_success is True
    assert measurement.terminal_status == "completed"
    assert measurement.verification == "passed"
    assert measurement.grader_passed is True
    assert measurement.duration_ms == 1250.0
    assert measurement.usage.reported is True
    assert measurement.usage.input_tokens == 120
    assert measurement.usage.output_tokens == 30
    assert measurement.usage.total_tokens == 150
    # App Server reports tokens but not a dollar price.  Unknown is not zero.
    assert measurement.usage.cost_usd is None
    assert measurement.to_dict()["usage"]["cost_source"] == "not_reported"
    assert measurement.to_dict()["trajectory"]["steps"]


def test_v2_records_schedule_and_distinguishes_requested_from_observed_models() -> None:
    measurement = measurement_from_trial(
        _trajectory(
            turn_fields={
                "params": {"model": "control-plane-resolved-model"},
                "backendModel": "backend-reported-model",
            }
        ),
        Verdict(passed=True),
        backend="codex",
        agent_id="coder",
        model="requested-alias",
        schedule_ordinal=7,
        trial_index=3,
    )

    payload = measurement.to_dict()
    assert payload["schema"] == "echo.engine_execution_measurement.v2"
    assert payload["version"] == 2
    assert payload["schedule_ordinal"] == 7
    assert payload["trial_index"] == 3
    assert payload["model"] == {
        "requested": "requested-alias",
        "observed_control_plane": "control-plane-resolved-model",
        "observed_backend": "backend-reported-model",
        "observed_backend_status": "observed",
    }


def test_outer_model_is_not_misreported_as_observed_backend_model() -> None:
    measurement = measurement_from_trial(
        _trajectory(turn_fields={"params": {"model": "outer-control-plane-alias"}}),
        Verdict(passed=True),
        backend="codex",
        agent_id="coder",
        model="requested-alias",
    )

    assert measurement.observed_control_plane_model == "outer-control-plane-alias"
    assert measurement.observed_backend_model is None
    assert measurement.to_dict()["model"]["observed_backend_status"] == "unattested"


def test_missing_usage_and_terminal_are_unknown_not_zero_or_failure() -> None:
    trajectory = Trajectory(
        trial_id="case.one.0.no-terminal",
        case_id="case.one",
        started_at=2.0,
        ended_at=3.0,
    )
    trajectory.append("error", error={"type": "timeout"})

    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=False, reason="no result"),
        backend="native",
        agent_id="coder",
        model="test-model",
    )

    assert measurement.execution_success is None
    assert measurement.terminal_status is None
    assert measurement.verification == "not_run"
    assert measurement.usage.reported is False
    assert measurement.usage.input_tokens is None
    assert measurement.usage.output_tokens is None
    assert measurement.usage.total_tokens is None
    assert measurement.usage.cost_usd is None
    assert measurement.valid_for_engine_rate is True
    assert measurement.failure_category is None


def test_failed_terminal_is_separate_from_independent_grader_verdict() -> None:
    measurement = measurement_from_trial(
        _trajectory(status="failed"),
        Verdict(passed=True, reason="workspace outcome happened to pass"),
        backend="native",
        agent_id="coder",
        model=None,
    )

    assert measurement.execution_success is False
    assert measurement.terminal_status == "failed"
    assert measurement.grader_passed is True
    assert measurement.to_dict()["execution_success"] is False
    assert measurement.to_dict()["outcome_grader"]["passed"] is True


def test_failed_verification_does_not_get_hidden_by_completed_turn() -> None:
    measurement = measurement_from_trial(
        _trajectory(
            items=[
                {
                    "id": "verify-failed",
                    "type": "verification",
                    "status": "failed",
                    "kind": "lint",
                    "command": "ruff check",
                    "exitCode": 1,
                }
            ]
        ),
        Verdict(passed=False, reason="lint failed"),
        backend="native",
        agent_id="coder",
        model=None,
    )

    assert measurement.execution_success is True
    assert measurement.verification == "failed"
    assert measurement.verification_items[0]["exit_code"] == 1
    assert measurement.grader_passed is False


def test_explicit_zero_cost_is_preserved_as_reported_not_missing() -> None:
    trajectory = _trajectory()
    trajectory.steps.insert(
        0,
        TrajectoryStep(
            kind="token_usage",
            payload={"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}},
            ts=100.5,
        ),
    )

    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=True),
        backend="native",
        agent_id="coder",
        model=None,
    )

    assert measurement.usage.reported is True
    assert measurement.usage.total_tokens == 0
    assert measurement.usage.cost_usd == 0.0
    assert measurement.to_dict()["usage"]["cost_source"] == "server_reported"


def test_infrastructure_failure_makes_grader_result_not_applicable() -> None:
    trajectory = _trajectory(status="failed")
    trajectory.failure_category = "infrastructure"
    trajectory.error = "grader raised: full verifier sandbox unavailable"

    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=False, reason="grader could not run"),
        backend="native",
        agent_id="coder",
        model=None,
    )

    assert measurement.infrastructure_valid is False
    assert measurement.failure_category == "infrastructure"
    assert measurement.infrastructure_reason == trajectory.error
    assert measurement.grader_passed is None
    payload = measurement.to_dict()
    assert payload["infrastructure"] == {
        "valid": False,
        "failure_category": "infrastructure",
        "reason": trajectory.error,
    }
    assert payload["outcome_grader"]["passed"] is None


def test_aggregate_excludes_infrastructure_from_engine_rate_denominator() -> None:
    passed = measurement_from_trial(
        _trajectory(),
        Verdict(passed=True),
        backend="native",
        agent_id="coder",
        model=None,
    )
    failed = measurement_from_trial(
        _trajectory(status="failed"),
        Verdict(passed=False),
        backend="native",
        agent_id="coder",
        model=None,
    )
    infra_trajectory = _trajectory(status="failed")
    infra_trajectory.failure_category = "infrastructure"
    infra_trajectory.error = "runner raised: transport unavailable"
    infrastructure = measurement_from_trial(
        infra_trajectory,
        Verdict(passed=False),
        backend="native",
        agent_id="coder",
        model=None,
    )

    assert aggregate_measurements([passed, failed, infrastructure], requested_k=3) == [
        {
            "backend": "native",
            "case_id": "case.one",
            "requested_k": 3,
            "scheduled": 3,
            "valid": 2,
            "invalid": 1,
            "passes": 1,
            "pass_rate": 0.5,
            "complete": False,
            "pass_at_k": None,
        }
    ]


def test_all_infrastructure_results_have_null_rate_and_pass_at_k() -> None:
    trajectory = _trajectory(status="failed")
    trajectory.failure_category = "infrastructure"
    trajectory.error = "setup failed: no fixture"
    measurement = measurement_from_trial(
        trajectory,
        Verdict(passed=False),
        backend="codex",
        agent_id="coder",
        model=None,
    )

    row = aggregate_measurements([measurement], requested_k=1)[0]
    assert row["valid"] == 0
    assert row["passes"] == 0
    assert row["pass_rate"] is None
    assert row["complete"] is False
    assert row["pass_at_k"] is None

