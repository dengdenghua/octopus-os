"""Backend delta coalescing in _ReactBridgeState.

Per-token LLM chunks must not become per-token WebSocket frames. The
bridge buffers and flushes on ~64 chars / 50ms / kind switch / item
finalization. Invariants pinned here:

  * time-to-first-token: the FIRST chunk of an item is never buffered
  * concatenated delta content is byte-identical to the input chunks
  * flush() drains the tail before completing the item
  * a mid-stream stall is bounded by the deadline timer
"""

import asyncio

import pytest

from runtime.protocol import Turn, TurnParams
from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState


class _StubLog:
    def item_started(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass

    def item_delta(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass

    def item_completed(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass


class _StubEmitter:
    def __init__(self) -> None:
        self.notified: list[tuple[str, dict]] = []

    async def notify(self, method, params) -> None:
        self.notified.append((str(method), params))

    def deltas(self, method_suffix: str = "delta") -> list[str]:
        return [p["delta"] for m, p in self.notified if method_suffix.lower() in m.lower()]


def _make_turn() -> Turn:
    return Turn(
        id="turn-1",
        threadId="th-1",
        params=TurnParams(threadId="th-1", input=[{"type": "text", "text": "go"}]),
    )


@pytest.mark.asyncio
async def test_first_chunk_emits_immediately() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_agent_message(turn, log, emitter, "hi")

    assert emitter.deltas() == ["hi"]


@pytest.mark.asyncio
async def test_small_chunks_coalesce_until_flush() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_agent_message(turn, log, emitter, "a")
    for ch in "bcdefg":
        await state.append_agent_message(turn, log, emitter, ch)
    # Tail still buffered (under both thresholds).
    assert emitter.deltas() == ["a"]

    await state.flush(turn, log, emitter)

    assert "".join(emitter.deltas()) == "abcdefg"
    # 1 first-chunk frame + 1 coalesced tail — not 7 frames.
    assert len(emitter.deltas()) == 2
    # Completed snapshot carries the full text.
    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    assert completed and completed[0]["item"]["text"] == "abcdefg"


@pytest.mark.asyncio
async def test_size_threshold_forces_flush() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_agent_message(turn, log, emitter, "x")
    await state.append_agent_message(turn, log, emitter, "y" * 70)

    # 70 chars crossed _DELTA_FLUSH_MAX_CHARS — no waiting for a timer.
    assert "".join(emitter.deltas()) == "x" + "y" * 70


@pytest.mark.asyncio
async def test_kind_switch_drains_in_order() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_reasoning(turn, log, emitter, "think")
    await state.append_reasoning(turn, log, emitter, "ing")
    await state.append_agent_message(turn, log, emitter, "answer")

    methods = [m for m, _ in emitter.notified if "delta" in m.lower()]
    # Reasoning frames (first + drained tail) strictly precede the
    # message frame.
    assert [m.split("/")[-1].lower() for m in methods] == [
        "textdelta",
        "textdelta",
        "delta",
    ]
    assert "".join(emitter.deltas("textDelta")) == "thinking"


@pytest.mark.asyncio
async def test_deadline_timer_flushes_a_stalled_tail() -> None:
    state = _ReactBridgeState()
    turn = _make_turn()
    emitter = _StubEmitter()
    log = _StubLog()

    await state.append_agent_message(turn, log, emitter, "head ")
    await state.append_agent_message(turn, log, emitter, "tail")
    assert "".join(emitter.deltas()) == "head "

    # No further chunks arrive — the deadline task must drain the tail.
    await asyncio.sleep(state._DELTA_FLUSH_INTERVAL_S * 3)

    assert "".join(emitter.deltas()) == "head tail"
