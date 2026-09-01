from __future__ import annotations

from runtime.safety.recovery.gepa_optimizer import PromptCandidate
from runtime.safety.recovery.native_llm_replay import replay_llm_candidates
from runtime.safety.recovery.native_turn_replay import TurnReplayCase
from runtime.sensing.model_router.models import ModelResponse, ToolCall


class FakeRouter:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests = []

    def call(self, request):
        self.requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return ModelResponse(text="complete final answer, progress marked complete")


def test_llm_replay_scores_real_model_output() -> None:
    candidate = PromptCandidate(
        prompt="When truncated, continue from checkpoint and deliver the complete report.",
        task_scores=[0.8],
    )
    router = FakeRouter(
        [
            ModelResponse(
                text="I will continue from the checkpoint and deliver the complete final report.",
                finish_reason="stop",
            ),
        ]
    )
    case = TurnReplayCase(
        case_id="trunc",
        kind="report_truncation",
        task_input="finish report",
        expected_behavior="continue",
    )

    report = replay_llm_candidates(
        [candidate],
        router=router,
        model="mock",
        cases=[case],
    )

    assert report.candidates[0].passed is True
    assert report.candidates[0].case_results[0].score >= 0.9
    assert router.requests[0].tools


def test_llm_replay_handles_native_tool_call_loop(tmp_path) -> None:
    candidate = PromptCandidate(
        prompt="Default agent mode may use tools; do not claim tools are unavailable.",
        task_scores=[0.8],
    )
    router = FakeRouter(
        [
            ModelResponse(
                text="I will search first.",
                tool_calls=[
                    ToolCall(id="tool-1", name="web_search", input={"query": "agent tools"}),
                ],
                finish_reason="tool_use",
            ),
            ModelResponse(
                text="I used the available tool and can proceed with the answer.",
                finish_reason="stop",
            ),
        ]
    )
    case = TurnReplayCase(
        case_id="tools",
        kind="tool_permission_confusion",
        task_input="research with tools",
        expected_behavior="use tools",
    )

    report = replay_llm_candidates(
        [candidate],
        router=router,
        model="mock",
        cases=[case],
        workspace_root=tmp_path,
    )

    result = report.candidates[0].case_results[0]
    assert result.passed is True
    assert result.tool_calls == ["web_search"]
    assert len(router.requests) == 2


def test_llm_replay_rejects_truncated_output() -> None:
    candidate = PromptCandidate(prompt="Write reports.", task_scores=[0.8])
    router = FakeRouter(
        [
            ModelResponse(text="partial report", finish_reason="length"),
        ]
    )
    case = TurnReplayCase(
        case_id="trunc",
        kind="report_truncation",
        task_input="finish report",
        expected_behavior="continue",
    )

    report = replay_llm_candidates([candidate], router=router, model="mock", cases=[case])

    result = report.candidates[0].case_results[0]
    assert result.passed is False
    assert result.reason == "model output was truncated"
