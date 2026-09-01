"""Regression tests for the red-verification gate.

The completion gate used to treat *any* non-empty verifier observation as
a pass — a ``pytest`` run printing "3 failed, 10 passed" counted as
"verified". These tests pin the fix: a failing verifier observation is not
a success, and a code-mode turn cannot finish while its most recent
verification run is red (the mechanism behind a live behavioral case that
finished with a red fixture suite yet declared done).
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import _red_verification_observation_guard
from runtime.core.cerebrum.react_parsing import (
    _has_successful_verification_observation,
    _latest_verification_observation_is_red,
    _verification_observation_is_red,
)
from runtime.core.cerebrum.react_types import ReActStep

PY_WRITE = 'write_text_file({"path": "runtime/foo.py", "content": "x"})'
PYTEST = 'exec_shell({"command": "pytest tests/"})'


def _step(iteration: int, *, action: str = "", observation: str = "") -> ReActStep:
    return ReActStep(iteration=iteration, thought="", action=action, observation=observation)


class TestRedDetector:
    def test_green_outputs_not_red(self) -> None:
        for green in (
            "===== 13 passed in 0.52s =====",
            "0 failed, 13 passed",
            "All checks passed!",
            "Found 0 errors.",
            "ok  github.com/x/y  0.02s",
            # A successful exec_shell receipt always carries the
            # execution-policy schema, whose ``"timed_out": false`` field
            # name previously tripped the red detector.
            '{"argv": ["python3", "-m", "py_compile", "hello.py"], '
            '"exit_code": 0, "stdout": "", "stderr": "", "timed_out": false}',
            '{"timed_out":false,"exit_code":0}',
        ):
            assert not _verification_observation_is_red(green), green

    def test_red_outputs_detected(self) -> None:
        for red in (
            "===== 3 failed, 10 passed in 1.2s =====",
            "tests/test_x.py::test_y FAILED",
            "FAIL  github.com/x/y  0.10s",
            "src/a.ts(5,3): error TS2345: bad arg",
            "AssertionError: expected 5 got 4",
            "构建失败",
            '{"timed_out": true, "exit_code": 124}',
            "command timed out after 30s",
        ):
            assert _verification_observation_is_red(red), red


class TestSuccessfulObservation:
    def test_red_observation_is_not_a_success(self) -> None:
        steps = [_step(1, action=PYTEST, observation="3 failed, 10 passed in 1.2s")]
        assert not _has_successful_verification_observation(steps)

    def test_green_observation_is_a_success(self) -> None:
        steps = [_step(1, action=PYTEST, observation="13 passed in 0.5s")]
        assert _has_successful_verification_observation(steps)

    def test_dedicated_verifier_needs_no_shell_marker(self) -> None:
        steps = [
            _step(1, action='run_tests({"cwd": "."})', observation="13 passed in 0.5s"),
            _step(2, action='lint_check({"cwd": "."})', observation="All checks passed!"),
        ]

        assert _has_successful_verification_observation(steps)

    def test_parallel_dedicated_verifier_is_detected(self) -> None:
        step = _step(1, observation="1 failed")
        step.actions = [
            'run_tests({"cwd": "."})',
            'lint_check({"cwd": "."})',
        ]
        step.action = "; ".join(step.actions)

        assert _latest_verification_observation_is_red([step])


class TestLatestObservation:
    def test_red_then_green_is_not_red(self) -> None:
        # Ran red, fixed, re-ran green — the latest run is what counts.
        steps = [
            _step(1, action=PYTEST, observation="2 failed, 8 passed"),
            _step(2, action=PYTEST, observation="10 passed in 0.4s"),
        ]
        assert not _latest_verification_observation_is_red(steps)

    def test_green_then_red_is_red(self) -> None:
        steps = [
            _step(1, action=PYTEST, observation="10 passed"),
            _step(2, action=PYTEST, observation="1 failed, 9 passed"),
        ]
        assert _latest_verification_observation_is_red(steps)

    def test_no_verifier_is_not_red(self) -> None:
        assert not _latest_verification_observation_is_red([_step(1, action=PY_WRITE)])


class TestRedVerificationGuard:
    def _final(self) -> str:
        return "Final Answer: implemented the rename."

    def test_blocks_on_code_write_plus_red(self) -> None:
        steps = [
            _step(1, action=PY_WRITE),
            _step(2, action=PYTEST, observation="3 failed, 10 passed"),
        ]
        msg = _red_verification_observation_guard(steps, self._final(), is_code_mode=True)
        assert msg is not None and "red verification" in msg.lower()

    def test_allows_on_green(self) -> None:
        steps = [
            _step(1, action=PY_WRITE),
            _step(2, action=PYTEST, observation="13 passed in 0.5s"),
        ]
        assert _red_verification_observation_guard(steps, self._final(), is_code_mode=True) is None

    def test_allows_read_only_turn(self) -> None:
        # No code write — a red run surfaced during inspection must not block.
        steps = [_step(1, action=PYTEST, observation="3 failed, 10 passed")]
        assert _red_verification_observation_guard(steps, self._final(), is_code_mode=True) is None

    def test_allows_after_fix_red_then_green(self) -> None:
        steps = [
            _step(1, action=PY_WRITE),
            _step(2, action=PYTEST, observation="2 failed, 8 passed"),
            _step(3, action=PY_WRITE),
            _step(4, action=PYTEST, observation="10 passed"),
        ]
        assert _red_verification_observation_guard(steps, self._final(), is_code_mode=True) is None

    def test_ignored_in_non_code_mode(self) -> None:
        steps = [
            _step(1, action=PY_WRITE),
            _step(2, action=PYTEST, observation="3 failed"),
        ]
        assert _red_verification_observation_guard(steps, self._final(), is_code_mode=False) is None

    def test_allows_when_requesting_user_help(self) -> None:
        steps = [
            _step(1, action=PY_WRITE),
            _step(2, action=PYTEST, observation="3 failed, 10 passed"),
        ]
        help_final = "I'm blocked and need your input: which config key should win?"
        assert _red_verification_observation_guard(steps, help_final, is_code_mode=True) is None

