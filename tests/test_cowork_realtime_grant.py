"""Realtime single-responder path enforces the cowork context grant.

Closes the gap where the realtime react path fed a responder the full thread
history regardless of their grant (async already sliced via context_view, the
realtime path did not).
"""

from __future__ import annotations

from types import SimpleNamespace

from runtime.memory.cowork.group import ContextGrant, MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.sensing.gateway.realtime_turn_lifecycle import _inject_cowork_turn_plan

MSGS = [{"role": "user", "content": f"m{i}"} for i in range(6)]


def _runtime(store: GroupStore) -> SimpleNamespace:
    return SimpleNamespace(_cowork_group_store=store)


def _intent() -> SimpleNamespace:
    return SimpleNamespace(user_context={"conversation_messages": list(MSGS)})


def test_from_join_responder_only_sees_post_join_history(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    # Sole agent, pulled in at message 3 with a from_join grant.
    store.append(
        "t1",
        MemberEvent(
            action="invite",
            actor="u",
            target_id="alice",
            target_kind="agent",
            grant=ContextGrant(scope="from_join"),
            at_message=3,
        ),
    )
    intent = _intent()
    _inject_cowork_turn_plan(_runtime(store), thread_id="t1", text="hi", intent=intent)

    msgs = intent.user_context["conversation_messages"]
    assert [m["content"] for m in msgs] == ["m3", "m4", "m5"]  # nothing pre-join leaks


def test_all_grant_responder_keeps_full_history(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    store.append(
        "t2",
        MemberEvent(
            action="invite",
            actor="u",
            target_id="bob",
            target_kind="agent",
            grant=ContextGrant(scope="all"),
            at_message=3,
        ),
    )
    intent = _intent()
    _inject_cowork_turn_plan(_runtime(store), thread_id="t2", text="hi", intent=intent)

    msgs = intent.user_context["conversation_messages"]
    assert [m["content"] for m in msgs] == ["m0", "m1", "m2", "m3", "m4", "m5"]


def test_no_group_store_is_a_noop(tmp_path) -> None:
    intent = _intent()
    _inject_cowork_turn_plan(SimpleNamespace(), thread_id="t1", text="hi", intent=intent)
    # No store → advisory no-op, history untouched.
    assert len(intent.user_context["conversation_messages"]) == 6

