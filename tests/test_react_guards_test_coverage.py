"""Regression tests for the §20 new-Python-code-without-test guard.

Catches the failure mode where the model adds a new public top-level
``def`` or ``class`` to runtime code AND the trajectory contains no
edit to any test file. Conservative: private symbols, nested defs,
non-Python files, and any trajectory that touches ``tests/`` are all
exempt.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _new_python_code_without_test_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _has_test_write,
    _is_test_path,
    _step_introduces_python_public_symbol,
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


# ──────────────────────────────────────────────────────────────────
# Path classifier
# ──────────────────────────────────────────────────────────────────


class TestIsTestPath:
    def test_tests_dir_root(self) -> None:
        assert _is_test_path("tests/test_foo.py")
        assert _is_test_path("tests\\test_foo.py")
        assert _is_test_path("/abs/repo/tests/sub/test_x.py")

    def test_test_filename(self) -> None:
        assert _is_test_path("a/b/test_helpers.py")
        assert _is_test_path("a/b/widget_test.py")

    def test_conftest(self) -> None:
        assert _is_test_path("tests/conftest.py")
        assert _is_test_path("conftest.py")

    def test_runtime_path_not_test(self) -> None:
        assert not _is_test_path("runtime/core/cerebrum/react_loop.py")
        assert not _is_test_path("runtime/foo/bar.py")

    def test_empty_safe(self) -> None:
        assert not _is_test_path("")
        assert not _is_test_path(None)


# ──────────────────────────────────────────────────────────────────
# New-public-symbol detector at step level
# ──────────────────────────────────────────────────────────────────


class TestNewPublicSymbolDetector:
    def test_write_text_file_new_def_detected(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
        )
        assert _step_introduces_python_public_symbol(step)

    def test_write_text_file_new_class_detected(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "class Foo:\\n    pass\\n"})',
        )
        assert _step_introduces_python_public_symbol(step)

    def test_async_def_detected(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "async def fetch():\\n    return 1\\n"})',
        )
        assert _step_introduces_python_public_symbol(step)

    def test_private_symbol_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def _helper():\\n    return 1\\n"})',
        )
        assert not _step_introduces_python_public_symbol(step)

    def test_nested_def_skipped(self) -> None:
        # Indented def is not top-level — guard ignores it.
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def outer():\\n    def inner():\\n        pass\\n"})',
        )
        # Outer is still public top-level → True, but the test below
        # confirms a nested-only payload doesn't trip.
        assert _step_introduces_python_public_symbol(step)

    def test_nested_only_skipped(self) -> None:
        step = _step(
            1,
            action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "    def helper():\\n        return 1"})',
        )
        assert not _step_introduces_python_public_symbol(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    pass\\n"})',
        )
        assert not _step_introduces_python_public_symbol(step)

    def test_non_python_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "frontend/Foo.tsx", "content": "function Bar() {}\\n"})',
        )
        assert not _step_introduces_python_public_symbol(step)

    def test_edit_file_old_only_change_not_new(self) -> None:
        # Only old_string contains a def — not a new symbol.
        step = _step(
            1,
            action='edit_file({"path": "runtime/foo.py", "old_string": "def removed():\\n    pass", "new_string": "# gone"})',
        )
        assert not _step_introduces_python_public_symbol(step)

    def test_multi_edit_file_new_string_in_edits(self) -> None:
        step = _step(
            1,
            action=(
                'multi_edit_file({"path": "runtime/foo.py", "edits": '
                '[{"old_string": "x", "new_string": "def fresh():\\n    return 1"}]})'
            ),
        )
        assert _step_introduces_python_public_symbol(step)

    def test_non_write_action_skipped(self) -> None:
        step = _step(1, action='read_file({"path": "runtime/foo.py"})')
        assert not _step_introduces_python_public_symbol(step)


# ──────────────────────────────────────────────────────────────────
# _has_test_write
# ──────────────────────────────────────────────────────────────────


class TestHasTestWrite:
    def test_test_edit_detected(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "tests/test_foo.py", "content": "x"})'),
        ]
        assert _has_test_write(steps)

    def test_runtime_edit_only_not_detected(self) -> None:
        steps = [
            _step(1, action='write_text_file({"path": "runtime/foo.py", "content": "x"})'),
        ]
        assert not _has_test_write(steps)

    def test_read_test_does_not_count(self) -> None:
        # Reading a test is not editing one.
        steps = [_step(1, action='read_file({"path": "tests/test_foo.py"})')]
        assert not _has_test_write(steps)


# ──────────────────────────────────────────────────────────────────
# Guard
# ──────────────────────────────────────────────────────────────────


class TestNewPythonCodeWithoutTestGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_writes_silent(self) -> None:
        steps = [_step(1, action='read_file({"path": "runtime/foo.py"})')]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_only_private_symbol_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def _helper():\\n    return 1\\n"})',
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_new_public_def_no_test_fires(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
            ),
        ]
        msg = _new_python_code_without_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test" in msg.lower()

    def test_new_public_class_no_test_fires(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "class Bar:\\n    pass\\n"})',
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is not None
        )

    def test_new_public_def_with_test_edit_silent(self) -> None:
        # ANY edit to a test file in the trajectory is enough signal.
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
            ),
            _step(
                2,
                action='write_text_file({"path": "tests/test_foo.py", "content": "def test_hello():\\n    pass\\n"})',
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
            ),
        ]
        # Use a phrase the help-request detector recognises.
        final = "I cannot continue — please provide the API key."
        assert (
            _new_python_code_without_test_guard(
                steps,
                final,
                is_code_mode=True,
            )
            is None
        )

    def test_refactor_only_silent(self) -> None:
        # Removing a function body (no NEW public symbol introduced).
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "def existing():\\n    return 1", "new_string": "def existing():\\n    return 2"})',
            ),
        ]
        # The new_string still contains "def existing(" → guard fires.
        # This is the rename/refactor trade-off documented in the
        # guard's docstring. We assert the trade-off explicitly so
        # future readers know it's intentional.
        msg = _new_python_code_without_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None  # Documented limitation: refactor → False positive.

    def test_old_write_outside_window_silent(self) -> None:
        # Symbol added > _NEW_SYMBOL_LOOKBACK steps ago — guard moves on.
        steps = [
            _step(
                1,
                action='write_text_file({"path": "runtime/foo.py", "content": "def hello():\\n    return 1\\n"})',
            ),
        ] + [_step(i, action='read_file({"path": "x.py"})') for i in range(2, 20)]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_test_path_edit_not_flagged(self) -> None:
        # Adding a public def to a test file should never trip the guard.
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_foo.py", "content": "def helper_for_tests():\\n    return 1\\n"})',
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_multi_edit_file_new_symbol_no_test_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'multi_edit_file({"path": "runtime/foo.py", "edits": '
                    '[{"old_string": "x", "new_string": "def fresh():\\n    return 1"}]})'
                ),
            ),
        ]
        assert (
            _new_python_code_without_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is not None
        )
