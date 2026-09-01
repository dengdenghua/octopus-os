"""Regression tests for §52 / §54 / §57 — quality-floor guards 2.

* §52: ``_generic_test_name_guard`` — placeholder test names like
  ``test_basic`` / ``test_works`` / ``test_x`` / ``test_1``.
* §54: ``_no_assertion_test_guard`` — substantive test body without
  any actual assertion.
* §57: ``_async_without_await_guard`` — ``async def`` with a body
  that never awaits / yields / uses async-with / async-for.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    _async_without_await_guard,
    _generic_test_name_guard,
    _no_assertion_test_guard,
)
from runtime.core.cerebrum.react_parsing import (
    _async_body_uses_await,
    _detect_async_without_await_in_payload,
    _detect_generic_test_names_in_payload,
    _detect_no_assertion_tests_in_payload,
    _is_abstract_or_stub_body,
    _is_generic_test_name,
    _step_introduces_async_without_await,
    _step_introduces_generic_test_name,
    _step_introduces_no_assertion_test,
    _test_body_has_assertion,
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
# §52 — generic test name guard
# ══════════════════════════════════════════════════════════════════


class TestIsGenericTestName:
    def test_basic_is_generic(self) -> None:
        assert _is_generic_test_name("test_basic")

    def test_works_is_generic(self) -> None:
        assert _is_generic_test_name("test_works")

    def test_x_is_generic(self) -> None:
        assert _is_generic_test_name("test_x")

    def test_1_is_generic(self) -> None:
        assert _is_generic_test_name("test_1")

    def test_meaningful_silent(self) -> None:
        assert not _is_generic_test_name("test_handles_empty_input")
        assert not _is_generic_test_name("test_retries_on_timeout")
        assert not _is_generic_test_name("test_rejects_negative_count")

    def test_non_test_silent(self) -> None:
        assert not _is_generic_test_name("helper_basic")

    def test_bare_test_underscore_is_generic(self) -> None:
        assert _is_generic_test_name("test_")


class TestDetectGenericTestNamesInPayload:
    def test_one_generic(self) -> None:
        assert _detect_generic_test_names_in_payload(
            "def test_basic():\n    pass\n",
        ) == ["test_basic"]

    def test_one_meaningful(self) -> None:
        assert (
            _detect_generic_test_names_in_payload(
                "def test_handles_empty_input():\n    pass\n",
            )
            == []
        )

    def test_mixed(self) -> None:
        text = "def test_basic():\n    pass\n\ndef test_handles_empty_input():\n    pass\n"
        assert _detect_generic_test_names_in_payload(text) == ["test_basic"]


class TestStepIntroducesGenericTestName:
    def test_test_path_with_generic(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_x.py", "content": "def test_basic():\\n    pass\\n"})',
        )
        assert _step_introduces_generic_test_name(step) == ["test_basic"]

    def test_runtime_path_skipped(self) -> None:
        # Non-test path shouldn't trip the guard.
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def test_basic():\\n    pass\\n"})',
        )
        assert _step_introduces_generic_test_name(step) == []

    def test_meaningful_silent(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "tests/test_x.py", "content": "def test_handles_empty_input():\\n    assert run([]) == 0\\n"})',
        )
        assert _step_introduces_generic_test_name(step) == []


class TestGenericTestNameGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_basic():\\n    pass\\n"})',
            ),
        ]
        assert (
            _generic_test_name_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_generic_silent(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_specific_behavior():\\n    pass\\n"})',
            ),
        ]
        assert (
            _generic_test_name_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_generic_fires(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_basic():\\n    pass\\n"})',
            ),
        ]
        msg = _generic_test_name_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test_basic" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action='write_text_file({"path": "tests/test_x.py", "content": "def test_basic():\\n    pass\\n"})',
            ),
        ]
        assert (
            _generic_test_name_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §54 — no-assertion test guard
# ══════════════════════════════════════════════════════════════════


class TestTestBodyHasAssertion:
    def test_assert_keyword(self) -> None:
        assert _test_body_has_assertion("    assert x == 1")

    def test_assert_called_with(self) -> None:
        assert _test_body_has_assertion("    mock.assert_called_with(42)")

    def test_pytest_raises(self) -> None:
        assert _test_body_has_assertion("    with pytest.raises(ValueError):\n        do()")

    def test_self_assert_equal(self) -> None:
        assert _test_body_has_assertion("    self.assertEqual(x, 1)")

    def test_no_assertion(self) -> None:
        assert not _test_body_has_assertion("    result = compute()\n    print(result)")


class TestDetectNoAssertionTestsInPayload:
    def test_substantive_no_assert(self) -> None:
        text = (
            "def test_x():\n"
            "    instance = Thing()\n"
            "    instance.do_work()\n"
            "    instance.cleanup()\n"
        )
        assert _detect_no_assertion_tests_in_payload(text) == ["test_x"]

    def test_substantive_with_assert_silent(self) -> None:
        text = (
            "def test_x():\n"
            "    instance = Thing()\n"
            "    result = instance.do_work()\n"
            "    assert result == 42\n"
        )
        assert _detect_no_assertion_tests_in_payload(text) == []

    def test_one_line_body_skipped(self) -> None:
        # §42's territory — we don't double-flag.
        text = "def test_x():\n    pass\n"
        assert _detect_no_assertion_tests_in_payload(text) == []

    def test_pytest_raises_silent(self) -> None:
        text = (
            "def test_x():\n"
            "    instance = Thing()\n"
            "    with pytest.raises(ValueError):\n"
            "        instance.bad()\n"
        )
        assert _detect_no_assertion_tests_in_payload(text) == []


class TestStepIntroducesNoAssertionTest:
    def test_test_path_with_no_assertion(self) -> None:
        step = _step(
            1,
            action=(
                'write_text_file({"path": "tests/test_x.py", "content": '
                '"def test_x():\\n    instance = Thing()\\n    instance.do_work()\\n    instance.cleanup()\\n"})'
            ),
        )
        assert _step_introduces_no_assertion_test(step) == ["test_x"]

    def test_runtime_path_skipped(self) -> None:
        step = _step(
            1,
            action='write_text_file({"path": "runtime/foo.py", "content": "def test_x():\\n    a = 1\\n    b = 2\\n"})',
        )
        assert _step_introduces_no_assertion_test(step) == []


class TestNoAssertionTestGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "tests/test_x.py", "content": '
                    '"def test_x():\\n    a = 1\\n    b = 2\\n    c = 3\\n"})'
                ),
            ),
        ]
        assert (
            _no_assertion_test_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_no_hits_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "tests/test_x.py", "content": '
                    '"def test_x():\\n    a = compute()\\n    assert a == 42\\n"})'
                ),
            ),
        ]
        assert (
            _no_assertion_test_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_no_assertion_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "tests/test_x.py", "content": '
                    '"def test_x():\\n    a = compute()\\n    b = compute()\\n    c = compute()\\n"})'
                ),
            ),
        ]
        msg = _no_assertion_test_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "test_x" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'write_text_file({"path": "tests/test_x.py", "content": '
                    '"def test_x():\\n    a = 1\\n    b = 2\\n    c = 3\\n"})'
                ),
            ),
        ]
        assert (
            _no_assertion_test_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )


# ══════════════════════════════════════════════════════════════════
# §57 — async-without-await guard
# ══════════════════════════════════════════════════════════════════


class TestAsyncBodyUsesAwait:
    def test_await(self) -> None:
        assert _async_body_uses_await("    result = await fetch()")

    def test_yield(self) -> None:
        assert _async_body_uses_await("    yield item")

    def test_async_for(self) -> None:
        assert _async_body_uses_await("    async for x in stream:\n        pass")

    def test_async_with(self) -> None:
        assert _async_body_uses_await("    async with conn:\n        pass")

    def test_no_await(self) -> None:
        assert not _async_body_uses_await("    return 42")


class TestIsAbstractOrStubBody:
    def test_pass_only(self) -> None:
        assert _is_abstract_or_stub_body("    pass")

    def test_ellipsis_only(self) -> None:
        assert _is_abstract_or_stub_body("    ...")

    def test_raise_notimpl(self) -> None:
        assert _is_abstract_or_stub_body("    raise NotImplementedError")

    def test_docstring_only(self) -> None:
        assert _is_abstract_or_stub_body('    """Async stub."""')

    def test_real_body(self) -> None:
        assert not _is_abstract_or_stub_body("    return 42")


class TestDetectAsyncWithoutAwaitInPayload:
    def test_async_with_await_silent(self) -> None:
        text = "async def fetch():\n    return await client.get()\n"
        assert _detect_async_without_await_in_payload(text) == []

    def test_async_no_await_detected(self) -> None:
        text = "async def fetch():\n    return client.get()\n"
        assert _detect_async_without_await_in_payload(text) == ["fetch"]

    def test_async_pass_silent(self) -> None:
        # Stub body — abstract / interface placeholder.
        text = "async def fetch():\n    pass\n"
        assert _detect_async_without_await_in_payload(text) == []

    def test_async_ellipsis_silent(self) -> None:
        text = "async def fetch():\n    ...\n"
        assert _detect_async_without_await_in_payload(text) == []

    def test_abstractmethod_silent(self) -> None:
        text = "@abstractmethod\nasync def fetch(self):\n    return 1\n"
        assert _detect_async_without_await_in_payload(text) == []


class TestStepIntroducesAsyncWithoutAwait:
    def test_runtime_new_async_no_await(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "x = 1", '
                '"new_string": "async def go():\\n    return 1"})'
            ),
        )
        assert _step_introduces_async_without_await(step) == ["go"]

    def test_test_path_skipped(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "tests/test_foo.py", '
                '"old_string": "x", '
                '"new_string": "async def go():\\n    return 1"})'
            ),
        )
        assert _step_introduces_async_without_await(step) == []

    def test_pre_existing_silent(self) -> None:
        step = _step(
            1,
            action=(
                'edit_file({"path": "runtime/foo.py", '
                '"old_string": "async def go():\\n    return 1", '
                '"new_string": "async def go():\\n    return 2"})'
            ),
        )
        assert _step_introduces_async_without_await(step) == []


class TestAsyncWithoutAwaitGuard:
    def test_non_code_mode_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "async def go():\\n    return 1"})'
                ),
            ),
        ]
        assert (
            _async_without_await_guard(
                steps,
                "done",
                is_code_mode=False,
            )
            is None
        )

    def test_proper_async_silent(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "async def go():\\n    return await client.fetch()"})'
                ),
            ),
        ]
        assert (
            _async_without_await_guard(
                steps,
                "done",
                is_code_mode=True,
            )
            is None
        )

    def test_async_no_await_fires(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x = 1", '
                    '"new_string": "async def go():\\n    return 1"})'
                ),
            ),
        ]
        msg = _async_without_await_guard(
            steps,
            "done",
            is_code_mode=True,
        )
        assert msg is not None
        assert "go" in msg
        assert "runtime/foo.py" in msg

    def test_help_request_short_circuits(self) -> None:
        steps = [
            _step(
                1,
                action=(
                    'edit_file({"path": "runtime/foo.py", '
                    '"old_string": "x", '
                    '"new_string": "async def go():\\n    return 1"})'
                ),
            ),
        ]
        assert (
            _async_without_await_guard(
                steps,
                "I cannot continue — please provide the API key.",
                is_code_mode=True,
            )
            is None
        )
