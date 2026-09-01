from __future__ import annotations

from runtime.safety.evolution.agent_benchmark import compute_agent_benchmark


def test_agent_benchmark_is_replayable_and_dimensioned() -> None:
    report = compute_agent_benchmark()

    assert report["schema"] == "echo.agent_benchmark.v1"
    assert report["score"] == 1.0
    assert report["ready"] is True
    assert report["passed"] == report["total"] == 12
    assert {
        "general_agent",
        "digital_employee",
        "coding_agent",
        "browser_computer_automation",
        "computer_automation",
        "multi_agent_orchestration",
        "stability",
        "security",
        "domestic_model_compat",
        "ux",
    } <= set(report["by_dimension"])


def test_agent_benchmark_cases_explain_missing_evidence(tmp_path) -> None:
    report = compute_agent_benchmark(root=tmp_path)

    assert report["ready"] is False
    assert report["score"] == 0.0
    assert report["cases"][0]["missing_paths"]
    assert report["next_actions"]


