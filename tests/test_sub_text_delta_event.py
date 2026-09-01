"""Turn-level transcript bridge: ``sub_text_delta`` journal events.

dsh's session-log invariant is "model-visible means logged": the prose
a sub-agent streams must be reconstructable from the append-only log,
not only from the in-memory emitter callback. These tests cover the
event model, the emitter helper's journal mirror, and the derivation
that rebuilds per-role round prose from the log alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.execution.subagents._ambient import (
    current_subagent_session_id,
    subagent_session_scope,
)
from runtime.execution.suckers._ephemeral_events import (
    _emit_sub_session_summary,
    _emit_sub_text_delta,
    _emit_sub_user_message,
)
from runtime.memory.journal import (
    InMemoryJournal,
    JSONLJournal,
    SubSessionSummaryEvent,
    SubTextDeltaEvent,
)
from runtime.memory.journal._journal_models import TokenUsageEvent
from runtime.memory.journal._journal_parse import _EVENT_CLASSES
from runtime.memory.journal.derive import (
    SessionSummary,
    SubagentRoundStream,
    assert_logged_stream_reconstructs,
    derive_session_summaries,
    derive_session_usage,
    derive_subagent_streams,
)
from runtime.platform.process.session import Session, session_scope


def _scoped_session_with_journal(journal: InMemoryJournal) -> Session:
    return Session(metadata={"journal": journal})


def test_event_class_is_registered_for_parse() -> None:
    assert _EVENT_CLASSES["sub_text_delta"] is SubTextDeltaEvent


def test_event_roundtrips_through_jsonl(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    journal.write(
        SubTextDeltaEvent(
            role_id="researcher",
            round=2,
            delta="found vendor X",
            parent_tool_use_id="tool-1",
        )
    )

    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, SubTextDeltaEvent)
    assert ev.role_id == "researcher"
    assert ev.round == 2
    assert ev.delta == "found vendor X"
    assert ev.parent_tool_use_id == "tool-1"


def test_emit_forwards_to_emitter_and_writes_journal() -> None:
    journal = InMemoryJournal()
    seen: list[dict[str, Any]] = []

    def emitter(event: dict[str, Any]) -> None:
        seen.append(event)

    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_text_delta("researcher", 1, "你好", emitter=emitter)
        _emit_sub_text_delta("researcher", 1, "，世界", emitter=emitter)

    assert len(seen) == 2
    assert seen[0] == {
        "type": "sub_text_delta",
        "agent_id": "researcher",
        "round": 1,
        "delta": "你好",
    }
    assert seen[1]["delta"] == "，世界"

    events = journal.read_all()
    assert len(events) == 2
    for ev in events:
        assert isinstance(ev, SubTextDeltaEvent)
        assert ev.role_id == "researcher"
        assert ev.round == 1


def test_emit_noops_without_session() -> None:
    """Best-effort: no session bound → no crash, no journal write."""

    journal = InMemoryJournal()
    seen: list[dict[str, Any]] = []

    def emitter(event: dict[str, Any]) -> None:
        seen.append(event)

    _emit_sub_text_delta("researcher", 1, "solo", emitter=emitter)
    # The emitter still fires (pure in-memory forward); journal is absent.
    assert len(seen) == 1
    assert journal.read_all() == []


def test_derive_reconstructs_multi_round_prose_in_order() -> None:
    journal = InMemoryJournal()
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="第一步 "))
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="结论。"))
    journal.write(SubTextDeltaEvent(role_id="critic", round=1, delta="反对:证据不足"))
    journal.write(SubTextDeltaEvent(role_id="researcher", round=2, delta="补充数据"))

    streams = derive_subagent_streams(journal)
    assert streams == [
        SubagentRoundStream(role_id="researcher", round=1, text="第一步 结论。", chunk_count=2),
        SubagentRoundStream(role_id="critic", round=1, text="反对:证据不足", chunk_count=1),
        SubagentRoundStream(role_id="researcher", round=2, text="补充数据", chunk_count=1),
    ]


def test_derive_filters_by_role() -> None:
    journal = InMemoryJournal()
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="A"))
    journal.write(SubTextDeltaEvent(role_id="critic", round=1, delta="B"))

    streams = derive_subagent_streams(journal, role_id="critic")
    assert streams == [SubagentRoundStream(role_id="critic", round=1, text="B", chunk_count=1)]


def test_derive_empty_journal_yields_nothing() -> None:
    assert derive_subagent_streams(InMemoryJournal()) == []


def test_derive_skips_non_delta_events() -> None:
    journal = InMemoryJournal()
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="live text"))
    journal.write_user_message("普通消息")

    streams = derive_subagent_streams(journal)
    assert streams == [
        SubagentRoundStream(role_id="researcher", round=1, text="live text", chunk_count=1)
    ]


def test_assert_logged_stream_reconstructs_roundtrip() -> None:
    journal = InMemoryJournal()
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="hi "))
    journal.write(SubTextDeltaEvent(role_id="researcher", round=1, delta="there"))

    assert_logged_stream_reconstructs(
        journal,
        [SubagentRoundStream(role_id="researcher", round=1, text="hi there", chunk_count=2)],
    )


def test_emit_writes_session_id_to_journal() -> None:
    journal = InMemoryJournal()
    seen: list[dict[str, Any]] = []

    def emitter(event: dict[str, Any]) -> None:
        seen.append(event)

    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_text_delta(
            "researcher",
            1,
            "needle",
            session_id="0123456789abcdef0123456789abcdef",
            emitter=emitter,
        )

    # The emitter payload is unchanged (no session_id leaks into SSE).
    assert seen[0] == {
        "type": "sub_text_delta",
        "agent_id": "researcher",
        "round": 1,
        "delta": "needle",
    }
    ev = journal.read_all()[0]
    assert ev.session_id == "0123456789abcdef0123456789abcdef"


def test_derive_filters_by_session_id() -> None:
    journal = InMemoryJournal()
    sid1 = "11111111111111111111111111111111"
    sid2 = "22222222222222222222222222222222"
    # Same role, two sessions — role_id alone cannot tell them apart.
    journal.write(SubTextDeltaEvent(session_id=sid1, role_id="researcher", round=1, delta="A"))
    journal.write(SubTextDeltaEvent(session_id=sid2, role_id="researcher", round=1, delta="B"))

    assert [(s.session_id, s.text) for s in derive_subagent_streams(journal)] == [
        (sid1, "A"),
        (sid2, "B"),
    ]
    assert [(s.session_id, s.text) for s in derive_subagent_streams(journal, session_id=sid2)] == [
        (sid2, "B")
    ]


def test_surface_events_from_journal_interleaves_prompts_and_rounds() -> None:
    from runtime.memory.journal.derive import surface_events_from_journal

    journal = InMemoryJournal()
    sid = "33333333333333333333333333333333"
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="researcher", round=1, delta="结论A"))
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="researcher", round=2, delta="补充B"))
    # A different session's prose must not leak in.
    journal.write(
        SubTextDeltaEvent(
            session_id="44444444444444444444444444444444",
            role_id="researcher",
            round=1,
            delta="别处",
        )
    )
    other = "55555555555555555555555555555555"
    journal.write(SubTextDeltaEvent(session_id=other, role_id="writer", round=1, delta="X"))

    surface = surface_events_from_journal(journal, session_id=sid, prompts=["p1", "p2"])
    assert [e["type"] for e in surface] == [
        "user/message",
        "assistant/message",
        "user/message",
        "assistant/message",
    ]
    assert surface[0]["data"]["content"][0]["text"] == "p1"
    assert surface[1]["data"]["message"]["content"][0]["text"] == "结论A"
    assert surface[2]["data"]["content"][0]["text"] == "p2"
    assert surface[3]["data"]["message"]["content"][0]["text"] == "补充B"


def test_surface_events_from_journal_feeds_projection() -> None:
    from runtime.execution.tool_engine.session_projection import (
        retain_session_reference,
    )
    from runtime.memory.journal.derive import surface_events_from_journal

    journal = InMemoryJournal()
    sid = "66666666666666666666666666666666"
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="researcher", round=1, delta="结论"))

    surface = surface_events_from_journal(journal, session_id=sid, prompts=["问题"])
    retained = retain_session_reference(
        surface,
        session_id=sid,
        label="researcher session",
        max_bytes=4096,
    )
    assert retained is not None
    data, stats = retained
    assert data.session_id == sid
    roles = [item["role"] for item in data.conversation]
    assert roles == ["user", "assistant"]
    assert data.conversation[0]["text"] == "问题"
    assert data.conversation[1]["text"] == "结论"
    assert stats.original_messages == 2


def test_emit_sub_user_message_writes_session_row() -> None:
    journal = InMemoryJournal()
    sid = "77777777777777777777777777777777"
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_user_message(sid, "问题prompt")
        # Empty session id (one-shot / remote child) is skipped.
        _emit_sub_user_message("", "不该落盘")

    events = journal.read_all()
    assert len(events) == 1
    assert events[0].event_type == "user/message"
    assert events[0].session_id == sid
    assert events[0].text == "问题prompt"
    assert events[0].goal_source is None  # goal fold keeps ignoring it


def test_write_user_message_session_id(tmp_path: Path) -> None:
    from runtime.memory.journal import JSONLJournal

    journal = JSONLJournal(tmp_path / "journal.jsonl")
    sid = "88888888888888888888888888888888"
    journal.write_user_message("prompt", session_id=sid)

    ev = journal.read_all()[0]
    assert ev.session_id == sid
    assert ev.text == "prompt"


def test_surface_uses_journal_user_lane_not_prompts() -> None:
    from runtime.memory.journal.derive import surface_events_from_journal

    journal = InMemoryJournal()
    sid = "99999999999999999999999999999999"
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="researcher", round=1, delta="A"))
    journal.write_user_message("日志里的问题", session_id=sid)
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="researcher", round=1, delta="B"))

    surface = surface_events_from_journal(journal, session_id=sid, prompts=["fallback不该用"])
    # The journal user lane wins over the caller-supplied prompts.
    assert surface[0]["type"] == "assistant/message"
    assert surface[0]["data"]["message"]["content"][0]["text"] == "A"
    assert surface[1]["type"] == "user/message"
    assert surface[1]["data"]["content"][0]["text"] == "日志里的问题"
    assert surface[2]["type"] == "assistant/message"
    assert surface[2]["data"]["message"]["content"][0]["text"] == "B"
    assert "fallback不该用" not in str(surface)


def test_surface_pure_journal_multi_turn_interleave() -> None:
    from runtime.memory.journal.derive import surface_events_from_journal

    journal = InMemoryJournal()
    sid = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    other = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    journal.write_user_message("p1", session_id=sid)
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="r", round=1, delta="a1"))
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="r", round=1, delta="a2"))
    journal.write_user_message("p2", session_id=sid)
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="r", round=2, delta="b1"))
    # A different session's user message must not leak in.
    journal.write_user_message("别处的", session_id=other)
    journal.write(SubTextDeltaEvent(session_id=other, role_id="r", round=1, delta="X"))

    surface = surface_events_from_journal(journal, session_id=sid)
    assert [e["type"] for e in surface] == [
        "user/message",
        "assistant/message",
        "user/message",
        "assistant/message",
    ]
    assert surface[0]["data"]["content"][0]["text"] == "p1"
    assert surface[1]["data"]["message"]["content"][0]["text"] == "a1a2"
    assert surface[2]["data"]["content"][0]["text"] == "p2"
    assert surface[3]["data"]["message"]["content"][0]["text"] == "b1"


def test_surface_pure_journal_feeds_projection() -> None:
    from runtime.execution.tool_engine.session_projection import (
        retain_session_reference,
    )
    from runtime.memory.journal.derive import surface_events_from_journal

    journal = InMemoryJournal()
    sid = "cccccccccccccccccccccccccccccccc"
    journal.write_user_message("日志问题", session_id=sid)
    journal.write(SubTextDeltaEvent(session_id=sid, role_id="r", round=1, delta="日志结论"))

    surface = surface_events_from_journal(journal, session_id=sid)
    retained = retain_session_reference(surface, session_id=sid, label="researcher", max_bytes=4096)
    assert retained is not None
    data, _stats = retained
    assert [item["role"] for item in data.conversation] == ["user", "assistant"]
    assert data.conversation[0]["text"] == "日志问题"
    assert data.conversation[1]["text"] == "日志结论"


def test_sub_session_summary_registered_and_roundtrips(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    journal.write(
        SubSessionSummaryEvent(
            session_id="dddddddddddddddddddddddddddddddd",
            agent_id="researcher",
            rounds=3,
            success=False,
            error="round cap",
            input_tokens=100,
            output_tokens=40,
            cost_usd=0.0012,
        )
    )
    ev = journal.read_all()[0]
    assert isinstance(ev, SubSessionSummaryEvent)
    assert ev.session_id == "dddddddddddddddddddddddddddddddd"
    assert ev.rounds == 3
    assert ev.success is False
    assert ev.error == "round cap"
    assert ev.input_tokens == 100
    assert ev.output_tokens == 40
    assert ev.cost_usd == 0.0012


def test_emit_sub_session_summary_writes_row() -> None:
    journal = InMemoryJournal()
    sid = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    other = "ffffffffffffffffffffffffffffffff"
    # Pre-seed usage rows: two attributed to this session, one to a different
    # session and one unattributed — only the matching rows are summed.
    journal.write(
        TokenUsageEvent(session_id=sid, input_tokens=100, output_tokens=30, cost_usd=0.001)
    )
    journal.write(
        TokenUsageEvent(session_id=sid, input_tokens=50, output_tokens=10, cost_usd=0.0005)
    )
    journal.write(
        TokenUsageEvent(session_id=other, input_tokens=900, output_tokens=900, cost_usd=0.9)
    )
    journal.write(TokenUsageEvent(session_id="", input_tokens=500, output_tokens=500, cost_usd=0.5))
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_session_summary(sid, agent_id="critic", rounds=2, success=True)
        _emit_sub_session_summary("", agent_id="critic", rounds=1)  # skipped

    summaries = [e for e in journal.read_all() if e.event_type == "sub_session_summary"]
    assert len(summaries) == 1
    assert summaries[0].session_id == sid
    assert summaries[0].agent_id == "critic"
    assert summaries[0].rounds == 2
    assert summaries[0].success is True
    assert summaries[0].input_tokens == 150
    assert summaries[0].output_tokens == 40
    assert summaries[0].cost_usd == 0.0015


def test_derive_session_summaries_filters_by_session() -> None:
    journal = InMemoryJournal()
    sid1 = "11112222333344445555666677778888"
    sid2 = "88887777666655554444333322221111"
    journal.write(
        SubSessionSummaryEvent(
            session_id=sid1,
            agent_id="r",
            rounds=3,
            success=True,
            input_tokens=120,
            output_tokens=30,
            cost_usd=0.0012,
        )
    )
    journal.write(
        SubSessionSummaryEvent(session_id=sid2, agent_id="c", rounds=1, success=False, error="x")
    )
    journal.write(
        SubSessionSummaryEvent(
            session_id=sid1, agent_id="r", rounds=5, success=True, input_tokens=60
        )
    )

    all_summaries = derive_session_summaries(journal)
    assert [(s.session_id, s.rounds) for s in all_summaries] == [(sid1, 3), (sid2, 1), (sid1, 5)]
    assert all_summaries[0].input_tokens == 120
    assert all_summaries[0].output_tokens == 30
    assert all_summaries[0].cost_usd == 0.0012
    assert all_summaries[2].input_tokens == 60
    assert all_summaries[2].output_tokens == 0
    filtered = derive_session_summaries(journal, session_id=sid2)
    assert filtered == [
        SessionSummary(session_id=sid2, agent_id="c", rounds=1, success=False, error="x")
    ]


def test_subagent_session_scope_ambient_context() -> None:
    assert current_subagent_session_id() == ""
    with subagent_session_scope("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"):
        assert current_subagent_session_id() == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert current_subagent_session_id() == ""


def test_derive_session_usage_surfaces_per_call_spend() -> None:
    journal = InMemoryJournal()
    sid = "11112222333344445555666677778888"
    other = "88887777666655554444333322221111"
    journal.write(
        TokenUsageEvent(
            session_id=sid,
            iteration=1,
            input_tokens=100,
            output_tokens=30,
            cost_usd=0.001,
            model="deepseek-v4",
        )
    )
    journal.write(TokenUsageEvent(session_id=sid, iteration=2, input_tokens=50, output_tokens=10))
    journal.write(
        TokenUsageEvent(session_id=other, iteration=1, input_tokens=900, output_tokens=900)
    )
    journal.write(TokenUsageEvent(session_id="", iteration=1, input_tokens=1, output_tokens=1))

    all_usage = derive_session_usage(journal)
    assert len(all_usage) == 4

    filtered = derive_session_usage(journal, session_id=sid)
    assert [(u.iteration, u.input_tokens, u.output_tokens, u.cost_usd) for u in filtered] == [
        (1, 100, 30, 0.001),
        (2, 50, 10, 0.0),
    ]
    assert filtered[0].session_id == sid
    assert filtered[0].model == "deepseek-v4"

    other_usage = derive_session_usage(journal, session_id=other)
    assert [(u.input_tokens, u.output_tokens) for u in other_usage] == [(900, 900)]
    assert derive_session_usage(journal, session_id=sid + "nope") == []

