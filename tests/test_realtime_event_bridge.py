"""text_delta protocol-tag stripping in ``_ReactBridgeState``.

Models occasionally leak ``<ReasoningBlock>...</ReasoningBlock>`` and
related structural tags as literal text instead of routing them through
the structured reasoning / tool_use / tool_result fields.  The backend
checkpoint path rejects such payloads wholesale (``_PUBLIC_CHECKPOINT_PROTOCOL_RE``
in ``tool_bridge.py``); the streaming ``text_delta`` path cannot drop the
whole message, so ``append_agent_message`` strips the leaked tags before
emit (Task 4).  Invariants pinned here:

  * paired ``<XBlock>...</XBlock>`` spans (tag + content) are stripped
  * individual opening/closing leaked tags are stripped
  * a chunk that is *entirely* a leaked block emits nothing
  * normal prose, code fences, ReAct prefixes, JSON-looking text survive
  * the completed snapshot carries the stripped text (not the raw leak)
"""

from types import SimpleNamespace

import pytest

from runtime.protocol import Turn, TurnParams
from runtime.sensing.gateway.realtime_cerebrum import _ReactBridgeState
from runtime.sensing.gateway.tool_bridge import strip_leaked_protocol_tags


class _StubLog:
    def item_started(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass

    def item_delta(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return SimpleNamespace(event_id="event-1")

    def item_completed(self, *args, **kwargs) -> None:  # noqa: ARG002
        pass

    def turn_updated(self, *args, **kwargs) -> None:  # noqa: ARG002
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


def _new_state():
    return _ReactBridgeState(), _make_turn(), _StubEmitter(), _StubLog()


# ── strip_leaked_protocol_tags unit tests ──────────────────────────


def test_strip_paired_block_removes_tag_and_content() -> None:
    assert (
        strip_leaked_protocol_tags("hi <ReasoningBlock>secret reasoning</ReasoningBlock> bye")
        == "hi  bye"
    )


def test_strip_paired_block_for_all_structural_kinds() -> None:
    for tag in (
        "ReasoningBlock",
        "ToolCallBlock",
        "ToolResultBlock",
        "ThinkingBlock",
        "ExecutionBlock",
    ):
        text = f"before <{tag}>leaked payload</{tag}> after"
        assert strip_leaked_protocol_tags(text) == "before  after", tag


def test_strip_individual_opening_and_closing_tags() -> None:
    # Tags split across deltas arrive as individual fragments; each is
    # stripped on its own so the literal marker never reaches the UI.
    assert strip_leaked_protocol_tags("<ReasoningBlock>") == ""
    assert strip_leaked_protocol_tags("</ReasoningBlock>") == ""
    assert strip_leaked_protocol_tags("<tool_call>") == ""
    assert strip_leaked_protocol_tags("</tool_call>") == ""
    assert strip_leaked_protocol_tags("<thinking>") == ""
    assert strip_leaked_protocol_tags("</thinking>") == ""
    assert strip_leaked_protocol_tags("<function>") == ""
    assert strip_leaked_protocol_tags("<TextBlock>") == ""


def test_strip_tags_with_attributes() -> None:
    assert (
        strip_leaked_protocol_tags('<ToolCallBlock id="x" name="read_file">payload</ToolCallBlock>')
        == ""
    )


def test_strip_backtick_wrapped_tags() -> None:
    # The frontend tolerates `` `<ReasoningBlock>` `` code-span leaks;
    # the backend strips them too so the two layers agree. A lone
    # backtick-wrapped tag is removed by the individual-tag pass.
    assert strip_leaked_protocol_tags("`<ReasoningBlock>`") == ""
    assert strip_leaked_protocol_tags("see `</ReasoningBlock>` done") == "see  done"
    # When an opening and closing backtick-wrapped tag both appear, the
    # paired-block pass treats them as a pair (consuming the span
    # between them) — same as the frontend's INTERNAL_PROCESS_BLOCK_RE.
    assert (
        strip_leaked_protocol_tags("see `<ReasoningBlock>` leak `</ReasoningBlock>` done")
        == "see  done"
    )


def test_strip_preserves_normal_prose() -> None:
    assert strip_leaked_protocol_tags("normal text only") == "normal text only"
    assert strip_leaked_protocol_tags("") == ""
    assert strip_leaked_protocol_tags("用户回答：这是正常文本") == "用户回答：这是正常文本"


def test_strip_does_not_touch_react_prefixes_code_fences_or_json() -> None:
    # These are *detected* by _PUBLIC_CHECKPOINT_PROTOCOL_RE on the
    # checkpoint path (where the whole payload is dropped), but they are
    # NOT structural tags — stripping them from text_delta would damage
    # legitimate prose.  They must survive the strip path.
    assert strip_leaked_protocol_tags("Thought: I should check the file") == (
        "Thought: I should check the file"
    )
    assert strip_leaked_protocol_tags("Action: read_file") == "Action: read_file"
    assert strip_leaked_protocol_tags("```python\nprint('hi')\n```") == (
        "```python\nprint('hi')\n```"
    )
    assert strip_leaked_protocol_tags('{"key": "value"}') == '{"key": "value"}'
    assert strip_leaked_protocol_tags("[1, 2, 3]") == "[1, 2, 3]"


def test_strip_keeps_surrounding_prose_with_inline_leak() -> None:
    # tool_call is a non-Block tag: only the wrappers are stripped, the
    # inner text survives (matches the frontend per-tag strip behavior).
    assert (
        strip_leaked_protocol_tags("The answer is 42 <tool_call>noise</tool_call> and done")
        == "The answer is 42 noise and done"
    )


# ── append_agent_message integration tests ────────────────────────


@pytest.mark.asyncio
async def test_text_delta_strips_paired_reasoning_block() -> None:
    state, turn, emitter, log = _new_state()

    await state.append_agent_message(
        turn, log, emitter, "Sure <ReasoningBlock>hidden thinking</ReasoningBlock> done"
    )
    await state.flush(turn, log, emitter)

    assert "".join(emitter.deltas()) == "Sure  done"
    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    assert completed[0]["item"]["text"] == "Sure  done"


@pytest.mark.asyncio
async def test_streamed_messages_keep_the_owning_agent_identity() -> None:
    state = _ReactBridgeState(
        agent_display_name="Eve",
        agent_avatar_url="/api/agents/general/avatar",
    )
    turn, emitter, log = _make_turn(), _StubEmitter(), _StubLog()

    await state.append_agent_message(turn, log, emitter, "已完成。")
    await state.flush(turn, log, emitter)

    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    item = completed[0]["item"]
    assert item["agentDisplayName"] == "Eve"
    assert item["agentAvatarUrl"] == "/api/agents/general/avatar"


@pytest.mark.asyncio
async def test_text_delta_strips_individual_leaked_tags_split_across_chunks() -> None:
    state, turn, emitter, log = _new_state()

    # The model streams the leaked block across several deltas; each
    # fragment is stripped so no literal tag reaches the wire.
    await state.append_agent_message(turn, log, emitter, "answer ")
    await state.append_agent_message(turn, log, emitter, "<ReasoningBlock>")
    await state.append_agent_message(turn, log, emitter, "leaked")
    await state.append_agent_message(turn, log, emitter, "</ReasoningBlock>")
    await state.append_agent_message(turn, log, emitter, " tail")
    await state.flush(turn, log, emitter)

    # The ``<ReasoningBlock>`` / ``</ReasoningBlock>`` markers vanish;
    # the split "leaked" content between them is not a structural tag so
    # it survives (mirrors the frontend per-chunk fallback behavior).
    assert "".join(emitter.deltas()) == "answer leaked tail"


@pytest.mark.asyncio
async def test_text_delta_emits_nothing_for_pure_leaked_block_chunk() -> None:
    state, turn, emitter, log = _new_state()

    # First chunk opens the agent message with real prose so an item exists.
    await state.append_agent_message(turn, log, emitter, "hello")
    # A chunk that is entirely a leaked block strips to empty → no frame.
    await state.append_agent_message(
        turn, log, emitter, "<ReasoningBlock>whole chunk leaked</ReasoningBlock>"
    )
    await state.append_agent_message(turn, log, emitter, " world")
    await state.flush(turn, log, emitter)

    assert "".join(emitter.deltas()) == "hello world"
    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    assert completed[0]["item"]["text"] == "hello world"


@pytest.mark.asyncio
async def test_text_delta_preserves_normal_prose() -> None:
    state, turn, emitter, log = _new_state()

    await state.append_agent_message(turn, log, emitter, "The answer is 42.")
    await state.flush(turn, log, emitter)

    assert "".join(emitter.deltas()) == "The answer is 42."
    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    assert completed[0]["item"]["text"] == "The answer is 42."


@pytest.mark.asyncio
async def test_text_delta_preserves_code_fences_and_react_prefixes() -> None:
    state, turn, emitter, log = _new_state()

    payload = "Thought: checking.\n```python\nprint('hi')\n```\nFinal Answer: 42"
    await state.append_agent_message(turn, log, emitter, payload)
    await state.flush(turn, log, emitter)

    # None of these are structural tags, so the strip must leave them
    # byte-identical (the checkpoint path would drop them, the delta
    # path must not).
    assert "".join(emitter.deltas()) == payload
    completed = [p for m, p in emitter.notified if m.endswith("item/completed")]
    assert completed[0]["item"]["text"] == payload


@pytest.mark.asyncio
async def test_text_delta_strips_tool_call_and_function_tags() -> None:
    state, turn, emitter, log = _new_state()

    await state.append_agent_message(
        turn,
        log,
        emitter,
        "ok <tool_call>read_file</tool_call> <function=foo>bar</function> end",
    )
    await state.flush(turn, log, emitter)

    # tool_call / function are paired non-Block tags: the individual tag
    # regex strips the wrappers (including attributes like ``=foo``),
    # leaving their inner text intact — matching the frontend's per-tag
    # strip rather than dropping the content.
    assert "".join(emitter.deltas()) == "ok read_file bar end"


# ── Adaptive delta batching wiring ──────────────────────────────────


@pytest.mark.asyncio
async def test_adaptive_batching_flushes_small_burst_at_low_throughput_threshold() -> None:
    """Default adaptive batching coalesces at 32 chars with no history."""
    state = _ReactBridgeState(enable_adaptive_batching=True)
    turn, emitter, log = _make_turn(), _StubEmitter(), _StubLog()

    await state.append_agent_message(turn, log, emitter, "a")  # first → flush_now
    await state.append_agent_message(turn, log, emitter, "b" * 30)  # 30 < 32 → buffered
    assert len(emitter.deltas()) == 1
    await state.append_agent_message(turn, log, emitter, "c" * 5)  # 35 ≥ 32 → flush
    assert len(emitter.deltas()) == 2
    await state.flush(turn, log, emitter)
    assert "".join(emitter.deltas()) == "a" + "b" * 30 + "c" * 5


@pytest.mark.asyncio
async def test_adaptive_batching_disabled_keeps_fixed_threshold() -> None:
    """``enable_adaptive_batching=False`` falls back to the 64-char cap."""
    state = _ReactBridgeState(enable_adaptive_batching=False)
    turn, emitter, log = _make_turn(), _StubEmitter(), _StubLog()

    await state.append_agent_message(turn, log, emitter, "a")  # first → flush_now
    await state.append_agent_message(turn, log, emitter, "b" * 30)  # 30 < 64
    await state.append_agent_message(turn, log, emitter, "c" * 5)  # 35 < 64 → buffered
    assert len(emitter.deltas()) == 1
    await state.append_agent_message(turn, log, emitter, "d" * 30)  # 65 ≥ 64 → flush
    assert len(emitter.deltas()) == 2
    await state.flush(turn, log, emitter)
    assert "".join(emitter.deltas()) == "a" + "b" * 30 + "c" * 5 + "d" * 30


@pytest.mark.asyncio
async def test_adaptive_batching_default_on_single_chunk_message_unchanged() -> None:
    """Default-constructed bridge (adaptive on) keeps single-chunk prose."""
    state, turn, emitter, log = _new_state()

    await state.append_agent_message(turn, log, emitter, "hello")
    await state.flush(turn, log, emitter)

    assert "".join(emitter.deltas()) == "hello"


# ── legacy plan-snapshot sunset ─────────────────────────────────


def _plan_updates(emitter: _StubEmitter) -> list[dict]:
    return [p for m, p in emitter.notified if m.endswith("turn/plan/updated")]


def _snapshot_updates(emitter: _StubEmitter) -> list[dict]:
    return [p for m, p in emitter.notified if m.endswith("workbench/snapshot")]


@pytest.mark.asyncio
async def test_plan_update_omits_embedded_snapshot_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUNSET: turn/plan/updated no longer embeds workbenchSnapshot by
    default — the identical frame ships on the dedicated
    workbench/snapshot notification only."""
    import runtime.sensing.gateway.realtime_event_bridge as bridge

    monkeypatch.setattr(bridge, "_LEGACY_PLAN_SNAPSHOT", False)
    state, turn, emitter, log = _new_state()

    await state._emit_turn_update(turn, log, emitter)

    plan_updates = _plan_updates(emitter)
    snapshot_updates = _snapshot_updates(emitter)
    assert len(plan_updates) == 1
    assert len(snapshot_updates) == 1
    assert "workbenchSnapshot" not in plan_updates[0]
    # phases still ride the plan channel; the snapshot channel carries the
    # identical frame.
    assert "phases" in plan_updates[0]
    assert snapshot_updates[0]["snapshot"]["version"] >= 1


@pytest.mark.asyncio
async def test_plan_update_embeds_snapshot_when_legacy_flag_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ECHO_LEGACY_PLAN_SNAPSHOT=1 restores the embedded copy for
    out-of-tree clients that still read it."""
    import runtime.sensing.gateway.realtime_event_bridge as bridge

    monkeypatch.setattr(bridge, "_LEGACY_PLAN_SNAPSHOT", True)
    state, turn, emitter, log = _new_state()

    await state._emit_turn_update(turn, log, emitter)

    plan_updates = _plan_updates(emitter)
    assert len(plan_updates) == 1
    assert plan_updates[0]["workbenchSnapshot"]["version"] >= 1
    # The dedicated channel is unaffected — both frames ship when the
    # legacy flag is on.
    assert len(_snapshot_updates(emitter)) == 1


# ── tool-call-delta live assembly preview (dsh lane) ──────────────────


@pytest.mark.asyncio
async def test_tool_call_delta_buffers_before_start_and_merges_into_preview() -> None:
    state, turn, emitter, log = _new_state()

    await state.append_tool_call_delta(
        turn,
        log,
        emitter,
        {
            "tool_call_id": "call_1",
            "tool_name": "read_file",
            "argumentsDelta": '{"path":',
        },
    )
    # No item exists yet — fragments buffer silently for start_tool.
    assert emitter.notified == []
    assert state._tool_call_delta_buffers["call_1"] == {
        "name": "read_file",
        "arguments": '{"path":',
    }

    await state.append_tool_call_delta(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "argumentsDelta": '"README.md"}'},
    )
    assert state._tool_call_delta_buffers["call_1"]["arguments"] == '{"path":"README.md"}'

    await state.start_tool(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "tool_name": "read_file", "input_preview": None},
    )
    item = state.tools["call_1"]
    assert item.input_preview == {
        "name": "read_file",
        "arguments": '{"path":"README.md"}',
    }

    await state.complete_tool(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "output_preview": "ok", "status": "success"},
    )
    assert "call_1" not in state._tool_call_delta_buffers
    assert "call_1" not in state._tool_call_delta_emitted


@pytest.mark.asyncio
async def test_tool_call_delta_reemits_throttled_on_open_item() -> None:
    state, turn, emitter, log = _new_state()
    await state.start_tool(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "tool_name": "read_file"},
    )
    item = state.tools["call_1"]
    assert item.input_preview is None

    def _started_count() -> int:
        return sum(1 for m, _ in emitter.notified if m == "item/started")

    baseline = _started_count()

    # First fragment re-emits immediately (time-to-first-preview).
    await state.append_tool_call_delta(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "argumentsDelta": '{"path":'},
    )
    assert _started_count() == baseline + 1
    assert item.input_preview == {
        "name": "",
        "arguments": '{"path":',
        "streaming": True,
    }

    # Small fragment inside the emit stride → silent update only.
    await state.append_tool_call_delta(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "argumentsDelta": "ab"},
    )
    assert _started_count() == baseline + 1
    # Inside the stride the buffer advances but the preview stays at the
    # last emitted snapshot.
    assert state._tool_call_delta_buffers["call_1"]["arguments"] == '{"path":ab'
    assert item.input_preview["arguments"] == '{"path":'

    # Past the stride → one more re-emit with the accumulated preview.
    await state.append_tool_call_delta(
        turn,
        log,
        emitter,
        {"tool_call_id": "call_1", "argumentsDelta": "x" * 100},
    )
    assert _started_count() == baseline + 2
    assert item.input_preview["arguments"] == '{"path":ab' + "x" * 100
    assert item.input_preview["streaming"] is True

