"""Regression tests for §42 / §44 / §45 — quality-floor guards.

* §42: ``_weak_test_assertion_guard`` — every new test added is no-op
  (``assert True`` / ``pass`` / ``assert x is not None`` / etc.).
* §44: ``_print_in_production_guard`` — bare ``print(...)`` added to
  non-CLI runtime code in a logging-based project.
* §45: ``_hardcoded_personal_path_guard`` — ``C:\\Users\\<name>``,
  ``/Users/<name>``, ``/home/<name>`` baked into committed code.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _hardcoded_personal_path_guard,
    _print_in_production_guard,
    _weak_test_assertion_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _classify_test_body,
    _detect_hardcoded_paths_in_payload,
    _detect_weak_tests_in_payload,
    _path_is_print_exempt,
    _payload_has_print_call,
    _step_introduces_hardcoded_path,
    _step_introduces_print,
    _step_introduces_weak_test,
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
# §42 — weak-test-assertion guard
# ══════════════════════════════════════════════════════════════════


class TestClassifyTestBody:
    def test_assert_true(self) -> None:
        assert _classify_test_body("\n    assert True") is not None

    def test_pass_only(self) -> None:
        assert _classify_test_body("\n    pass") is not None

    def test_ellipsis_only(self) -> None:
        assert _classify_test_body("\n    ...") is not None

    def test_assert_is_not_none(self) -> None:
        assert _classify_test_body("\n    assert result is not None") is not None

    def test_assert_truthiness(self) -> None:
        assert _classify_test_body("\n    assert result") is not None

    def test_real_comparison_silent(self) -> None:
        assert _classify_test_body("\n    assert result == 42") is None

    def test_multi_line_silent(self) -> None:
        body = "\n    result = compute()\n    assert result == 42"
        assert _classify_test_body(body) is None

    def test_docstring_then_assert_true_still_weak(self) -> None:
        body = '\n    """Tests the thing."""\n    assert True'
        assert _classify_test_body(body) is not None


class TestDetectWeakTestsInPayload:
    def test_one_weak_test_detected(self) -> None:
        payload = "def test_x():\n    assert True\n"
        weak = _detect_weak_tests_in_payload(payload)
        assert weak == [("test_x", weak[0][1])]

    def test_one_strong_test_silent(self) -> None:
        payload = "def test_x():\n    assert compute() == 42\n"
        assert _detect_weak_tests_in_payload(payload) == []

    def test_two_tests_one_weak(self) -> None:
        payload = "def test_x():\n    assert True\n\ndef test_y():\n    assert compute() == 42\n"
        weak = _detect_weak_tests_in_payload(payload)
        assert len(weak) == 1
        assert weak[0][0] == "test_x"


class TestStepIntroducesWeakTest:
    def test_test_path_with_weak_test_detected(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    assert True\\n"})',
        )
        assert _step_introduces_weak_test(step)

    def test_runtime_path_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def test_x():\\n    assert True\\n"})',
        )
        # Not a test path → guard doesn't classify.
        assert not _step_introduces_weak_test(step)

    def test_strong_test_silent(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    assert compute() == 42\\n"})',
        )
        assert not _step_introduces_weak_test(step)


class TestWeakTestAssertionGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    assert True\\n"})',
            ),
        ]
        assert (
            _weak_test_assertion_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_weak_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    assert compute() == 42\\n"})',
            ),
        ]
        assert (
            _weak_test_assertion_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_only_weak_fires(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    assert True\\n"})',
            ),
        ]
        msg = _weak_test_assertion_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test_x" in msg

    def test_weak_plus_strong_silent(self) -> None:
        # Mixed — at least one strong test means we accept.
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "tests/test_foo.py", '
                    '"content": "def test_a():\\n    assert True\\n\\ndef test_b():\\n    assert compute() == 42\\n"})'
                ),
            ),
        ]
        assert (
            _weak_test_assertion_guard(
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
                action='write_text_file({"path": "tests/test_foo.py", "content": "def test_x():\\n    pass\\n"})',
            ),
        ]
        assert (
            _weak_test_assertion_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §44 — print-in-production guard
# ══════════════════════════════════════════════════════════════════


class TestPathIsPrintExempt:
    def test_cli_module_exempt(self) -> None:
        assert _path_is_print_exempt("runtime/cli.py")

    def test_main_module_exempt(self) -> None:
        assert _path_is_print_exempt("runtime/__main__.py")

    def test_scripts_dir_exempt(self) -> None:
        assert _path_is_print_exempt("scripts/setup.py")

    def test_tools_dir_exempt(self) -> None:
        assert _path_is_print_exempt("tools/format.py")

    def test_random_runtime_not_exempt(self) -> None:
        assert not _path_is_print_exempt("runtime/core/cerebrum/react_loop.py")


class TestPayloadHasPrintCall:
    def test_print_call(self) -> None:
        assert _payload_has_print_call("print('hi')")

    def test_indented_print(self) -> None:
        assert _payload_has_print_call("    print(value)")

    def test_object_method_named_print_silent(self) -> None:
        # ``logger.print(...)`` shouldn't trigger.
        assert not _payload_has_print_call("logger.print('hi')")

    def test_no_print_silent(self) -> None:
        assert not _payload_has_print_call("logger.info('hi')")


class TestStepIntroducesPrint:
    def test_runtime_new_print_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/core/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "print(x)\\nx = 1"})'
            ),
        )
        assert _step_introduces_print(step)

    def test_cli_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/cli.py", '
                '"old_string": "x", "new_string": "print(\'hi\')"})'
            ),
        )
        assert not _step_introduces_print(step)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", "new_string": "print(\'hi\')"})'
            ),
        )
        assert not _step_introduces_print(step)

    def test_pre_existing_print_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/core/foo.py", '
                '"old_string": "print(x)", '
                '"new_string": "print(x)  # tweak"})'
            ),
        )
        assert not _step_introduces_print(step)


class TestPrintInProductionGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/core/foo.py", '
                    '"old_string": "x", "new_string": "print(x)"})'
                ),
            ),
        ]
        assert (
            _print_in_production_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_print_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/core/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _print_in_production_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_print_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/core/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "print(x)\\nx = 1"})'
                ),
            ),
        ]
        msg = _print_in_production_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "print" in msg.lower()
        assert "runtime/core/foo.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/core/foo.py", '
                    '"old_string": "x", "new_string": "print(x)"})'
                ),
            ),
        ]
        assert (
            _print_in_production_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §45 — hardcoded personal path guard
# ══════════════════════════════════════════════════════════════════


class TestDetectHardcodedPaths:
    def test_windows_user_dir(self) -> None:
        hits = _detect_hardcoded_paths_in_payload(
            'PATH = "C:\\\\Users\\\\alice\\\\Documents\\\\repo"',
        )
        assert any("Windows" in h for h in hits)

    def test_macos_user_dir(self) -> None:
        hits = _detect_hardcoded_paths_in_payload(
            'PATH = "/Users/alice/Code/repo"',
        )
        assert any("macOS" in h for h in hits)

    def test_linux_user_home(self) -> None:
        hits = _detect_hardcoded_paths_in_payload(
            'PATH = "/home/alice/projects"',
        )
        assert any("Linux" in h for h in hits)

    def test_users_public_silent(self) -> None:
        # /Users/Public is not user-specific.
        assert (
            _detect_hardcoded_paths_in_payload(
                'PATH = "/Users/Shared/data"',
            )
            == []
        )

    def test_home_runner_silent(self) -> None:
        # GitHub Actions runner is intentionally exempt.
        assert (
            _detect_hardcoded_paths_in_payload(
                'PATH = "/home/runner/work/repo"',
            )
            == []
        )

    def test_clean_path_silent(self) -> None:
        assert (
            _detect_hardcoded_paths_in_payload(
                'PATH = "./config"',
            )
            == []
        )


class TestStepIntroducesHardcodedPath:
    def test_runtime_new_personal_path(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "PATH = \\"/Users/alice/data\\""})'
            ),
        )
        labels = _step_introduces_hardcoded_path(step)
        assert labels
        assert any("macOS" in label for label in labels)

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "PATH = \\"/Users/alice/data\\""})'
            ),
        )
        assert _step_introduces_hardcoded_path(step) == []

    def test_pre_existing_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "PATH = \\"/Users/alice/data\\"", '
                '"new_string": "PATH = \\"/Users/alice/data\\"  # tweak"})'
            ),
        )
        assert _step_introduces_hardcoded_path(step) == []


class TestHardcodedPersonalPathGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "PATH = \\"/Users/alice/data\\""})'
                ),
            ),
        ]
        assert (
            _hardcoded_personal_path_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_hardcoded_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "runtime/foo.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _hardcoded_personal_path_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_hardcoded_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "PATH = \\"/Users/alice/data\\""})'
                ),
            ),
        ]
        msg = _hardcoded_personal_path_guard(
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
                    '"new_string": "PATH = \\"/Users/alice/data\\""})'
                ),
            ),
        ]
        assert (
            _hardcoded_personal_path_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
