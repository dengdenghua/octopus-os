"""Security regression tests for team_rooms_router membership enforcement.

These pin the fix that eliminated 8 bare ``_auth(request)`` calls and
replaced them with ``_require_member`` / ``_require_owner``. Without
these checks, any authenticated user could enumerate all teams, modify
any team, delete any team, or kick participants from any team they
weren't a member of.

Companion to ``test_team_tasks_router_security.py`` — both routers
share the same underlying authz model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.team_rooms_router import (  # noqa: E402
    create_team_rooms_router,
)


def _build_app(
    tmp_path: Path,
) -> tuple[TestClient, dict[str, str]]:
    """Build app with require_auth=True + 3 identities (alice, bob, carol)."""
    store = IdentityStore()
    keys: dict[str, str] = {}
    for actor in ("alice", "bob", "carol"):
        api_key = f"sk-test-{actor}"
        # These actors model users in one organization.  Identities without
        # tenant metadata are intentionally isolated into actor-local legacy
        # tenants and therefore cannot accept one another's room invites.
        store.add(
            Identity(actor_id=actor, metadata={"tenant_id": "test-org"}),
            api_key_plaintext=api_key,
        )
        keys[actor] = api_key

    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "rooms.json",
            identity_store=store,
            require_auth=True,
        )
    )
    return TestClient(app), keys


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


def _create_team(
    client: TestClient,
    keys: dict[str, str],
    team_id: str,
    owner: str,
) -> dict[str, Any]:
    """Create a team owned by ``owner``."""
    resp = client.post(
        "/api/teams",
        json={
            "id": team_id,
            "name": f"Team {team_id}",
            "members": [{"name": owner, "role": "owner"}],
        },
        headers=_bearer(keys[owner]),
    )
    assert resp.status_code == 200, resp.json()
    return resp.json()


# ── list_teams: filter to caller's own teams ───────────────────────


def test_list_teams_filters_by_membership(tmp_path: Path) -> None:
    """User A should NOT see User B's teams in the list."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _create_team(client, keys, "bob-team", owner="bob")

    # alice sees only her team
    resp = client.get("/api/teams", headers=_bearer(keys["alice"]))
    assert resp.status_code == 200
    team_ids = {t["id"] for t in resp.json()["teams"]}
    assert team_ids == {"alice-team"}

    # bob sees only his
    resp = client.get("/api/teams", headers=_bearer(keys["bob"]))
    team_ids = {t["id"] for t in resp.json()["teams"]}
    assert team_ids == {"bob-team"}

    # carol sees nothing (no teams)
    resp = client.get("/api/teams", headers=_bearer(keys["carol"]))
    assert resp.json()["count"] == 0


# ── get_team: only members can read ────────────────────────────────


def test_get_team_blocks_non_member(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    # bob can't read alice's team
    resp = client.get("/api/teams/alice-team", headers=_bearer(keys["bob"]))
    assert resp.status_code == 403
    assert "not a member" in resp.json()["detail"]


def test_get_team_allows_member(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.get("/api/teams/alice-team", headers=_bearer(keys["alice"]))
    assert resp.status_code == 200


# ── update_team: only members can rename ───────────────────────────


def test_update_team_blocks_non_member(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.put(
        "/api/teams/alice-team",
        json={"name": "Hijacked", "members": [{"name": "bob", "role": "owner"}]},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── delete_team: only owner can delete ─────────────────────────────


def test_delete_team_blocks_non_owner(tmp_path: Path) -> None:
    """Even a member who isn't the owner can't delete the team."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    # bob tries to delete — 403
    resp = client.delete("/api/teams/alice-team", headers=_bearer(keys["bob"]))
    assert resp.status_code == 403
    assert "owner" in resp.json()["detail"].lower()


def test_delete_team_allows_owner(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.delete("/api/teams/alice-team", headers=_bearer(keys["alice"]))
    assert resp.status_code == 200
    assert resp.json()["deleted"] is True


# ── create_invite: only members can invite ─────────────────────────


def test_create_invite_blocks_non_member(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.post(
        "/api/teams/alice-team/invite",
        json={},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── unauthenticated callers are blocked at the auth layer ──────────


def test_no_auth_token_returns_401(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.get("/api/teams/alice-team")
    assert resp.status_code == 401


# ── invite preview is intentionally actor-agnostic ─────────────────


def test_invite_preview_works_for_non_member(tmp_path: Path) -> None:
    """``GET /api/team-invites/{token}`` is intentionally actor-agnostic
    — anyone with a valid invite token may preview the target team
    before joining. Pinned here so a future hardening doesn't
    accidentally lock it down without an explicit decision."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    invite = client.post(
        "/api/teams/alice-team/invite",
        json={},
        headers=_bearer(keys["alice"]),
    )
    assert invite.status_code == 200
    token = invite.json()["invite_token"]

    # carol (not a member) can preview
    resp = client.get(
        f"/api/team-invites/{token}",
        headers=_bearer(keys["carol"]),
    )
    assert resp.status_code == 200
    assert resp.json()["team"]["id"] == "alice-team"


# ── single-user dev mode: require_auth=False bypasses checks ───────


def test_dev_mode_bypasses_membership_checks(tmp_path: Path) -> None:
    """When require_auth=False, _require_member is a no-op so local
    development isn't broken."""
    app = FastAPI()
    app.include_router(
        create_team_rooms_router(
            state_path=tmp_path / "rooms.json",
            require_auth=False,
        )
    )
    client = TestClient(app)

    # No auth header at all — should still work in dev mode
    create = client.post(
        "/api/teams",
        json={
            "id": "team-x",
            "name": "Dev Team",
            "members": [{"name": "anyone", "role": "owner"}],
        },
    )
    assert create.status_code == 200
    resp = client.get("/api/teams/team-x")
    assert resp.status_code == 200


# ── participant management: role/status/targeting is owner-only ─────
#
# Regression guard for the privilege-escalation hole where
# ``update_participant`` / ``remove_participant`` only called
# ``_require_member``: any member could promote themselves to owner,
# rewrite another member's role, or kick anyone. The fix limits a plain
# member to editing their OWN display_name and removing only themselves.


def _invite_and_join(
    client: TestClient,
    keys: dict[str, str],
    team_id: str,
    owner: str,
    joiner: str,
    role: str = "member",
) -> str:
    """Owner mints an invite; ``joiner`` accepts it and becomes an active
    participant. Returns the joiner's participant id (``actor-<joiner>``)."""
    invite = client.post(
        f"/api/teams/{team_id}/invite",
        json={"role": role},
        headers=_bearer(keys[owner]),
    )
    assert invite.status_code == 200, invite.json()
    token = invite.json()["invite_token"]
    joined = client.post(
        f"/api/team-invites/{token}/join",
        json={},
        headers=_bearer(keys[joiner]),
    )
    assert joined.status_code == 200, joined.json()
    return joined.json()["participant"]["id"]


def test_member_cannot_promote_self_to_owner(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"role": "owner"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403
    assert "only the team owner" in resp.json()["detail"]


def test_member_cannot_change_another_participant(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    # bob tries to rewrite carol's role
    resp = client.patch(
        f"/api/teams/alice-team/participants/{carol_pid}",
        json={"role": "owner"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_member_cannot_rename_another_participant(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{carol_pid}",
        json={"display_name": "Pwned"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403
    assert "your own" in resp.json()["detail"]


def test_member_can_rename_self(tmp_path: Path) -> None:
    """Self-service display-name change must still work for a plain member."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"display_name": "Bobby"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["participant"]["display_name"] == "Bobby"
    # role is untouched — rename does not silently grant privilege
    assert resp.json()["participant"]["role"] == "member"


def test_member_cannot_kick_another(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    resp = client.delete(
        f"/api/teams/alice-team/participants/{carol_pid}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_member_can_leave(tmp_path: Path) -> None:
    """A member removing THEMSELVES (leaving) is allowed."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.delete(
        f"/api/teams/alice-team/participants/{bob_pid}",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200, resp.json()


def test_owner_can_manage_participants(tmp_path: Path) -> None:
    """The owner retains full control: promote, and kick others."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    promote = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"role": "owner"},
        headers=_bearer(keys["alice"]),
    )
    assert promote.status_code == 200, promote.json()
    assert promote.json()["participant"]["role"] == "owner"

    kick = client.delete(
        f"/api/teams/alice-team/participants/{carol_pid}",
        headers=_bearer(keys["alice"]),
    )
    assert kick.status_code == 200, kick.json()


# ── governance: mute + speaker-policy are owner-only ───────────────


def test_owner_can_mute_member(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"muted": True},
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["participant"]["muted"] is True


def test_member_cannot_mute_another(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{carol_pid}",
        json={"muted": True},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_member_cannot_unmute_self(tmp_path: Path) -> None:
    """A muted member must not be able to lift their own mute."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    # owner mutes bob
    muted = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"muted": True},
        headers=_bearer(keys["alice"]),
    )
    assert muted.status_code == 200, muted.json()

    # bob tries to unmute himself — privileged, denied
    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}",
        json={"muted": False},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_owner_can_set_speaker_policy(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")

    resp = client.patch(
        "/api/teams/alice-team/speaker-policy",
        json={"speaker_policy": "admin_only"},
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["speaker_policy"] == "admin_only"
    assert resp.json()["team"]["speaker_policy"] == "admin_only"


def test_member_cannot_set_speaker_policy(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        "/api/teams/alice-team/speaker-policy",
        json={"speaker_policy": "admin_only"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── delegation (twin/hosted): the bound person's OWN opt-in only ────
#
# The inverse of mute: an admin must NOT be able to bind a twin or host
# to someone else (that would be impersonation). Only the participant
# themselves can set their own speaking delegation.


def test_participant_sets_own_delegation(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}/delegation",
        json={"speak_mode": "hosted", "host_id": "alice"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200, resp.json()
    assert resp.json()["participant"]["speak_mode"] == "hosted"
    assert resp.json()["participant"]["host_id"] == "alice"


def test_owner_cannot_impose_delegation(tmp_path: Path) -> None:
    """The critical impersonation guard: even the owner cannot bind a
    twin/host onto another participant."""
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}/delegation",
        json={"speak_mode": "twin", "twin_agent_id": "alice-puppet"},
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 403
    assert "themselves" in resp.json()["detail"]


def test_member_cannot_set_others_delegation(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    _invite_and_join(client, keys, "alice-team", "alice", "bob")
    carol_pid = _invite_and_join(client, keys, "alice-team", "alice", "carol")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{carol_pid}/delegation",
        json={"speak_mode": "hosted", "host_id": "bob"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_hosted_mode_requires_host_id(tmp_path: Path) -> None:
    client, keys = _build_app(tmp_path)
    _create_team(client, keys, "alice-team", owner="alice")
    bob_pid = _invite_and_join(client, keys, "alice-team", "alice", "bob")

    resp = client.patch(
        f"/api/teams/alice-team/participants/{bob_pid}/delegation",
        json={"speak_mode": "hosted"},  # missing host_id
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 400
