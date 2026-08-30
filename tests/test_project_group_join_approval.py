from __future__ import annotations

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
    InvitationError,
    TeamInvitationStore,
)
from runtime.projectos.model import Project  # noqa: E402
from runtime.projectos.store import ProjectStore  # noqa: E402
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.team_rooms_router import create_team_rooms_router  # noqa: E402


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


def _room_body(room_id: str, *, thread_id: str | None = None) -> dict[str, Any]:
    return {
        "id": room_id,
        "name": f"Room {room_id}",
        "members": [{"name": "general", "display_name": "Echo"}],
        "leaderId": "general",
        "thread_id": thread_id,
    }


def _build_app(
    tmp_path: Path,
    *,
    clock=None,
) -> tuple[TestClient, dict[str, str], TeamInvitationStore, Any, ProjectStore]:
    identities = IdentityStore()
    keys: dict[str, str] = {}
    tenants = {
        "alice": "tenant-acme",
        "bob": "tenant-acme",
        "carol": "tenant-acme",
        "dave": "tenant-acme",
        "mallory": "tenant-other",
    }
    for actor, tenant_id in tenants.items():
        key = f"sk-{actor}"
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": tenant_id}),
            api_key_plaintext=key,
        )
        keys[actor] = key
    invitation_store = TeamInvitationStore(
        tmp_path / "team_invitations.db",
        **({"clock": clock} if clock is not None else {}),
    )
    project_store = ProjectStore(base_dir=tmp_path / "projectos")
    router = create_team_rooms_router(
        state_path=tmp_path / "rooms.json",
        identity_store=identities,
        require_auth=True,
        invitation_store=invitation_store,
        project_store=project_store,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app), keys, invitation_store, router, project_store


def _create_project_room(
    keys: dict[str, str],
    router: Any,
    projects: ProjectStore,
    *,
    room_id: str = "project-room",
    thread_id: str = "thread-project",
) -> dict[str, Any]:
    projects.save_project(
        Project(
            id=f"project-{room_id}",
            name=f"Project {room_id}",
            goal="ship",
            owner_id="alice",
            tenant_id="tenant-acme",
        )
    )
    projects.bind_thread(thread_id, f"project-{room_id}")
    return router.create_team_from_payload(
        _request(keys["alice"]),
        _room_body(room_id, thread_id=thread_id),
    )


def _invite(
    client: TestClient,
    keys: dict[str, str],
    room_id: str,
    *,
    expires_in_seconds: int = 3600,
    max_uses: int = 10,
) -> dict[str, Any]:
    response = client.post(
        f"/api/teams/{room_id}/invites",
        headers=_bearer(keys, "alice"),
        json={
            "role": "member",
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


def test_ordinary_room_stays_direct_while_project_room_defaults_to_approval(
    tmp_path: Path,
) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    ordinary = client.post(
        "/api/teams",
        headers=_bearer(keys, "alice"),
        json=_room_body("ordinary-room"),
    )
    assert ordinary.status_code == 200
    assert ordinary.json()["join_policy"] == "direct_join"
    project = _create_project_room(keys, router, projects)
    assert project["join_policy"] == "apply_then_join"
    assert project["is_project_group"] is True

    direct_invite = _invite(client, keys, "ordinary-room")
    direct = client.post(
        f"/api/team-invites/{direct_invite['invite_token']}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert direct.status_code == 200
    assert direct.json()["outcome"] == "joined"

    project_invite = _invite(client, keys, "project-room")
    preview = client.get(
        f"/api/team-invites/{project_invite['invite_token']}",
        headers=_bearer(keys, "carol"),
    )
    assert preview.status_code == 200
    assert preview.json()["join_policy"] == "apply_then_join"
    assert preview.json()["thread_id"] is None


def test_project_store_is_late_bound_and_cross_tenant_binding_is_ignored(tmp_path: Path) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    project = _create_project_room(keys, router, projects)
    assert project["join_policy"] == "apply_then_join"

    router.bind_project_store(None)
    unbound = client.get(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
    )
    assert unbound.json()["join_policy"] == "direct_join"
    router.bind_project_store(projects)
    rebound = client.get(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
    )
    assert rebound.json()["join_policy"] == "apply_then_join"

    projects.save_project(
        Project(
            id="project-other-tenant",
            name="Foreign project",
            goal="no leak",
            owner_id="mallory",
            tenant_id="tenant-other",
        )
    )
    projects.bind_thread("thread-foreign-project", "project-other-tenant")
    foreign_binding_room = router.create_team_from_payload(
        _request(keys["alice"]),
        _room_body("foreign-binding-room", thread_id="thread-foreign-project"),
    )
    assert foreign_binding_room["join_policy"] == "direct_join"
    assert foreign_binding_room["is_project_group"] is False

    class BrokenProjectStore:
        @staticmethod
        def project_for_thread(_thread_id: str) -> None:
            raise RuntimeError("project database unavailable")

    router.bind_project_store(BrokenProjectStore())
    failed_closed = client.get(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
    )
    assert failed_closed.status_code == 200
    assert failed_closed.json()["join_policy"] == "apply_then_join"


def test_project_join_request_approve_and_all_retries_are_idempotent(tmp_path: Path) -> None:
    client, keys, store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    invite = _invite(client, keys, "project-room", max_uses=2)
    token = invite["invite_token"]

    first = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={"display_name": "Bob"},
    )
    second = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={"display_name": "Changed on retry"},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert first.json()["join_request"]["id"] == second.json()["join_request"]["id"]
    assert first.json()["thread_id"] is None
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 0

    request_id = first.json()["join_request"]["id"]
    denied = client.post(
        f"/api/teams/project-room/join-requests/{request_id}/approve",
        headers=_bearer(keys, "bob"),
    )
    assert denied.status_code == 403
    listed = client.get(
        "/api/teams/project-room/join-requests?status=pending",
        headers=_bearer(keys, "alice"),
    )
    assert listed.status_code == 200
    assert listed.json()["count"] == 1
    assert listed.json()["join_requests"][0]["actor_id"] == "bob"

    approved = client.post(
        f"/api/teams/project-room/join-requests/{request_id}/approve",
        headers=_bearer(keys, "alice"),
    )
    replay = client.post(
        f"/api/teams/project-room/join-requests/{request_id}/approve",
        headers=_bearer(keys, "alice"),
    )
    assert approved.status_code == replay.status_code == 200
    assert approved.json()["changed"] is True
    assert replay.json()["changed"] is False
    assert approved.json()["participant"]["actor_id"] == "bob"
    assert approved.json()["thread_id"] == "thread-project"
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert len(store.acceptances(invite_id=invite["invite_id"])) == 1

    polled = client.get(
        f"/api/team-invites/{token}/join-request",
        headers=_bearer(keys, "bob"),
    )
    assert polled.status_code == 200
    assert polled.json()["outcome"] == "joined"
    assert polled.json()["thread_id"] == "thread-project"
    assert (
        client.get(
            "/api/teams/project-room/join-requests",
            headers=_bearer(keys, "bob"),
        ).status_code
        == 403
    )
    promoted = client.patch(
        "/api/teams/project-room/participants/actor-bob",
        headers=_bearer(keys, "alice"),
        json={"role": "owner"},
    )
    assert promoted.status_code == 200
    delegated_admin = client.patch(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "bob"),
        json={"join_policy": "direct_join"},
    )
    assert delegated_admin.status_code == 200

    # Even an admin of a different same-tenant room cannot move this request.
    _create_project_room(
        keys,
        router,
        projects,
        room_id="project-room-two",
        thread_id="thread-project-two",
    )
    wrong_room = client.post(
        f"/api/teams/project-room-two/join-requests/{request_id}/approve",
        headers=_bearer(keys, "alice"),
    )
    assert wrong_room.status_code == 404


def test_reject_withdraw_revoke_and_expiry_close_pending_requests(tmp_path: Path) -> None:
    current = [datetime(2026, 8, 22, 10, 0, tzinfo=UTC)]
    client, keys, _store, router, projects = _build_app(
        tmp_path,
        clock=lambda: current[0],
    )
    _create_project_room(keys, router, projects)

    rejected_invite = _invite(client, keys, "project-room")
    rejected_token = rejected_invite["invite_token"]
    pending = client.post(
        f"/api/team-invites/{rejected_token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    ).json()["join_request"]
    rejected = client.post(
        f"/api/teams/project-room/join-requests/{pending['id']}/reject",
        headers=_bearer(keys, "alice"),
        json={"reason": "project is at capacity"},
    )
    rejected_again = client.post(
        f"/api/teams/project-room/join-requests/{pending['id']}/reject",
        headers=_bearer(keys, "alice"),
        json={"reason": "ignored replay"},
    )
    assert rejected.status_code == rejected_again.status_code == 200
    assert rejected.json()["changed"] is True
    assert rejected_again.json()["changed"] is False
    retry = client.post(
        f"/api/team-invites/{rejected_token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert retry.status_code == 200
    assert retry.json()["outcome"] == "rejected"

    withdrawn_invite = _invite(client, keys, "project-room")
    withdrawn_token = withdrawn_invite["invite_token"]
    withdrawn_request = client.post(
        f"/api/team-invites/{withdrawn_token}/join",
        headers=_bearer(keys, "carol"),
        json={},
    ).json()["join_request"]
    withdrawn = client.delete(
        f"/api/team-invites/{withdrawn_token}/join-request",
        headers=_bearer(keys, "carol"),
    )
    withdrawn_again = client.delete(
        f"/api/team-invites/{withdrawn_token}/join-request",
        headers=_bearer(keys, "carol"),
    )
    assert withdrawn.status_code == withdrawn_again.status_code == 200
    assert withdrawn.json()["outcome"] == "withdrawn"
    assert (
        client.post(
            f"/api/teams/project-room/join-requests/{withdrawn_request['id']}/approve",
            headers=_bearer(keys, "alice"),
        ).status_code
        == 409
    )

    revoked_invite = _invite(client, keys, "project-room")
    revoked_token = revoked_invite["invite_token"]
    revoked_request = client.post(
        f"/api/team-invites/{revoked_token}/join",
        headers=_bearer(keys, "dave"),
        json={},
    ).json()["join_request"]
    assert (
        client.delete(
            f"/api/teams/project-room/invites/{revoked_invite['invite_id']}",
            headers=_bearer(keys, "alice"),
        ).status_code
        == 200
    )
    cancelled = client.get(
        f"/api/team-invites/{revoked_token}/join-request",
        headers=_bearer(keys, "dave"),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["outcome"] == "cancelled"
    assert (
        client.post(
            f"/api/teams/project-room/join-requests/{revoked_request['id']}/approve",
            headers=_bearer(keys, "alice"),
        ).status_code
        == 409
    )

    expiring_invite = _invite(client, keys, "project-room", expires_in_seconds=1)
    expiring_token = expiring_invite["invite_token"]
    expiring_request = client.post(
        f"/api/team-invites/{expiring_token}/join",
        headers=_bearer(keys, "carol"),
        json={},
    ).json()["join_request"]
    current[0] += timedelta(seconds=2)
    expired = client.get(
        f"/api/team-invites/{expiring_token}/join-request",
        headers=_bearer(keys, "carol"),
    )
    assert expired.status_code == 200
    assert expired.json()["outcome"] == "expired"
    assert (
        client.post(
            f"/api/teams/project-room/join-requests/{expiring_request['id']}/approve",
            headers=_bearer(keys, "alice"),
        ).status_code
        == 409
    )
    with sqlite3.connect(tmp_path / "team_invitations.db") as conn:
        status = conn.execute(
            "SELECT status FROM team_join_requests WHERE request_id = ?",
            (expiring_request["id"],),
        ).fetchone()[0]
    assert status == "expired"


def test_policy_override_is_admin_only_persistent_and_tenant_scoped(tmp_path: Path) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    invite = _invite(client, keys, "project-room")
    token = invite["invite_token"]

    assert (
        client.patch(
            "/api/teams/project-room/join-policy",
            headers=_bearer(keys, "bob"),
            json={"join_policy": "direct_join"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/team-invites/{token}",
            headers=_bearer(keys, "mallory"),
        ).status_code
        == 404
    )
    changed = client.patch(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
        json={"join_policy": "direct_join"},
    )
    assert changed.status_code == 200
    assert changed.json()["join_policy"] == "direct_join"
    joined = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert joined.status_code == 200
    assert joined.json()["outcome"] == "joined"

    identities = IdentityStore()
    for actor in ("alice", "bob"):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": "tenant-acme"}),
            api_key_plaintext=keys[actor],
        )
    restarted = FastAPI()
    restarted.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "rooms.json",
            identity_store=identities,
            require_auth=True,
            invitation_store=TeamInvitationStore(tmp_path / "team_invitations.db"),
            project_store=projects,
        )
    )
    policy = TestClient(restarted).get(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
    )
    assert policy.status_code == 200
    assert policy.json()["join_policy"] == "direct_join"
    assert policy.json()["overridden"] is True


def test_pending_request_is_closed_when_policy_switches_to_direct_join(tmp_path: Path) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    invite = _invite(client, keys, "project-room")
    token = invite["invite_token"]
    pending = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert pending.status_code == 202
    assert (
        client.patch(
            "/api/teams/project-room/join-policy",
            headers=_bearer(keys, "alice"),
            json={"join_policy": "direct_join"},
        ).status_code
        == 200
    )
    joined = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert joined.status_code == 200
    polled = client.get(
        f"/api/team-invites/{token}/join-request",
        headers=_bearer(keys, "bob"),
    )
    assert polled.json()["outcome"] == "joined"
    assert polled.json()["join_request"]["status"] == "approved"


def test_pending_request_survives_router_restart_and_can_then_be_approved(tmp_path: Path) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    invite = _invite(client, keys, "project-room")
    pending = client.post(
        f"/api/team-invites/{invite['invite_token']}/join",
        headers=_bearer(keys, "bob"),
        json={"display_name": "Bob"},
    ).json()["join_request"]

    identities = IdentityStore()
    for actor in ("alice", "bob"):
        identities.add(
            Identity(actor_id=actor, metadata={"tenant_id": "tenant-acme"}),
            api_key_plaintext=keys[actor],
        )
    restarted = FastAPI()
    restarted.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "rooms.json",
            identity_store=identities,
            require_auth=True,
            invitation_store=TeamInvitationStore(tmp_path / "team_invitations.db"),
            project_store=projects,
        )
    )
    restarted_client = TestClient(restarted)
    listed = restarted_client.get(
        "/api/teams/project-room/join-requests?status=pending",
        headers=_bearer(keys, "alice"),
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["join_requests"]] == [pending["id"]]
    approved = restarted_client.post(
        f"/api/teams/project-room/join-requests/{pending['id']}/approve",
        headers=_bearer(keys, "alice"),
    )
    assert approved.status_code == 200
    assert approved.json()["participant"]["actor_id"] == "bob"


def test_internal_agent_roster_projection_preserves_humans_and_thread(tmp_path: Path) -> None:
    client, keys, _store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    client.patch(
        "/api/teams/project-room/join-policy",
        headers=_bearer(keys, "alice"),
        json={"join_policy": "direct_join"},
    )
    invite = _invite(client, keys, "project-room")
    joined = client.post(
        f"/api/team-invites/{invite['invite_token']}/join",
        headers=_bearer(keys, "bob"),
        json={},
    )
    assert joined.status_code == 200

    projected = router.replace_team_agent_members(
        _request(keys["alice"]),
        "project-room",
        [{"name": "research-agent"}, {"name": "build-agent"}],
        "build-agent",
    )
    assert [item["name"] for item in projected["members"]] == [
        "research-agent",
        "build-agent",
    ]
    assert projected["leaderId"] == "build-agent"
    assert projected["thread_id"] == "thread-project"
    assert any(item["actor_id"] == "bob" for item in projected["participants"])
    with pytest.raises(fastapi.HTTPException) as denied:
        router.replace_team_agent_members(
            _request(keys["bob"]),
            "project-room",
            [{"name": "hijack"}],
        )
    assert denied.value.status_code == 403


def test_failed_approval_membership_write_retains_capacity_for_retry(tmp_path: Path) -> None:
    store = TeamInvitationStore(tmp_path / "approval-rollback.db")
    invitation, token = store.create(
        tenant_id="tenant-acme",
        room_id="room-one",
        role="member",
        created_by="alice",
        expires_in_seconds=3600,
        max_uses=1,
    )
    _invite_row, application, _created = store.create_join_request(
        token,
        tenant_id="tenant-acme",
        room_id="room-one",
        actor_id="bob",
        display_name="Bob",
    )
    _invite_row, other_application, _created = store.create_join_request(
        token,
        tenant_id="tenant-acme",
        room_id="room-one",
        actor_id="carol",
        display_name="Carol",
    )

    def fail(_consumed: dict[str, Any], _approved: dict[str, Any]) -> None:
        raise RuntimeError("room persistence failed")

    with pytest.raises(RuntimeError, match="room persistence failed"):
        store.approve_join_request_with(
            application["id"],
            tenant_id="tenant-acme",
            room_id="room-one",
            decided_by="alice",
            participant_id="actor-bob",
            audit_request_id="approval-request",
            apply=fail,
        )
    current = store.get_join_request(
        application["id"],
        tenant_id="tenant-acme",
        room_id="room-one",
    )
    assert current is not None and current["status"] == "pending"
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert store.acceptances(invite_id=invitation["id"]) == []
    with pytest.raises(InvitationError, match="approval is in progress"):
        store.withdraw_join_request(
            token,
            tenant_id="tenant-acme",
            actor_id="bob",
        )
    with pytest.raises(InvitationError):
        store.approve_join_request_with(
            other_application["id"],
            tenant_id="tenant-acme",
            room_id="room-one",
            decided_by="alice",
            participant_id="actor-carol",
            audit_request_id="approval-carol",
            apply=lambda _invite, _request: "carol",
        )

    consumed, approved, result, changed = store.approve_join_request_with(
        application["id"],
        tenant_id="tenant-acme",
        room_id="room-one",
        decided_by="alice",
        participant_id="actor-bob",
        audit_request_id="approval-retry",
        apply=lambda _invite, _request: "bob",
    )
    assert (approved["status"], result, changed) == ("approved", "bob", True)
    assert consumed["use_count"] == 1
    assert [item["actor_id"] for item in store.acceptances(invite_id=invitation["id"])] == ["bob"]


@pytest.mark.parametrize("boundary", ["json", "sqlite"])
def test_approval_commit_then_raise_cannot_oversell_and_replays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    boundary: str,
) -> None:
    client, keys, store, router, projects = _build_app(tmp_path)
    _create_project_room(keys, router, projects)
    invite = _invite(client, keys, "project-room", max_uses=1)
    token = invite["invite_token"]
    applications: dict[str, str] = {}
    for actor in ("bob", "carol"):
        joined = client.post(
            f"/api/team-invites/{token}/join",
            headers=_bearer(keys, actor),
            json={"display_name": actor.title()},
        )
        assert joined.status_code == 202
        applications[actor] = joined.json()["join_request"]["id"]

    _raise_once_after_commit(monkeypatch, store, boundary)
    with pytest.raises(RuntimeError, match=f"{boundary} .* before response failure"):
        client.post(
            f"/api/teams/project-room/join-requests/{applications['bob']}/approve",
            headers=_bearer(keys, "alice"),
        )
    durable = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "project-room")
    assert len([item for item in room["participants"] if item.get("actor_id") == "bob"]) == 1
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    revoked = client.delete(
        f"/api/teams/project-room/invites/{invite['invite_id']}",
        headers=_bearer(keys, "alice"),
    )
    assert revoked.status_code == 200
    denied = client.post(
        f"/api/teams/project-room/join-requests/{applications['carol']}/approve",
        headers=_bearer(keys, "alice"),
    )
    assert denied.status_code == 409

    replay = client.post(
        f"/api/teams/project-room/join-requests/{applications['bob']}/approve",
        headers=_bearer(keys, "alice"),
    )
    assert replay.status_code == 200, replay.json()
    bob = [item for item in replay.json()["team"]["participants"] if item.get("actor_id") == "bob"]
    assert len(bob) == 1
    assert len(store.acceptances(invite_id=invite["invite_id"])) == 1


@pytest.mark.parametrize("terminal_state", ["revoked", "expired"])
def test_reserved_approval_without_membership_stays_blocked_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_state: str,
) -> None:
    current = [datetime(2026, 1, 1, tzinfo=UTC)]

    def clock() -> datetime:
        return current[0]

    client, keys, store, router, projects = _build_app(tmp_path, clock=clock)
    _create_project_room(keys, router, projects)
    invite = _invite(
        client,
        keys,
        "project-room",
        max_uses=1,
        expires_in_seconds=1 if terminal_state == "expired" else 3600,
    )
    token = invite["invite_token"]
    joined = client.post(
        f"/api/team-invites/{token}/join",
        headers=_bearer(keys, "bob"),
        json={"display_name": "Bob"},
    )
    assert joined.status_code == 202
    request_id = joined.json()["join_request"]["id"]
    _fail_once_before_membership_commit(monkeypatch)

    with pytest.raises(RuntimeError, match="before durable commit"):
        client.post(
            f"/api/teams/project-room/join-requests/{request_id}/approve",
            headers=_bearer(keys, "alice"),
        )
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert store.acceptances(invite_id=invite["invite_id"]) == []
    durable = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "project-room")
    assert not [
        item
        for item in room["participants"]
        if item.get("actor_id") == "bob" and item["status"] != "removed"
    ]
    if terminal_state == "revoked":
        revoked = client.delete(
            f"/api/teams/project-room/invites/{invite['invite_id']}",
            headers=_bearer(keys, "alice"),
        )
        assert revoked.status_code == 200
    else:
        current[0] += timedelta(seconds=2)
    client.close()

    restarted, restarted_keys, restarted_store, _router, _projects = _build_app(
        tmp_path,
        clock=clock,
    )
    retry = restarted.post(
        f"/api/teams/project-room/join-requests/{request_id}/approve",
        headers=_bearer(restarted_keys, "alice"),
    )
    assert retry.status_code == 410, retry.json()
    durable = json.loads((tmp_path / "rooms.json").read_text(encoding="utf-8"))
    room = next(item for item in durable["teams"] if item["id"] == "project-room")
    assert not [
        item
        for item in room["participants"]
        if item.get("actor_id") == "bob" and item["status"] != "removed"
    ]
    assert restarted_store.acceptances(invite_id=invite["invite_id"]) == []


def test_concurrent_approvals_cannot_exceed_invitation_capacity(tmp_path: Path) -> None:
    store = TeamInvitationStore(tmp_path / "approval-capacity.db")
    invitation, token = store.create(
        tenant_id="tenant-acme",
        room_id="room-one",
        role="member",
        created_by="alice",
        expires_in_seconds=3600,
        max_uses=1,
    )
    applications = []
    for actor in ("bob", "carol"):
        _invite_row, application, _created = store.create_join_request(
            token,
            tenant_id="tenant-acme",
            room_id="room-one",
            actor_id=actor,
            display_name=actor.title(),
        )
        applications.append(application)
    stores = (store, TeamInvitationStore(store.db_path))

    def approve(item: tuple[TeamInvitationStore, dict[str, Any]]) -> str:
        worker, application = item
        try:
            _used, approved, _result, _changed = worker.approve_join_request_with(
                application["id"],
                tenant_id="tenant-acme",
                room_id="room-one",
                decided_by="alice",
                participant_id=f"actor-{application['actor_id']}",
                audit_request_id=f"approve-{application['actor_id']}",
                apply=lambda _invite, request: request["actor_id"],
            )
            return approved["status"]
        except InvitationError as exc:
            return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(approve, zip(stores, applications, strict=True)))
    assert outcomes.count("approved") == 1
    assert store.find_by_token(token, tenant_id="tenant-acme")["use_count"] == 1
    assert len(store.acceptances(invite_id=invitation["id"])) == 1
    final = store.list_join_requests(tenant_id="tenant-acme", room_id="room-one")
    assert sorted(item["status"] for item in final) == ["approved", "cancelled"]

