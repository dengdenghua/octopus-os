"""Tests for the runtime-side auto-verification salvage.

Regression: a model that keeps emitting plain-text final answers while
the language-verification guard demands a matching verifier used to
hard-stop with ``guard_impasse`` after three rejections — even when the
missing check is ``py_compile``, which the runtime can execute itself.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_auto_verify import (
    _try_auto_verification_salvage,
)
from runtime.core.cerebrum.react_parsing import (
    _has_language_specific_verification,
)
from runtime.core.cerebrum.react_types import ReActStep


def _step(
    iteration: int,
    *,
    action: str = "",
    observation: str = "",
) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        thought="t",
        action=action,
        observation=observation,
    )


class TestTryAutoVerificationSalvage:
    def test_other_guard_label_returns_none(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "a.py", "content": "x"})'),
        ]
        assert (
            _try_auto_verification_salvage(
                "path-verification guard",
                steps,
                iteration=3,
                cwd="/tmp/wp",
            )
            is None
        )

    def test_no_python_write_returns_none(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "Bar.tsx", "content": "x"})'),
            _step(2, action='write_text_file({"path": "readme.md", "content": "x"})'),
        ]
        assert (
            _try_auto_verification_salvage(
                "language-verification guard",
                steps,
                iteration=3,
                cwd="/tmp/wp",
            )
            is None
        )

    def test_empty_trajectory_returns_none(self) -> None:
        assert (
            _try_auto_verification_salvage(
                "language-verification guard",
                [],
                iteration=1,
                cwd="/tmp/wp",
            )
            is None
        )

    def test_missing_cwd_returns_none(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "a.py", "content": "x"})'),
        ]
        assert (
            _try_auto_verification_salvage(
                "language-verification guard",
                steps,
                iteration=2,
            )
            is None
        )

    def test_recent_python_write_builds_py_compile_step(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "src/a.py", "content": "x"})'),
            _step(2, action='write_text_file({"path": "src/b.py", "content": "y"})'),
        ]
        step = _try_auto_verification_salvage(
            "language-verification guard",
            steps,
            iteration=3,
            cwd="/tmp/wp",
        )
        assert step is not None
        assert step.iteration == 3
        assert "run_tests" in step.action
        assert "cwd" in step.action
        assert "py_compile" in step.action
        assert "src/a.py" in step.action
        assert "src/b.py" in step.action
        assert step.actions == [step.action]

    def test_written_step_counts_as_python_verification(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "hello.py", "content": "x"})'),
        ]
        step = _try_auto_verification_salvage(
            "language-verification guard",
            steps,
            iteration=2,
            cwd="/tmp/wp",
        )
        assert step is not None
        assert _has_language_specific_verification([step], language="python")

    def test_duplicate_paths_deduplicated(self) -> None:
        steps = [
            _step(1, action='edit_file({"path": "a.py", "new_string": "x"})'),
            _step(2, action='edit_file({"path": "a.py", "new_string": "y"})'),
        ]
        step = _try_auto_verification_salvage(
            "language-verification guard",
            steps,
            iteration=3,
            cwd="/tmp/wp",
        )
        assert step is not None
        assert step.action.count("a.py") == 1

