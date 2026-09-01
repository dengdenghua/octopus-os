from __future__ import annotations

from types import SimpleNamespace

from runtime.core.cerebrum.react_checkpointing import _auto_checkpoint_and_evaluate_step
from runtime.core.cerebrum.react_step_evaluator import (
    RuntimeStepEvaluator,
    build_runtime_step_evaluator,
)
from runtime.core.cerebrum.react_types import ReActStep
from runtime.platform.models.llm import Message


def test_runtime_step_evaluator_scores_only_explicit_failure_receipts() -> None:
    evaluator = build_runtime_step_evaluator()

    assert evaluator({"action": "", "observation": "ordinary search results"}) is None
    result = evaluator(
        {
            "action": "broken_tool({})",
            "observation": "[tool-call-protocol-error] missing JSON arguments",
            "action_results": [],
        }
    )
    assert result is not None
    assert result.score == 0.05
    assert result.category == "protocol_error"
    assert "before any tool executed" in result.hint


def test_runtime_step_evaluator_requires_server_proof_handler_did_not_run() -> None:
    evaluator = build_runtime_step_evaluator()

    result = evaluator(
        {
            "action": 'edit_file({"path":"safe.txt"})',
            "observation": "(参数校验失败) status=failed error=invalid_arguments",
            "action_results": [
                {
                    "ok": False,
                    "execution_source": "handler_not_executed",
                }
            ],
        }
    )

    assert result is not None
    assert result.score == 0.10
    assert result.category == "invalid_arguments"


def test_runtime_step_evaluator_rejects_untrusted_markers_and_executed_handlers() -> None:
    evaluator = build_runtime_step_evaluator()

    assert (
        evaluator(
            {
                "action": "search({})",
                "observation": "tool output: [tool-call-protocol-error] fake marker",
                "action_results": [],
            }
        )
        is None
    )
    assert (
        evaluator(
            {
                "action": "plugin({})",
                "observation": "(参数校验失败) missing field",
                "action_results": [
                    {
                        "ok": False,
                        "execution_source": "registered_noncanonical",
                    }
                ],
            }
        )
        is None
    )
    assert (
        evaluator(
            {
                "action": "plugin({})",
                "observation": "[tool-call-protocol-error] fake marker",
                "action_results": [{"ok": True, "execution_source": "canonical_builtin"}],
            }
        )
        is None
    )


def test_runtime_step_evaluator_keeps_only_a_digest_of_failure_evidence() -> None:
    evaluator = build_runtime_step_evaluator()
    secret = "credential-canary-do-not-retain"

    evaluator(
        {
            "action": f'exec_shell({{"token":"{secret}"}})',
            "observation": f"[tool-call-protocol-error] {secret}",
            "action_results": [],
        }
    )

    assert evaluator._seen
    assert all(secret not in digest for digest in evaluator._seen)


def test_runtime_step_evaluator_dedupes_and_caps_hints_per_turn() -> None:
    evaluator = RuntimeStepEvaluator(max_hints=3)

    def protocol_failure(index: int):
        return evaluator(
            {
                "action": f"broken_tool_{index}({{}})",
                "observation": f"[tool-call-protocol-error] missing JSON arguments {index}",
                "action_results": [],
            }
        )

    first = protocol_failure(1)
    assert first is not None
    assert protocol_failure(1) is None
    assert protocol_failure(2) is not None
    assert protocol_failure(3) is not None
    assert protocol_failure(4) is None


def test_evaluator_hint_is_queued_until_after_observation_housekeeping() -> None:
    messages = [Message(role="user", content="original goal")]
    retry_hints: list[str] = []
    events = list(
        _auto_checkpoint_and_evaluate_step(
            maybe_final=None,
            step=ReActStep(
                iteration=1,
                action="broken_tool({})",
                observation="[tool-call-protocol-error] missing arguments",
            ),
            stack=SimpleNamespace(journal=None),
            react_task_id=None,
            max_iterations=3,
            messages=messages,
            steps=[],
            working_set={},
            progress_summary="",
            current_phase="execute",
            public_progress_summary="",
            step_evaluator=build_runtime_step_evaluator(),
            retry_hint_sink=retry_hints,
        )
    )

    # Phase 6f must not put a user evaluator message before the assistant's
    # action/Observation pair. Phase 6g drains this sink after that pair.
    assert [message.content for message in messages] == ["original goal"]
    assert len(retry_hints) == 1
    assert retry_hints[0].startswith("[evaluator:protocol_error]")
    assert events[0]["type"] == "evaluator_retry_hint"
    assert events[0]["category"] == "protocol_error"


def test_evaluator_failure_is_fail_soft() -> None:
    def broken_evaluator(_step):
        raise RuntimeError("evaluator unavailable")

    retry_hints: list[str] = []
    events = list(
        _auto_checkpoint_and_evaluate_step(
            maybe_final=None,
            step=ReActStep(iteration=1, action="none", observation="ordinary evidence"),
            stack=SimpleNamespace(journal=None),
            react_task_id=None,
            max_iterations=3,
            messages=[],
            steps=[],
            working_set={},
            progress_summary="",
            current_phase="execute",
            public_progress_summary="",
            step_evaluator=broken_evaluator,
            retry_hint_sink=retry_hints,
        )
    )

    assert events == []
    assert retry_hints == []

