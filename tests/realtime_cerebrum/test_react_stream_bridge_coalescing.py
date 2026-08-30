"""The react stream bridge never blocks on decorative deltas.

When the bridge queue is full, high-frequency, individually disposable
delta metrics (throughput/visibility) are dropped via a coalescing
``put_nowait`` instead of making the producer block 10s. That blocking
was the cascade root cause behind "half replies": a stalled producer
delayed — and could drop — *structural* events (tool results, final
answer) downstream. Text-bearing deltas (thinking/commentary/text) and
tool deltas are deliberately NOT coalesced so reasoning/tool streams
stay intact. This test pins that classification.
"""

from __future__ import annotations

import runtime.sensing.gateway._realtime_react_stream_drive as drive_rs


def test_coalescable_deltas_classified_true() -> None:
    for kind in ("throughput", "visibility"):
        assert drive_rs._is_coalescable_delta({"type": kind})


def test_structural_and_text_deltas_classified_false() -> None:
    for kind in (
        "react_completed",
        "react_error",
        "react_cancelled",
        "react_paused",
        "react_started",
        "text_delta",
        "thinking_delta",
        "commentary_delta",
        "tool_call_delta",
        "tool_output_delta",
    ):
        assert not drive_rs._is_coalescable_delta({"type": kind})


def test_non_dict_and_none_not_coalescable() -> None:
    assert not drive_rs._is_coalescable_delta(None)
    assert not drive_rs._is_coalescable_delta("throughput")
    assert not drive_rs._is_coalescable_delta({})

