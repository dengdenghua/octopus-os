from __future__ import annotations

from types import SimpleNamespace

from runtime.platform.models import ParsedIntent
from runtime.sensing.gateway.realtime_cerebrum import (
    _agentic_stream_event_to_react_event,
    _should_default_planning_mode,
    _should_use_native_tool_loop,
)


def _intent(goal: str, mode: str) -> ParsedIntent:
    return ParsedIntent(
        raw=goal,
        intent_type="task",
        normalized_goal=goal,
        user_context={"mode": mode},
    )


def test_agentic_tool_start_event_maps_to_react_shape():
    evt = _agentic_stream_event_to_react_event(
        "tool_start",
        {
            "id": "call_1",
            "name": "read_file",
            "input": {"path": "README.md"},
            "iteration": 2,
        },
        None,
    )

    assert evt == {
        "type": "tool_start",
        "tool_call_id": "call_1",
        "tool_name": "read_file",
        "input_preview": {"path": "README.md"},
        "iteration": 2,
    }


def test_agentic_tool_error_maps_to_failed_tool_end():
    evt = _agentic_stream_event_to_react_event(
        "tool_end",
        {
            "id": "call_1",
            "name": "read_file",
            "output": "not found",
            "is_error": True,
            "iteration": 1,
        },
        None,
    )

    assert evt == {
        "type": "tool_end",
        "tool_call_id": "call_1",
        "tool_name": "read_file",
        "status": "error",
        "output_preview": "not found",
        "iteration": 1,
    }


def test_native_tool_loop_enabled_for_tool_capable_router():
    router = SimpleNamespace(
        capabilities=SimpleNamespace(supports_tool_use=True),
        call_stream=lambda _request: iter(()),
    )
    stack = SimpleNamespace(
        executor=object(),
        planner=SimpleNamespace(router=router),
    )
    intent = _intent("改代码", "react")

    assert _should_use_native_tool_loop(stack, intent, planning_mode=False)


def test_native_tool_loop_disabled_for_chat_but_enabled_for_planning():
    router = SimpleNamespace(
        capabilities=SimpleNamespace(supports_tool_use=True),
        call_stream=lambda _request: iter(()),
    )
    stack = SimpleNamespace(
        executor=object(),
        planner=SimpleNamespace(router=router),
    )

    chat = _intent("聊聊", "chat")
    react = _intent("先给方案", "react")

    assert not _should_use_native_tool_loop(stack, chat, planning_mode=False)
    # Planning mode is a plan-first prompt policy, not a plan-only execution
    # tier; capable models should retain structured native tools.
    assert _should_use_native_tool_loop(stack, react, planning_mode=True)


def test_complex_turn_defaults_to_planning_mode_when_not_explicit():
    from runtime.protocol.items import TurnParams

    params = TurnParams(
        threadId="t1",
        input=[{"type": "input_text", "text": "请完整实现这个功能并测试"}],
    )

    assert _should_default_planning_mode("请完整实现这个功能并测试", params)


def test_explicit_planning_false_and_chat_mode_do_not_default():
    from runtime.protocol.items import TurnParams

    explicit = TurnParams.model_validate(
        {
            "threadId": "t1",
            "input": [{"type": "input_text", "text": "请完整实现这个功能并测试"}],
            "planningMode": False,
        }
    )
    chat = TurnParams(
        threadId="t1",
        input=[
            {
                "type": "input_text",
                "text": "请完整实现这个功能并测试",
                "metadata": {"context": {"mode": "chat"}},
            }
        ],
    )

    assert not _should_default_planning_mode("请完整实现这个功能并测试", explicit)
    assert not _should_default_planning_mode("请完整实现这个功能并测试", chat)
