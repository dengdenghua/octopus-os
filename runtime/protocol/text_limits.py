"""Shared bounds for retained realtime text and event-log replay."""

from __future__ import annotations

MAX_AGGREGATED_OUTPUT = 256 * 1024
OUTPUT_TRUNCATION_MARK = "\n…(输出已截断,超过单条命令保留上限)"
MAX_STREAM_ITEM_CONTENT = 512 * 1024
STREAM_CONTENT_TRUNCATION_MARK = "\n…(流式内容已截断,完整增量已实时发送)"

# Sub-agent mission brief carried on the spawn lifecycle preview. The parent
# workbench renders this as the "task" lane, so keep the full mission instead
# of a tiny 120-char tease while still bounding the SSE spawn frame.
MAX_SUBAGENT_MISSION_CHARS = 4000
# Sub-agent final answer carried on the finish lifecycle preview. The parent
# workbench renders this verbatim as the answer lane, so it must not be cut to
# a preview-sized snippet. Bounded well under the realtime frame ceiling.
MAX_SUBAGENT_ANSWER_CHARS = 20000


def append_capped_text(existing: str, delta: str, *, cap: int, marker: str) -> str:
    """Append text with bounded retained size and amortized copy cost."""

    if len(existing) > cap:
        return existing
    remaining = cap - len(existing)
    if len(delta) <= remaining:
        return existing + delta
    return existing + delta[:remaining] + marker


__all__ = [
    "MAX_AGGREGATED_OUTPUT",
    "MAX_STREAM_ITEM_CONTENT",
    "MAX_SUBAGENT_MISSION_CHARS",
    "MAX_SUBAGENT_ANSWER_CHARS",
    "OUTPUT_TRUNCATION_MARK",
    "STREAM_CONTENT_TRUNCATION_MARK",
    "append_capped_text",
]
