from __future__ import annotations

import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

from runtime.memory.cowork.team_invitation_store import (  # noqa: E402
    InvitationExhausted,
    TeamInvitationStore,
)
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router  # noqa: E402


def _team_body(*, thread_id: str | None = None) -> dict[str, Any]:
    return {
        "id": "secure-room",
        "name": "Secure room",
        "members": [{"name": "general", "display_name": "Echo"}],
        "leaderId": "general",
        "thread_id": thread_id,
    }


def _bearer(keys: dict[str, str], actor: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {keys[actor]}"}


def _request(api_key: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/cowork-room",
            "headers": [(b"authorization", f"Bearer {api_key}".encode())],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _build_app(
    tmp_path: Path,
    *,
    clock=None,
    room_projection=None,
) -> tuple[TestClient, dict[str, str], TeamInvitationStore, Any]:
    identities = IdentityStore()
    keys: dict[str, str] = {}
    tenants = {
        "alice": "tenant-acme",
        "bob": "tenant-acme",
        "carol": "tenant-acme",
        "mallory": "tenant-other",
    }
    for actor, tenant_id in tenants.items():
        key = f"sk-{actor}"
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": tenant_id}),
            api_key_plaintext=key,
        )
        keys[actor] = key

    store = TeamInvitationStore(
        tmp_path / "team_invitations.db",
        **({"clock": clock} if clock is not None else {}),
    )
    router = create_team_rooms_router(
        state_path=tmp_path / "rooms.json",
        identity_store=identities,
        require_auth=True,
        invitation_store=store,
        room_projection=room_projection,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), keys, store, router


def _create_room(client: TestClient, keys: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/teams",
        headers=_bearer(keys, "alice"),
        json=_team_body(),
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _create_invite(
    client: TestClient,
    keys: dict[str, str],
    *,
    role: str = "member",
    expires_in_seconds: int = 3600,
    max_uses: int = 2,
) -> dict[str, Any]:
    response = client.post(
        "/api/teams/secure-room/invites",
        headers=_bearer(keys, "alice"),
        json={
            "role": role,
            "expires_in_seconds": expires_in_seconds,
            "max_uses": max_uses,
        },
    )
    assert response.status_code == 200, response.json()
    return response.json()


def _raise_once_after_commit(
    monkeypatch: pytest.MonkeyPatch,
    store: TeamInvitationStore,
    boundary: str,
) -> None:
    if boundary == "json":
        from runtime.sensing.gateway import _team_room_persistence as persistence

        original = persistence._save_state

        def save_then_raise(path: Path, teams: dict[str, Any]) -> None:
            original(path, teams)
            monkeypatch.setattr(persistence, "_save_state", original)
            raise RuntimeError("json committed before response failure")

        monkeypatch.setattr(persistence, "_save_state", save_then_raise)
        return

    original_finalize = store._finalize_reservation

    def finalize_then_raise(reservation_id: str) -> None:
        original_finalize(reservation_id)
        monkeypatch.setattr(store, "_finalize_reservation", original_finalize)
        raise RuntimeError("sqlite finalized before response failure")

    monkeypatch.setattr(store, "_finalize_reservation", finalize_then_raise)


def _fail_once_before_membership_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.sensing.gateway import _team_room_persistence as persistence

    original = persistence._save_state

    def fail_before_save(_path: Path, _teams: dict[str, Any]) -> None:
        monkeypatch.setattr(persistence, "_save_state", original)
        raise RuntimeError("membership failed before durable commit")

    monkeypatch.setattr(persistence, "_save_state", fail_before_save)


def test_create_is_backwards_compatible_but_secret_is_not_projected_or_persisted(
    tmp_path: Path,
) -> None:
    projections: list[dict[str, Any]] = []
    client, keys, store, _router = _build_app(tmp_path, room_projection=projections.append)
    room = _create_room(client, keys)
    created = _create_invite(client, keys, role="viewer")

    token = created["invite_token"]
    assert created["invite_role"] == "viewer"
    assert created["invite_id"].startswith("invite-")
    assert created["invite_hash_path"].endswith(token)
    assert token not in (tmp_path / "rooms.json").read_text(encoding="utf-8")
    assert "invite_token" not in json.dumps(room)
    assert projections and "invite_token" not in json.dumps(projections[-1])

    with sqlite3.connect(store.db_path) as conn:
        token_hash, role = conn.execute(
            "SELECT token_hash, role FROM team_invitations WHERE invite_id = ?",
            (created["invite_id"],),
        ).fetchone()
    assert token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token != token_hash
    assert token.encode() not in store.db_path.read_bytes()
    assert role == "viewer"

    listed = client.get(
        "/api/teams/secure-room/invites",
        headers=_bearer(keys, "alice"),
    ).json()
    assert listed["count"] == 1
    assert "token" not in json.dumps(listed).lower()


def test_preview_is_minimal_and_tenant_scoped(tmp_path: Path) -> None:
    client, keys, _store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    created = _create_invite(client, keys)
    token = created["invite_token"]

    preview = client.get(
        f"/api/team-invites/{token}",
        headers=_bearer(keys, "bob"),
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert set(payload) == {"invite", "join_policy", "team", "thread_id"}
    assert payload["join_policy"] == "direct_join"
    assert set(payload["invite"]) == {
        "id",
        "role",
        "expires_at",
        "status",
        "remaining_uses",
    }
    assert set(payload["team"]) == {
        "id",
        "name",
        "member_count",
        "participant_count",
    }
    assert "participants" not in json.dumps(payload)
    assert token not in json.dumps(payload)

    hidden = client.get(
        f"/api/team-invites/{token}",
        headers=_bearer(keys, "mallory"),
    )
    assert hidden.status_code == 404
    rejected_join = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "mallory"),
        json={},
    )
    assert rejected_join.status_code == 404


def test_legacy_create_endpoint_defaults_to_seven_days(tmp_path: Path) -> None:
    client, keys, _store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    created = client.post(
        "/api/teams/secure-room/invite",
        headers=_bearer(keys, "alice"),
        json={"role": "member"},
    )
    assert created.status_code == 200
    payload = created.json()
    lifetime = datetime.fromisoformat(payload["expires_at"]) - datetime.fromisoformat(
        payload["created_at"]
    )
    assert lifetime == timedelta(days=7)


def test_only_owner_or_admin_can_manage_invitations(tmp_path: Path) -> None:
    client, keys, _store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    created = _create_invite(client, keys, role="viewer")
    joined = client.post(
        f"/api/team-invites/{created['invite_token']}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert joined.status_code == 200
    assert joined.json()["participant"]["role"] == "viewer"

    assert (
        client.post(
            "/api/teams/secure-room/invites",
            headers=_bearer(keys, "bob"),
            json={"role": "member"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/api/teams/secure-room/invites",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/teams/secure-room/invites/{created['invite_id']}",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 403
    )

    viewer_update = client.put(
        "/api/teams/secure-room",
        headers=_bearer(keys, "bob"),
        json={
            "name": "Viewer rewrite",
            "members": [{"name": "general"}, {"name": "viewer-added-agent"}],
            "leaderId": "general",
        },
    )
    assert viewer_update.status_code == 403

    promoted = client.patch(
        f"/api/teams/secure-room/participants/{joined.json()['participant']['id']}",
        headers=_bearer(keys, "alice"),
        json={"role": "owner"},
    )
    assert promoted.status_code == 200
    admin_created = client.post(
        "/api/teams/secure-room/invites",
        headers=_bearer(keys, "bob"),
        json={"role": "viewer"},
    )
    assert admin_created.status_code == 200
    assert (
        client.get(
            "/api/teams/secure-room/invites",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/teams/secure-room/invites/{admin_created.json()['invite_id']}",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 200
    )

    member_invite = _create_invite(client, keys, role="member")
    assert (
        client.post(
            f"/api/team-invites/{member_invite['invite_token']}/join",
            headers=_bearer(keys, "carol"),
            json={},
        ).status_code
        == 200
    )
    member_update = client.put(
        "/api/teams/secure-room",
        headers=_bearer(keys, "carol"),
        json={
            "name": "Member collaboration",
            "members": [{"name": "general"}],
            "leaderId": "general",
        },
    )
    assert member_update.status_code == 200


def test_revoke_is_durable_and_blocks_preview_and_join(tmp_path: Path) -> None:
    client, keys, _store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    created = _create_invite(client, keys)

    revoked = client.delete(
        f"/api/teams/secure-room/invites/{created['invite_id']}",
        headers=_bearer(keys, "alice"),
    )
    assert revoked.status_code == 200
    assert revoked.json()["invite"]["status"] == "revoked"
    token = created["invite_token"]
    assert (
        client.get(
            f"/api/team-invites/{token}",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 410
    )
    assert (
        client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, "bob"),
            json={},
        ).status_code
        == 410
    )


def test_expiration_is_enforced_using_server_time(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 22, 10, 0, tzinfo=UTC)]
    client, keys, _store, _router = _build_app(tmp_path, clock=lambda: current[0])
    _create_room(client, keys)
    created = _create_invite(client, keys, expires_in_seconds=1)
    current[0] += timedelta(seconds=2)

    preview = client.get(
        f"/api/team-invites/{created['invite_token']}",
        headers=_bearer(keys, "bob"),
    )
    assert preview.status_code == 410
    listed = client.get(
        "/api/teams/secure-room/invites",
        headers=_bearer(keys, "alice"),
    ).json()
    assert listed["invites"][0]["status"] == "expired"


def test_max_uses_are_consumed_atomically_and_audited(tmp_path: Path) -> None:
    store = TeamInvitationStore(tmp_path / "atomic.db")
    invitation, token = store.create(
        tenant_id="tenant-acme",
        room_id="room-one",
        role="member",
        created_by="alice",
        expires_in_seconds=3600,
        max_uses=1,
    )
    stores = (store, TeamInvitationStore(store.db_path))

    def consume(item: tuple[TeamInvitationStore, str]) -> str:
        worker_store, actor = item
        try:
            _used, result = worker_store.consume_with(
                token,
                tenant_id="tenant-acme",
                room_id="room-one",
                actor_id=actor,
                request_id=f"request-{actor}",
                apply=lambda _invite: actor,
            )
            return result
        except InvitationExhausted:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, zip(stores, ("bob", "carol"), strict=True)))
    assert sorted(results).count("exhausted") == 1
    assert len(store.acceptances(invite_id=invitation["id"])) == 1


def test_failed_room_mutation_retains_capacity_for_same_actor_retry(tmp_path: Path) -> None:
    store = TeamInvitationStore(tmp_path / "rollback.db")
    invitation, token = store.create(
        tenant_id="tenant-acme",
        room_id="room-one",
        role="member",
        created_by="alice",
        expires_in_seconds=3600,
        max_uses=1,
    )

    def fail(_invite: dict[str, Any]) -> None:
        raise RuntimeError("room persistence failed")

    with pytest.raises(RuntimeError, match="room persistence failed"):
        store.consume_with(
            token,
            tenant_id="tenant-acme",
            room_id="room-one",
            actor_id="bob",
            request_id="request-failed",
            apply=fail,
        )
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert store.acceptances(invite_id=invitation["id"]) == []
    with pytest.raises(InvitationExhausted):
        store.consume_with(
            token,
            tenant_id="tenant-acme",
            room_id="room-one",
            actor_id="carol",
            request_id="request-carol",
            apply=lambda _invite: "carol",
        )

    consumed, result = store.consume_with(
        token,
        tenant_id="tenant-acme",
        room_id="room-one",
        actor_id="bob",
        request_id="request-retry",
        apply=lambda _invite: "bob",
    )
    assert result == "bob"
    assert consumed["status"] == "exhausted"
    assert [item["actor_id"] for item in store.acceptances(invite_id=invitation["id"])] == ["bob"]


@pytest.mark.parametrize("boundary", ["json", "sqlite"])
def test_direct_join_commit_then_raise_cannot_oversell_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    client, keys, store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    invite = _create_invite(client, keys, max_uses=1)
    token = invite["invite_token"]
    _raise_once_after_commit(monkeypatch, store, boundary)

    with pytest.raises(RuntimeError, match=f"{boundary} .* before response failure"):
        client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, "bob"),
            json={"display_name": "Bob"},
        )
    durable = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "secure-room")
    assert len([item for item in room["participants"] if item.get("actor_id") == "bob"]) == 1
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    revoked = client.delete(
        f"/api/teams/secure-room/invites/{invite['invite_id']}",
        headers=_bearer(keys, "alice"),
    )
    assert revoked.status_code == 200
    denied = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "carol"),
        json={"display_name": "Carol"},
    )
    assert denied.status_code == 410

    replay = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={"display_name": "Bob retry"},
    )
    assert replay.status_code == 200, replay.json()
    bob = [item for item in replay.json()["team"]["participants"] if item.get("actor_id") == "bob"]
    assert len(bob) == 1
    assert len(store.acceptances(invite_id=invite["invite_id"])) == 1


@pytest.mark.parametrize("terminal_state", ["revoked", "expired"])
def test_reserved_direct_join_without_membership_stays_blocked_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    client, keys, store, _router = _build_app(tmp_path, clock=clock)
    _create_room(client, keys)
    invite = _create_invite(
        client,
        keys,
        max_uses=1,
        expires_in_seconds=1 if terminal_state == "expired" else 3600,
    )
    token = invite["invite_token"]
    _fail_once_before_membership_commit(monkeypatch)

    with pytest.raises(RuntimeError, match="before durable commit"):
        client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, "bob"),
            json={"display_name": "Bob"},
        )
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert store.acceptances(invite_id=invite["invite_id"]) == []
    if terminal_state == "revoked":
        revoked = client.delete(
            f"/api/teams/secure-room/invites/{invite['invite_id']}",
            headers=_bearer(keys, "alice"),
        )
        assert revoked.status_code == 200
    else:
        current[0] += timedelta(seconds=2)
    client.close()

    restarted, restarted_keys, restarted_store, _router = _build_app(tmp_path, clock=clock)
    retry = restarted.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(restarted_keys, "bob"),
        json={"display_name": "Bob retry"},
    )
    assert retry.status_code == 410, retry.json()
    durable = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "secure-room")
    assert not [
        item
        for item in room["participants"]
        if item.get("actor_id") == "bob" and item["status"] != "removed"
    ]
    assert restarted_store.acceptances(invite_id=invite["invite_id"]) == []


def test_removed_member_with_pending_direct_reservation_is_not_resurrected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, keys, store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    invite = _create_invite(client, keys, max_uses=1)
    token = invite["invite_token"]
    _raise_once_after_commit(monkeypatch, store, "json")
    with pytest.raises(RuntimeError, match="json committed"):
        client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, "bob"),
            json={},
        )

    removed = client.delete(
        "/api/teams/secure-room/participants/actor-bob",
        headers=_bearer(keys, "alice"),
    )
    assert removed.status_code == 200
    replay = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert replay.status_code == 403
    assert store.acceptances(invite_id=invite["invite_id"]) == []
    participants = removed.json()["team"]["participants"]
    assert not [
        item
        for item in participants
        if item.get("actor_id") == "bob" and item["status"] != "removed"
    ]


def test_direct_join_stable_participant_conflict_returns_409(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, keys, store, _router = _build_app(tmp_path)
    _create_room(client, keys)
    invite = _create_invite(client, keys, max_uses=1)
    token = invite["invite_token"]
    _fail_once_before_membership_commit(monkeypatch)
    with pytest.raises(RuntimeError, match="before durable commit"):
        client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, "bob"),
            json={},
        )

    state_path = tmp_path / "rooms.json"
    durable = json.loads(state_path.read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "secure-room")
    owner = room["participants"][0]
    room["participants"].append(
        {
            **owner,
            "id": "legacy-bob",
            "actor_id": "bob",
            "display_name": "Bob legacy",
        }
    )
    state_path.write_text(json.dumps(durable), encoding="utf-8")
    client.close()

    restarted, restarted_keys, restarted_store, _router = _build_app(tmp_path)
    retry = restarted.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(restarted_keys, "bob"),
        json={},
    )
    assert retry.status_code == 409, retry.json()
    assert restarted_store.acceptances(invite_id=invite["invite_id"]) == []
    assert restarted_store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1


def test_internal_room_binding_returns_canonical_thread_on_preview_and_join(
    tmp_path: Path,
) -> None:
    client, keys, _store, router = _build_app(tmp_path)
    room = router.create_team_from_payload(
        _request(keys["alice"]),
        _team_body(thread_id="thread-canonical"),
    )
    assert room["thread_id"] == "thread-canonical"
    created = _create_invite(client, keys)

    preview = client.get(
        f"/api/team-invites/{created['invite_token']}",
        headers=_bearer(keys, "bob"),
    ).json()
    assert preview["thread_id"] == "thread-canonical"
    joined = client.post(
        f"/api/team-invites/{created['invite_token']}/join",
        headers=_bearer(keys, "bob"),
        json={},
    ).json()
    assert joined["thread_id"] == "thread-canonical"


def test_startup_scrubs_plaintext_legacy_room_invitation(tmp_path: Path) -> None:
    state_path = tmp_path / "rooms.json"
    state_path.write_text(
        json.dumps(
            {
                "teams": [
                    {
                        **_team_body(),
                        "owner_id": "alice",
                        "created_at": "2026-08-20T00:00:00+00:00",
                        "updated_at": "2026-08-20T00:00:00+00:00",
                        "invite_token": "legacy-plaintext-secret",
                        "invite_role": "member",
                        "invite_created_at": "2026-08-20T00:00:00+00:00",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    client, keys, _store, _router = _build_app(tmp_path)
    migrated = state_path.read_text(encoding="utf-8")
    assert "legacy-plaintext-secret" not in migrated
    assert "invite_token" not in migrated
    assert (
        client.get(
            "/api/team-invites/legacy-plaintext-secret",
            headers=_bearer(keys, "alice"),
        ).status_code
        == 404
    )


def test_public_room_create_cannot_choose_canonical_thread(tmp_path: Path) -> None:
    client, keys, _store, _router = _build_app(tmp_path)
    created = client.post(
        "/api/teams",
        headers=_bearer(keys, "alice"),
        json=_team_body(thread_id="thread-not-owned-proof"),
    )
    assert created.status_code == 200
    assert created.json()["thread_id"] is None

