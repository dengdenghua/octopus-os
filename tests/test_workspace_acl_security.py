"""Alice/Bob workspace isolation regression tests."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform import feature_flags as ff
from runtime.platform.io.lease import LeaseStore
from runtime.safety.auth import Identity, IdentityStore
from runtime.safety.auth.scope import TenantScope
from runtime.sensing.gateway.workspace_api_router import create_workspace_api_router
from runtime.sensing.server.mount_backend import LocalMountBackend, MountBackendRegistry
from runtime.workspace import WorkspaceStore


def _client(tmp_path: Path) -> tuple[TestClient, WorkspaceStore, IdentityStore]:
    store = WorkspaceStore(db_path=tmp_path / "workspaces.db")
    identity_store = IdentityStore()
    identity_store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
    identity_store.add(Identity(actor_id="bob"), api_key_plaintext="sk-bob")
    registry = MountBackendRegistry()
    registry.register("local", LocalMountBackend)
    app = FastAPI()
    app.include_router(
        create_workspace_api_router(
            workspace_store=store,
            lease_store=LeaseStore(db_path=tmp_path / "leases.db"),
            registry=registry,
            identity_store=identity_store,
            require_auth=True,
        )
    )
    return TestClient(app), store, identity_store


def test_workspace_acl_uses_principal_and_not_body_identity(tmp_path: Path, monkeypatch) -> None:
    original_specs = dict(ff._SPECS)
    original_snapshot = ff._SNAPSHOT
    original_file = ff._FILE_PATH
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_WORKSPACE", "1")
    ff.reload()
    try:
        client, store, _ = _client(tmp_path)
        alice = {"Authorization": "Bearer sk-alice"}
        bob = {"Authorization": "Bearer sk-bob"}
        mount = tmp_path / "mount"
        mount.mkdir()

        created = client.post(
            "/api/workspaces",
            headers=alice,
            json={
                "name": "alice-ws",
                "mount_type": "local",
                "mount_target": str(mount),
                "owner_id": "alice",
            },
        )
        assert created.status_code == 200
        workspace_id = created.json()["workspace"]["id"]

        assert client.get(f"/api/workspaces/{workspace_id}", headers=alice).status_code == 200
        assert client.get(f"/api/workspaces/{workspace_id}", headers=bob).status_code == 404
        assert client.get("/api/workspaces", headers=alice).json()["workspaces"]
        assert client.get("/api/workspaces", headers=bob).json()["workspaces"] == []
        assert client.get("/api/workspaces?user_id=alice", headers=bob).status_code == 403

        spoofed = client.post(
            "/api/workspaces",
            headers=alice,
            json={
                "name": "spoofed",
                "mount_type": "local",
                "mount_target": str(mount),
                "owner_id": "bob",
            },
        )
        assert spoofed.status_code == 403

        store.add_member(workspace_id, "bob", role="viewer")
        assert client.get(f"/api/workspaces/{workspace_id}", headers=bob).status_code == 200
        assert (
            client.post(
                f"/api/workspaces/{workspace_id}/members",
                headers=bob,
                json={"member_id": "mallory", "role": "viewer"},
            ).status_code
            == 403
        )
        assert client.delete(f"/api/workspaces/{workspace_id}", headers=bob).status_code == 403
        assert (
            client.post(
                f"/api/workspaces/{workspace_id}/lease",
                headers=bob,
                json={"file_path": "x", "holder_id": "bob"},
            ).status_code
            == 403
        )
    finally:
        ff._SPECS.clear()
        ff._SPECS.update(original_specs)
        ff._SNAPSHOT = original_snapshot
        ff._FILE_PATH = original_file


def test_workspace_and_lease_store_views_enforce_tenant_scope(tmp_path: Path) -> None:
    alice_scope = TenantScope(tenant_id="tenant-a", actor_id="alice")
    bob_scope = TenantScope(tenant_id="tenant-b", actor_id="bob")
    workspace_store = WorkspaceStore(db_path=tmp_path / "workspaces.db")
    alice_store = workspace_store.with_scope(alice_scope)
    bob_store = workspace_store.with_scope(bob_scope)
    ws = alice_store.create_workspace(
        name="scoped",
        mount_type="local",
        mount_target=str(tmp_path),
        owner_id="alice",
    )
    assert bob_store.get_workspace(ws.id) is None
    assert bob_store.list_workspaces() == []
    assert bob_store.list_members(ws.id) == []
    assert bob_store.delete_workspace(ws.id) is False

    lease_store = LeaseStore(db_path=tmp_path / "leases.db")
    lease = lease_store.with_scope(alice_scope).acquire(ws.id, "a.txt", "alice")
    bob_leases = lease_store.with_scope(bob_scope)
    assert bob_leases.list_active() == []
    assert bob_leases.release(lease.lease_id) is False


def test_workspace_acl_rejects_cross_tenant_membership(tmp_path: Path, monkeypatch) -> None:
    original_specs = dict(ff._SPECS)
    original_snapshot = ff._SNAPSHOT
    original_file = ff._FILE_PATH
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_WORKSPACE", "1")
    ff.reload()
    try:
        store = WorkspaceStore(db_path=tmp_path / "workspaces.db")
        identities = IdentityStore()
        identities.add(
            Identity(actor_id="alice", metadata={"tenant_id": "tenant-a"}),
            api_key_plaintext="sk-alice",
        )
        identities.add(
            Identity(actor_id="carol", metadata={"tenant_id": "tenant-a"}),
            api_key_plaintext="sk-carol",
        )
        identities.add(
            Identity(actor_id="bob", metadata={"tenant_id": "tenant-b"}),
            api_key_plaintext="sk-bob",
        )
        registry = MountBackendRegistry()
        registry.register("local", LocalMountBackend)
        app = FastAPI()
        app.include_router(
            create_workspace_api_router(
                workspace_store=store,
                lease_store=LeaseStore(db_path=tmp_path / "leases.db"),
                registry=registry,
                identity_store=identities,
                require_auth=True,
            )
        )
        client = TestClient(app)
        mount = tmp_path / "mount"
        mount.mkdir()
        created = client.post(
            "/api/workspaces",
            headers={"Authorization": "Bearer sk-alice"},
            json={
                "name": "tenant-a-ws",
                "mount_type": "local",
                "mount_target": str(mount),
                "owner_id": "alice",
            },
        )
        assert created.status_code == 200
        workspace_id = created.json()["workspace"]["id"]
        store.add_member(workspace_id, "carol", role="viewer")
        store.add_member(workspace_id, "bob", role="viewer")

        assert (
            client.get(
                f"/api/workspaces/{workspace_id}",
                headers={"Authorization": "Bearer sk-carol"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/workspaces/{workspace_id}",
                headers={"Authorization": "Bearer sk-bob"},
            ).status_code
            == 404
        )
    finally:
        ff._SPECS.clear()
        ff._SPECS.update(original_specs)
        ff._SNAPSHOT = original_snapshot
        ff._FILE_PATH = original_file

