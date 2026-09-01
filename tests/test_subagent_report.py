"""Subagent report lane tests — dsh ``tool-subagent-report`` port."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from runtime.execution.subagents import bridge
from runtime.execution.subagents.sessions import (
    DEFAULT_MAX_CONSECUTIVE_WAKES,
    SubagentReport,
    SubagentSessionStore,
    get_subagent_session_store,
    set_subagent_session_store,
)


def _store(tmp_path: Path) -> SubagentSessionStore:
    return SubagentSessionStore(base_dir=tmp_path / "sessions")


# ─── store: report persistence ───────────────────────────────────────────


def test_append_report_persists_across_instances(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="找到 3 个专利", delivery="wakeup")
    store.append_report(session.session_id, content="第二个发现", delivery="quiet")

    fresh = SubagentSessionStore(base_dir=tmp_path / "sessions")
    loaded = fresh.get(session.session_id)
    assert loaded is not None
    assert len(loaded.reports) == 2
    assert loaded.reports[0].content == "找到 3 个专利"
    assert loaded.reports[0].delivery == "wakeup"
    assert loaded.reports[1].delivery == "quiet"


def test_append_report_rejects_empty_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    with pytest.raises(ValueError):
        store.append_report(session.session_id, content="   ")
    with pytest.raises(ValueError):
        store.append_report(session.session_id, content="")


def test_append_report_unknown_session_returns_none(tmp_path: Path) -> None:
    assert _store(tmp_path).append_report("missing", content="x") is None


# ─── delivery semantics ───────────────────────────────────────────────────


def test_pending_and_ack_advance_pointer(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="r1")
    store.append_report(session.session_id, content="r2")
    store.append_report(session.session_id, content="r3")

    assert [i for i, _ in store.pending_reports(session.session_id)] == [0, 1, 2]
    store.mark_reports_delivered(session.session_id, up_to_index=1)
    assert [i for i, _ in store.pending_reports(session.session_id)] == [2]

    # default acks through the latest
    store.append_report(session.session_id, content="r4")
    store.mark_reports_delivered(session.session_id)
    assert store.pending_reports(session.session_id) == []


def test_ack_never_moves_pointer_backward(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="r1")
    store.mark_reports_delivered(session.session_id)
    store.mark_reports_delivered(session.session_id, up_to_index=0)
    assert store.pending_reports(session.session_id) == []


def test_reports_prompt_renders_pending_only(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    assert store.reports_prompt(session) == ""

    store.append_report(session.session_id, content="结论一", delivery="quiet")
    store.append_report(session.session_id, content="结论二", delivery="wakeup")
    prompt = store.reports_prompt(store.get(session.session_id))
    assert "Subagent reports (child → parent)" in prompt
    assert "结论一" in prompt
    assert "(quiet)" in prompt
    assert "(wakeup)" in prompt

    store.mark_reports_delivered(session.session_id, up_to_index=0)
    prompt2 = store.reports_prompt(store.get(session.session_id))
    assert "结论一" not in prompt2
    assert "结论二" in prompt2


def test_reports_prompt_truncates_long_content(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="长" * 5000)
    prompt = store.reports_prompt(store.get(session.session_id))
    assert len(prompt) < 5000


def test_wakeup_hook_fires_and_failure_is_swallowed(tmp_path: Path) -> None:
    seen: list[tuple[str, SubagentReport]] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append((sid, report)),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="wake me")
    assert len(seen) == 1
    assert seen[0][1].content == "wake me"

    bad = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    bad.append_report(session.session_id, content="still lands")
    loaded = bad.get(session.session_id)
    assert loaded is not None and loaded.reports[-1].content == "still lands"


# ─── bounded consecutive-wake budget (dsh tool-jobs.maxConsecutiveWakes) ────


def test_wake_budget_default_is_three(tmp_path: Path) -> None:
    assert DEFAULT_MAX_CONSECUTIVE_WAKES == 3


def test_wake_budget_limits_consecutive_wakes(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=2,
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    # Two wakeups within budget → both wake the parent.
    store.append_report(session.session_id, content="wake-1", delivery="wakeup")
    store.append_report(session.session_id, content="wake-2", delivery="wakeup")
    assert seen == ["wake-1", "wake-2"]
    # Third wakeup exceeds the budget → downgraded to quiet (no new wake).
    store.append_report(session.session_id, content="wake-3", delivery="wakeup")
    assert seen == ["wake-1", "wake-2"]
    loaded = store.get(session.session_id)
    assert loaded is not None
    assert loaded.reports[-1].delivery == "quiet"
    # Quiet reports never spend the budget and never wake.
    store.append_report(session.session_id, content="quiet-1", delivery="quiet")
    assert seen == ["wake-1", "wake-2"]


def test_wake_budget_untouched_by_quiet_reports(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=1,
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="quiet-a", delivery="quiet")
    # The single budget slot is still available for the next wakeup.
    store.append_report(session.session_id, content="wake-b", delivery="wakeup")
    assert seen == ["wake-b"]


def test_refill_wake_budget_resets_after_human_turn(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=1,
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="wake-1", delivery="wakeup")
    assert seen == ["wake-1"]
    # Budget exhausted → downgraded to quiet.
    store.append_report(session.session_id, content="wake-2", delivery="wakeup")
    assert seen == ["wake-1"]
    assert store.get(session.session_id).reports[-1].delivery == "quiet"
    # Parent claims a human turn → budget refills → wakeup works again.
    store.refill_wake_budget(session.session_id)
    store.append_report(session.session_id, content="wake-3", delivery="wakeup")
    assert seen == ["wake-1", "wake-3"]


def test_refill_wake_budget_unknown_session_is_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.refill_wake_budget("not-a-real-session")  # no raise


# ─── weak-reference wake budget lifecycle (dsh spentWakes WeakMap) ───────


def test_evicted_session_starts_with_fresh_wake_budget(tmp_path: Path) -> None:
    woke: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        max_consecutive_wakes=1,
        max_cached_sessions=1,
        on_report=lambda session_id, report: woke.append(session_id),
    )
    first = store.create(agent_id="researcher", thread_id="th-parent")
    store.create(agent_id="coder", thread_id="th-other")  # evicts first

    # The first wake spends the only budget token…
    delivered = store.append_report(first.session_id, content="r1", delivery="wakeup")
    assert delivered is not None and delivered.reports[-1].delivery == "wakeup"
    # …and the second, on the still-cached session, is downgraded to quiet.
    delivered = store.append_report(first.session_id, content="r2", delivery="wakeup")
    assert delivered.reports[-1].delivery == "quiet"

    # Eviction drops the spent-wake entry (dsh WeakMap): the next cold load
    # is a session replacement with a full budget, so it wakes again.
    store.create(agent_id="writer", thread_id="th-other")  # evicts first
    delivered = store.append_report(first.session_id, content="r3", delivery="wakeup")
    assert delivered is not None and delivered.reports[-1].delivery == "wakeup"
    assert woke == [first.session_id, first.session_id]


def test_invalid_wake_budget_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_consecutive_wakes=-1)
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_consecutive_wakes=2.5)
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_consecutive_wakes=True)


def test_invalid_cache_bound_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_cached_sessions=0)
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_cached_sessions=1.5)
    with pytest.raises(ValueError):
        SubagentSessionStore(base_dir=tmp_path / "sessions", max_cached_sessions=True)


def test_zero_wake_budget_never_wakes(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=0,
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="never", delivery="wakeup")
    assert seen == []
    assert store.get(session.session_id).reports[-1].delivery == "quiet"


# ─── busy owner semantics (dsh ``inject`` vs ``followup``) ───────────────


def test_wakeup_while_owner_busy_is_queued_not_woken(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="mid-turn finding", delivery="wakeup")

    # No wake while the owner is mid-turn; the report is injected as queued.
    assert seen == []
    report = store.get(session.session_id).reports[-1]
    assert report.delivery == "queued"
    prompt = store.reports_prompt(store.get(session.session_id))
    assert "(queued)" in prompt


def test_busy_owner_does_not_consume_wake_budget(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=1,
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="busy-1", delivery="wakeup")
    store.append_report(session.session_id, content="busy-2", delivery="wakeup")
    assert seen == []
    assert [r.delivery for r in store.get(session.session_id).reports] == [
        "queued",
        "queued",
    ]

    # The single budget slot was untouched: once the owner is idle again a
    # wakeup report still wakes (dsh ``inject`` never spends ``spentWakes``).
    store.mark_owner_idle(session.session_id)
    store.append_report(session.session_id, content="after-idle", delivery="wakeup")
    assert seen == ["after-idle"]


def test_quiet_while_owner_busy_stays_quiet(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="quiet note", delivery="quiet")
    assert seen == []
    assert store.get(session.session_id).reports[-1].delivery == "quiet"


def test_mark_owner_idle_restores_wakeup(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="queued-1", delivery="wakeup")
    assert seen == []
    store.mark_owner_idle(session.session_id)
    store.append_report(session.session_id, content="woken-2", delivery="wakeup")
    assert seen == ["woken-2"]


def test_owner_busy_state_is_live_not_persisted(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="queued-1", delivery="wakeup")
    assert seen == []

    # A restarted store starts every owner idle (dsh restart-into-idle): the
    # queued report stays queued, but a new wakeup may open a parent turn.
    fresh = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    assert fresh.get(session.session_id).reports[-1].delivery == "queued"
    fresh.append_report(session.session_id, content="after-restart", delivery="wakeup")
    assert seen == ["after-restart"]


def test_mark_owner_unknown_session_noop(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_owner_busy("not-a-real-session")  # no raise
    store.mark_owner_idle("not-a-real-session")  # no raise


def test_queued_report_round_trips_across_instances(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_owner_busy(session.session_id)
    store.append_report(session.session_id, content="queued finding", delivery="wakeup")

    fresh = SubagentSessionStore(base_dir=tmp_path / "sessions")
    loaded = fresh.get(session.session_id)
    assert loaded is not None
    assert loaded.reports[-1].delivery == "queued"


# ─── thread-scoped busy state (react-loop production wiring) ─────────────


def test_thread_busy_markers_do_not_wait_for_durable_store_lock(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-parent")
    durable_locked = threading.Event()
    release_durable = threading.Event()

    def _hold_durable_lock() -> None:
        with store._lock:  # noqa: SLF001 - intentional lock-contention regression
            durable_locked.set()
            release_durable.wait(2.0)

    holder = threading.Thread(target=_hold_durable_lock, daemon=True)
    holder.start()
    assert durable_locked.wait(1.0)
    markers_finished = threading.Event()

    def _mark_live_state() -> None:
        store.mark_thread_busy("th-parent")
        store.mark_thread_idle("th-parent")
        store.mark_owner_busy(session.session_id)
        store.mark_owner_idle(session.session_id)
        store.refill_wake_budget(session.session_id)
        markers_finished.set()

    marker = threading.Thread(target=_mark_live_state, daemon=True)
    marker.start()
    try:
        assert markers_finished.wait(0.25), "live busy markers waited for durable store lock"
    finally:
        release_durable.set()
        holder.join(timeout=2.0)
        marker.join(timeout=2.0)


def test_thread_busy_queues_reports_for_all_sessions(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    first = store.create(agent_id="researcher", thread_id="th-parent")
    second = store.create(agent_id="coder", thread_id="th-parent")
    other = store.create(agent_id="researcher", thread_id="th-other")

    store.mark_thread_busy("th-parent")
    store.append_report(first.session_id, content="busy-a", delivery="wakeup")
    store.append_report(second.session_id, content="busy-b", delivery="wakeup")
    store.append_report(other.session_id, content="other-c", delivery="wakeup")

    assert seen == ["other-c"]
    assert [r.delivery for r in store.get(first.session_id).reports] == ["queued"]
    assert [r.delivery for r in store.get(second.session_id).reports] == ["queued"]


def test_thread_busy_applies_to_sessions_created_during_turn(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_thread_busy("th-parent")
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.append_report(session.session_id, content="created-mid-turn", delivery="wakeup")
    assert store.get(session.session_id).reports[-1].delivery == "queued"


def test_thread_idle_restores_wakeup_for_all_sessions(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.mark_thread_busy("th-parent")
    store.append_report(session.session_id, content="queued-1", delivery="wakeup")
    assert seen == []

    store.mark_thread_idle("th-parent")
    store.append_report(session.session_id, content="woken-2", delivery="wakeup")
    assert seen == ["woken-2"]


def test_nested_thread_busy_owner_cannot_clear_parent_early(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="reader", thread_id="th-parent")

    # Parent loop + one child loop share the public thread.
    store.mark_thread_busy("th-parent")
    store.mark_thread_busy("th-parent")
    store.mark_thread_idle("th-parent")
    store.append_report(session.session_id, content="sibling still running")

    assert seen == []
    assert store.get(session.session_id).reports[-1].delivery == "queued"

    store.mark_thread_idle("th-parent")
    store.append_report(session.session_id, content="all owners idle")
    assert seen == ["all owners idle"]


def test_empty_thread_never_queues_and_markers_are_noop(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="")
    store.mark_thread_busy("")  # no raise
    store.mark_thread_idle("")  # no raise
    store.append_report(session.session_id, content="no-thread", delivery="wakeup")
    assert seen == ["no-thread"]
    assert store.get(session.session_id).reports[-1].delivery == "wakeup"


def test_refill_thread_wake_budget_resets_all_sessions_on_thread(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=1,
    )
    first = store.create(agent_id="researcher", thread_id="th-parent")
    second = store.create(agent_id="coder", thread_id="th-parent")
    store.append_report(first.session_id, content="a1", delivery="wakeup")
    store.append_report(second.session_id, content="b1", delivery="wakeup")
    assert seen == ["a1", "b1"]
    # Budgets are spent for both sessions → both go quiet.
    store.append_report(first.session_id, content="a2", delivery="wakeup")
    store.append_report(second.session_id, content="b2", delivery="wakeup")
    assert seen == ["a1", "b1"]

    store.refill_thread_wake_budget("th-parent")
    store.append_report(first.session_id, content="a3", delivery="wakeup")
    store.append_report(second.session_id, content="b3", delivery="wakeup")
    assert seen == ["a1", "b1", "a3", "b3"]


def test_refill_thread_wake_budget_unknown_thread_noop(tmp_path: Path) -> None:
    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
        max_consecutive_wakes=1,
    )
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.refill_thread_wake_budget("th-other")  # no raise, no reset
    store.refill_thread_wake_budget("")  # no raise
    store.append_report(session.session_id, content="a1", delivery="wakeup")
    store.append_report(session.session_id, content="a2", delivery="wakeup")
    assert seen == ["a1"]
    assert store.get(session.session_id).reports[-1].delivery == "quiet"


# ─── react-loop lifecycle wiring ─────────────────────────────────────────


def _monkeypatched_react_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    import runtime.core.cerebrum.react_loop as rl

    def fake_impl(stack, intent, agent, **kwargs):  # noqa: ANN001 — test stub
        yield {"type": "react_started", "task_id": "t", "thread_id": kwargs["thread_id"]}
        return None

    monkeypatch.setattr(rl, "_stream_react_loop_impl", fake_impl)


def test_react_loop_marks_thread_busy_during_turn_and_idle_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.core.cerebrum import react_loop as rl

    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    previous = get_subagent_session_store()
    set_subagent_session_store(store)
    try:
        session = store.create(agent_id="researcher", thread_id="th-loop")
        observed: list[str] = []

        def busy_probe(stack, intent, agent, **kwargs):  # noqa: ANN001 — test stub
            store.append_report(session.session_id, content="mid-turn", delivery="wakeup")
            observed.append(store.get(session.session_id).reports[-1].delivery)
            yield {"type": "react_started", "task_id": "t", "thread_id": kwargs["thread_id"]}
            return None

        monkeypatch.setattr(rl, "_stream_react_loop_impl", busy_probe)
        gen = rl.stream_react_loop(None, None, None, thread_id="th-loop")
        list(gen)
    finally:
        set_subagent_session_store(previous)

    # Mid-turn wakeup reports queued; after the turn a wakeup fires again.
    assert observed == ["queued"]
    assert seen == []
    store.append_report(session.session_id, content="after-turn", delivery="wakeup")
    assert seen == ["after-turn"]


def test_react_loop_early_close_clears_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.core.cerebrum import react_loop as rl

    seen: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: seen.append(report.content),
    )
    previous = get_subagent_session_store()
    set_subagent_session_store(store)
    try:
        session = store.create(agent_id="researcher", thread_id="th-loop")

        def endless(stack, intent, agent, **kwargs):  # noqa: ANN001 — test stub
            while True:
                yield {"type": "react_started", "task_id": "t", "thread_id": kwargs["thread_id"]}

        monkeypatch.setattr(rl, "_stream_react_loop_impl", endless)
        gen = rl.stream_react_loop(None, None, None, thread_id="th-loop")
        next(gen)
        gen.close()  # GeneratorExit must still clear the busy flag
    finally:
        set_subagent_session_store(previous)

    store.append_report(session.session_id, content="after-close", delivery="wakeup")
    assert seen == ["after-close"]


# ─── queued-report live injection (dsh ``inject`` via steering) ──────────


def _patch_injector() -> list[str]:
    captured: list[str] = []
    # inject_report_into_thread looks up the *global* store's registered
    # injector, so register on the conftest-provided private store.
    store = get_subagent_session_store()
    store.register_thread_injector("th-parent", lambda text: captured.append(text) or True)
    return captured


def test_queued_report_injects_into_running_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    captured = _patch_injector()
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.mark_thread_busy("th-parent")
    store.append_report(session.session_id, content="中途发现", delivery="wakeup")

    assert captured == ["[子代理报告] 中途发现"]


def test_wakeup_and_quiet_reports_never_inject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    captured = _patch_injector()
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.append_report(session.session_id, content="唤醒报告", delivery="wakeup")
    store.append_report(session.session_id, content="静默报告", delivery="quiet")
    store.mark_thread_busy("th-parent")
    store.append_report(session.session_id, content="忙碌时静默", delivery="quiet")
    assert captured == []


def test_queued_report_injection_is_truncated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.execution.subagents.sessions import QUEUED_REPORT_INJECT_MAX_CHARS

    store = _store(tmp_path)
    captured = _patch_injector()
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.mark_thread_busy("th-parent")
    store.append_report(
        session.session_id,
        content="长" * (QUEUED_REPORT_INJECT_MAX_CHARS + 100),
        delivery="wakeup",
    )
    assert len(captured) == 1
    text = captured[0]
    assert text.startswith("[子代理报告] ")
    assert len(text) <= QUEUED_REPORT_INJECT_MAX_CHARS + len("[子代理报告] ")


def test_queued_report_injection_failure_never_breaks_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(text: str) -> bool:  # noqa: ANN001 — test stub
        raise RuntimeError("injector down")

    store = _store(tmp_path)
    store.register_thread_injector("th-parent", boom)
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.mark_thread_busy("th-parent")
    delivered = store.append_report(session.session_id, content="仍要落盘", delivery="wakeup")
    assert delivered is not None
    assert delivered.reports[-1].delivery == "queued"


# ─── turn-start surfacing (dsh inject consumed at next wake) ─────────────


def test_pending_thread_reports_lists_undelivered_across_sessions(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.create(agent_id="researcher", thread_id="th-parent")
    second = store.create(agent_id="coder", thread_id="th-parent")
    other = store.create(agent_id="researcher", thread_id="th-other")
    store.append_report(first.session_id, content="r1", delivery="quiet")
    store.append_report(first.session_id, content="r2", delivery="quiet")
    store.append_report(second.session_id, content="s1", delivery="quiet")
    store.append_report(other.session_id, content="other", delivery="quiet")

    pending = store.pending_thread_reports("th-parent")
    assert [(sid, index, report.content) for sid, index, report in pending] == [
        (first.session_id, 0, "r1"),
        (first.session_id, 1, "r2"),
        (second.session_id, 0, "s1"),
    ]
    assert store.pending_thread_reports("th-other")[0][2].content == "other"
    assert store.pending_thread_reports("") == []
    assert store.pending_thread_reports("th-missing") == []

    store.mark_reports_delivered(first.session_id, up_to_index=0)
    pending_after = store.pending_thread_reports("th-parent")
    assert [(sid, index) for sid, index, _ in pending_after] == [
        (first.session_id, 1),
        (second.session_id, 0),
    ]


def test_pending_thread_reports_reads_durable_sessions_across_instances(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-parent")
    store.append_report(session.session_id, content="r1", delivery="quiet")

    fresh = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        max_cached_sessions=1,
    )
    pending = fresh.pending_thread_reports("th-parent")
    assert [(sid, index, report.content) for sid, index, report in pending] == [
        (session.session_id, 0, "r1"),
    ]


def test_evicted_sessions_stay_discoverable(tmp_path: Path) -> None:
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        max_cached_sessions=1,
    )
    first = store.create(agent_id="researcher", thread_id="th-parent")
    store.append_report(first.session_id, content="r1", delivery="quiet")
    store.create(agent_id="coder", thread_id="th-other")  # evicts first

    pending = store.pending_thread_reports("th-parent")
    assert [(sid, index, report.content) for sid, index, report in pending] == [
        (first.session_id, 0, "r1"),
    ]

    candidates = store.list_reference_candidates(target_id="th-parent")
    assert [c["sessionId"] for c in candidates] == [first.session_id]
    # Cross-thread discovery stays blocked even after eviction.
    cross_ids = [c["sessionId"] for c in store.list_reference_candidates(target_id="th-other")]
    assert first.session_id not in cross_ids

    out = store.resolve_session_mentions(
        f"use @session:{first.session_id}",
        target_id="th-parent",
    )
    assert out.content == "use"
    assert out.additional_context is not None


def test_inject_report_into_thread_public_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from runtime.execution.subagents.sessions import (
        QUEUED_REPORT_INJECT_MAX_CHARS,
        inject_report_into_thread,
        set_subagent_session_store,
    )

    captured: list[str] = []
    store = _store(tmp_path)
    previous = get_subagent_session_store()
    set_subagent_session_store(store)
    try:
        store.register_thread_injector("th-1", lambda text: captured.append(text) or True)
        assert inject_report_into_thread("th-1", "内容") is True
        assert captured == ["[子代理报告] 内容"]
        assert inject_report_into_thread("", "内容") is False
        assert inject_report_into_thread("th-1", "") is False
        assert len(captured) == 1
        assert inject_report_into_thread("th-1", "长" * (QUEUED_REPORT_INJECT_MAX_CHARS + 50))
        assert len(captured[1]) <= QUEUED_REPORT_INJECT_MAX_CHARS + len("[子代理报告] ")
        # No registered injector for an unknown thread → no-op.
        assert inject_report_into_thread("th-nobody", "内容") is False
    finally:
        set_subagent_session_store(previous)


def test_legacy_session_without_reports_loads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_turn(session.session_id, prompt="p", output="o", success=True)
    raw = (tmp_path / "sessions" / f"{session.session_id}.json").read_text(encoding="utf-8")
    (tmp_path / "sessions" / f"{session.session_id}.json").write_text(
        raw.replace('"reports": []', '"reports": null'), encoding="utf-8"
    )
    fresh = SubagentSessionStore(base_dir=tmp_path / "sessions")
    loaded = fresh.get(session.session_id)
    assert loaded is not None
    assert loaded.reports == []
    assert loaded.reports_delivered_up_to == 0


# ─── bridge wiring ────────────────────────────────────────────────────────


def test_call_subagent_attaches_pending_reports_and_acks(tmp_path: Path) -> None:
    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    store = _store(tmp_path)
    try:
        bridge.set_sub_agent_runner(lambda prompt, **kw: "the answer")  # type: ignore[arg-type]
        set_subagent_session_store(store)
        result = bridge.call_subagent(agent_id="zzz_custom_report_role", prompt="go")
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)

    assert result["success"] is True
    session_id = result["session_id"]
    assert session_id

    # Seed two undelivered reports as a continuable child would have.
    store.append_report(session_id, content="部分发现", delivery="quiet")
    store.append_report(session_id, content="最终结论", delivery="wakeup")

    previous_runner = bridge.get_sub_agent_runner()
    previous_store = get_subagent_session_store()
    try:
        bridge.set_sub_agent_runner(lambda prompt, **kw: "next answer")  # type: ignore[arg-type]
        set_subagent_session_store(store)
        second = bridge.call_subagent(
            agent_id="zzz_custom_report_role",
            prompt="继续",
            continue_session_id=session_id,
        )
    finally:
        bridge.set_sub_agent_runner(previous_runner)
        set_subagent_session_store(previous_store)

    pending = second.get("pending_reports")
    assert pending is not None
    assert [p["content"] for p in pending] == ["部分发现", "最终结论"]
    assert [p["delivery"] for p in pending] == ["quiet", "wakeup"]
    assert "最终结论" in second.get("reports_prompt", "")
    # Acked: the next call no longer sees them.
    assert store.pending_reports(session_id) == []


# ─── thread wake-handler registry (on_report production wiring) ──────────


def test_registered_thread_handler_fires_on_wakeup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[tuple[str, str]] = []
    store.register_thread_wake_handler(
        "th-1", lambda sid, report: seen.append((sid, report.content))
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="结果在这")
    assert seen == [(session.session_id, "结果在这")]


def test_registered_thread_handler_takes_precedence_over_ctor_hook(
    tmp_path: Path,
) -> None:
    ctor_calls: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: ctor_calls.append(report.content),
    )
    thread_calls: list[str] = []
    store.register_thread_wake_handler(
        "th-1", lambda sid, report: thread_calls.append(report.content)
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="wake")
    assert thread_calls == ["wake"]
    assert ctor_calls == []


def test_unregistered_thread_falls_back_to_ctor_hook(tmp_path: Path) -> None:
    ctor_calls: list[str] = []
    store = SubagentSessionStore(
        base_dir=tmp_path / "sessions",
        on_report=lambda sid, report: ctor_calls.append(report.content),
    )
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="fallback")
    assert ctor_calls == ["fallback"]


def test_unregister_thread_handler_stops_wakeup(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: seen.append(r.content))
    store.unregister_thread_wake_handler("th-1")
    assert store.registered_thread_wake_handler("th-1") is None
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="quietly")
    assert seen == []


def test_thread_handler_not_fired_for_other_thread(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: seen.append(r.content))
    session = store.create(agent_id="researcher", thread_id="th-other")
    store.append_report(session.session_id, content="elsewhere")
    assert seen == []


def test_thread_handler_not_fired_on_quiet_delivery(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: seen.append(r.content))
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="no wake", delivery="quiet")
    assert seen == []


def test_thread_handler_replaced_idempotently(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first: list[str] = []
    second: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: first.append(r.content))
    store.register_thread_wake_handler("th-1", lambda sid, r: second.append(r.content))
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="latest")
    assert second == ["latest"]
    assert first == []


def test_thread_handler_failure_is_swallowed(tmp_path: Path) -> None:
    store = _store(tmp_path)

    def boom(sid: str, report: SubagentReport) -> None:
        raise RuntimeError("wake scheduler down")

    store.register_thread_wake_handler("th-1", boom)
    session = store.create(agent_id="researcher", thread_id="th-1")
    delivered = store.append_report(session.session_id, content="still saved")
    assert delivered is not None
    assert delivered.reports[-1].content == "still saved"


def test_thread_handler_not_fired_when_budget_exhausted(tmp_path: Path) -> None:
    store = SubagentSessionStore(base_dir=tmp_path / "sessions", max_consecutive_wakes=1)
    seen: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: seen.append(r.content))
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.append_report(session.session_id, content="wake-1")
    store.append_report(session.session_id, content="wake-2")
    assert seen == ["wake-1"]


def test_thread_handler_queued_while_thread_busy(tmp_path: Path) -> None:
    store = _store(tmp_path)
    seen: list[str] = []
    store.register_thread_wake_handler("th-1", lambda sid, r: seen.append(r.content))
    session = store.create(agent_id="researcher", thread_id="th-1")
    store.mark_thread_busy("th-1")
    store.append_report(session.session_id, content="mid-turn")
    assert seen == []
    store.mark_thread_idle("th-1")
    store.append_report(session.session_id, content="after")
    assert seen == ["after"]

