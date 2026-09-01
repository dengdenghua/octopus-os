from __future__ import annotations

from runtime.safety.recovery.evolution_dataset import (
    EvolutionDataset,
    EvolutionExample,
)
from runtime.safety.recovery.gepa_optimizer import PromptCandidate
from runtime.safety.recovery.native_replay import (
    build_replay_cases,
    replay_candidates,
)


def _positive_dataset() -> EvolutionDataset:
    return EvolutionDataset(
        train=[
            EvolutionExample(
                task_input="edit src/app.py",
                expected_behavior="Preserve the successful read, edit, and verification flow.",
                source="journal_success",
                category="successful_tool_chain",
                metadata={"action_chain": ["read_file", "edit_file", "run_tests"]},
            ),
        ]
    )


def test_build_replay_cases_weights_repeated_failure_clusters() -> None:
    cases = build_replay_cases(
        failures=[
            {
                "goal": "fix truncated report",
                "failure_cluster": "length_limit:output truncated",
                "failure_cluster_count": 4,
                "turn_id": "turn-1",
            }
        ],
        positive_dataset=_positive_dataset(),
    )

    assert len(cases) == 2
    assert cases[0].case_id == "turn-1"
    assert cases[0].kind == "failure"
    assert cases[0].weight == 4.0
    assert cases[1].kind == "positive"
    assert cases[1].weight == 0.7


def test_replay_candidates_prefers_failure_coverage_and_success_preservation() -> None:
    failures = [
        {
            "goal": "fix truncated report",
            "failure_cluster": "length_limit:output truncated",
            "failure_source": "length_limit",
            "last_error": "output truncated after max_tokens",
            "failure_cluster_count": 3,
        }
    ]
    good = PromptCandidate(
        prompt=(
            "When output reaches max_tokens, continue or resume from the last "
            "checkpoint. Use read_file, edit_file, and run_tests before completion."
        ),
        task_scores=[0.8],
    )
    generic = PromptCandidate(
        prompt="Handle tasks carefully.",
        task_scores=[0.8],
    )

    report = replay_candidates(
        [generic, good],
        failures=failures,
        positive_dataset=_positive_dataset(),
    )

    assert report.candidates[0].candidate_id == good.candidate_id
    assert report.candidates[0].total > report.candidates[1].total
    assert report.candidates[0].case_results[0].score >= 0.7


def test_replay_candidates_penalizes_success_path_regressions() -> None:
    safe = PromptCandidate(
        prompt="Use read_file, edit_file, and run_tests when changing existing code.",
        task_scores=[0.7],
    )
    risky = PromptCandidate(
        prompt="Never use tools. Always answer from memory.",
        task_scores=[0.9],
    )

    report = replay_candidates(
        [risky, safe],
        positive_dataset=_positive_dataset(),
    )

    assert report.candidates[0].candidate_id == safe.candidate_id
    risky_report = next(
        candidate for candidate in report.candidates if candidate.candidate_id == risky.candidate_id
    )
    assert risky_report.case_results[0].score == 0.0
    assert "success regression risk" in risky_report.reasons[0]


def test_replay_report_is_serializable() -> None:
    candidate = PromptCandidate(
        prompt="Verify work before completion.",
        task_scores=[0.8],
    )

    payload = replay_candidates([candidate]).to_dict()

    assert payload["candidates"][0]["candidate_id"] == candidate.candidate_id
    assert payload["candidates"][0]["native_score"]["candidate_id"] == candidate.candidate_id
    assert payload["cases"] == []
