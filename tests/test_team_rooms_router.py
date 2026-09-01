from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.sensing.gateway.team_rooms_router import (  # noqa: E402
    TeamParticipantWire,
    TeamRoomWire,
    _authorized_to_speak_for,
    _initial_floor_state,
    _next_speaker,
    _normalize_speak_mode,
    _normalize_speaker_policy,
    _participant_can_speak,
    create_team_rooms_router,
)


def _client(
    tmp_path: Path,
    *,
    identity_store=None,
    require_auth: bool = False,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "team_rooms.json",
            identity_store=identity_store,
            require_auth=require_auth,
        ),
    )
    return TestClient(app)


def _team_body(name: str = "Family") -> dict:
    return {
        "name": name,
        "members": [
            {
                "name": "general",
                "display_name": "Echo",
                "description": "General assistant",
                "model": None,
                "tool_groups": None,
            },
            {
                "name": "coder",
                "display_name": "Coder",
                "description": "Code assistant",
                "model": None,
                "tool_groups": None,
            },
        ],
        "leaderId": "general",
    }


def test_create_list_and_reload_team_rooms(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post("/api/teams", json=_team_body()).json()
    assert created["id"].startswith("team-family")
    assert created["leaderId"] == "general"
    assert created["members"][1]["name"] == "coder"

    listed = client.get("/api/teams").json()
    assert listed["count"] == 1
    assert listed["teams"][0]["name"] == "Family"

    reloaded = _client(tmp_path).get("/api/teams").json()
    assert reloaded["count"] == 1
    assert reloaded["teams"][0]["id"] == created["id"]


def test_invite_join_adds_human_participant(tmp_path: Path) -> None:
    client = _client(tmp_path)
    team = client.post("/api/teams", json=_team_body()).json()

    invite = client.post(
        f"/api/teams/{team['id']}/invite",
        json={"role": "viewer"},
    ).json()
    token = invite["invite_token"]
    assert invite["invite_role"] == "viewer"
    assert invite["invite_hash_path"].endswith(token)

    info = client.get(f"/api/team-invites/{token}").json()
    assert info["team"]["id"] == team["id"]

    joined = client.post(
        f"/api/team-invites/{token}/join",
        json={"display_name": "Remote Alice", "participant_id": "alice"},
    ).json()
    assert joined["participant"]["display_name"] == "Remote Alice"
    assert joined["participant"]["role"] == "viewer"
    participants = joined["team"]["participants"]
    assert any(p["id"] == "alice" for p in participants)


def test_team_room_websocket_presence_and_events(tmp_path: Path) -> None:
    client = _client(tmp_path)
    team = client.post("/api/teams", json=_team_body()).json()
    url = f"/api/teams/{team['id']}/ws"

    with client.websocket_connect(
        f"{url}?participant_id=alice&display_name=Alice&thread_id=thread-a"
    ) as alice:
        ready = alice.receive_json()
        assert ready["type"] == "ready"
        assert ready["participant"]["id"] == "alice"
        assert alice.receive_json()["type"] == "presence"

        with client.websocket_connect(
            f"{url}?participant_id=bob&display_name=Bob&thread_id=thread-a"
        ) as bob:
            bob_ready = bob.receive_json()
            assert bob_ready["participant"]["id"] == "bob"
            alice_presence = alice.receive_json()
            bob_presence = bob.receive_json()
            assert alice_presence["type"] == "presence"
            assert bob_presence["type"] == "presence"
            assert {p["id"] for p in alice_presence["participants"]} == {"alice", "bob"}

            bob.send_json({"type": "cursor", "position": {"x": 12, "y": 34}})
            cursor = alice.receive_json()
            assert cursor["type"] == "cursor"
            assert cursor["thread_id"] == "thread-a"
            assert cursor["participant_id"] == "bob"
            assert cursor["position"] == {"x": 12, "y": 34}

            bob.send_json({"type": "message", "text": "Can you review this?"})
            message = alice.receive_json()
            assert message["type"] == "message"
            assert message["thread_id"] == "thread-a"
            assert message["participant_id"] == "bob"
            assert message["text"] == "Can you review this?"

            bob.send_json({"type": "thread:update", "reason": "finished"})
            update = alice.receive_json()
            assert update["type"] == "thread:update"
            assert update["thread_id"] == "thread-a"
            assert update["participant_id"] == "bob"
            assert update["reason"] == "finished"


def test_team_room_websocket_respects_auth_and_actor_binding(tmp_path: Path) -> None:
    from runtime.safety.auth import Identity, IdentityStore

    store = IdentityStore()
    store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")
    client = _client(tmp_path, identity_store=store, require_auth=True)

    team = client.post(
        "/api/teams",
        headers={"Authorization": "Bearer sk-alice"},
        json=_team_body(),
    ).json()
    owner_id = team["participants"][0]["id"]
    url = f"/api/teams/{team['id']}/ws?participant_id={owner_id}&display_name=Alice"

    with client.websocket_connect(url) as ws:
        assert ws.receive_json() == {
            "type": "error",
            "message": "missing Authorization: Bearer <token>",
        }

    with client.websocket_connect(
        url,
        headers={"Authorization": "Bearer sk-bob"},
    ) as ws:
        assert ws.receive_json() == {
            "type": "error",
            "message": "participant actor mismatch",
        }

    with client.websocket_connect(
        url,
        headers={"Authorization": "Bearer sk-alice"},
    ) as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"
        assert ready["participant"]["id"] == owner_id


def test_update_and_remove_team_participants(tmp_path: Path) -> None:
    client = _client(tmp_path)
    team = client.post("/api/teams", json=_team_body()).json()
    owner_id = team["participants"][0]["id"]

    invite = client.post(
        f"/api/teams/{team['id']}/invite",
        json={"role": "member"},
    ).json()
    joined = client.post(
        f"/api/team-invites/{invite['invite_token']}/join",
        json={"display_name": "Alice", "participant_id": "alice"},
    ).json()
    assert joined["participant"]["role"] == "member"

    updated = client.patch(
        f"/api/teams/{team['id']}/participants/alice",
        json={"role": "viewer", "display_name": "Alice Viewer"},
    ).json()
    assert updated["participant"]["role"] == "viewer"
    assert updated["participant"]["display_name"] == "Alice Viewer"

    removed = client.delete(f"/api/teams/{team['id']}/participants/alice").json()
    assert removed["ok"] is True
    alice = next(p for p in removed["team"]["participants"] if p["id"] == "alice")
    assert alice["status"] == "removed"

    guard = client.patch(
        f"/api/teams/{team['id']}/participants/{owner_id}",
        json={"role": "viewer"},
    )
    assert guard.status_code == 400


def test_delete_team(tmp_path: Path) -> None:
    client = _client(tmp_path)
    team = client.post("/api/teams", json=_team_body("Delete me")).json()

    deleted = client.delete(f"/api/teams/{team['id']}").json()
    assert deleted == {"ok": True, "deleted": True, "team_id": team["id"]}
    assert client.get("/api/teams").json()["count"] == 0


# ── speaker governance: mute + speaker_policy resolution ───────────


def _gov_participant(
    pid: str = "p1",
    *,
    role: str = "member",
    muted: bool = False,
    actor_id: str | None = None,
) -> TeamParticipantWire:
    return TeamParticipantWire(
        id=pid,
        display_name=pid,
        role=role,
        actor_id=actor_id,
        joined_at="t0",
        muted=muted,
    )


def _gov_room(
    policy: str = "free",
    *,
    owner_id: str = "owner-actor",
    current_speaker_id: str | None = None,
    moderator_id: str | None = None,
    participants: list[TeamParticipantWire] | None = None,
) -> TeamRoomWire:
    return TeamRoomWire(
        id="t",
        name="T",
        created_at="t0",
        updated_at="t0",
        owner_id=owner_id,
        speaker_policy=policy,
        current_speaker_id=current_speaker_id,
        moderator_id=moderator_id,
        participants=participants or [],
    )


def test_speaker_policy_normalization() -> None:
    assert _normalize_speaker_policy("free") == "free"
    assert _normalize_speaker_policy("ADMIN_ONLY") == "admin_only"
    assert _normalize_speaker_policy("owner_only") == "admin_only"  # alias
    assert _normalize_speaker_policy(None) == "free"
    # turn-based modes are now enforced (the floor engine landed)
    assert _normalize_speaker_policy("round_robin") == "round_robin"
    assert _normalize_speaker_policy("round-robin") == "round_robin"  # dash alias
    assert _normalize_speaker_policy("rollcall") == "roll_call"  # alias
    assert _normalize_speaker_policy("HOST") == "moderated"  # alias
    # genuinely unknown values still fall back to the open default
    assert _normalize_speaker_policy("garbage") == "free"


def test_can_speak_free_policy_allows_unmuted() -> None:
    ok, reason = _participant_can_speak(_gov_room("free"), _gov_participant(muted=False))
    assert ok is True
    assert reason is None


def test_can_speak_muted_member_denied_under_any_policy() -> None:
    for policy in ("free", "admin_only"):
        ok, reason = _participant_can_speak(_gov_room(policy), _gov_participant(muted=True))
        assert ok is False
        assert reason is not None and "muted" in reason


def test_can_speak_admin_only_blocks_member_allows_owner() -> None:
    room = _gov_room("admin_only", owner_id="alice")
    # plain member → denied
    ok, reason = _participant_can_speak(room, _gov_participant(role="member", actor_id="bob"))
    assert ok is False
    assert reason is not None and "admin-only" in reason
    # owner-role participant → allowed
    ok, _ = _participant_can_speak(room, _gov_participant(role="owner", actor_id="x"))
    assert ok is True
    # the recorded room owner (matched by actor_id) → allowed even if role differs
    ok, _ = _participant_can_speak(room, _gov_participant(role="member", actor_id="alice"))
    assert ok is True


def test_muted_participant_cannot_speak_over_ws(tmp_path: Path) -> None:
    """End-to-end: a muted member's message is rejected at the WS speech
    chokepoint and the mute survives the connect rebuild."""
    client = _client(tmp_path)
    team = client.post("/api/teams", json=_team_body()).json()
    tid = team["id"]

    # bob joins, then is muted by the owner — all before he opens a socket
    invite = client.post(f"/api/teams/{tid}/invite", json={"role": "member"}).json()
    client.post(
        f"/api/team-invites/{invite['invite_token']}/join",
        json={"display_name": "Bob", "participant_id": "bob"},
    )
    muted = client.patch(f"/api/teams/{tid}/participants/bob", json={"muted": True})
    assert muted.status_code == 200, muted.json()
    assert muted.json()["participant"]["muted"] is True

    url = f"/api/teams/{tid}/ws"
    with client.websocket_connect(f"{url}?participant_id=bob&display_name=Bob") as bob:
        ready = bob.receive_json()
        assert ready["type"] == "ready"
        # the mute is preserved across the (re)connect participant rebuild
        assert ready["participant"]["muted"] is True
        assert bob.receive_json()["type"] == "presence"

        bob.send_json({"type": "message", "text": "let me in"})
        denied = bob.receive_json()
        assert denied["type"] == "error"
        assert denied["code"] == "speech_denied"
        assert "muted" in denied["message"]


# ── turn engine: pure resolution (round_robin / roll_call / moderated) ──


def test_next_speaker_wraps_and_skips_muted() -> None:
    room = _gov_room(
        "round_robin",
        participants=[
            _gov_participant("a"),
            _gov_participant("b", muted=True),  # muted → not a seat
            _gov_participant("c"),
        ],
    )
    assert _next_speaker(room, None) == "a"  # first eligible
    assert _next_speaker(room, "a") == "c"  # b skipped
    assert _next_speaker(room, "c") == "a"  # wraps
    assert _next_speaker(room, "ghost") == "a"  # departed holder → first seat


def test_next_speaker_none_when_nobody_eligible() -> None:
    room = _gov_room("round_robin", participants=[_gov_participant("a", muted=True)])
    assert _next_speaker(room, None) is None


def test_initial_floor_state_per_policy() -> None:
    room = _gov_room(
        "free",
        owner_id="alice",
        participants=[_gov_participant("a"), _gov_participant("b")],
    )
    rr = _initial_floor_state(room, "round_robin")
    assert rr["current_speaker_id"] == "a"  # seats the first eligible speaker
    assert rr["moderator_id"] == "alice"  # owner becomes moderator
    assert rr["floor_requests"] == []
    for mode in ("roll_call", "moderated"):
        st = _initial_floor_state(room, mode)
        assert st["current_speaker_id"] is None  # floor opens empty
        assert st["moderator_id"] == "alice"
    free = _initial_floor_state(room, "free")
    assert free["current_speaker_id"] is None  # leaving a turn mode tears it down
    assert free["moderator_id"] is None


def test_can_speak_round_robin_only_floor_holder() -> None:
    room = _gov_room(
        "round_robin",
        current_speaker_id="a",
        participants=[_gov_participant("a"), _gov_participant("b")],
    )
    ok, _ = _participant_can_speak(room, _gov_participant("a"))
    assert ok is True
    ok, reason = _participant_can_speak(room, _gov_participant("b"))
    assert ok is False and reason is not None and "turn" in reason
    # strict: even an owner-role participant waits their turn in round_robin
    ok, _ = _participant_can_speak(room, _gov_participant("oseat", role="owner"))
    assert ok is False


def test_can_speak_roll_call_moderator_and_called() -> None:
    room = _gov_room(
        "roll_call", owner_id="alice", current_speaker_id="b", participants=[_gov_participant("b")]
    )
    ok, _ = _participant_can_speak(room, _gov_participant("b"))  # called on
    assert ok is True
    ok, _ = _participant_can_speak(room, _gov_participant("pa", actor_id="alice"))  # moderator
    assert ok is True
    ok, reason = _participant_can_speak(room, _gov_participant("c", actor_id="carol"))
    assert ok is False and reason is not None and "called on" in reason


def test_can_speak_moderated_floor_holder_and_moderator() -> None:
    room = _gov_room(
        "moderated", owner_id="alice", current_speaker_id="b", participants=[_gov_participant("b")]
    )
    ok, _ = _participant_can_speak(room, _gov_participant("b"))  # holds the floor
    assert ok is True
    ok, _ = _participant_can_speak(room, _gov_participant("pa", actor_id="alice"))  # moderator
    assert ok is True
    ok, reason = _participant_can_speak(room, _gov_participant("c", actor_id="carol"))
    assert ok is False and reason is not None and "raise your hand" in reason


# ── turn engine: end-to-end over the WebSocket (single-socket, drained) ──


def _room_with_two_members(tmp_path: Path) -> tuple[TestClient, str]:
    """Team + two human members joined via invite (ids 'alice','bob')."""
    client = _client(tmp_path)
    tid = client.post("/api/teams", json=_team_body()).json()["id"]
    invite = client.post(f"/api/teams/{tid}/invite", json={"role": "member"}).json()
    token = invite["invite_token"]
    for who in ("alice", "bob"):
        client.post(
            f"/api/team-invites/{token}/join",
            json={"display_name": who.title(), "participant_id": who},
        )
    return client, tid


def test_round_robin_enforces_and_advances_over_ws(tmp_path: Path) -> None:
    client, tid = _room_with_two_members(tmp_path)
    # Switch to round_robin; the response tells us who got seated first.
    res = client.patch(f"/api/teams/{tid}/speaker-policy", json={"speaker_policy": "round_robin"})
    assert res.status_code == 200
    seated = res.json()["team"]["current_speaker_id"]
    assert seated is not None
    # A participant who is NOT the seated speaker is blocked.
    other = "bob" if seated != "bob" else "alice"
    url = f"/api/teams/{tid}/ws"
    with client.websocket_connect(f"{url}?participant_id={other}&display_name={other}") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "message", "text": "my turn?"})
        denied = ws.receive_json()
        assert denied["type"] == "error"
        assert denied["code"] == "speech_denied"

    # The seated speaker CAN talk, and the floor then advances off them.
    with client.websocket_connect(f"{url}?participant_id={seated}&display_name={seated}") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "message", "text": "hello team"})
        echoed = ws.receive_json()
        assert echoed["type"] == "message" and echoed["text"] == "hello team"
        floor = ws.receive_json()
        assert floor["type"] == "floor"
        assert floor["current_speaker_id"] != seated  # advanced to the next seat


def test_roll_call_moderator_grants_floor_over_ws(tmp_path: Path) -> None:
    client, tid = _room_with_two_members(tmp_path)
    client.patch(f"/api/teams/{tid}/speaker-policy", json={"speaker_policy": "roll_call"})
    url = f"/api/teams/{tid}/ws"

    # The owner participant (role 'owner') is the moderator in dev mode.
    with client.websocket_connect(f"{url}?participant_id=owner-local&display_name=Owner") as mod:
        assert mod.receive_json()["type"] == "ready"
        assert mod.receive_json()["type"] == "presence"
        mod.send_json({"type": "floor:grant", "target": "bob"})
        floor = mod.receive_json()
        assert floor["type"] == "floor"
        assert floor["current_speaker_id"] == "bob"

    # A non-moderator cannot move the floor.
    with client.websocket_connect(f"{url}?participant_id=alice&display_name=Alice") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "floor:grant", "target": "alice"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "not_moderator"


def test_moderated_raise_hand_then_grant_over_ws(tmp_path: Path) -> None:
    client, tid = _room_with_two_members(tmp_path)
    client.patch(f"/api/teams/{tid}/speaker-policy", json={"speaker_policy": "moderated"})
    url = f"/api/teams/{tid}/ws"

    # bob raises his hand → queued.
    with client.websocket_connect(f"{url}?participant_id=bob&display_name=Bob") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert ws.receive_json()["type"] == "presence"
        ws.send_json({"type": "floor:request"})
        floor = ws.receive_json()
        assert floor["type"] == "floor"
        assert "bob" in floor["floor_requests"]

    # the moderator grants bob the floor, clearing him from the queue.
    with client.websocket_connect(f"{url}?participant_id=owner-local&display_name=Owner") as mod:
        assert mod.receive_json()["type"] == "ready"
        assert mod.receive_json()["type"] == "presence"
        mod.send_json({"type": "floor:grant", "target": "bob"})
        floor = mod.receive_json()
        assert floor["type"] == "floor"
        assert floor["current_speaker_id"] == "bob"
        assert "bob" not in floor["floor_requests"]


# ── delegation (C-layer): twin / hosted speak-resolution ───────────


def test_speak_mode_normalization() -> None:
    assert _normalize_speak_mode("manual") == "manual"
    assert _normalize_speak_mode(None) == "manual"
    assert _normalize_speak_mode("SELF") == "manual"  # alias
    assert _normalize_speak_mode("twin") == "twin"
    assert _normalize_speak_mode("agent") == "twin"  # alias
    assert _normalize_speak_mode("hosted") == "hosted"
    assert _normalize_speak_mode("delegate") == "hosted"  # alias
    assert _normalize_speak_mode("garbage") == "manual"


def test_authorized_to_speak_for_honors_optin() -> None:
    host = _gov_participant("host-h", actor_id="h")
    twin = _gov_participant("agent-x", actor_id="x")
    # manual participant: nobody may speak for them
    p_manual = _gov_participant("p", actor_id="p")
    assert _authorized_to_speak_for(host, p_manual) is False
    # hosted: only the bound host_id matches
    p_hosted = TeamParticipantWire(
        id="p", display_name="P", joined_at="t0", speak_mode="hosted", host_id="host-h"
    )
    assert _authorized_to_speak_for(host, p_hosted) is True
    assert _authorized_to_speak_for(twin, p_hosted) is False
    # twin: matches by the bound agent id (or its actor id)
    p_twin = TeamParticipantWire(
        id="p", display_name="P", joined_at="t0", speak_mode="twin", twin_agent_id="x"
    )
    assert _authorized_to_speak_for(twin, p_twin) is True  # matched by actor_id
    assert _authorized_to_speak_for(host, p_twin) is False


def test_hosted_speaking_over_ws(tmp_path: Path) -> None:
    """A host speaks on a consenting participant's behalf; the broadcast
    is attributed to the participant, marked as spoken_by the host."""
    client, tid = _room_with_two_members(tmp_path)
    # bob opts in to being hosted by alice (self-service; dev mode).
    res = client.patch(
        f"/api/teams/{tid}/participants/bob/delegation",
        json={"speak_mode": "hosted", "host_id": "alice"},
    )
    assert res.status_code == 200, res.json()

    url = f"/api/teams/{tid}/ws"
    with client.websocket_connect(f"{url}?participant_id=alice&display_name=Alice") as alice:
        assert alice.receive_json()["type"] == "ready"
        assert alice.receive_json()["type"] == "presence"
        alice.send_json({"type": "message", "text": "speaking for bob", "on_behalf_of": "bob"})
        msg = alice.receive_json()
        assert msg["type"] == "message"
        assert msg["participant_id"] == "bob"  # attributed to bob
        assert msg["spoken_by"] == "alice"  # ...via alice
        assert msg["via"] == "hosted"
        assert msg["text"] == "speaking for bob"


def test_unauthorized_delegation_denied_over_ws(tmp_path: Path) -> None:
    """Speaking on someone's behalf without their opt-in is rejected."""
    client, tid = _room_with_two_members(tmp_path)
    # bob has NOT opted in to anyone hosting him.
    url = f"/api/teams/{tid}/ws"
    with client.websocket_connect(f"{url}?participant_id=alice&display_name=Alice") as alice:
        assert alice.receive_json()["type"] == "ready"
        assert alice.receive_json()["type"] == "presence"
        alice.send_json({"type": "message", "text": "hijack", "on_behalf_of": "bob"})
        denied = alice.receive_json()
        assert denied["type"] == "error"
        assert denied["code"] == "delegation_denied"
