"""Regression tests for structured failure classification + turn disposition.

Covers the three "why did my turn block" fixes:

* ``classify_tool_failure`` / ``classify_turn_failure`` — map cryptic tool
  failures (pnpm no-TTY purge, network, permission, missing binary, git hook)
  to a readable ``{kind, code, readable}`` the gateway surfaces verbatim.
* ``_react_completion_receipt`` — attaches ``receipt["failure"]`` on failed
  turns so the realtime layer has the structured reason.
* ``_turn_disposition`` — a failed turn whose final answer is a *genuine*
  tight-marker hand-off is disposed ``blocked_on_user`` (waiting), not
  ``failed``; guard impasses and short reports that merely mention token/
  permission stay failed (honest downgrade only).
* gateway ``_apply_react_event`` — a ``react_completed`` failure builds
  ``turn.error`` / ``turn.outcome_reason`` from the structured reason.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from runtime.core.cerebrum._react_execution_results import (
    _has_structured_user_block,
    _react_completion_receipt,
    classify_turn_failure,
)
from runtime.core.cerebrum._react_failure_classification import (
    classify_tool_failure,
)
from runtime.core.cerebrum.completion_decision import decide_completion
from runtime.core.cerebrum.react_terminal import _turn_disposition

_PNPM_STDERR = (
    "[ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY] "
    "Aborted removal of modules directory due to no TTY"
)


def _failed_step(tool_name: str, output: dict, *, status: str = "failed") -> SimpleNamespace:
    return SimpleNamespace(
        action=SimpleNamespace(sucker_id=tool_name, name=tool_name),
        result=SimpleNamespace(
            status=status,
            output=output,
            error_type="non_zero_exit",
        ),
    )


def _success_step(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        action=SimpleNamespace(sucker_id=tool_name, name=tool_name),
        result=SimpleNamespace(status="success", output={"success": True}),
    )


# ══════════════════════════════════════════════════════════
# classify_tool_failure — pattern taxonomy
# ══════════════════════════════════════════════════════════


class TestClassifyToolFailure:
    def test_pnpm_no_tty_purge(self) -> None:
        got = classify_tool_failure("git_commit", _PNPM_STDERR)
        assert got is not None
        assert got["kind"] == "environment"
        assert got["code"] == "pnpm_modules_purge_no_tty"
        assert "pnpm" in got["readable"]

    def test_husky_commit_msg_with_pnpm_prefers_environment(self) -> None:
        # The real pnpm+husky stack: both markers present, but the pnpm
        # no-TTY purge is the actual cause and must win over git_hook.
        detail = f"{_PNPM_STDERR} husky - commit-msg script failed (code 1)"
        got = classify_tool_failure("git_commit", detail)
        assert got is not None
        assert got["kind"] == "environment"
        assert got["code"] == "pnpm_modules_purge_no_tty"

    def test_network_unreachable(self) -> None:
        got = classify_tool_failure("exec_shell", "network is unreachable")
        assert got is not None
        assert got["kind"] == "environment"
        assert got["code"] == "network_unavailable"

    def test_econnrefused(self) -> None:
        got = classify_tool_failure(
            "fetch_page", "ConnectionRefusedError: [Errno 61] Connection refused"
        )
        assert got is not None
        assert got["code"] == "network_unavailable"

    def test_permission_denied(self) -> None:
        got = classify_tool_failure("exec_shell", "PermissionError: [Errno 13] Permission denied")
        assert got is not None
        assert got["code"] == "permission_denied"

    def test_command_not_found(self) -> None:
        got = classify_tool_failure("exec_shell", "zsh: command not found: pnpm")
        assert got is not None
        assert got["code"] == "tool_not_found"

    def test_bare_git_hook_rejection(self) -> None:
        # No environmental marker — the hook itself rejected the commit.
        got = classify_tool_failure("git_commit", "husky - commit-msg script failed (code 1)")
        assert got is not None
        assert got["kind"] == "git_hook"
        assert got["code"] == "git_hook_rejected"

    def test_unrelated_failure_is_none(self) -> None:
        assert (
            classify_tool_failure("exec_shell", "TypeError: 'NoneType' is not subscriptable")
            is None
        )

    def test_empty_detail_is_none(self) -> None:
        assert classify_tool_failure("exec_shell", "  ") is None


# ══════════════════════════════════════════════════════════
# classify_turn_failure — trajectory walk
# ══════════════════════════════════════════════════════════


class TestClassifyTurnFailure:
    def test_latest_failed_pnpm_step(self) -> None:
        steps = [
            _success_step("read_file"),
            _failed_step("git_commit", {"stderr": _PNPM_STDERR}),
        ]
        got = classify_turn_failure(steps)
        assert got is not None
        assert got["kind"] == "environment"
        assert got["code"] == "pnpm_modules_purge_no_tty"
        assert got["tool"] == "git_commit"

    def test_unrecoverable_step_without_marker_is_none(self) -> None:
        steps = [_failed_step("run_tests", {"stderr": "AssertionError: boom"})]
        assert classify_turn_failure(steps) is None

    def test_recovered_failure_not_classified(self) -> None:
        # The failed git_commit was followed by a successful retry — not the
        # failure the user hit.
        steps = [
            _failed_step("git_commit", {"stderr": _PNPM_STDERR}),
            _success_step("exec_shell"),
        ]
        assert classify_turn_failure(steps) is None

    def test_all_success_is_none(self) -> None:
        assert classify_turn_failure([_success_step("read_file")]) is None


# ══════════════════════════════════════════════════════════
# _react_completion_receipt — structured failure attachment
# ══════════════════════════════════════════════════════════


class TestReceiptStructuredFailure:
    def test_failed_receipt_carries_readable_failure(self) -> None:
        receipt = _react_completion_receipt(
            final_answer=None,
            terminated_reason="final_answer",
            effective_success=False,
            executed_beak_steps=[_failed_step("git_commit", {"stderr": _PNPM_STDERR})],
        )
        failure = receipt.get("failure")
        assert failure is not None
        assert failure["code"] == "pnpm_modules_purge_no_tty"
        assert failure["readable"]

    def test_success_receipt_has_no_failure(self) -> None:
        receipt = _react_completion_receipt(
            final_answer="done",
            terminated_reason="final_answer",
            effective_success=True,
            executed_beak_steps=[_success_step("echo")],
        )
        assert "failure" not in receipt

    def test_cleaned_delivery_receipt_is_completed_with_warning(self) -> None:
        decision = decide_completion(
            terminated_reason="final_answer_with_warning",
            effective_success=True,
        )
        receipt = _react_completion_receipt(
            final_answer="clean answer",
            terminated_reason="final_answer_with_warning",
            effective_success=True,
            executed_beak_steps=[_success_step("read_file")],
            completion_decision=decision.to_dict(),
        )
        assert decision.outcome == "completed_with_warning"
        assert decision.success is True
        assert receipt["ready"] is True
        assert "completed_with_warning" in receipt["warnings"]

    def test_terminal_handoff_is_preserved_as_failed_receipt_message(self) -> None:
        handoff = "最终汇总超过了单轮时限。已完成的工具结果仍保留；点击继续可从当前进度重新收敛。"
        decision = decide_completion(
            terminated_reason="model_stall",
            effective_success=False,
        )
        receipt = _react_completion_receipt(
            final_answer=handoff,
            terminated_reason="model_stall",
            effective_success=False,
            executed_beak_steps=[],
            completion_decision=decision.to_dict(),
        )

        assert receipt["ready"] is False
        assert receipt["message"] == handoff
        assert receipt["code"] == "model_stall"


class TestCompletionDecision:
    @pytest.mark.parametrize(
        ("reason", "effective_success", "outcome", "success", "resumable"),
        [
            ("final_answer", True, "completed", True, False),
            ("final_answer_with_warning", True, "completed_with_warning", True, False),
            ("max_iter", True, "partial", True, True),
            ("paused", True, "paused", False, True),
            ("cancelled", True, "cancelled", False, False),
            ("guard_impasse", True, "partial", True, True),
            ("model_stall", False, "failed", False, False),
        ],
    )
    def test_reason_mapping(
        self,
        reason: str,
        effective_success: bool,
        outcome: str,
        success: bool,
        resumable: bool,
    ) -> None:
        decision = decide_completion(
            terminated_reason=reason,
            effective_success=effective_success,
        )
        assert decision.outcome == outcome
        assert decision.success is success
        assert decision.resumable is resumable

    def test_explicit_blocked_signal_wins_without_parsing_answer_text(self) -> None:
        decision = decide_completion(
            terminated_reason="final_answer",
            effective_success=False,
            blocked_on_user=True,
        )
        assert decision.outcome == "blocked_on_user"
        assert decision.resumable is True

    def test_executor_protocol_tags_signal_user_block(self) -> None:
        step = _failed_step("exec_shell", {"error": "approval hold"}, status="immune_reject")
        step.result.stderr_tags = ["immune_reject", "waiting_user", "approval_required"]
        assert _has_structured_user_block([step]) is True

    def test_plain_failure_is_not_a_user_block(self) -> None:
        step = _failed_step("exec_shell", {"error": "boom"})
        step.result.stderr_tags = ["failed"]
        assert _has_structured_user_block([step]) is False


# ══════════════════════════════════════════════════════════
# _turn_disposition — honest failed → waiting downgrade
# ══════════════════════════════════════════════════════════


class TestTurnDisposition:
    def test_success_is_completed(self) -> None:
        assert (
            _turn_disposition(
                final_answer="done",
                terminated_reason="final_answer",
                final_success=True,
            )
            == "completed"
        )

    def test_paused_preserved(self) -> None:
        assert (
            _turn_disposition(
                final_answer=None,
                terminated_reason="paused",
                final_success=False,
            )
            == "paused"
        )

    def test_cancelled_preserved(self) -> None:
        assert (
            _turn_disposition(
                final_answer=None,
                terminated_reason="cancelled",
                final_success=False,
            )
            == "cancelled"
        )

    def test_genuine_handoff_is_blocked_on_user(self) -> None:
        # Tight-marker hand-off (请确认 + 无法继续) after a tool failure.
        answer = (
            "git 提交被环境阻塞了,请确认是否允许我直接调用 node_modules/.bin 重试,否则无法继续。"
        )
        assert (
            _turn_disposition(
                final_answer=answer,
                terminated_reason="final_answer",
                final_success=False,
            )
            == "blocked_on_user"
        )

    def test_guard_impasse_stays_failed(self) -> None:
        # Guard enforcement reuses a help-sounding answer, but it already has
        # its own presentation (guardBlocked) — must not become a hand-off.
        answer = "我还不能把这个任务标记为完成。请点击继续让我接着执行,或提供必要的权限后再继续。"
        assert (
            _turn_disposition(
                final_answer=answer,
                terminated_reason="guard_impasse",
                final_success=False,
            )
            == "failed"
        )

    def test_short_report_mentioning_permission_stays_failed(self) -> None:
        # The loose short-answer escape: a brief report that merely mentions
        # 权限 is NOT a hand-off and must not be downgraded to waiting.
        answer = "已完成迁移,清理逻辑保留以处理 token 格式。"
        assert (
            _turn_disposition(
                final_answer=answer,
                terminated_reason="final_answer",
                final_success=False,
            )
            == "failed"
        )

    def test_generic_failure_is_failed(self) -> None:
        assert (
            _turn_disposition(
                final_answer="我不确定发生了什么,任务失败了。",
                terminated_reason="final_answer",
                final_success=False,
            )
            == "failed"
        )


# ══════════════════════════════════════════════════════════
# gateway react_completed → structured turn.error
# ══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_react_completed_failure_builds_structured_error() -> None:
    from runtime.protocol import ItemStatus, Turn, TurnParams, TurnStatus
    from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState
    from runtime.sensing.gateway.realtime_react_stream import _apply_react_event

    class _StubLog:
        def item_started(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_delta(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_completed(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def turn_updated(self, *a, **k) -> None:  # noqa: ARG002
            pass

    class _StubEmitter:
        def __init__(self) -> None:
            self.notified: list[tuple[str, dict]] = []

        async def notify(self, method, params) -> None:  # noqa: ARG002
            self.notified.append((str(method), params))

    class _StubRuntime:
        def _record_react_trace_event(self, turn, event) -> None:  # noqa: ARG002
            pass

    turn = Turn(
        id="turn-1",
        threadId="th-1",
        params=TurnParams(threadId="th-1", input=[{"type": "text", "text": "go"}]),
    )
    state = _ReactBridgeState()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_agent_message(turn, log, emitter, "unfinished answer")
    await _apply_react_event(
        _StubRuntime(),  # type: ignore[arg-type]
        turn,
        log,  # type: ignore[arg-type]
        emitter,  # type: ignore[arg-type]
        state,
        {
            "type": "react_completed",
            "success": False,
            "disposition": "blocked_on_user",
            "failure": {
                "kind": "environment",
                "code": "pnpm_modules_purge_no_tty",
                "readable": "环境阻塞:pnpm 想清理 node_modules 但无 TTY。",
                "tool": "git_commit",
                "detail": _PNPM_STDERR,
            },
            "completion_receipt": {
                "status": "failed",
                "message": "git_commit failed: " + _PNPM_STDERR,
                "code": "tool_execution_failed",
            },
        },
    )

    assert turn.status == TurnStatus.FAILED
    assert turn.items[0].status == ItemStatus.FAILED
    assert turn.outcome_reason == "环境阻塞:pnpm 想清理 node_modules 但无 TTY。"
    assert turn.error is not None
    assert turn.error["message"] == "环境阻塞:pnpm 想清理 node_modules 但无 TTY。"
    assert turn.error["code"] == "pnpm_modules_purge_no_tty"
    assert turn.error["disposition"] == "blocked_on_user"
    assert turn.error["failure_kind"] == "environment"
    assert turn.error["details"]["status"] == "failed"  # receipt attached


@pytest.mark.asyncio
async def test_react_completed_generic_failure_keeps_legacy_error() -> None:
    """Without a structured failure the gateway keeps the prior behavior."""
    from runtime.protocol import Turn, TurnParams, TurnStatus
    from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState
    from runtime.sensing.gateway.realtime_react_stream import _apply_react_event

    class _StubLog:
        def item_started(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_delta(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_completed(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def turn_updated(self, *a, **k) -> None:  # noqa: ARG002
            pass

    class _StubEmitter:
        def __init__(self) -> None:
            self.notified: list[tuple[str, dict]] = []

        async def notify(self, method, params) -> None:  # noqa: ARG002
            self.notified.append((str(method), params))

    class _StubRuntime:
        def _record_react_trace_event(self, turn, event) -> None:  # noqa: ARG002
            pass

    turn = Turn(
        id="turn-1",
        threadId="th-1",
        params=TurnParams(threadId="th-1", input=[{"type": "text", "text": "go"}]),
    )
    await _apply_react_event(
        _StubRuntime(),  # type: ignore[arg-type]
        turn,
        _StubLog(),  # type: ignore[arg-type]
        _StubEmitter(),  # type: ignore[arg-type]
        _ReactBridgeState(),
        {"type": "react_completed", "success": False},
    )

    assert turn.status == TurnStatus.FAILED
    assert turn.outcome_reason == "react_failed"
    assert turn.error is not None
    assert turn.error["code"] == "react_failed"
    assert turn.error["disposition"] == "failed"


@pytest.mark.asyncio
async def test_gateway_persists_canonical_completion_decision() -> None:
    from runtime.protocol import Turn, TurnParams, TurnStatus
    from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState
    from runtime.sensing.gateway.realtime_react_stream import _apply_react_event

    class _StubLog:
        def item_started(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_delta(self, *a, **k) -> None:  # noqa: ARG002
            pass

        def item_completed(self, *a, **k) -> None:  # noqa: ARG002
            pass

    class _StubEmitter:
        async def notify(self, *a, **k) -> None:  # noqa: ARG002
            pass

    class _StubRuntime:
        def _record_react_trace_event(self, *a, **k) -> None:  # noqa: ARG002
            pass

    turn = Turn(
        id="turn-1",
        threadId="th-1",
        params=TurnParams(threadId="th-1", input=[]),
    )
    decision = decide_completion(
        terminated_reason="max_iter",
        effective_success=True,
    ).to_dict()
    await _apply_react_event(
        _StubRuntime(),  # type: ignore[arg-type]
        turn,
        _StubLog(),  # type: ignore[arg-type]
        _StubEmitter(),  # type: ignore[arg-type]
        _ReactBridgeState(),
        {
            "type": "react_completed",
            "success": True,
            "completion_decision": decision,
        },
    )

    assert turn.status == TurnStatus.IN_PROGRESS
    assert turn.completion_decision == decision

