"""Per-member read state + presence + unread over the event-sourced group log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from runtime.memory.cowork.group import MemberEvent
from runtime.memory.cowork.group_store import GroupStore
from runtime.memory.cowork.presence import (
    PresenceStore,
    group_presence,
    notify_targets,
)


def _seed(tmp_path) -> tuple[GroupStore, PresenceStore]:
    gs = GroupStore(base_dir=tmp_path)
    ps = PresenceStore(base_dir=tmp_path)
    return gs, ps


def test_mark_read_is_monotonic(tmp_path) -> None:
    _, ps = _seed(tmp_path)
    ps.mark_read("t1", "alice", 5)
    assert ps.get("t1", "alice")["last_read"] == 5
    ps.mark_read("t1", "alice", 2)  # lower must not rewind
    assert ps.get("t1", "alice")["last_read"] == 5
    ps.mark_read("t1", "alice", 9)
    assert ps.get("t1", "alice")["last_read"] == 9


def test_unread_counts_events_past_read_marker(tmp_path) -> None:
    gs, ps = _seed(tmp_path)
    gs.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="user", target_kind="human")
    )  # seq 1
    gs.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent")
    )  # seq 2
    gs.append("t1", MemberEvent(action="mode", actor="u", mode="swarm"))  # seq 3

    # user joined at seq 1, hasn't read → unread = head(3) - join(1) = 2
    pres = {p.member_id: p for p in group_presence(gs, ps, "t1")}
    assert pres["user"].unread == 2

    # after marking read at head, unread clears
    ps.mark_read("t1", "user", 3)
    pres = {p.member_id: p for p in group_presence(gs, ps, "t1")}
    assert pres["user"].unread == 0


def test_join_seq_is_the_unread_floor(tmp_path) -> None:
    gs, ps = _seed(tmp_path)
    for name in ("user", "alice"):
        gs.append(
            "t1", MemberEvent(action="invite", actor="u", target_id=name, target_kind="agent")
        )
    gs.append("t1", MemberEvent(action="mode", actor="u", mode="cluster"))  # seq 3
    # bob is pulled in late, at seq 4
    gs.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="bob", target_kind="agent")
    )  # seq 4

    pres = {p.member_id: p for p in group_presence(gs, ps, "t1")}
    # bob joined at head — nothing happened after him, so zero unread (no
    # pre-join spam), while user sees everything after their seq-1 join.
    assert pres["bob"].unread == 0
    assert pres["user"].unread == 3


def test_presence_online_within_window(tmp_path) -> None:
    gs, ps = _seed(tmp_path)
    gs.append("t1", MemberEvent(action="invite", actor="u", target_id="alice", target_kind="agent"))
    now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
    ps.heartbeat("t1", "alice", now=(now - timedelta(seconds=10)).isoformat())

    fresh = {p.member_id: p for p in group_presence(gs, ps, "t1", now=now)}
    assert fresh["alice"].online is True

    stale_now = now + timedelta(seconds=120)
    stale = {p.member_id: p for p in group_presence(gs, ps, "t1", now=stale_now)}
    assert stale["alice"].online is False


def test_member_with_no_heartbeat_is_offline(tmp_path) -> None:
    gs, ps = _seed(tmp_path)
    gs.append("t1", MemberEvent(action="invite", actor="u", target_id="ghost", target_kind="agent"))
    pres = {p.member_id: p for p in group_presence(gs, ps, "t1")}
    assert pres["ghost"].online is False
    assert pres["ghost"].last_seen_at is None


def test_notify_targets_are_offline_with_unread(tmp_path) -> None:
    gs, ps = _seed(tmp_path)
    gs.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="online-bob", target_kind="agent")
    )  # seq 1
    gs.append(
        "t1", MemberEvent(action="invite", actor="u", target_id="away-amy", target_kind="agent")
    )  # seq 2
    gs.append("t1", MemberEvent(action="mode", actor="u", mode="swarm"))  # seq 3
    now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=UTC)
    # bob is online (recent heartbeat) and caught up; amy is offline with unread
    ps.heartbeat("t1", "online-bob", now=(now - timedelta(seconds=5)).isoformat())
    ps.mark_read("t1", "online-bob", 3)

    targets = notify_targets(gs, ps, "t1", now=now)
    assert targets == ["away-amy"]


def test_state_is_thread_scoped(tmp_path) -> None:
    _, ps = _seed(tmp_path)
    ps.mark_read("t1", "alice", 4)
    assert ps.get("t2", "alice")["last_read"] == 0
    assert ps.all("t1") == {"alice": {"last_read": 4, "last_seen_at": None}}

