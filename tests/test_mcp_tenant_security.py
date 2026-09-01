"""Cross-tenant regression tests for runtime MCP state and capabilities."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.adapters.mcp_client import oauth
from runtime.adapters.mcp_client.trust import get_trust_store, reset_trust_store_for_tests
from runtime.execution.suckers import Skill, SkillRegistry
from runtime.platform.io.lease import LeaseConflictError, LeaseStore
from runtime.platform.process.session import Session, session_scope
from runtime.safety.auth import Identity, IdentityStore
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway.mcp_router import create_mcp_router


def test_skill_registry_hides_tenant_owned_mcp_skills() -> None:
    registry = SkillRegistry()
    registry.register(
        Skill(
            name="mcp_tenant_a_read",
            trusted_source="mcp://server/read",
            tenant_id="tenant-a",
            handler=lambda: "a",
        ),
    )

    with session_scope(Session(metadata={"tenant_id": "tenant-a"})):
        assert registry.has("mcp_tenant_a_read")
        assert registry.all_names() == ["mcp_tenant_a_read"]

    with session_scope(Session(metadata={"tenant_id": "tenant-b"})):
        assert not registry.has("mcp_tenant_a_read")
        assert "mcp_tenant_a_read" not in registry.all_names()


def test_trust_and_oauth_stores_are_tenant_partitioned(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("ECHO_HOME", str(tmp_path))
    reset_trust_store_for_tests()
    oauth.reset_oauth_store_for_tests()

    trust_a = get_trust_store("tenant-a")
    trust_b = get_trust_store("tenant-b")
    trust_a.approve("shared-server", ["read"])
    assert trust_a.is_approved("shared-server", ["read"])
    assert not trust_b.is_approved("shared-server", ["read"])
    assert trust_b.list_all() == []

    oauth_a = oauth.get_oauth_store("tenant-a")
    oauth_b = oauth.get_oauth_store("tenant-b")
    oauth_a.save_tokens(
        "shared-server",
        {"access_token": "tenant-a-token", "expires_in": 3600},
        token_url="https://auth.example.com/token",
        client_id="client-a",
    )
    assert oauth_a.bearer("shared-server") == "tenant-a-token"
    assert oauth_b.bearer("shared-server") is None

    state = oauth_a.start_pending(
        server="shared-server",
        code_verifier="v",
        redirect_uri="https://echo.example/callback",
        token_url="https://auth.example.com/token",
        client_id="client-a",
    )
    assert oauth.get_oauth_store_for_state(state).pop_pending(state) is not None
    assert oauth_b.pop_pending(state) is None


def test_mcp_trust_route_does_not_cross_tenants() -> None:
    identities = IdentityStore()
    identities.add(
        Identity(actor_id="alice", roles=("operator",), metadata={"tenant_id": "tenant-a"}),
        api_key_plaintext="sk-a",
    )
    identities.add(
        Identity(actor_id="bob", roles=("operator",), metadata={"tenant_id": "tenant-b"}),
        api_key_plaintext="sk-b",
    )
    app = FastAPI()
    app.include_router(
        create_mcp_router(
            registry=SkillRegistry(),
            identity_store=identities,
            require_auth=True,
        ).router,
    )
    client = TestClient(app)

    assert (
        client.post(
            "/api/mcp/trust",
            headers={"Authorization": "Bearer sk-a"},
            json={"server_name": "shared-server", "tool_names": ["read"]},
        ).status_code
        == 200
    )
    assert (
        client.get(
            "/api/mcp/trust",
            headers={"Authorization": "Bearer sk-b"},
        ).json()["entries"]
        == []
    )
    assert (
        client.get(
            "/api/mcp/trust",
            headers={"Authorization": "Bearer sk-a"},
        ).json()["entries"][0]["server_name"]
        == "shared-server"
    )


def test_scoped_lease_conflict_query_does_not_miss_same_tenant_row(tmp_path: Path) -> None:
    store = LeaseStore(db_path=tmp_path / "leases.db")
    tenant_a = TenantScope(tenant_id="tenant-a", actor_id="alice")
    tenant_b = TenantScope(tenant_id="tenant-b", actor_id="bob")

    first = store.with_scope(tenant_a).acquire("workspace", "same.txt", "alice")
    # Different tenants may independently lease their own namespace.
    store.with_scope(tenant_b).acquire("workspace", "same.txt", "bob")
    try:
        store.with_scope(tenant_a).acquire("workspace", "same.txt", "carol")
    except LeaseConflictError as exc:
        assert exc.lease.tenant_id == "tenant-a"
        assert exc.lease.lease_id == first.lease_id
    else:  # pragma: no cover - regression guard
        raise AssertionError("same-tenant lease conflict was missed")

