from __future__ import annotations

import pytest

from runtime.memory.threads.event_log import EventLog
from runtime.platform.process.session import Session, session_scope
from runtime.protocol import (
    AgentMessageItem,
    CommandExecutionItem,
    ItemStatus,
    ReasoningItem,
    Turn,
    TurnParams,
    TurnStatus,
    UserMessageItem,
)
from runtime.sensing.gateway.realtime_engine_history import engine_history_for_turn
from runtime.sensing.gateway.realtime_execution_evidence import record_execution


@pytest.fixture
def journal(tmp_path):
    log = EventLog(tmp_path / "history.jsonl")
    log.thread_started("history")
    return log


def start(log, *, actor=None, tenant=None, thread="history"):
    turn = Turn(
        threadId=thread,
        params=TurnParams(
            threadId=thread,
            owner_actor_id=actor,
            tenant_id=tenant,
            model="preview-model",
        ),
    )
    log.turn_started(thread, turn)
    return turn


def append(log, engine, label, *, status=TurnStatus.COMPLETED, received=True, extra=()):
    turn = start(log)
    record_execution(log, turn, engine=engine, driver="test", model="actual-model")
    for item in [
        UserMessageItem(text=f"question-{label}"),
        *extra,
        AgentMessageItem(text=f"answer-{label}"),
    ]:
        item.status = ItemStatus.COMPLETED
        log.item_completed(turn.thread_id, turn.id, item)
    if received:
        engine_history_for_turn(log, turn, engine).mark_delivered()
    log.turn_completed(turn.thread_id, turn.id, status)
    return turn


def test_fresh_history_and_resumed_increment_follow_actual_engine(journal):
    append(journal, "native", "old")
    append(journal, "codex", "own")
    append(
        journal,
        "native",
        "missing",
        extra=[
            CommandExecutionItem(
                command="read_file",
                aggregatedOutput="verified-result",
                exitCode=0,
            )
        ],
    )
    turn = start(journal)
    history = engine_history_for_turn(journal, turn, "codex")
    resumed = history.prompt("latest", resumed=True)
    assert "question-missing" in resumed and "verified-result" in resumed
    assert "question-own" not in resumed and "question-old" not in resumed
    fresh = history.prompt("latest", resumed=False)
    assert all(f"question-{key}" in fresh for key in ("old", "own", "missing"))
    assert fresh.endswith("latest")
    assert "permission grants" in fresh


def test_same_turn_delivery_survives_a_recreated_host_session(journal):
    append(journal, "native", "missing")
    turn = start(journal)
    with session_scope(
        Session(
            thread_id=turn.thread_id,
            turn_id=turn.id,
            metadata={"_host_history_delivered": {"codex"}},
        )
    ):
        history = engine_history_for_turn(journal, turn, "codex")
        assert "question-missing" in history.prompt("first", resumed=True)
        history.mark_delivered()
        assert history.prompt("steer", resumed=True) == "steer"
    # Receipt is server-owned; mutable client-looking metadata cannot skip it.
    with session_scope(Session(thread_id=turn.thread_id, turn_id=turn.id)):
        recreated = engine_history_for_turn(journal, turn, "codex")
        assert recreated.prompt("steer", resumed=True) == "steer"
        assert "question-missing" in recreated.prompt("fresh", resumed=False)


def test_failed_turn_does_not_advance_boundary_or_replay_private_reasoning(journal):
    append(journal, "codex", "old")
    append(journal, "native", "important")
    append(
        journal,
        "codex",
        "failure",
        status=TurnStatus.FAILED,
        extra=[
            ReasoningItem(content="PRIVATE_REASONING"),
            AgentMessageItem(text="UNFINISHED_DRAFT", messageKind="commentary"),
        ],
    )
    history = engine_history_for_turn(journal, start(journal), "codex")
    text = history.prompt("next", resumed=True)
    assert "question-important" in text
    assert "PRIVATE_REASONING" not in text
    assert "UNFINISHED_DRAFT" not in text


def test_current_and_future_turns_are_excluded(journal):
    append(journal, "native", "earlier")
    turn = start(journal)
    journal.item_completed(turn.thread_id, turn.id, UserMessageItem(text="CURRENT_SECRET"))
    append(journal, "native", "future")
    text = engine_history_for_turn(journal, turn, "codex").prompt("latest", resumed=False)
    assert "question-earlier" in text
    assert "CURRENT_SECRET" not in text and "question-future" not in text


@pytest.mark.parametrize(
    "actor,tenant,thread",
    [
        ("other", "tenant-a", "history"),
        ("alice", "other", "history"),
        ("alice", "tenant-a", "other"),
    ],
)
def test_durable_history_rejects_other_principal_or_thread(journal, actor, tenant, thread):
    old = start(journal, actor=actor, tenant=tenant, thread=thread)
    journal.turn_completed(old.thread_id, old.id, TurnStatus.COMPLETED)
    current = start(journal, actor="alice", tenant="tenant-a")
    with pytest.raises(ValueError):
        engine_history_for_turn(journal, current, "codex")


def test_history_is_bounded(journal):
    for index in range(40):
        append(journal, "native", f"{index}-" + "X" * 10_000, received=False)
    text = engine_history_for_turn(journal, start(journal), "codex").prompt("next", resumed=False)
    assert len(text) < 50_000
    assert '"omitted_turns": 0' not in text
    assert "question-0-" not in text


def test_model_replay_accepts_only_matching_engine_and_invocation(journal):
    turn = append(journal, "codex", "selected")
    original = journal.replay()[0]
    assert original.execution.engine == "codex"
    assert original.params.model == "actual-model"
    for bad in [
        {"engine": "native", "invocation": 1, "model": "wrong-engine"},
        {"engine": "codex", "invocation": 0, "model": "stale"},
        {"engine": "codex", "invocation": True, "model": "bool-is-not-invocation"},
        {"engine": "codex", "invocation": 1, "model": ""},
    ]:
        journal.turn_updated(turn.thread_id, turn.id, execution_model=bad)
    assert journal.replay()[0].params.model == "actual-model"
    journal.turn_updated(
        turn.thread_id,
        turn.id,
        execution={
            "engine": "native",
            "driver": "forged",
            "invocation": 2,
        },
    )
    assert journal.replay()[0].execution.engine == "codex"


def test_same_turn_cannot_silently_switch_engine(journal):
    turn = start(journal)
    record_execution(journal, turn, engine="codex", driver="codex")
    with pytest.raises(ValueError, match="cannot change"):
        record_execution(journal, turn, engine="native", driver="react")
