"""History maintenance must not add a hidden provider call for Codex."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from runtime.memory.threads.compaction import CompactionPolicy
from runtime.memory.threads.event_log import EventLog
from runtime.platform.process.keyed_lock import KeyedLock
from runtime.protocol import AgentMessageItem, ItemStatus, Turn, TurnStatus
from runtime.sensing.gateway.realtime_execution_evidence import record_execution
from runtime.sensing.gateway.realtime_thread_ops import _maybe_compact_locked, compact_thread


@pytest.mark.parametrize("engine", ["native", "codex", None])
@pytest.mark.parametrize("manual", [False, True])
@pytest.mark.parametrize("custom", [False, True])
def test_compaction_model_policy_retains_journal_and_recent_turns(tmp_path, engine, manual, custom):
    log = EventLog(tmp_path / "history.jsonl")
    log.thread_started("thread")
    ids = []
    for index in range(5):
        turn = Turn(threadId="thread")
        ids.append(turn.id)
        log.turn_started("thread", turn)
        if engine is not None:
            record_execution(log, turn, engine=engine, driver="test")
        log.item_completed(
            "thread",
            turn.id,
            AgentMessageItem(
                text=f"Verified result {index}",
                status=ItemStatus.COMPLETED,
            ),
        )
        log.turn_completed("thread", turn.id, TurnStatus.COMPLETED)
    custom_summary = Mock(return_value="Explicit summary")
    router = Mock()
    router.call.return_value = SimpleNamespace(text="Summarized history")
    runtime = SimpleNamespace(
        _compaction_policy=CompactionPolicy(
            trigger_at=5,
            keep_recent=2,
            custom_summariser=custom_summary if custom else None,
        ),
        _summary_router=router,
        _compaction_locks=KeyedLock(),
        _log_for=lambda _: log,
    )
    emitter = SimpleNamespace(notify=AsyncMock())
    if manual:
        assert asyncio.run(compact_thread(runtime, "thread", emitter))["compacted"] is True
    else:
        asyncio.run(_maybe_compact_locked(runtime, "thread", log, emitter))
    replayed = EventLog(log.path).replay()
    assert len(replayed) == 3
    assert [turn.id for turn in replayed[-2:]] == ids[-2:]
    assert replayed[0].items
    assert emitter.notify.await_count == 2
    assert router.call.call_count == (0 if custom or engine == "codex" else 1)
    assert custom_summary.call_count == (1 if custom else 0)
