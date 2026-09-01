"""Test-quality guards: cheats that satisfy coverage letter, not spirit.

Extracted from ``react_guards.py`` (Wave 3, cluster 2b): weak
assertions, mock-only tests, undocumented skips, deleted tests,
generic test names, and assertion-free tests. Leaf module: depends
only on react_goal_analysis / react_parsing / react_types — must
never import react_guards.
"""

from __future__ import annotations

from runtime.core.cerebrum.react_goal_analysis import _final_answer_requests_user_help
from runtime.core.cerebrum.react_parsing import (
    _TEST_FUNC_RE,
    _detect_no_assertion_tests_in_payload,
    _detect_weak_tests_in_payload,
    _extract_step_payloads,
    _is_code_write_step,
    _is_test_path,
    _parse_action,
    _step_deleted_test_functions,
    _step_introduces_generic_test_name,
    _step_introduces_mock_only_test,
    _step_introduces_no_assertion_test,
    _step_introduces_undocumented_skip,
    _step_introduces_weak_test,
)
from runtime.core.cerebrum.react_types import ReActStep

# ──────────────────────────────────────────────────────────────────
# §42 — weak-test-assertion guard
# ──────────────────────────────────────────────────────────────────
# After §20 forced "no new public symbol without a test edit", the
# obvious cheat is to write a test that asserts nothing meaningful.
# This guard catches: ``assert True``, ``pass``, ``...``, ``assert
# obj is not None``, ``assert obj`` (truthiness only). One weak test
# in a multi-test file is tolerated; we only fire when EVERY new test
# function added is a no-op.

_WEAK_TEST_LOOKBACK = 12


def _trajectory_weak_test_hits(steps: list[ReActStep]) -> dict[str, list[tuple[str, str]]]:
    """Map ``test_path -> [(test_name, weakness_label)]`` for steps
    that introduced new weak tests."""
    out: dict[str, list[tuple[str, str]]] = {}
    window = steps[-_WEAK_TEST_LOOKBACK:] if steps else []
    for step in window:
        weak = _step_introduces_weak_test(step)
        if not weak:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(weak)
    return out


def _trajectory_added_strong_test(steps: list[ReActStep]) -> bool:
    """Whether any test edit in trajectory added at least ONE function
    that ISN'T classified as weak. Lets a file with one weak + one
    strong test pass."""
    window = steps[-_WEAK_TEST_LOOKBACK:] if steps else []
    for step in window:
        if not _is_code_write_step(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str) or not _is_test_path(path):
            continue
        new_chunks: list[str] = []
        for key in ("content", "new_string", "new_str"):
            value = args.get(key)
            if isinstance(value, str):
                new_chunks.append(value)
        edits = args.get("edits")
        if isinstance(edits, list):
            for edit in edits:
                if not isinstance(edit, dict):
                    continue
                for key in ("new_string", "new_str", "content"):
                    value = edit.get(key)
                    if isinstance(value, str):
                        new_chunks.append(value)
        new_text = "\n".join(new_chunks)
        all_funcs = {match.group("name") for match in _TEST_FUNC_RE.finditer(new_text)}
        weak_funcs = {name for name, _label in _detect_weak_tests_in_payload(new_text)}
        if all_funcs - weak_funcs:
            return True
    return False


def _weak_test_assertion_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    """Reject finals where every new test function added is a no-op."""
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_weak_test_hits(steps)
    if not hits:
        return None
    # If at least one added test is non-weak, accept the trajectory.
    if _trajectory_added_strong_test(steps):
        return None
    items = [
        f"{path} :: {name} ({label})"
        for path, weak_list in hits.items()
        for name, label in weak_list
    ]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: every new test added in this trajectory has "
        f"a no-op body — {preview}. ``assert True`` / ``pass`` / "
        "``assert x is not None`` doesn't actually exercise the code "
        "under test. Replace each weak assertion with a real comparison "
        "(expected output, exception type, side-effect on a fixture), "
        "or remove the test if it can't be made meaningful."
    )


# ──────────────────────────────────────────────────────────────────
# §47 — mock-only test guard
# ──────────────────────────────────────────────────────────────────
# Catch the failure mode where §20 + §42 are both satisfied but the
# new test only asserts ``mock.called`` / ``mock.call_count == 1`` —
# proving the mock was hit, not that it was hit with the RIGHT args.

_MOCK_ONLY_LOOKBACK = 12


def _trajectory_mock_only_hits(steps: list[ReActStep]) -> dict[str, list[str]]:
    """``test_path -> [test_name, ...]`` for new mock-only tests."""
    out: dict[str, list[str]] = {}
    window = steps[-_MOCK_ONLY_LOOKBACK:] if steps else []
    for step in window:
        names = _step_introduces_mock_only_test(step)
        if not names:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(names)
    return out


def _mock_only_test_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_mock_only_hits(steps)
    if not hits:
        return None
    items = [f"{path} :: {name}" for path, names in hits.items() for name in names]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: new test(s) only assert mock truthiness without "
        f"checking call arguments — {preview}. ``assert mock.called`` proves "
        "the function was invoked but not that it received the right args. "
        "Use ``mock.assert_called_with(...)`` / ``mock.call_args`` / "
        "``mock.assert_called_once_with(...)`` to verify the actual call "
        "shape, or replace the mock-only assertion with a real-output check."
    )


# ──────────────────────────────────────────────────────────────────
# §48 — undocumented pytest skip guard
# ──────────────────────────────────────────────────────────────────
# ``@pytest.mark.skip`` and ``pytest.skip()`` without an explicit
# reason longer than 8 chars (and not just "TODO" / "FIXME") is
# almost always papering over a real failure.

_SKIP_LOOKBACK = 12


def _trajectory_undocumented_skip_hits(steps: list[ReActStep]) -> list[str]:
    out: list[str] = []
    window = steps[-_SKIP_LOOKBACK:] if steps else []
    for step in window:
        if not _step_introduces_undocumented_skip(step):
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str) and path not in out:
            out.append(path)
    return out


def _undocumented_skip_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    paths = _trajectory_undocumented_skip_hits(steps)
    if not paths:
        return None
    preview = "; ".join(paths[:3])
    if len(paths) > 3:
        preview += f"; +{len(paths) - 3} more"
    return (
        f"Cannot finish yet: pytest.skip / @pytest.mark.skip was added to "
        f"test(s) without a meaningful reason: {preview}. A skip without "
        "a clear explanation ('requires GPU', 'covered by integration "
        "test foo', etc.) is hiding a real failure. Either give the skip "
        "a specific, non-placeholder reason of at least 8 characters, "
        "fix the underlying test, or remove the skip entirely."
    )


# ──────────────────────────────────────────────────────────────────
# §49 — deleted-test guard
# ──────────────────────────────────────────────────────────────────
# Removing test functions in the same trajectory that adds runtime
# code is a textbook "make the failing test go away" cheat. We list
# the deleted test names; the model has to either restore them or
# explicitly justify the deletion in the Final Answer.

_DELETED_TEST_LOOKBACK = 12


def _trajectory_deleted_tests(steps: list[ReActStep]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    window = steps[-_DELETED_TEST_LOOKBACK:] if steps else []
    for step in window:
        names = _step_deleted_test_functions(step)
        if not names:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(names)
    return out


def _deleted_test_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_deleted_tests(steps)
    if not hits:
        return None
    items = [f"{path} :: {name}" for path, names in hits.items() for name in names]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        f"Cannot finish yet: existing test function(s) were deleted in this "
        f"trajectory: {preview}. Removing a failing test instead of fixing "
        "the cause is a textbook cheat. Either restore the test and fix the "
        "underlying issue, OR — if the test really is obsolete (covers "
        "removed functionality, replaced by a better test elsewhere) — "
        "explicitly state in the Final Answer which test was removed and "
        "why no coverage was lost."
    )


# ──────────────────────────────────────────────────────────────────
# §52 — generic test name guard
# ──────────────────────────────────────────────────────────────────
# Catch test functions named ``test_basic`` / ``test_works`` /
# ``test_x`` / ``test_1`` etc. — placeholder names that tell the next
# reader nothing about what the test guards.

_GENERIC_NAME_LOOKBACK = 12


def _trajectory_generic_test_hits(steps: list[ReActStep]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    window = steps[-_GENERIC_NAME_LOOKBACK:] if steps else []
    for step in window:
        names = _step_introduces_generic_test_name(step)
        if not names:
            continue
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if isinstance(path, str):
            out.setdefault(path, []).extend(names)
    return out


def _generic_test_name_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_generic_test_hits(steps)
    if not hits:
        return None
    items = [f"{path} :: {name}" for path, names in hits.items() for name in names]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: new test function(s) have placeholder names "
        f"that don't describe the behavior under test: {preview}. Names "
        "like ``test_basic`` / ``test_works`` / ``test_x`` / ``test_1`` "
        "give the next reader zero hints about what the test guards. "
        "Rename each to describe the BEHAVIOR being asserted "
        "(``test_handles_empty_input``, ``test_retries_on_timeout``, "
        "``test_rejects_negative_count`` …)."
    )


# ──────────────────────────────────────────────────────────────────
# §54 — no-assertion test guard
# ──────────────────────────────────────────────────────────────────
# Test body has substantive code (more than 1 line, dodging §42) but
# zero ``assert`` / ``assert_called_*`` / ``pytest.raises`` etc. Such
# a test passes if the call doesn't raise — almost never the intent.

_NO_ASSERT_LOOKBACK = 12


def _trajectory_no_assertion_test_hits(steps: list[ReActStep]) -> dict[str, list[str]]:
    out: dict[str, set[str]] = {}
    window = steps[-_NO_ASSERT_LOOKBACK:] if steps else []
    for step in window:
        parsed = _parse_action(step.action)
        if parsed is None:
            continue
        _name, args = parsed
        path = args.get("path") or args.get("file") or args.get("file_path")
        if not isinstance(path, str):
            continue

        new_text, _old_text = _extract_step_payloads(step)
        touched_test_names = {match.group("name") for match in _TEST_FUNC_RE.finditer(new_text)}
        if touched_test_names:
            # A later rewrite of the same test function supersedes the earlier
            # defect. Keeping every historical hit forever made a repaired
            # test impossible to finish: the guard still rejected the final
            # answer after the new body added a concrete assertion.
            out.setdefault(path, set()).difference_update(touched_test_names)

        names = set(_step_introduces_no_assertion_test(step))
        if names:
            out.setdefault(path, set()).update(names)
        elif touched_test_names:
            current_bad = set(_detect_no_assertion_tests_in_payload(new_text))
            out.setdefault(path, set()).update(current_bad)

        if path in out and not out[path]:
            del out[path]
    return {path: sorted(names) for path, names in out.items()}


def _no_assertion_test_guard(
    steps: list[ReActStep],
    final_answer: str,
    *,
    is_code_mode: bool,
) -> str | None:
    if not is_code_mode or not steps:
        return None
    if _final_answer_requests_user_help(final_answer):
        return None
    hits = _trajectory_no_assertion_test_hits(steps)
    if not hits:
        return None
    items = [f"{path} :: {name}" for path, names in hits.items() for name in names]
    preview = "; ".join(items[:3])
    if len(items) > 3:
        preview += f"; +{len(items) - 3} more"
    return (
        "Cannot finish yet: new test function(s) execute code but don't "
        f"actually assert anything — {preview}. A test that just calls the "
        "function under test passes whenever the call doesn't raise — that "
        "verifies almost nothing. Add an explicit ``assert`` on the return "
        "value, an ``assert_called_with(...)`` on the mock, a "
        "``pytest.raises(...)`` block, or some other concrete check."
    )
