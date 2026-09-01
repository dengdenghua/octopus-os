from runtime.core.cerebrum.todo_protocol import (
    context_mode,
    render_todo_protocol_guidance,
    should_require_todo_protocol,
)


def test_todo_protocol_skips_short_acknowledgements() -> None:
    assert not should_require_todo_protocol("ok", {"metadata": {"mode": "code"}})
    assert not should_require_todo_protocol("\u55ef", {"metadata": {"mode": "team"}})
    assert not should_require_todo_protocol(
        "\u5927\u5bb6\u597d",
        {"metadata": {"mode": "team"}},
    )
    assert not should_require_todo_protocol(
        "hello everyone",
        {"metadata": {"mode": "team"}},
    )


def test_todo_protocol_is_optional_for_code_but_required_for_team() -> None:
    assert not should_require_todo_protocol(
        "fix the frontend and run tests",
        {"metadata": {"mode": "code"}},
    )
    assert should_require_todo_protocol(
        "\u6574\u7406\u4e00\u4e2a\u65b9\u6848",
        {"mode": "team"},
    )


def test_todo_protocol_does_not_gate_on_freeform_wording() -> None:
    assert not should_require_todo_protocol("audit the streaming modules")
    assert not should_require_todo_protocol("\u7ee7\u7eed\u4f18\u5316\u6df1\u5ea6\u7814\u7a76")


def test_todo_protocol_requires_goal_mode_even_for_short_tasks() -> None:
    assert should_require_todo_protocol(
        "rename this",
        {"mode": "code", "goal_mode": True},
    )
    assert should_require_todo_protocol(
        "rename this",
        {"metadata": {"completion_policy": "goal"}},
    )


def test_todo_protocol_context_mode_uses_metadata_and_workspace() -> None:
    assert context_mode({"metadata": {"mode": "deep_research"}}) == "deep_research"
    assert context_mode({"metadata": {"workspace_path": "/repo"}}) == "code"


def test_todo_protocol_guidance_marks_required_state() -> None:
    guidance = render_todo_protocol_guidance(required=True, mode="team")

    assert "TASK CHECKLIST PROTOCOL REQUIRED for team mode" in guidance
    assert "todo_write" in guidance
