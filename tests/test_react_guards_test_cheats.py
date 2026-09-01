"""Regression tests for §47 / §48 / §49 — test-cheat guards.

* §47: ``_mock_only_test_guard`` — new test only asserts mock truthiness
  (mock.called / mock.call_count == N) without checking call args.
* §48: ``_undocumented_skip_guard`` — new pytest.skip / @pytest.mark.skip
  without a meaningful reason string.
* §49: ``_deleted_test_guard`` — existing test_NAME functions removed
  in this trajectory.

These all assume the agent passed §20 (test-coverage) and §42
(weak-test) — the cheats they catch are subtler ways to satisfy those
gates while delivering no real coverage.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _deleted_test_guard,
    _mock_only_test_guard,
    _undocumented_skip_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _classify_mock_only_test_body,
    _detect_mock_only_tests_in_payload,
    _is_meaningful_skip_reason,
    _payload_has_undocumented_skip,
    _step_deleted_test_functions,
    _step_introduces_mock_only_test,
    _step_introduces_undocumented_skip,
    _test_function_names,
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
# §47 — mock-only test guard
# ══════════════════════════════════════════════════════════════════


class TestClassifyMockOnlyTestBody:
    def test_assert_called_only(self) -> None:
        assert _classify_mock_only_test_body("\n    assert mock.called")

    def test_assert_call_count_only(self) -> None:
        assert _classify_mock_only_test_body("\n    assert mock.call_count == 1")

    def test_assert_called_with_silent(self) -> None:
        # Has proper introspection.
        body = "\n    mock.assert_called_with(42)"
        assert not _classify_mock_only_test_body(body)

    def test_real_assertion_silent(self) -> None:
        body = "\n    assert result == 42"
        assert not _classify_mock_only_test_body(body)

    def test_mixed_called_plus_real_silent(self) -> None:
        body = "\n    assert mock.called\n    assert result == 42"
        # Real assertion present → not mock-only.
        assert not _classify_mock_only_test_body(body)

    def test_call_args_inspect_silent(self) -> None:
        body = "\n    assert mock.called\n    assert mock.call_args[0] == (42,)"
        assert not _classify_mock_only_test_body(body)


class TestDetectMockOnlyTestsInPayload:
    def test_one_mock_only(self) -> None:
        payload = "def test_x():\n    assert mock.called\n"
        assert _detect_mock_only_tests_in_payload(payload) == ["test_x"]

    def test_one_proper(self) -> None:
        payload = "def test_x():\n    mock.assert_called_with(42)\n"
        assert _detect_mock_only_tests_in_payload(payload) == []

    def test_mix(self) -> None:
        payload = (
            "def test_a():\n    assert mock.called\n\ndef test_b():\n    assert real_value == 42\n"
        )
        assert _detect_mock_only_tests_in_payload(payload) == ["test_a"]


class TestStepIntroducesMockOnlyTest:
    def test_test_path_with_mock_only(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    assert mock.called\\n"})',
        )
        assert _step_introduces_mock_only_test(step) == ["test_x"]

    def test_runtime_path_skipped(self) -> None:
        # Mock-only assertions in non-test code aren't this guard's job.
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def test_x():\\n    assert mock.called\\n"})',
        )
        assert _step_introduces_mock_only_test(step) == []

    def test_proper_test_silent(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    mock.assert_called_with(42)\\n"})',
        )
        assert _step_introduces_mock_only_test(step) == []


class TestMockOnlyTestGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    assert mock.called\\n"})',
            ),
        ]
        assert (
            _mock_only_test_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_mock_only_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    assert result == 42\\n"})',
            ),
        ]
        assert (
            _mock_only_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_mock_only_fires(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    assert mock.called\\n"})',
            ),
        ]
        msg = _mock_only_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test_x" in msg
        assert "mock" in msg.lower()

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_x():\\n    assert mock.called\\n"})',
            ),
        ]
        assert (
            _mock_only_test_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §48 — undocumented pytest.skip guard
# ══════════════════════════════════════════════════════════════════


class TestIsMeaningfulSkipReason:
    def test_empty_silent(self) -> None:
        assert not _is_meaningful_skip_reason("")
        assert not _is_meaningful_skip_reason("   ")

    def test_no_string_silent(self) -> None:
        # Like ``pytest.skip(some_var)`` — not a literal reason.
        assert not _is_meaningful_skip_reason("some_var")

    def test_short_reason_silent(self) -> None:
        assert not _is_meaningful_skip_reason('"todo"')
        assert not _is_meaningful_skip_reason('"skip"')

    def test_placeholder_reason_silent(self) -> None:
        assert not _is_meaningful_skip_reason('"FIXME later"')
        assert not _is_meaningful_skip_reason('"TODO: revisit"')

    def test_real_reason_passes(self) -> None:
        assert _is_meaningful_skip_reason('"requires GPU which CI lacks"')
        assert _is_meaningful_skip_reason('reason="covered by integration test foo"')


class TestPayloadHasUndocumentedSkip:
    def test_skip_decorator_no_reason(self) -> None:
        text = "@pytest.mark.skip\ndef test_x(): pass"
        assert _payload_has_undocumented_skip(text)

    def test_skip_decorator_empty_reason(self) -> None:
        text = '@pytest.mark.skip("todo")\ndef test_x(): pass'
        assert _payload_has_undocumented_skip(text)

    def test_skip_decorator_real_reason(self) -> None:
        text = '@pytest.mark.skip(reason="requires GPU; runs in nightly only")\ndef test_x(): pass'
        assert not _payload_has_undocumented_skip(text)

    def test_pytest_skip_call_no_reason(self) -> None:
        text = "def test_x():\n    pytest.skip()"
        assert _payload_has_undocumented_skip(text)

    def test_pytest_skip_call_real_reason(self) -> None:
        text = 'def test_x():\n    pytest.skip("requires postgres for this branch")'
        assert not _payload_has_undocumented_skip(text)


class TestStepIntroducesUndocumentedSkip:
    def test_test_path_new_skip(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "def test_x(): pass", '
                '"new_string": "@pytest.mark.skip\\ndef test_x(): pass"})'
            ),
        )
        assert _step_introduces_undocumented_skip(step)

    def test_runtime_path_skipped(self) -> None:
        # Non-test path → guard doesn't apply.
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x", '
                '"new_string": "@pytest.mark.skip"})'
            ),
        )
        assert not _step_introduces_undocumented_skip(step)

    def test_skip_with_reason_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "def test_x(): pass", '
                '"new_string": "@pytest.mark.skip(reason=\\"requires postgres for this branch\\")\\ndef test_x(): pass"})'
            ),
        )
        assert not _step_introduces_undocumented_skip(step)

    def test_pre_existing_skip_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "@pytest.mark.skip\\ndef test_x(): pass", '
                '"new_string": "@pytest.mark.skip\\ndef test_x():\\n    pass"})'
            ),
        )
        assert not _step_introduces_undocumented_skip(step)


class TestUndocumentedSkipGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_x(): pass", '
                    '"new_string": "@pytest.mark.skip\\ndef test_x(): pass"})'
                ),
            ),
        ]
        assert (
            _undocumented_skip_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_skip_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "tests/test_x.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _undocumented_skip_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_skip_no_reason_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_x(): pass", '
                    '"new_string": "@pytest.mark.skip\\ndef test_x(): pass"})'
                ),
            ),
        ]
        msg = _undocumented_skip_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "tests/test_x.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_x(): pass", '
                    '"new_string": "@pytest.mark.skip\\ndef test_x(): pass"})'
                ),
            ),
        ]
        assert (
            _undocumented_skip_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §49 — deleted-test guard
# ══════════════════════════════════════════════════════════════════


class TestTestFunctionNames:
    def test_one_test(self) -> None:
        assert _test_function_names("def test_foo():\n    pass") == {"test_foo"}

    def test_multi_tests(self) -> None:
        text = "def test_a():\n    pass\n\ndef test_b():\n    pass\n"
        assert _test_function_names(text) == {"test_a", "test_b"}

    def test_non_test_def_excluded(self) -> None:
        assert _test_function_names("def helper():\n    pass") == set()


class TestStepDeletedTestFunctions:
    def test_deletion_detected(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "def test_a():\\n    assert 1 == 1\\n\\ndef test_b():\\n    assert 2 == 2", '
                '"new_string": "def test_a():\\n    assert 1 == 1"})'
            ),
        )
        assert _step_deleted_test_functions(step) == ["test_b"]

    def test_no_deletion_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_x.py", '
                '"old_string": "def test_a():\\n    assert 1 == 1", '
                '"new_string": "def test_a():\\n    assert 1 == 2"})'
            ),
        )
        assert _step_deleted_test_functions(step) == []

    def test_runtime_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "def test_a(): pass", '
                '"new_string": "x = 1"})'
            ),
        )
        assert _step_deleted_test_functions(step) == []

    def test_no_old_payload_silent(self) -> None:
        # write_text_file → no old_string → guard can't compare.
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_x.py", "content": "def test_a(): pass"})',
        )
        assert _step_deleted_test_functions(step) == []


class TestDeletedTestGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_a():\\n    assert 1\\n\\ndef test_b():\\n    assert 2", '
                    '"new_string": "def test_a():\\n    assert 1"})'
                ),
            ),
        ]
        assert (
            _deleted_test_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_deletion_silent(self) -> None:
        steps = [
            _step(
                1,
                action='edit_file({"path": "tests/test_x.py", "old_string": "x", "new_string": "y"})',
            )
        ]
        assert (
            _deleted_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_deletion_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_a():\\n    assert 1 == 1\\n\\ndef test_b():\\n    assert 2 == 2", '
                    '"new_string": "def test_a():\\n    assert 1 == 1"})'
                ),
            ),
        ]
        msg = _deleted_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test_b" in msg
        assert "tests/test_x.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "tests/test_x.py", '
                    '"old_string": "def test_a():\\n    assert 1\\n\\ndef test_b():\\n    assert 2", '
                    '"new_string": "def test_a():\\n    assert 1"})'
                ),
            ),
        ]
        assert (
            _deleted_test_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
