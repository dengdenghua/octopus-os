from __future__ import annotations

from runtime.memory.threads._replay import _merge_delta
from runtime.protocol import CommandExecutionItem, ReasoningItem
from runtime.protocol.text_limits import (
    MAX_AGGREGATED_OUTPUT,
    MAX_STREAM_ITEM_CONTENT,
    OUTPUT_TRUNCATION_MARK,
    STREAM_CONTENT_TRUNCATION_MARK,
)


def test_event_log_replay_caps_reasoning_content() -> None:
    item = ReasoningItem(content="")

    _merge_delta(item, "reasoning", "r" * (MAX_STREAM_ITEM_CONTENT + 1000))
    frozen = item.content
    _merge_delta(item, "reasoning", "more")

    assert item.content == frozen
    assert len(item.content) == MAX_STREAM_ITEM_CONTENT + len(STREAM_CONTENT_TRUNCATION_MARK)


def test_event_log_replay_caps_command_output() -> None:
    item = CommandExecutionItem(command="test")

    _merge_delta(item, "commandOutput", "x" * (MAX_AGGREGATED_OUTPUT + 1000))

    assert len(item.aggregated_output) == MAX_AGGREGATED_OUTPUT + len(OUTPUT_TRUNCATION_MARK)

