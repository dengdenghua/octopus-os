"""Regression tests for §59 / §61 / §63 — quality-floor guards 3.

* §59: ``_exception_swallow_via_log_guard`` — ``except: log.error(...)``
  without re-raise.
* §61: ``_long_function_guard`` — single function with > 150 lines of
  substantive body.
* §63: ``_dynamic_exec_guard`` — eval / exec / __import__ in runtime.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _dynamic_exec_guard,
    _exception_swallow_via_log_guard,
    _long_function_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _count_function_body_lines,
    _detect_dynamic_exec_in_payload,
    _detect_long_functions_in_payload,
    _payload_has_log_swallow,
    _step_introduces_dynamic_exec,
    _step_introduces_log_swallow,
    _step_introduces_long_function,
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
# §59 — exception-swallow-via-log guard
# ══════════════════════════════════════════════════════════════════


class TestPayloadHasLogSwallow:
    def test_log_then_silent(self) -> None:
        text = "try:\n    do()\nexcept Exception:\n    log.error('failed')\n    return None\n"
        assert _payload_has_log_swallow(text)

    def test_log_then_reraise_silent(self) -> None:
        text = "try:\n    do()\nexcept Exception:\n    log.error('failed')\n    raise\n"
        assert not _payload_has_log_swallow(text)

    def test_logger_variant(self) -> None:
        text = "try:\n    do()\nexcept ValueError:\n    _logger.warning('bad input')\n"
        assert _payload_has_log_swallow(text)

    def test_no_except_silent(self) -> None:
        assert not _payload_has_log_swallow("def hello():\n    return 1")

    def test_except_pass_not_log_silent(self) -> None:
        # §30 territory — guard §59 only fires when there IS a log call.
        text = "try:\n    do()\nexcept Exception:\n    pass\n"
        assert not _payload_has_log_swallow(text)


class TestStepIntroducesLogSwallow:
    def test_runtime_new_log_swallow(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "try:\\n    x = 1\\nexcept Exception:\\n    log.error(\'bad\')"})'
            ),
        )
        assert _step_introduces_log_swallow(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "try: x\\nexcept Exception: log.error(\'bad\')"})'
            ),
        )
        assert not _step_introduces_log_swallow(step)

    def test_pre_existing_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "try:\\n    x = 1\\nexcept Exception:\\n    log.error(\'bad\')", '
                '"new_string": "try:\\n    x = 2\\nexcept Exception:\\n    log.error(\'bad\')"})'
            ),
        )
        assert not _step_introduces_log_swallow(step)


class TestExceptionSwallowGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "try: x\\nexcept Exception: log.error(\'bad\')"})'
                ),
            ),
        ]
        assert (
            _exception_swallow_via_log_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_swallow_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _exception_swallow_via_log_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_swallow_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "try:\\n    x = 1\\nexcept Exception:\\n    log.error(\'bad\')"})'
                ),
            ),
        ]
        msg = _exception_swallow_via_log_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "runtime/foo.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "try: x\\nexcept Exception: log.error(\'bad\')"})'
                ),
            ),
        ]
        assert (
            _exception_swallow_via_log_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §61 — long-function guard
# ══════════════════════════════════════════════════════════════════


class TestCountFunctionBodyLines:
    def test_simple(self) -> None:
        body = "\n    a = 1\n    b = 2\n    return a + b"
        assert _count_function_body_lines(body) == 3

    def test_blanks_skipped(self) -> None:
        body = "\n    a = 1\n\n    b = 2\n"
        assert _count_function_body_lines(body) == 2

    def test_comments_skipped(self) -> None:
        body = "\n    # comment\n    a = 1\n"
        assert _count_function_body_lines(body) == 1

    def test_docstring_skipped(self) -> None:
        body = '\n    """Doc."""\n    a = 1\n'
        assert _count_function_body_lines(body) == 1

    def test_empty(self) -> None:
        assert _count_function_body_lines("") == 0


class TestDetectLongFunctionsInPayload:
    def test_short_silent(self) -> None:
        body = "\n".join(["    x = 1"] * 50)
        text = f"def hello():\n{body}\n"
        assert _detect_long_functions_in_payload(text) == []

    def test_long_detected(self) -> None:
        body = "\n".join(["    x = 1"] * 200)
        text = f"def big():\n{body}\n"
        hits = _detect_long_functions_in_payload(text)
        assert len(hits) == 1
        assert hits[0][0] == "big"
        assert hits[0][1] >= 150


class TestStepIntroducesLongFunction:
    def test_short_silent(self) -> None:
        body = "\\n".join(["    x = 1"] * 50)
        step = _step(
            1,
            action=f'write_text_file({{"path": "runtime/foo.py", "content": "def hello():\\n{body}"}})',
        )
        assert _step_introduces_long_function(step) == []

    def test_long_detected(self) -> None:
        body = "\\n".join(["    x = 1"] * 200)
        step = _step(
            1,
            action=f'write_text_file({{"path": "runtime/foo.py", "content": "def big():\\n{body}"}})',
        )
        hits = _step_introduces_long_function(step)
        assert len(hits) == 1
        assert hits[0][0] == "big"

    def test_test_path_skipped(self) -> None:
        body = "\\n".join(["    x = 1"] * 200)
        step = _step(
            1,
            action=f'write_text_file({{"path": "tests/test_x.py", "content": "def big():\\n{body}"}})',
        )
        assert _step_introduces_long_function(step) == []

    def test_pre_existing_silent(self) -> None:
        # Same long name in old payload → not new.
        body = "\\n".join(["    x = 1"] * 200)
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                f'"old_string": "def big():\\n{body}", '
                f'"new_string": "def big():\\n{body}\\n    # tweak"}})'
            ),
        )
        assert _step_introduces_long_function(step) == []


class TestLongFunctionGuard:
    def test_non_code_mode_silent(self) -> None:
        body = "\\n".join(["    x = 1"] * 200)
        steps = [
            _step(
                1,
                action=f'write_text_file({{"path": "runtime/foo.py", "content": "def big():\\n{body}"}})',
            ),
        ]
        assert (
            _long_function_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_long_silent(self) -> None:
        body = "\\n".join(["    x = 1"] * 50)
        steps = [
            _step(
                1,
                action=f'write_text_file({{"path": "runtime/foo.py", "content": "def hello():\\n{body}"}})',
            ),
        ]
        assert (
            _long_function_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_long_fires(self) -> None:
        body = "\\n".join(["    x = 1"] * 200)
        steps = [
            _step(
                1,
                action=f'write_text_file({{"path": "runtime/foo.py", "content": "def big():\\n{body}"}})',
            ),
        ]
        msg = _long_function_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "big" in msg
        assert "runtime/foo.py" in msg

    def test_help_request_short_circuits(self) -> None:
        body = "\\n".join(["    x = 1"] * 200)
        steps = [
            _step(
                1,
                action=f'write_text_file({{"path": "runtime/foo.py", "content": "def big():\\n{body}"}})',
            ),
        ]
        assert (
            _long_function_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §63 — dynamic-exec guard
# ══════════════════════════════════════════════════════════════════


class TestDetectDynamicExecInPayload:
    def test_eval(self) -> None:
        assert "eval()" in _detect_dynamic_exec_in_payload("result = eval(user_input)")

    def test_exec(self) -> None:
        assert "exec()" in _detect_dynamic_exec_in_payload("exec(code_str)")

    def test_dunder_import(self) -> None:
        labels = _detect_dynamic_exec_in_payload("mod = __import__(name)")
        assert any("__import__" in label for label in labels)

    def test_method_named_eval_silent(self) -> None:
        # foo.eval(...) is not the builtin.
        assert _detect_dynamic_exec_in_payload("self.eval(x)") == []

    def test_clean_silent(self) -> None:
        assert _detect_dynamic_exec_in_payload("def hello(): return 1") == []


class TestStepIntroducesDynamicExec:
    def test_runtime_new_eval(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "x = eval(user_input)"})'
            ),
        )
        labels = _step_introduces_dynamic_exec(step)
        assert any("eval" in label for label in labels)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "x = eval(literal)"})'
            ),
        )
        assert _step_introduces_dynamic_exec(step) == []

    def test_pre_existing_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = eval(a)", '
                '"new_string": "x = eval(a)  # tweak"})'
            ),
        )
        assert _step_introduces_dynamic_exec(step) == []


class TestDynamicExecGuard:
    def test_non_code_mode_still_blocks_dynamic_exec(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "x = eval(user_input)"})'
                ),
            ),
        ]
        msg = _dynamic_exec_guard(steps, "done", is_code_mode=False)
        assert msg is not None
        assert "eval" in msg

    def test_no_exec_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _dynamic_exec_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_eval_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "x = eval(user_input)"})'
                ),
            ),
        ]
        msg = _dynamic_exec_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "runtime/foo.py" in msg
        assert "eval" in msg.lower()

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "x = eval(user_input)"})'
                ),
            ),
        ]
        assert (
            _dynamic_exec_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
