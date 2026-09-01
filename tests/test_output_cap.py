"""A runaway command's aggregated output must stay bounded so the
turn/completed and workbench-snapshot frames never exceed the realtime
WS 16 MiB message ceiling (which dropped sockets with 1009 and took down
mid-run backends). The live per-delta stream is unaffected."""

from __future__ import annotations

from runtime.sensing.gateway.realtime_event_bridge import (
    _MAX_AGGREGATED_OUTPUT,
    _MAX_STREAM_ITEM_CONTENT,
    _OUTPUT_TRUNCATION_MARK,
    _STREAM_CONTENT_TRUNCATION_MARK,
    _append_capped_output,
    _append_capped_stream_content,
)


def test_small_output_is_untouched() -> None:
    assert _append_capped_output("abc", "def") == "abcdef"


def test_output_is_capped_with_marker() -> None:
    out = _append_capped_output("", "x" * (_MAX_AGGREGATED_OUTPUT + 5000))
    assert len(out) == _MAX_AGGREGATED_OUTPUT + len(_OUTPUT_TRUNCATION_MARK)
    assert out.endswith(_OUTPUT_TRUNCATION_MARK)


def test_frozen_once_capped_and_marker_added_once() -> None:
    out = _append_capped_output("", "a" * _MAX_AGGREGATED_OUTPUT)
    out = _append_capped_output(out, "b" * 100_000)
    out = _append_capped_output(out, "c" * 100_000)
    # Marker present exactly once; buffer never grows past cap + one marker.
    assert out.count(_OUTPUT_TRUNCATION_MARK) == 1
    assert len(out) == _MAX_AGGREGATED_OUTPUT + len(_OUTPUT_TRUNCATION_MARK)


def test_many_capped_items_stay_under_ws_frame_limit() -> None:
    # A turn with dozens of noisy command items must still fit one frame.
    per_item = _MAX_AGGREGATED_OUTPUT + len(_OUTPUT_TRUNCATION_MARK)
    assert per_item * 60 < 16 * 1024 * 1024


def test_reasoning_snapshot_is_capped_and_frozen() -> None:
    out = _append_capped_stream_content("", "r" * (_MAX_STREAM_ITEM_CONTENT + 1000))
    frozen = _append_capped_stream_content(out, "more private reasoning")

    assert frozen == out
    assert len(out) == _MAX_STREAM_ITEM_CONTENT + len(_STREAM_CONTENT_TRUNCATION_MARK)
    assert out.endswith(_STREAM_CONTENT_TRUNCATION_MARK)

