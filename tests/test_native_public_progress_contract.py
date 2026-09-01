from __future__ import annotations

from runtime.core.cerebrum.react_native import trim_text_protocol_for_native
from runtime.core.cerebrum.react_types import REACT_SYSTEM_PROMPT_BASE
from runtime.sensing.gateway.realtime_react_stream import (
    _agentic_stream_event_to_react_event,
)
from runtime.sensing.gateway.tool_bridge import (
    _native_public_checkpoint,
    _public_narrative_silence_s,
)


def test_realtime_native_progress_has_no_default_silence_window() -> None:
    assert _public_narrative_silence_s({}) == 0.0


def test_native_prompt_requires_evidence_update_before_more_tools() -> None:
    prompt = trim_text_protocol_for_native(REACT_SYSTEM_PROMPT_BASE)

    assert "收到工具结果后若还要继续调用工具" in prompt
    assert "概括刚确认的事实以及它如何影响下一步" in prompt


def test_native_public_checkpoint_is_capped_at_two_sentences() -> None:
    checkpoint = _native_public_checkpoint(
        "已经确认第一项证据。第二项证据决定下一步！第三句开始展开最终答案。"
    )

    assert checkpoint == "已经确认第一项证据。第二项证据决定下一步！"


def test_native_model_stall_maps_to_terminal_error_not_answer_text() -> None:
    event = _agentic_stream_event_to_react_event(
        "error",
        {"kind": "model_stall", "message": "provider stayed silent"},
        None,
    )

    assert event == {
        "type": "react_error",
        "kind": "model_stall",
        "message": "provider stayed silent",
    }

