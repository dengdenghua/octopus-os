"""Per-session token/cost attribution for sub-agent spend.

dsh's session-log invariant extended to spend: the child react loop writes
``token_usage`` rows attributed to the durable session the bridge scoped
around it, the turn summary sums those rows, and a resume path can report
how many tokens / how much a session cost from the log alone. These tests
cover the ambient scope, the journal attribution seam, the summary
aggregation, and the derivation surface.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path

from runtime.core.cerebrum.react_model_stream import _ambient_subagent_session_id
from runtime.execution.suckers._ephemeral_events import _emit_sub_session_summary
from runtime.memory.journal import InMemoryJournal, JSONLJournal, SubSessionSummaryEvent
from runtime.memory.journal._journal_models import TokenUsageEvent
from runtime.memory.journal.derive import derive_session_summaries
from runtime.platform.process.session import Session, session_scope


def _scoped_session_with_journal(journal: InMemoryJournal) -> Session:
    return Session(metadata={"journal": journal})


def _sid(seed: int = 0) -> str:
    return f"{seed:032x}"


def _tid(seed: int = 0) -> str:
    return str(uuid.UUID(int=seed))


def test_ambient_scope_sets_and_resets() -> None:
    from runtime.execution.subagents._ambient import (
        current_subagent_session_id,
        subagent_session_scope,
    )

    assert current_subagent_session_id() == ""
    with subagent_session_scope("0123456789abcdef0123456789abcdef"):
        assert current_subagent_session_id() == "0123456789abcdef0123456789abcdef"
    assert current_subagent_session_id() == ""


def test_ambient_scope_isolates_per_thread() -> None:
    from runtime.execution.subagents._ambient import (
        current_subagent_session_id,
        subagent_session_scope,
    )

    seen: list[str] = []

    def worker(sid: str) -> None:
        with subagent_session_scope(sid):
            seen.append(current_subagent_session_id())

    with subagent_session_scope("main-session"):
        threads = [threading.Thread(target=worker, args=(f"sid-{i}",)) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # Parent context is untouched by child threads.
        assert current_subagent_session_id() == "main-session"
    assert sorted(seen) == ["sid-0", "sid-1", "sid-2"]


def test_ambient_helper_in_core_stream_reads_scope() -> None:
    from runtime.execution.subagents._ambient import subagent_session_scope

    assert _ambient_subagent_session_id() == ""
    with subagent_session_scope("feed-me"):
        assert _ambient_subagent_session_id() == "feed-me"


def test_write_token_usage_attributed_roundtrips_jsonl(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    journal.write_token_usage(
        task_id=_tid(1),
        session_id=_sid(1),
        iteration=2,
        input_tokens=120,
        output_tokens=80,
        cost_usd=0.0034,
        model="deepseek-v4",
    )

    events = journal.read_all()
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, TokenUsageEvent)
    assert ev.session_id == _sid(1)
    assert ev.iteration == 2
    assert ev.input_tokens == 120
    assert ev.output_tokens == 80
    assert ev.cost_usd == 0.0034
    assert ev.model == "deepseek-v4"


def test_write_token_usage_zero_tokens_is_noop() -> None:
    journal = InMemoryJournal()
    journal.write_token_usage(
        task_id=_tid(1),
        session_id=_sid(1),
        iteration=1,
        input_tokens=0,
        output_tokens=0,
    )
    assert journal.read_all() == []


def test_unattributed_usage_row_stays_unattributed() -> None:
    journal = InMemoryJournal()
    journal.write_token_usage(
        task_id=_tid(1),
        iteration=1,
        input_tokens=10,
        output_tokens=5,
    )
    ev = journal.read_all()[0]
    assert isinstance(ev, TokenUsageEvent)
    assert ev.session_id == ""


def test_emit_summary_sums_only_attributed_rows() -> None:
    journal = InMemoryJournal()
    sid = _sid(1)
    other = _sid(2)
    # This session's spend across two rounds.
    journal.write_token_usage(
        task_id=_tid(1),
        session_id=sid,
        iteration=1,
        input_tokens=100,
        output_tokens=200,
        cost_usd=0.001,
    )
    journal.write_token_usage(
        task_id=_tid(1),
        session_id=sid,
        iteration=2,
        input_tokens=50,
        output_tokens=100,
        cost_usd=0.002,
    )
    # A different session and a parent (unattributed) row must not leak in.
    journal.write_token_usage(
        task_id=_tid(2),
        session_id=other,
        iteration=1,
        input_tokens=9999,
        output_tokens=9999,
        cost_usd=9.0,
    )
    journal.write_token_usage(
        task_id=_tid(3), iteration=1, input_tokens=8888, output_tokens=8888, cost_usd=8.0
    )

    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_session_summary(sid, agent_id="researcher", rounds=2, success=True)

    summaries = derive_session_summaries(journal, session_id=sid)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.input_tokens == 150
    assert s.output_tokens == 300
    assert s.cost_usd == 0.003
    assert s.rounds == 2
    assert s.success is True


def test_emit_summary_without_session_is_noop() -> None:
    journal = InMemoryJournal()
    with session_scope(_scoped_session_with_journal(journal)):
        _emit_sub_session_summary("", rounds=1)
    assert journal.read_all() == []


def test_derive_surfaces_usage_and_legacy_rows_default_to_zero() -> None:
    journal = InMemoryJournal()
    sid = _sid(1)
    journal.write(
        SubSessionSummaryEvent(
            session_id=sid,
            agent_id="critic",
            rounds=1,
            success=True,
            input_tokens=42,
            output_tokens=17,
            cost_usd=0.0009,
        )
    )
    # A pre-usage row (fields absent → defaults) must still derive cleanly.
    journal.write(
        SubSessionSummaryEvent(
            session_id=sid,
            agent_id="writer",
            rounds=3,
            success=False,
            error="nope",
        )
    )

    summaries = derive_session_summaries(journal, session_id=sid)
    assert [(s.agent_id, s.input_tokens, s.output_tokens, s.cost_usd) for s in summaries] == [
        ("critic", 42, 17, 0.0009),
        ("writer", 0, 0, 0.0),
    ]


def test_emit_summary_is_best_effort_without_journal() -> None:
    """No session bound → helper returns silently, nothing crashes."""

    _emit_sub_session_summary(_sid(1), rounds=1)


def test_summary_event_roundtrips_jsonl(tmp_path: Path) -> None:
    journal = JSONLJournal(tmp_path / "journal.jsonl")
    journal.write(
        SubSessionSummaryEvent(
            session_id=_sid(1),
            agent_id="researcher",
            rounds=2,
            success=True,
            input_tokens=100,
            output_tokens=50,
            cost_usd=0.0015,
        )
    )
    ev = journal.read_all()[0]
    assert isinstance(ev, SubSessionSummaryEvent)
    assert ev.input_tokens == 100
    assert ev.output_tokens == 50
    assert ev.cost_usd == 0.0015

