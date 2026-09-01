"""Turn planning: mode + @mentions → who acts this turn (the auto-mode seam)."""

from __future__ import annotations

import pytest

from runtime.memory.cowork.group import ContextGrant, GroupState, Member
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.service import invite_member, set_mode
from runtime.memory.cowork.turn_plan import plan_turn
from runtime.platform.models import ParsedIntent
from runtime.sensing.gateway.realtime_turn_lifecycle import _inject_cowork_turn_plan


def _agents(*ids, role="participant", muted=False):
    return [Member(i, "agent", role, 0, ContextGrant(), muted) for i in ids]


def test_one_to_one_responds_without_mention() -> None:
    state = GroupState(roster=_agents("alice"), mode="chat")
    plan = plan_turn(state, "hi there")
    assert plan.responders == ["alice"]
    assert plan.is_multi is False
    assert "1:1" in plan.reason


def test_linked_room_with_one_agent_waits_for_mention() -> None:
    state = GroupState(roster=_agents("alice"), mode="chat", room_id="room-1")

    plan = plan_turn(state, "hi room")

    assert plan.responders == []
    assert plan.is_multi is False
    assert "persistent group chat" in plan.reason


def test_project_bound_chat_without_linked_room_can_force_group_semantics() -> None:
    state = GroupState(roster=_agents("alice"), mode="chat")

    plan = plan_turn(state, "project update", persistent_group=True)

    assert plan.responders == []
    assert "persistent group chat" in plan.reason


def test_linked_room_still_routes_an_explicit_mention() -> None:
    state = GroupState(roster=_agents("alice"), mode="chat", room_id="room-1")

    plan = plan_turn(state, "@agent:alice please check")

    assert plan.responders == ["alice"]
    assert plan.addressed == ["alice"]


def test_group_chat_waits_for_mention() -> None:
    state = GroupState(roster=_agents("alice", "bob"), mode="chat")
    plan = plan_turn(state, "what does everyone think?")
    assert plan.responders == []  # no @ → wait
    assert "waiting" in plan.reason


@pytest.mark.parametrize("mention", ["@所有人", "@全员", "@all", "@everyone", "@ALL"])
def test_group_chat_broadcast_mention_routes_to_all_active_agents(mention: str) -> None:
    state = GroupState(
        roster=[
            *_agents("alice", "bob"),
            Member("muted", "agent", "participant", 0, ContextGrant(), True),
            Member("observer", "agent", "observer", 0, ContextGrant(), False),
            Member("human", "human", "participant", 0, ContextGrant(), False),
        ],
        mode="chat",
    )

    plan = plan_turn(state, f"{mention} 请分别给出意见")

    assert plan.responders == ["alice", "bob"]
    assert plan.addressed == ["alice", "bob"]
    assert plan.is_multi is True
    assert "@all" in plan.reason


@pytest.mark.parametrize("text", ["@alliance hello", "mail me at foo@all.com", "@everyone_else"])
def test_group_chat_does_not_expand_broadcast_like_text(text: str) -> None:
    state = GroupState(roster=_agents("alice", "bob"), mode="chat")

    plan = plan_turn(state, text)

    assert plan.responders == []
    assert plan.addressed == []


def test_broadcast_alias_does_not_override_non_chat_mode() -> None:
    state = GroupState(roster=_agents("lead", "helper"), mode="cluster")

    plan = plan_turn(state, "@所有人 看一下")

    assert plan.responders == ["lead"]
    assert plan.addressed == []
    assert plan.is_multi is False


def test_at_mention_routes_to_addressed_agent() -> None:
    state = GroupState(roster=_agents("alice", "bob"), mode="chat")
    plan = plan_turn(state, "hey @agent:bob can you take this")
    assert plan.responders == ["bob"]
    assert plan.addressed == ["bob"]
    assert plan.is_multi is False


def test_swarm_runs_all_agents_in_parallel() -> None:
    state = GroupState(roster=_agents("alice", "bob"), mode="swarm")
    plan = plan_turn(state, "divide and conquer")
    assert set(plan.responders) == {"alice", "bob"}
    assert plan.is_multi is True
    assert "swarm" in plan.reason


def test_cluster_routes_to_leader() -> None:
    state = GroupState(roster=_agents("lead", "helper"), mode="cluster")
    plan = plan_turn(state, "let's plan this")
    assert plan.responders == ["lead"]
    assert plan.is_multi is False
    assert "cluster" in plan.reason


def test_legacy_project_mode_normalizes_to_group_chat() -> None:
    state = GroupState(roster=_agents("lead", "helper"), mode="project")
    plan = plan_turn(state, "ship the roadmap")
    assert plan.mode == "chat"
    assert plan.responders == []
    assert plan.is_multi is False
    assert "waiting for an @mention" in plan.reason


def test_legacy_project_mode_keeps_normal_chat_mentions() -> None:
    state = GroupState(roster=_agents("lead", "helper"), mode="project")
    plan = plan_turn(state, "@agent:helper quick answer")
    assert plan.mode == "chat"
    assert plan.addressed == ["helper"]
    assert plan.responders == ["helper"]
    assert "@addressed" in plan.reason


def test_mention_overrides_mode() -> None:
    # Even in swarm, an explicit @mention narrows to that agent.
    state = GroupState(roster=_agents("alice", "bob"), mode="swarm")
    plan = plan_turn(state, "@agent:alice just you")
    assert plan.responders == ["alice"]


def test_realtime_intent_gets_cowork_turn_plan(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    invite_member(store, "thread-1", actor="u", target_id="db-agent", kind="agent")
    invite_member(store, "thread-1", actor="u", target_id="ui-agent", kind="agent")
    set_mode(store, "thread-1", actor="u", mode="swarm")
    runtime = type("Runtime", (), {"_cowork_group_store": store})()
    intent = ParsedIntent(
        raw="check it",
        intent_type="task",
        normalized_goal="check it",
        user_context={},
    )

    _inject_cowork_turn_plan(
        runtime,
        thread_id="thread-1",
        text="check it",
        intent=intent,
    )

    assert intent.user_context["cowork_mode"] == "swarm"
    assert intent.user_context["cowork_is_multi"] is True
    assert intent.user_context["cowork_responders"] == ["db-agent", "ui-agent"]
    assert intent.user_context["cowork_plan"]["reason"].startswith("swarm")


def test_realtime_explicit_response_mode_overrides_default_but_not_roster(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    invite_member(store, "thread-1", actor="u", target_id="lead", kind="agent")
    invite_member(store, "thread-1", actor="u", target_id="critic", kind="agent")
    set_mode(store, "thread-1", actor="u", mode="swarm")
    runtime = type("Runtime", (), {"_cowork_group_store": store})()
    intent = ParsedIntent(
        raw="ordinary group note",
        intent_type="task",
        normalized_goal="ordinary group note",
        user_context={
            "response_mode_override": "chat",
            "cowork_responders": ["not-a-member"],
            "agent_roster": [{"agent_id": "not-a-member"}],
        },
    )

    _inject_cowork_turn_plan(
        runtime,
        thread_id="thread-1",
        text="ordinary group note",
        intent=intent,
    )

    assert intent.user_context["cowork_mode"] == "chat"
    assert intent.user_context["cowork_responders"] == []
    assert intent.user_context["cowork_waiting_for_mention"] is True
    assert [item["agent_id"] for item in intent.user_context["agent_roster"]] == [
        "lead",
        "critic",
    ]
    # The persisted default remains untouched for the next sender/turn.
    assert store.state("thread-1").mode == "swarm"


def test_realtime_intent_ignores_unknown_thread_default_state(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    runtime = type("Runtime", (), {"_cowork_group_store": store})()
    intent = ParsedIntent(
        raw="normal private chat",
        intent_type="task",
        normalized_goal="normal private chat",
        user_context={},
    )

    _inject_cowork_turn_plan(
        runtime,
        thread_id="ordinary-thread",
        text="normal private chat",
        intent=intent,
    )

    assert "cowork_plan" not in intent.user_context
    assert "cowork_waiting_for_mention" not in intent.user_context


def test_realtime_intent_treats_bound_project_as_persistent_group(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    invite_member(store, "project-thread", actor="u", target_id="general", kind="agent")

    class ProjectStore:
        @staticmethod
        def project_for_thread(thread_id: str):
            return object() if thread_id == "project-thread" else None

    runtime = type(
        "Runtime",
        (),
        {
            "_cowork_group_store": store,
            "_project_store": ProjectStore(),
            "_collaboration_store": None,
        },
    )()
    intent = ParsedIntent(
        raw="project update",
        intent_type="task",
        normalized_goal="project update",
        user_context={},
    )

    _inject_cowork_turn_plan(
        runtime,
        thread_id="project-thread",
        text="project update",
        intent=intent,
    )

    assert intent.user_context["cowork_persistent_group"] is True
    assert intent.user_context["cowork_waiting_for_mention"] is True
    assert intent.user_context["cowork_responders"] == []


def test_realtime_intent_normalizes_legacy_project_mode_to_chat(tmp_path) -> None:
    store = GroupStore(base_dir=tmp_path)
    invite_member(store, "thread-1", actor="u", target_id="db-agent", kind="agent")
    invite_member(store, "thread-1", actor="u", target_id="ui-agent", kind="agent")
    set_mode(store, "thread-1", actor="u", mode="project")
    runtime = type("Runtime", (), {"_cowork_group_store": store})()
    intent = ParsedIntent(
        raw="ship it",
        intent_type="task",
        normalized_goal="ship it",
        user_context={},
    )

    _inject_cowork_turn_plan(
        runtime,
        thread_id="thread-1",
        text="ship it",
        intent=intent,
    )

    assert store.state("thread-1").mode == "chat"
    assert intent.user_context["cowork_mode"] == "chat"
    assert intent.user_context["cowork_is_multi"] is False
    assert intent.user_context["cowork_responders"] == []
    assert "waiting for an @mention" in intent.user_context["cowork_plan"]["reason"]

