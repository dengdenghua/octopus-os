"""Context-grant enforcement: a member only sees the history their grant permits."""

from __future__ import annotations

from runtime.memory.cowork.context_view import resolve_view, slice_messages
from runtime.memory.cowork.group import ContextGrant, GroupState, Member


def _member(scope, join=10, f=None, t=None):
    return Member(
        "spec",
        "agent",
        "participant",
        joined_at_message=join,
        grant=ContextGrant(scope=scope, from_msg=f, to_msg=t),
    )


def _state(member):
    return GroupState(roster=[member], mode="chat")


MSGS = [f"m{i}" for i in range(20)]  # 20 messages, indices 0..19


def test_all_grant_sees_everything() -> None:
    view = resolve_view(_state(_member("all")), "spec", max_message=19)
    assert view and view.message_range == (0, 19)
    assert slice_messages(view, MSGS) == MSGS


def test_from_join_hides_prior_private_context() -> None:
    view = resolve_view(_state(_member("from_join", join=8)), "spec", max_message=19)
    assert view.message_range == (8, 19)
    sliced = slice_messages(view, MSGS)
    assert sliced == MSGS[8:]  # nothing before message 8 leaks
    assert "m0" not in sliced and "m7" not in sliced


def test_range_grant_clamps_to_bounds() -> None:
    view = resolve_view(_state(_member("range", f=5, t=9)), "spec", max_message=19)
    assert slice_messages(view, MSGS) == ["m5", "m6", "m7", "m8", "m9"]
    # out-of-range hi is clamped, never raises
    wide = resolve_view(_state(_member("range", f=18, t=999)), "spec", max_message=19)
    assert slice_messages(wide, MSGS) == ["m18", "m19"]


def test_summary_grant_yields_no_raw_history() -> None:
    view = resolve_view(_state(_member("summary")), "spec", max_message=19)
    assert view.summary_only is True
    assert view.message_range is None
    assert slice_messages(view, MSGS) == []  # caller substitutes a summary


def test_non_member_has_no_view() -> None:
    assert resolve_view(_state(_member("all")), "stranger", max_message=19) is None


def test_empty_range_never_raises() -> None:
    view = resolve_view(_state(_member("range", f=50, t=60)), "spec", max_message=19)
    assert slice_messages(view, MSGS) == []  # entirely past the end

