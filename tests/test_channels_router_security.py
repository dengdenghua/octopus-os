"""Security regression tests for channels_router admin enforcement.

Channels manage shared IM credentials, agent assignments, and pairing
approvals — all global server state. Before this fix, any authenticated
user could rotate credentials, reassign agents, or approve external
pairings. Now all mutations require admin role.

See channels_router.py ``_require_admin`` + system_router pattern.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from runtime.safety.auth.identity import Identity, IdentityStore  # noqa: E402
from runtime.sensing.gateway.channels_router import (  # noqa: E402
    LocalChannelManager,
    create_channels_router,
)


def _build_app() -> tuple[TestClient, dict[str, str]]:
    """Build app with require_auth=True + 3 identities (alice=admin, bob=user, carol=user)."""
    store = IdentityStore()
    keys: dict[str, str] = {}

    # alice is admin
    alice = Identity(actor_id="alice", roles=["admin"])
    api_key_alice = "sk-test-alice"
    store.add(alice, api_key_plaintext=api_key_alice)
    keys["alice"] = api_key_alice

    # bob and carol are regular users
    for actor in ("bob", "carol"):
        api_key = f"sk-test-{actor}"
        store.add(Identity(actor_id=actor), api_key_plaintext=api_key)
        keys[actor] = api_key

    manager = LocalChannelManager()
    app = FastAPI()
    app.include_router(
        create_channels_router(
            manager=manager,
            identity_store=store,
            require_auth=True,
        )
    )
    return TestClient(app), keys


def _bearer(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}


# ── Read endpoints: authenticated but actor-agnostic ───────────────


def test_list_channels_allows_any_authenticated() -> None:
    """GET /api/channels is actor-agnostic (returns global registry)."""
    client, keys = _build_app()

    # bob (non-admin) can list channels
    resp = client.get("/api/channels", headers=_bearer(keys["bob"]))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_channel_assignment_allows_any_authenticated() -> None:
    """GET /api/channels/{id}/assistant is actor-agnostic."""
    client, keys = _build_app()

    resp = client.get(
        "/api/channels/test_channel/assistant",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200


def test_list_credentials_allows_any_authenticated() -> None:
    """GET /api/channels/credentials is actor-agnostic (credentials are masked)."""
    client, keys = _build_app()

    resp = client.get("/api/channels/credentials", headers=_bearer(keys["bob"]))
    assert resp.status_code == 200


def test_list_pairings_allows_any_authenticated() -> None:
    """GET /api/channels/{id}/pairings is actor-agnostic."""
    client, keys = _build_app()

    resp = client.get(
        "/api/channels/test_channel/pairings",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 200


def test_channels_detail_allows_any_authenticated() -> None:
    """GET /api/channels/detail is actor-agnostic."""
    client, keys = _build_app()

    resp = client.get("/api/channels/detail", headers=_bearer(keys["bob"]))
    assert resp.status_code == 200


# ── Mutation endpoints: require admin ──────────────────────────────


def test_set_channel_assignment_blocks_non_admin() -> None:
    """POST /api/channels/{id}/assistant requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/test_channel/assistant",
        json={"agent_id": "malicious_agent"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403
    assert "admin role required" in resp.json()["detail"]


def test_set_channel_assignment_allows_admin() -> None:
    """Admin can set channel assignment."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/test_channel/assistant",
        json={"agent_id": "legit_agent"},
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200
    assert resp.json()["agent_id"] == "legit_agent"


def test_delete_channel_assignment_blocks_non_admin() -> None:
    """DELETE /api/channels/{id}/assistant requires admin."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/channels/test_channel/assistant",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_delete_channel_assignment_allows_admin() -> None:
    """Admin can delete channel assignment."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/channels/test_channel/assistant",
        headers=_bearer(keys["alice"]),
    )
    assert resp.status_code == 200


def test_set_credentials_blocks_non_admin() -> None:
    """POST /api/channels/credentials/{platform} requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/credentials/discord",
        json={"token": "malicious_token"},
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_delete_credentials_blocks_non_admin() -> None:
    """DELETE /api/channels/credentials/{platform} requires admin."""
    client, keys = _build_app()

    resp = client.delete(
        "/api/channels/credentials/discord",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_wechat_qr_start_blocks_non_admin() -> None:
    """POST /api/channels/wechat/qr/start requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/wechat/qr/start",
        headers=_bearer(keys["bob"]),
    )
    # Might 404 if wechat not configured, but should NOT 200 for bob
    assert resp.status_code in (403, 404)


def test_wechat_qr_poll_blocks_non_admin() -> None:
    """POST /api/channels/wechat/qr/poll requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/wechat/qr/poll",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code in (403, 404)


def test_approve_pairing_blocks_non_admin() -> None:
    """POST /api/channels/pairing/{id}/approve requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/pairing/fake_pairing_id/approve",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


def test_reject_pairing_blocks_non_admin() -> None:
    """POST /api/channels/pairing/{id}/reject requires admin."""
    client, keys = _build_app()

    resp = client.post(
        "/api/channels/pairing/fake_pairing_id/reject",
        headers=_bearer(keys["bob"]),
    )
    assert resp.status_code == 403


# ── inbound webhook: no auth (IM platforms don't send tokens) ──────


def test_inbound_webhook_no_auth_required() -> None:
    """POST /api/channels/{id}/inbound accepts requests without bearer token.

    IM platforms (Discord/Slack/WeChat) authenticate via signature
    headers, not bearer tokens. The endpoint must not 401 on missing
    Authorization header — that would break all webhooks when
    require_auth=True.
    """
    client, keys = _build_app()

    # No Authorization header at all
    resp = client.post(
        "/api/channels/fake_channel/inbound",
        json={"test": "webhook"},
    )
    # Should 404 (channel not found) NOT 401 (auth required)
    assert resp.status_code == 404


# ── unauthenticated callers are blocked (except inbound) ───────────


def test_no_auth_token_returns_401() -> None:
    """All non-webhook endpoints require authentication."""
    client, keys = _build_app()

    resp = client.get("/api/channels")
    assert resp.status_code == 401


# ── dev mode: require_auth=False bypasses admin checks ─────────────


def test_dev_mode_bypasses_admin_checks() -> None:
    """When require_auth=False, non-admins can mutate channels."""
    store = IdentityStore()
    manager = LocalChannelManager()
    app = FastAPI()
    app.include_router(
        create_channels_router(
            manager=manager,
            identity_store=store,
            require_auth=False,
        )
    )
    client = TestClient(app)

    # No auth header — should still work in dev mode
    resp = client.post(
        "/api/channels/test_channel/assistant",
        json={"agent_id": "dev_agent"},
    )
    assert resp.status_code == 200
