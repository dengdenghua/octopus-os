from __future__ import annotations

from runtime.core.cerebrum.react_guards import (
    GuardContext,
    _mixed_mode_completion_guard,
    evaluate_guards,
)
from runtime.core.cerebrum.react_types import ReActStep

GOAL = (
    "Using the browser UI, complete onboarding with a native select, a rich-text "
    "editor, and an upload. Submit once and verify the delayed iframe confirmation."
)


def _step(iteration: int, action: str, observation: str) -> ReActStep:
    return ReActStep(
        iteration=iteration,
        action=action,
        actions=[action],
        observation=observation,
        action_results=[{"ok": True}],
    )


def _context(steps: list[ReActStep]) -> GuardContext:
    return GuardContext(
        steps=steps,
        final_answer="Done.",
        goal=GOAL,
        browser_operation_mode=True,
        tools_active=True,
        is_code_mode=False,
    )


def test_browser_completion_guard_blocks_file_inspection_only() -> None:
    ctx = _context([_step(1, 'read_file({"path":"EVAL_URL.txt"})', "http://localhost")])

    hit = evaluate_guards(ctx)

    assert hit is not None
    assert hit[0] == "browser-completion guard"
    assert "browser_upload receipt" in hit[1]
    assert "iframe confirmation" in hit[1]


def test_browser_completion_guard_accepts_executed_form_and_frame_evidence() -> None:
    ctx = _context(
        [
            _step(1, 'browser_type({"selector":"#role","value":"Administrator"})', "selected"),
            _step(
                2, 'browser_type({"selector":"#bio","value":"Building reliable agents."})', "typed"
            ),
            _step(3, 'browser_upload({"selector":"#avatar","path":"profile.txt"})', "uploaded"),
            _step(4, 'browser_click({"selector":"#submit"})', "clicked"),
            _step(
                5,
                'browser_get({"wait_ms":300})',
                '{"frames":[{"url":"/confirmation.html","content":"Onboarding complete"}]}',
            ),
        ]
    )

    assert evaluate_guards(ctx) is None


def test_browser_completion_guard_never_requests_second_submit() -> None:
    ctx = _context(
        [
            _step(1, 'browser_click({"selector":"#submit"})', "clicked"),
        ]
    )

    hit = evaluate_guards(ctx)

    assert hit is not None
    assert "do not click Submit again" in hit[1]
    assert "browser_get(wait_ms=300)" in hit[1]


MIXED_GOAL = "Use the browser UI to reproduce the bug, patch the source code, and run tests."


def _mixed_context(steps: list[ReActStep]) -> GuardContext:
    return GuardContext(
        steps=steps,
        final_answer="Reproduced, fixed, and verified.",
        goal=MIXED_GOAL,
        browser_operation_mode=True,
        tools_active=True,
        is_code_mode=True,
    )


def test_mixed_mode_guard_requires_browser_code_and_verification_lanes() -> None:
    browser_only = [_step(1, 'browser_navigate({"url":"http://localhost"})', "loaded")]
    message = _mixed_mode_completion_guard(_mixed_context(browser_only))

    assert message is not None
    assert "workspace code edit" in message
    assert "code verification command" in message


def test_mixed_mode_guard_requires_browser_evidence_even_when_code_passes() -> None:
    code_only = [
        _step(1, 'edit_file({"path":"src/app.py","old_string":"x","new_string":"y"})', "ok"),
        _step(2, 'exec_shell({"command":"python -m pytest tests/test_app.py -q"})', "1 passed"),
    ]
    message = _mixed_mode_completion_guard(_mixed_context(code_only))

    assert message is not None
    assert "executed browser reproduction or inspection" in message


def test_mixed_mode_guard_accepts_evidence_from_all_requested_lanes() -> None:
    steps = [
        _step(1, 'browser_navigate({"url":"http://localhost"})', "loaded"),
        _step(2, 'edit_file({"path":"src/app.py","old_string":"x","new_string":"y"})', "ok"),
        _step(3, 'exec_shell({"command":"python -m pytest tests/test_app.py -q"})', "1 passed"),
    ]

    assert _mixed_mode_completion_guard(_mixed_context(steps)) is None

