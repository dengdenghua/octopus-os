"""Tests for context compaction.

Two layers:

1. Pure ``compact()`` function — does it pick the right range, build
   a summary, and leave the keep-recent turns untouched?
2. Integration with ``EventLog`` — does ``turn_compacted`` + ``replay()``
   produce the expected surviving turn list?
"""

from __future__ import annotations

from pathlib import Path

from runtime.memory.threads.compaction import (
    CompactionPolicy,
    CompactionResult,
    compact,
    should_compact,
)
from runtime.memory.threads.event_log import EventLog
from runtime.platform.models.primitives import new_id
from runtime.protocol.items import (
    AgentMessageItem,
    CommandExecutionItem,
    ErrorItem,
    McpToolCallItem,
    PlanItem,
    TodoEntry,
    TodoListItem,
    Turn,
    TurnStatus,
    UserMessageItem,
)


def _make_turn(
    idx: int,
    thread_id: str = "th",
    user_text: str = "",
    agent_text: str = "",
    command: str | None = None,
) -> Turn:
    items: list = []
    if user_text:
        items.append(UserMessageItem(text=user_text))
    if agent_text:
        items.append(AgentMessageItem(text=agent_text))
    if command:
        items.append(CommandExecutionItem(command=command))
    return Turn(
        id=f"trn_{idx:04d}_{new_id().hex[:6]}",
        threadId=thread_id,
        status=TurnStatus.COMPLETED,
        items=items,
    )


class TestTrigger:
    def test_no_compact_when_under_threshold(self) -> None:
        turns = [_make_turn(i) for i in range(5)]
        assert should_compact(turns, CompactionPolicy(trigger_at=20, keep_recent=10)) is False

    def test_no_compact_when_disabled(self) -> None:
        turns = [_make_turn(i) for i in range(50)]
        assert should_compact(turns, CompactionPolicy(trigger_at=5, keep_recent=10)) is False

    def test_fires_when_over_threshold(self) -> None:
        turns = [_make_turn(i) for i in range(25)]
        assert should_compact(turns, CompactionPolicy(trigger_at=20, keep_recent=10)) is True


class TestCompact:
    def test_returns_none_below_trigger(self) -> None:
        turns = [_make_turn(i) for i in range(5)]
        assert compact("th", turns, CompactionPolicy(trigger_at=20, keep_recent=10)) is None

    def test_summary_covers_stale_range_only(self) -> None:
        turns = [_make_turn(i, user_text=f"ask-{i}", agent_text=f"ans-{i}") for i in range(25)]
        policy = CompactionPolicy(trigger_at=20, keep_recent=10)
        result = compact("th", turns, policy)
        assert isinstance(result, CompactionResult)
        assert len(result.superseded_ids) == 15  # 25 - keep_recent(10)
        # Should not include any of the kept-recent ids.
        recent_ids = {t.id for t in turns[-10:]}
        assert not recent_ids.intersection(result.superseded_ids)

    def test_summary_text_references_all_stale_turns(self) -> None:
        turns = [_make_turn(i, user_text=f"ask-{i}", agent_text=f"ans-{i}") for i in range(15)]
        policy = CompactionPolicy(trigger_at=10, keep_recent=5)
        result = compact("th", turns, policy)
        assert result is not None
        text = result.summary_turn.items[0].text  # type: ignore[attr-defined]
        # 15 - 5 = 10 stale → summary should mention them by count
        assert "[turn 1/10" in text
        assert "[turn 10/10" in text
        assert "ask-0" in text

    def test_custom_summariser_used(self) -> None:
        turns = [_make_turn(i, user_text=f"ask-{i}") for i in range(15)]
        called: list[int] = []

        def summariser(stale):  # type: ignore[no-untyped-def]
            called.append(len(stale))
            return "my-custom-summary"

        result = compact(
            "th",
            turns,
            CompactionPolicy(
                trigger_at=10,
                keep_recent=5,
                custom_summariser=summariser,
            ),
        )
        assert result is not None
        assert called == [10]
        item = result.summary_turn.items[0]
        assert getattr(item, "text", "") == "my-custom-summary"

    def test_respects_max_summary_chars(self) -> None:
        turns = [_make_turn(i, user_text=f"ask {i}") for i in range(50)]
        result = compact(
            "th",
            turns,
            CompactionPolicy(trigger_at=15, keep_recent=5, max_summary_chars=120),
        )
        assert result is not None
        item = result.summary_turn.items[0]
        assert len(getattr(item, "text", "")) <= 120

    def test_summary_counts_non_text_operational_items(self) -> None:
        turn = Turn(
            threadId="th",
            status=TurnStatus.COMPLETED,
            items=[
                UserMessageItem(text="run tools"),
                McpToolCallItem(server="fs", tool="read", result={"ok": True}),
                PlanItem(text="1. inspect\n2. patch"),
                TodoListItem(plan=[TodoEntry(title="ship", status="completed")]),
                ErrorItem(message="tool failed"),
            ],
        )
        result = compact(
            "th",
            [turn, _make_turn(1)],
            CompactionPolicy(trigger_at=2, keep_recent=1),
        )
        assert result is not None
        text = result.summary_turn.items[0].text  # type: ignore[attr-defined]
        assert "1 MCP tool call(s)" in text
        assert "1 plan item(s)" in text
        assert "1 todo list(s)" in text
        assert "error: tool failed" in text


class TestEventLogIntegration:
    def test_replay_drops_superseded_turns_and_inserts_summary(self, tmp_path: Path) -> None:
        log = EventLog(tmp_path / "th.jsonl")
        log.thread_started("th")

        turns = [_make_turn(i, user_text=f"u-{i}") for i in range(5)]
        for t in turns:
            log.turn_started("th", t)
            for it in t.items:
                log.item_started("th", t.id, it)
                log.item_completed("th", t.id, it)
            log.turn_completed("th", t.id, TurnStatus.COMPLETED)

        # Compact the first 3.
        policy = CompactionPolicy(trigger_at=5, keep_recent=2)
        assert should_compact(turns, policy) is True
        result = compact("th", turns, policy)
        assert result is not None
        log.turn_compacted("th", result.summary_turn, result.superseded_ids)

        rebuilt = log.replay()
        ids = [t.id for t in rebuilt]
        # Summary replaces the first 3; kept-recent 2 intact.
        assert ids == [result.summary_turn.id] + [t.id for t in turns[-2:]]

    def test_compaction_event_leaves_prior_events_untouched(self, tmp_path: Path) -> None:
        """Append-only invariant: the raw events for the superseded
        turns are still on disk. A downstream audit that ignores
        ``turn_compacted`` events sees the full history."""
        log = EventLog(tmp_path / "th.jsonl")
        log.thread_started("th")
        turns = [_make_turn(i) for i in range(3)]
        for t in turns:
            log.turn_started("th", t)
            log.turn_completed("th", t.id, TurnStatus.COMPLETED)

        result = compact(
            "th",
            turns,
            CompactionPolicy(trigger_at=3, keep_recent=1),
        )
        assert result is not None
        log.turn_compacted("th", result.summary_turn, result.superseded_ids)

        all_events = list(log.iter_events())
        turn_started_ids = [e.turn_id for e in all_events if e.event == "turn_started"]
        assert turn_started_ids == [t.id for t in turns]
