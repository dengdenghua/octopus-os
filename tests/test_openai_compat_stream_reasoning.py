from __future__ import annotations

from runtime.sensing.model_router.openai_compat_stream import iter_openai_sse


class _FakeSSE:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self):
        yield from self._lines


def test_iter_openai_sse_preserves_reasoning_content():
    response = _FakeSSE(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"plan"}}]}',
            'data: {"choices":[{"delta":{"content":"answer"}}]}',
            'data: {"usage":{"prompt_tokens":2,"completion_tokens":3},"choices":[]}',
            "data: [DONE]",
        ]
    )

    events = list(iter_openai_sse(response, model="deepseek-v4-pro"))

    assert [event.type for event in events] == [
        "thinking_delta",
        "text_delta",
        "done",
    ]
    assert events[0].delta == "plan"
    assert events[1].delta == "answer"
    assert events[-1].final is not None
    assert events[-1].final.thinking == "plan"
    assert events[-1].final.text == "answer"
    assert events[-1].final.input_tokens == 2
    assert events[-1].final.output_tokens == 3


def test_iter_openai_sse_emits_streamed_tool_call():
    response = _FakeSSE(
        [
            (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"id":"call_1","type":"function","function":{"name":"read_file",'
                '"arguments":"{\\"path\\""}}]}}]}'
            ),
            (
                'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
                '"function":{"arguments":":\\"README.md\\"}"}}]}}]}'
            ),
            'data: {"usage":{"prompt_tokens":5,"completion_tokens":7},"choices":[]}',
            "data: [DONE]",
        ]
    )

    events = list(iter_openai_sse(response, model="gpt-4o-mini"))

    assert [event.type for event in events] == [
        "tool_call_delta",
        "tool_call_delta",
        "tool_call_delta",
        "tool_use",
        "done",
    ]
    assert events[0].name == "read_file"
    assert "".join(event.arguments_delta or "" for event in events[:3]) == (
        '{"path":"README.md"}'
    )
    tool_event = events[-2]
    assert tool_event.tool_call is not None
    assert tool_event.tool_call.id == "call_1"
    assert tool_event.tool_call.name == "read_file"
    assert tool_event.tool_call.input == {"path": "README.md"}
    assert events[-1].final is not None
    assert events[-1].final.tool_calls == [tool_event.tool_call]


def test_iter_openai_sse_preserves_finish_reason():
    response = _FakeSSE(
        [
            'data: {"choices":[{"delta":{"content":"part one"},"finish_reason":"length"}]}',
            "data: [DONE]",
        ]
    )

    events = list(iter_openai_sse(response, model="gpt-4o-mini"))

    assert events[-1].final is not None
    assert events[-1].final.finish_reason == "length"
