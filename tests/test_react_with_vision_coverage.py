"""Dense coverage for react_with_vision pure helpers (audit Q-05)."""

from __future__ import annotations

from runtime.tentacle.llm.chat_types import ToolCall
from runtime.tentacle.mobile.vlm.client import ScreenAnalysis, SuggestedAction
from runtime.tentacle.mobile.vlm.react_with_vision import (
    VisionReAct,
    _tool_fingerprint,
)


def test_format_vlm_analysis() -> None:
    analysis = ScreenAnalysis(
        description="A login screen",
        current_app="Settings",
        screen_state="showing form",
        suggested_actions=[
            SuggestedAction(
                action="tap", target="login button", coordinates=(10, 20), confidence=0.9
            ),
            SuggestedAction(action="type", target="field", text="hi", confidence=0.7),
        ],
    )
    text = VisionReAct._format_vlm_analysis(analysis)
    assert "[VLM 视觉分析]" in text
    assert "登录" in text or "login" in text.lower()
    assert "坐标(10, 20)" in text or "(10, 20)" in text
    assert "90%" in text

    minimal = ScreenAnalysis(description="blank", suggested_actions=[])
    t2 = VisionReAct._format_vlm_analysis(minimal)
    assert "blank" in t2


def test_suggested_action_to_tool_calls() -> None:
    calls = VisionReAct.suggested_action_to_tool_calls(
        [
            SuggestedAction(action="tap", target="btn", coordinates=(5, 6)),
            SuggestedAction(action="type", target="field", text="hi", coordinates=(1, 1)),
            SuggestedAction(action="scroll", target="list", coordinates=None),
        ],
        tentacle_id="d1",
        platform="android",
    )
    names = [c.name for c in calls]
    assert any("android.tap" in n for n in names)
    assert any("android.input_text" in n for n in names)
    assert any("android.swipe" in n for n in names)
    # the "type" action emits a tap (to focus) then input_text.
    assert len(calls) == 4


def test_tool_fingerprint_and_stuck() -> None:
    tc1 = ToolCall(id="a", name="android.tap", arguments={"x": 1, "y": 2, "tentacle_id": "d"})
    tc2 = ToolCall(id="b", name="android.tap", arguments={"y": 2, "x": 1, "tentacle_id": "d"})
    assert _tool_fingerprint(tc1) == _tool_fingerprint(tc2)  # order-insensitive args

    vr = VisionReAct.__new__(VisionReAct)
    from collections import deque

    vr._recent_window = deque()
    vr.stuck_window = 2
    assert vr._is_stuck([tc1]) is False  # window not full
    assert vr._is_stuck([tc2]) is True  # same fingerprints twice in window
    assert vr._is_stuck([]) is False

