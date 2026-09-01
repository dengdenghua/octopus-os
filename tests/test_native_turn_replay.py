from __future__ import annotations

from runtime.safety.recovery.gepa_optimizer import PromptCandidate
from runtime.safety.recovery.native_turn_replay import (
    TurnReplayCase,
    build_turn_replay_cases,
    replay_turn_candidates,
)


def test_build_turn_replay_cases_classifies_recent_failures() -> None:
    cases = build_turn_replay_cases(
        failures=[
            {
                "goal": "输出完整调研报告",
                "failure_cluster": "length_limit:output truncated",
                "last_error": "finish_reason length",
            },
            {
                "goal": "默认 agent 调用搜索技能",
                "failure_source": "tool permission confusion",
                "last_error": "无法调用工具",
            },
            {
                "goal": "报告已输出但最后一步仍转圈",
                "failure_source": "final step stuck",
                "last_error": "progress in_progress",
            },
        ]
    )

    assert [case.kind for case in cases] == [
        "report_truncation",
        "tool_permission_confusion",
        "final_step_stuck",
    ]
    assert cases[0].weight > 1.0


def test_turn_replay_prefers_prompt_that_covers_real_turn_failures() -> None:
    good = PromptCandidate(
        prompt=(
            "If finish_reason is length or output is truncated, continue from "
            "the last checkpoint until the complete final answer/report is delivered. "
            "Default agent mode may use tools/skills; only inspiration discussion mode "
            "is talk-first, so do not claim tools are unavailable. After final answer, "
            "mark todo/progress complete and stop the active step."
        ),
        task_scores=[0.6],
    )
    bad = PromptCandidate(
        prompt=(
            "Never use tools. If the answer is long, only summarize instead. "
            "Keep running after final."
        ),
        task_scores=[0.9],
    )
    cases = [
        TurnReplayCase(
            case_id="trunc",
            kind="report_truncation",
            task_input="write a full report",
            expected_behavior="continue",
        ),
        TurnReplayCase(
            case_id="tools",
            kind="tool_permission_confusion",
            task_input="research with tools",
            expected_behavior="use tools",
        ),
        TurnReplayCase(
            case_id="spinner",
            kind="final_step_stuck",
            task_input="finish report",
            expected_behavior="close progress",
        ),
    ]

    report = replay_turn_candidates([bad, good], cases=cases)

    assert report.candidates[0].candidate_id == good.candidate_id
    assert report.candidates[0].passed is True
    bad_report = next(
        candidate for candidate in report.candidates if candidate.candidate_id == bad.candidate_id
    )
    assert bad_report.passed is False
    assert bad_report.total < 0.5


def test_turn_replay_report_is_serializable() -> None:
    candidate = PromptCandidate(
        prompt="When truncated, continue from checkpoint.",
        task_scores=[0.5],
    )
    payload = replay_turn_candidates([candidate]).to_dict()

    assert payload["candidates"][0]["candidate_id"] == candidate.candidate_id
    assert payload["cases"] == []
