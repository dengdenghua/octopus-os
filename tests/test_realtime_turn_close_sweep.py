"""``_close_turn`` must leave no item stuck at inProgress.

An item that stays inProgress spins in the UI forever. Thread
teD7hPf9dkGOExwO0dIiBE ended a turn as ``interrupted`` with three ``bb_read``
calls and one ``call_agent_parallel`` still open -- and the reason the user
was asking for was inside those very bb_read calls.

The sweep has two deliberate exemptions, so both directions are tested here:
a blanket sweep would break background_exec (whose item is completed later by
a watcher) and would emit a phantom second result for a subagent spawn tile.
"""

from __future__ import annotations

from typing import Any

from runtime.protocol import ItemStatus, Turn, TurnStatus
from runtime.protocol.items import CommandExecutionItem, McpToolCallItem
from runtime.sensing.gateway.realtime_turn_lifecycle import _close_turn


class _RecordingLog:
    def __init__(self) -> None:
        self.completed: list[Any] = []
        self.turns: list[tuple[str, TurnStatus]] = []

    def item_completed(self, thread_id: str, turn_id: str, item: Any) -> None:
        self.completed.append(item)

    def turn_completed(
        self, thread_id: str, turn_id: str, status: TurnStatus, error: Any = None
    ) -> None:
        self.turns.append((turn_id, status))


def _turn(status: TurnStatus, items: list[Any]) -> Turn:
    turn = Turn(id="trn_x", thread_id="th", status=status)
    turn.items.extend(items)
    return turn


def _cmd(item_id: str, **preview: Any) -> CommandExecutionItem:
    return CommandExecutionItem(
        id=item_id,
        command="bb_read",
        status=ItemStatus.IN_PROGRESS,
        input_preview=preview or None,
    )


def test_interrupted_turn_terminates_open_items() -> None:
    log = _RecordingLog()
    turn = _turn(TurnStatus.INTERRUPTED, [_cmd("a"), _cmd("b")])

    _close_turn(log, "th", turn)

    assert [i.status for i in turn.items] == [ItemStatus.FAILED, ItemStatus.FAILED]
    assert len(log.completed) == 2
    # The turn's own outcome is the cause; a bare failure would read as if the
    # command itself broke.
    assert "interrupted" in (turn.items[0].aggregated_output or "")


def test_sweep_runs_before_the_turn_is_closed() -> None:
    """Chronology: an item completion appended after turn_completed replays
    out of order."""
    order: list[str] = []
    log = _RecordingLog()
    log.item_completed = lambda *a, **k: order.append("item")  # type: ignore[method-assign]
    log.turn_completed = lambda *a, **k: order.append("turn")  # type: ignore[method-assign]

    _close_turn(log, "th", _turn(TurnStatus.INTERRUPTED, [_cmd("a")]))

    assert order == ["item", "turn"]


def test_already_terminal_items_are_left_alone() -> None:
    log = _RecordingLog()
    done = _cmd("a")
    done.status = ItemStatus.COMPLETED
    _close_turn(log, "th", _turn(TurnStatus.COMPLETED, [done]))

    assert done.status == ItemStatus.COMPLETED
    assert log.completed == []


def test_background_items_are_exempt() -> None:
    """background_exec is completed later by a watcher; sweeping it would
    overwrite a real result that is still coming."""
    log = _RecordingLog()
    bg = _cmd("c_bg", background=True, task_id="task-1")
    _close_turn(log, "th", _turn(TurnStatus.COMPLETED, [bg]))

    assert bg.status == ItemStatus.IN_PROGRESS
    assert log.completed == []


def test_subagent_spawn_marker_is_exempt() -> None:
    """The spawn tile is half of a pair; __subagent_finished__ carries the
    outcome, so completing it too would render a phantom second result."""
    log = _RecordingLog()
    spawn = McpToolCallItem(
        id="subagent_spawn_1",
        server="subagent",
        tool="__subagent_spawned__",
        status=ItemStatus.IN_PROGRESS,
    )
    _close_turn(log, "th", _turn(TurnStatus.INTERRUPTED, [spawn]))

    assert spawn.status == ItemStatus.IN_PROGRESS
    assert log.completed == []

