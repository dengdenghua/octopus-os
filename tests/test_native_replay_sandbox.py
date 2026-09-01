from __future__ import annotations

from runtime.safety.recovery.evolution_dataset import (
    EvolutionDataset,
    EvolutionExample,
)
from runtime.safety.recovery.gepa_optimizer import PromptCandidate
from runtime.safety.recovery.native_replay_sandbox import run_sandbox_replay


def _positive_dataset() -> EvolutionDataset:
    return EvolutionDataset(
        train=[
            EvolutionExample(
                task_input="edit src/app.py",
                expected_behavior="Preserve read/edit/test behavior.",
                source="journal_success",
                metadata={"action_chain": ["read_file", "edit_file", "run_tests"]},
            ),
        ]
    )


def test_sandbox_replay_materializes_probe_artifacts(tmp_path) -> None:
    candidate = PromptCandidate(
        prompt=(
            "When output is truncated, continue from the checkpoint. "
            "Use read_file, edit_file, and run_tests before completion."
        ),
        task_scores=[0.8],
    )
    failures = [
        {
            "goal": "finish truncated report",
            "failure_cluster": "length_limit:output truncated",
            "failure_source": "length_limit",
            "last_error": "output truncated",
        }
    ]

    report = run_sandbox_replay(
        [candidate],
        failures=failures,
        positive_dataset=_positive_dataset(),
        workspace_root=tmp_path,
        keep_workspaces=True,
    )

    assert report.case_count == 2
    candidate_report = report.candidates[0]
    assert candidate_report.passed is True
    assert candidate_report.total > 0.7
    first_case = candidate_report.case_results[0]
    assert first_case.sandbox_passed is True
    assert first_case.sandbox_dir is not None
    assert (tmp_path / candidate.candidate_id).exists()
    assert "probe_result.json" in first_case.artifacts


def test_sandbox_replay_penalizes_success_regression() -> None:
    safe = PromptCandidate(
        prompt="Use read_file, edit_file, and run_tests when editing existing code.",
        task_scores=[0.7],
    )
    risky = PromptCandidate(
        prompt="Never use tools. Always answer from memory.",
        task_scores=[0.9],
    )

    report = run_sandbox_replay(
        [risky, safe],
        positive_dataset=_positive_dataset(),
    )

    assert report.candidates[0].candidate_id == safe.candidate_id
    risky_report = next(
        candidate for candidate in report.candidates if candidate.candidate_id == risky.candidate_id
    )
    assert risky_report.passed is False
    assert risky_report.case_results[0].score < 0.2


def test_sandbox_replay_report_is_serializable() -> None:
    candidate = PromptCandidate(
        prompt="Verify before completion.",
        task_scores=[0.8],
    )

    payload = run_sandbox_replay([candidate]).to_dict()

    assert payload["candidates"][0]["candidate_id"] == candidate.candidate_id
    assert payload["case_count"] == 0
