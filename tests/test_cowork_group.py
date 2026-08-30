"""Thread-group folding / grant / speaker logic — pure, no I/O."""

from __future__ import annotations

from runtime.memory.cowork.group import (
    ContextGrant,
    Member,
    MemberEvent,
    fold_state,
    responders,
    visible_message_range,
)


def _seq(*events: MemberEvent) -> list[MemberEvent]:
    for i, e in enumerate(events, start=1):
        e.seq = i
    return list(events)


def test_one_to_one_is_just_a_two_member_group() -> None:
    events = _seq(
        MemberEvent(action="invite", actor="user", target_id="user", target_kind="human"),
        MemberEvent(action="invite", actor="user", target_id="alice", target_kind="agent"),
    )
    state = fold_state(events)
    assert len(state.roster) == 2
    assert state.is_one_to_one is True
    assert state.mode == "chat"


def test_pull_someone_in_then_remove_folds_roster() -> None:
    events = _seq(
        MemberEvent(action="invite", actor="user", target_id="user", target_kind="human"),
        MemberEvent(action="invite", actor="user", target_id="alice", target_kind="agent"),
        # mid-conversation, pull in a second agent
        MemberEvent(
            action="invite", actor="user", target_id="bob", target_kind="agent", at_message=42
        ),
        MemberEvent(action="leave", actor="user", target_id="alice"),
    )
    state = fold_state(events)
    ids = {m.id for m in state.roster}
    assert ids == {"user", "bob"}  # alice removed, bob added
    assert state.is_one_to_one is True  # back to 1 agent + 1 human
    bob = state.member("bob")
    assert bob and bob.joined_at_message == 42 and bob.invited_by == "user"


def test_reinvite_after_leave_refreshes_join_anchor() -> None:
    events = _seq(
        MemberEvent(action="invite", actor="u", target_id="bob", target_kind="agent", at_message=1),
        MemberEvent(action="leave", actor="u", target_id="bob"),
        MemberEvent(
            action="invite", actor="u", target_id="bob", target_kind="agent", at_message=99
        ),
    )
    state = fold_state(events)
    bob = state.member("bob")
    assert bob is not None and bob.joined_at_message == 99


def test_mute_and_mode_fold() -> None:
    events = _seq(
        MemberEvent(action="invite", actor="u", target_id="a", target_kind="agent"),
        MemberEvent(action="invite", actor="u", target_id="b", target_kind="agent"),
        MemberEvent(action="mute", actor="u", target_id="b"),
        MemberEvent(action="mode", actor="u", mode="swarm"),
    )
    state = fold_state(events)
    assert state.mode == "swarm"
    assert state.member("b").muted is True
    assert state.member("a").muted is False


def test_legacy_project_event_is_read_as_chat() -> None:
    event = MemberEvent.from_dict(
        {
            "action": "mode",
            "actor": "legacy-client",
            "mode": "project",
        }
    )

    assert event.mode == "chat"
    assert fold_state(_seq(event)).mode == "chat"


def test_context_grant_ranges() -> None:
    def m(scope, **kw):
        return Member(
            "x",
            "agent",
            "participant",
            joined_at_message=kw.get("join", 10),
            grant=ContextGrant(scope=scope, from_msg=kw.get("f"), to_msg=kw.get("t")),
        )

    assert visible_message_range(m("all"), 100) == (0, 100)
    assert visible_message_range(m("from_join", join=10), 100) == (10, 100)
    assert visible_message_range(m("range", f=5, t=20), 100) == (5, 20)
    # summary → no raw history slice (the member gets a summary instead)
    assert visible_message_range(m("summary"), 100) is None


def _agents(*specs):
    # specs: (id, role, muted)
    return [Member(i, "agent", role, 0, ContextGrant(), muted) for i, role, muted in specs]


def test_responders_follow_mode_and_addressing() -> None:
    from runtime.memory.cowork.group import GroupState

    roster = _agents(
        ("a", "participant", False), ("b", "participant", False), ("c", "observer", False)
    )
    roster.append(Member("human", "human", "participant", 0, ContextGrant()))

    chat = GroupState(roster=roster, mode="chat")
    # chat + nobody addressed + multiple agents → wait for an @mention
    assert responders(chat) == []
    # chat + @addressed → only the addressed agent
    assert responders(chat, addressed=["b"]) == ["b"]
    # observer never auto-responds even if addressed
    assert responders(chat, addressed=["c"]) == []

    cluster = GroupState(roster=roster, mode="cluster")
    assert responders(cluster) == ["a"]  # leader = first agent participant

    swarm = GroupState(roster=roster, mode="swarm")
    assert responders(swarm) == ["a", "b"]  # all participant agents, not observer

    # true 1:1: the sole agent answers without an @mention
    solo = GroupState(roster=_agents(("a", "participant", False)), mode="chat")
    assert responders(solo) == ["a"]


def test_muted_agent_does_not_respond() -> None:
    from runtime.memory.cowork.group import GroupState

    roster = _agents(("a", "participant", True), ("b", "participant", False))
    state = GroupState(roster=roster, mode="swarm")
    assert responders(state) == ["b"]  # a is muted

