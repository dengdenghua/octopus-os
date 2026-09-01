"""Advanced cowork behaviours: catch-up, async work, breakout, nomination."""

from __future__ import annotations

from runtime.memory.cowork.async_work import AsyncWorkStore
from runtime.memory.cowork.breakout import fork, merge_back
from runtime.memory.cowork.catchup import build_catchup
from runtime.memory.cowork.group import ContextGrant, MemberEvent, fold_state
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.nominate import CompetenceStore, gate, suggest
from runtime.memory.cowork.service import invite_member, set_mode


def test_group_state_can_replay_to_event_seq() -> None:
    events = [
        MemberEvent(action="invite", actor="u", target_id="alice"),
        MemberEvent(action="mode", actor="u", mode="cluster"),
        MemberEvent(action="invite", actor="u", target_id="bob"),
    ]
    for idx, event in enumerate(events, start=1):
        event.seq = idx

    state = fold_state(events, until_seq=2)

    assert state.mode == "cluster"
    assert [member.id for member in state.roster] == ["alice"]
    assert state.event_count == 2


def test_service_allows_agent_initiated_invite_and_mode(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)

    invite_member(
        store,
        "thread-1",
        actor="alice",
        target_id="db-agent",
        grant=ContextGrant(scope="from_join"),
        at_message=7,
    )
    set_mode(store, "thread-1", actor="alice", mode="swarm")

    state = store.state("thread-1")
    member = state.member("db-agent")
    assert member is not None
    assert member.invited_by == "alice"
    assert member.grant.scope == "from_join"
    assert state.mode == "swarm"


def test_async_work_completion_posts_to_thread_blackboard(tmp_path) -> None:
    groups = GroupStore(base_dir=tmp_path)
    store = AsyncWorkStore(base_dir=tmp_path, group_store=groups)

    task = store.assign("thread-1", "researcher", "check pricing", actor="lead")
    assert store.claim(task.task_id) is True
    assert store.claim(task.task_id) is False
    assert store.complete(task.task_id, "pricing checked", blackboard_key="pricing")

    assert store.get(task.task_id).status == "done"
    assert groups.blackboard_snapshot("thread-1")["pricing"] == "pricing checked"


def test_breakout_thread_links_and_merges_back(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)

    fork(
        store,
        "parent",
        "child",
        actor="lead",
        members=[{"id": "analyst"}, {"id": "critic"}],
        grant=ContextGrant(scope="summary"),
        at_message=4,
    )
    assert {member.id for member in store.state("child").roster} == {
        "analyst",
        "critic",
    }
    assert store.blackboard_snapshot("child")["forked_from"]["parent"] == "parent"
    assert store.blackboard_snapshot("parent")["breakout:child"]["status"] == "open"

    merge_back(store, "child", "parent", actor="analyst", summary="ship")

    parent_board = store.blackboard_snapshot("parent")
    assert parent_board["breakout:child"]["status"] == "merged"
    assert parent_board["breakout:child:summary"] == "ship"


def test_catchup_respects_context_grant_and_blackboard_keys() -> None:
    state = fold_state(
        [
            MemberEvent(
                action="invite",
                actor="lead",
                target_id="late-agent",
                grant=ContextGrant(scope="from_join"),
                at_message=2,
                seq=1,
            )
        ]
    )

    brief = build_catchup(
        state,
        "late-agent",
        ["private", "old", "visible", "latest"],
        {"decision": "go"},
    )

    assert brief is not None
    assert brief.visible_count == 2
    assert brief.recent == ["visible", "latest"]
    assert brief.blackboard_keys == ["decision"]


def test_nomination_ranks_relevant_candidates_and_gates_participants(tmp_path) -> None:
    store = CompetenceStore(base_dir=tmp_path)
    store.record("db-agent", "database", True)
    store.record("db-agent", "database", True)
    store.record("ui-agent", "database", False)

    ranked = suggest(
        "database indexing latency",
        [("db-agent", "database"), ("ui-agent", "frontend")],
        store,
    )

    assert ranked[0]["agent_id"] == "db-agent"
    assert gate(
        [("db-agent", "database"), ("ui-agent", "frontend")],
        "database indexing latency",
    ) == ["db-agent"]

