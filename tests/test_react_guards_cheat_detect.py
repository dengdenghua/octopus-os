"""Regression tests for §24 / §28 / §30 — the cheat-detection guards.

These three target the failure shape "model claims success without
actually fixing the underlying problem". They are independent of
§18-§23 (which guard the trajectory shape) — these guard the *content*
of edits and final answers.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _broad_except_suppression_guard,
    _commented_out_as_fix_guard,
    _false_verification_claim_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _final_answer_claims_verification,
    _has_successful_verification_observation,
    _payload_has_broad_except_suppression,
    _payload_has_executable_python,
    _step_introduces_broad_except_suppression,
    _step_replaced_code_with_comment,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    thought: str = "",
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought=thought,
        action=action,
        observation=observation,
    )


# ══════════════════════════════════════════════════════════════════
# §24 — false-verification claim
# ══════════════════════════════════════════════════════════════════


class TestFinalAnswerClaimsVerification:
    def test_english_phrases(self) -> None:
        assert _final_answer_claims_verification("All tests pass.")
        assert _final_answer_claims_verification("Tests passing now.")
        assert _final_answer_claims_verification("Verified the fix.")
        assert _final_answer_claims_verification("Build succeeded.")

    def test_chinese_phrases(self) -> None:
        assert _final_answer_claims_verification("全部测试通过")
        assert _final_answer_claims_verification("已通过测试")
        assert _final_answer_claims_verification("测试已通过，无错误")

    def test_neutral_summary_no_claim(self) -> None:
        assert not _final_answer_claims_verification("Reformatted the imports.")
        assert not _final_answer_claims_verification("已经完成重构")

    def test_empty_safe(self) -> None:
        assert not _final_answer_claims_verification("")
        assert not _final_answer_claims_verification(None)


class TestHasSuccessfulVerificationObservation:
    def test_clean_pytest_output(self) -> None:
        steps = [
            _step(
                1,
                action='exec_shell({"command": "pytest tests/test_x.py"})',
                observation="===== 5 passed in 1.2s =====",
            ),
        ]
        assert _has_successful_verification_observation(steps)

    def test_module_not_found_excluded(self) -> None:
        steps = [
            _step(
                1,
                action='exec_shell({"command": "pytest"})',
                observation="ModuleNotFoundError: No module named 'foo'",
            ),
        ]
        assert not _has_successful_verification_observation(steps)

    def test_traceback_excluded(self) -> None:
        steps = [
            _step(
                1,
                action='exec_shell({"command": "pytest"})',
                observation="Traceback (most recent call last):\n  File...",
            ),
        ]
        assert not _has_successful_verification_observation(steps)

    def test_command_not_found_excluded(self) -> None:
        steps = [
            _step(
                1,
                action='exec_shell({"command": "mypy ."})',
                observation="bash: mypy: command not found",
            ),
        ]
        assert not _has_successful_verification_observation(steps)

    def test_empty_observation_excluded(self) -> None:
        steps = [
            _step(
                1,
                action='exec_shell({"command": "pytest"})',
                observation="",
            ),
        ]
        assert not _has_successful_verification_observation(steps)


class TestFalseVerificationClaimGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(1, action='exec_shell({"command": "pytest"})', observation="ModuleNotFoundError")
        ]
        assert (
            _false_verification_claim_guard(
                steps,
                "All tests pass!",
                is_code_mode=False,
            )
            is None
        )

    def test_no_claim_silent(self) -> None:
        steps = [
            _step(1, action='exec_shell({"command": "pytest"})', observation="ModuleNotFoundError")
        ]
        assert (
            _false_verification_claim_guard(
                steps,
                "Refactored the imports.",
                is_code_mode=True,
            )
            is None
        )

    def test_claim_with_failed_verifier_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", "new_string": "x = 2"})'
                ),
                observation="updated",
            ),
            _step(
                2,
                action='exec_shell({"command": "pytest"})',
                observation="ModuleNotFoundError: No module named 'foo'",
            ),
        ]
        msg = _false_verification_claim_guard(
            steps,
            "All tests pass.",
            is_code_mode=True,
        )
        assert msg is not None
        assert "verifier" in msg.lower() or "verification" in msg.lower()

    def test_claim_with_clean_run_silent(self) -> None:
        steps = [
            _step(
                1, action='exec_shell({"command": "pytest"})', observation="===== 5 passed ====="
            ),
        ]
        assert (
            _false_verification_claim_guard(
                steps,
                "All tests pass.",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(1, action='exec_shell({"command": "pytest"})', observation="ModuleNotFoundError")
        ]
        assert (
            _false_verification_claim_guard(
                steps,
                "Tests pass — but I cannot continue, please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §28 — commented-out-as-fix
# ══════════════════════════════════════════════════════════════════


class TestPayloadHasExecutablePython:
    def test_executable_call(self) -> None:
        assert _payload_has_executable_python("foo()\n")

    def test_function_def(self) -> None:
        assert _payload_has_executable_python("def hello():\n    pass\n")

    def test_assert(self) -> None:
        assert _payload_has_executable_python("    assert x > 0\n")

    def test_raise(self) -> None:
        assert _payload_has_executable_python("    raise ValueError('x')\n")

    def test_only_comments_false(self) -> None:
        assert not _payload_has_executable_python("# was: foo()\n# bar()\n")

    def test_only_blanks_false(self) -> None:
        assert not _payload_has_executable_python("\n\n   \n")

    def test_empty_false(self) -> None:
        assert not _payload_has_executable_python("")
        assert not _payload_has_executable_python(None)


class TestStepReplacedCodeWithComment:
    def test_executable_to_comment_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "    raise ValueError(x)", '
                '"new_string": "    # was: raise ValueError(x)"})'
            ),
        )
        assert _step_replaced_code_with_comment(step)

    def test_executable_to_executable_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", "new_string": "x = 2"})'
            ),
        )
        assert not _step_replaced_code_with_comment(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "    assert x", "new_string": "    # disabled"})'
            ),
        )
        assert not _step_replaced_code_with_comment(step)

    def test_non_python_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "x.tsx", "old_string": "doStuff()", "new_string": "// gone"})'
            ),
        )
        assert not _step_replaced_code_with_comment(step)

    def test_multi_edit_one_pair_triggers(self) -> None:
        step = _step(
            1,
            action=(
                'multi_edit_file({"path": "runtime/foo.py", "edits": ['
                '{"old_string": "x=1", "new_string": "x=2"},'
                '{"old_string": "raise X", "new_string": "# was: raise X"}'
                "]})"
            ),
        )
        assert _step_replaced_code_with_comment(step)


class TestCommentedOutAsFixGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "raise X", "new_string": "# gone"})',
            ),
        ]
        assert (
            _commented_out_as_fix_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_replacement_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _commented_out_as_fix_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_executable_to_comment_fires(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "    raise ValueError(x)", "new_string": "    # was: raise ValueError(x)"})',
            ),
        ]
        msg = _commented_out_as_fix_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "comment" in msg.lower()

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "raise X", "new_string": "# gone"})',
            ),
        ]
        assert (
            _commented_out_as_fix_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §30 — broad-except suppression
# ══════════════════════════════════════════════════════════════════


class TestPayloadHasBroadExceptSuppression:
    def test_except_exception_pass(self) -> None:
        text = "try:\n    foo()\nexcept Exception:\n    pass\n"
        assert _payload_has_broad_except_suppression(text)

    def test_except_bare_pass(self) -> None:
        text = "try:\n    foo()\nexcept:\n    pass\n"
        assert _payload_has_broad_except_suppression(text)

    def test_except_baseexception_ellipsis(self) -> None:
        text = "try:\n    foo()\nexcept BaseException:\n    ...\n"
        assert _payload_has_broad_except_suppression(text)

    def test_except_exception_comment_only(self) -> None:
        text = "try:\n    foo()\nexcept Exception:\n    # ignore\n"
        assert _payload_has_broad_except_suppression(text)

    def test_except_with_real_handling_silent(self) -> None:
        text = "try:\n    foo()\nexcept Exception as e:\n    log.error(e)\n"
        assert not _payload_has_broad_except_suppression(text)

    def test_specific_exception_silent(self) -> None:
        text = "try:\n    foo()\nexcept ValueError:\n    pass\n"
        assert not _payload_has_broad_except_suppression(text)

    def test_no_try_silent(self) -> None:
        assert not _payload_has_broad_except_suppression("def hello():\n    return 1\n")


class TestStepIntroducesBroadExceptSuppression:
    def test_new_suppression_in_runtime_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "try:\\n    x = 1\\nexcept Exception:\\n    pass"})'
            ),
        )
        assert _step_introduces_broad_except_suppression(step)

    def test_pre_existing_suppression_silent(self) -> None:
        # Already in old_string — moving it doesn't count as new.
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "try:\\n    x = 1\\nexcept Exception:\\n    pass", '
                '"new_string": "try:\\n    x = 2\\nexcept Exception:\\n    pass"})'
            ),
        )
        assert not _step_introduces_broad_except_suppression(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "try: x\\nexcept Exception: pass"})'
            ),
        )
        assert not _step_introduces_broad_except_suppression(step)

    def test_specific_exception_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "try:\\n    x = 1\\nexcept ValueError:\\n    pass"})'
            ),
        )
        assert not _step_introduces_broad_except_suppression(step)


class TestBroadExceptSuppressionGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", "new_string": "try: x\\nexcept Exception: pass"})'
                ),
            ),
        ]
        assert (
            _broad_except_suppression_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_new_suppression_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _broad_except_suppression_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_new_suppression_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "try:\\n    x = 1\\nexcept Exception:\\n    pass"})'
                ),
            ),
        ]
        msg = _broad_except_suppression_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "except" in msg.lower()

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "try: x\\nexcept Exception: pass"})'
                ),
            ),
        ]
        assert (
            _broad_except_suppression_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
