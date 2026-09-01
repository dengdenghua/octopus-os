"""End-to-end tests for the remote-workspace collaboration feature.

Covers Task 13.1 — integrates every backend module that participates in
multi-user remote workspace collaboration:

  * ``runtime.workspace.store.WorkspaceStore``      — workspace + members
  * ``runtime.platform.io.lease.LeaseStore``        — file leases with TTL
  * ``runtime.sensing.server.mount_backend``        — LocalMountBackend + Registry
  * ``runtime.sensing.gateway.fs_router``           — remote FS + ACL + lease gate
  * ``runtime.workspace.cowork_bridge``             — role → grant mapping

Most tests operate at the business-logic layer (direct Store/Backend
calls, no HTTP). The ACL enforcement tests (``test_acl_*``) and the
lease-conflict-via-router test use ``TestClient`` because role checks
and holder-id checks live in the router middleware, not in the stores.

Uses ``tmp_path`` for SQLite DBs and the mount root so every test
starts from a clean slate. ``LocalMountBackend`` is the only backend
exercised — no real NAS / SFTP / S3 connectivity required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.group import ContextGrant
from runtime.platform.io.lease import (
    LeaseConflictError,
    LeaseStore,
)
from runtime.sensing.gateway.fs_router import create_fs_router
from runtime.sensing.server.mount_backend import (
    LocalMountBackend,
    MountBackendRegistry,
)
from runtime.workspace import WorkspaceStore
from runtime.workspace import crypto as crypto_mod
from runtime.workspace.cowork_bridge import grant_for_workspace_role

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_crypto_cache() -> None:
    """Reset the workspace crypto module cache before/after each test.

    Mirrors the fixture in ``test_workspace_store.py`` so per-test
    env-var changes (``ECHO_WORKSPACE_KEY``) don't leak across tests.
    """
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None
    yield
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None


@pytest.fixture
def workspace_store(tmp_path: Path) -> WorkspaceStore:
    return WorkspaceStore(db_path=tmp_path / "workspaces.db")


@pytest.fixture
def lease_store(tmp_path: Path) -> LeaseStore:
    return LeaseStore(db_path=tmp_path / "file_leases.db")


@pytest.fixture
def mount_root(tmp_path: Path) -> Path:
    """Real filesystem root for ``LocalMountBackend``."""
    root = tmp_path / "mount_root"
    root.mkdir()
    return root


@pytest.fixture
def registry() -> MountBackendRegistry:
    """Fresh registry pre-populated with the local backend only.

    Using a private registry (instead of ``default_registry``) prevents
    cross-test cache leakage — same pattern as ``test_mount_backend.py``.
    """
    reg = MountBackendRegistry()
    reg.register("local", LocalMountBackend)
    return reg


def _wire_router(
    workspace_store: WorkspaceStore,
    lease_store: LeaseStore,
    registry: MountBackendRegistry,
) -> TestClient:
    """Build a ``TestClient`` with the FS router wired to the given stores.

    Used by the ACL / lease-conflict tests that need to exercise the
    router-level role + holder-id checks.
    """
    app = FastAPI()
    app.include_router(
        create_fs_router(
            workspace_store=workspace_store,
            lease_store=lease_store,
            mount_registry=registry,
        )
    )
    return TestClient(app)


def _seed_local_workspace(
    store: WorkspaceStore,
    mount_root: Path,
    *,
    workspace_id: str = "ws-e2e",
    owner_id: str = "alice",
    name: str = "E2E Project",
) -> str:
    """Create a local-mount workspace + seed the registry cache.

    Returns the workspace id. The registry's ``_instances`` cache is
    pre-seeded with a real ``LocalMountBackend`` so the router doesn't
    try to instantiate one itself (keeps the test deterministic).
    """
    store.create_workspace(
        name=name,
        mount_type="local",
        mount_target=str(mount_root),
        mount_options={},
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    return workspace_id


# ═══════════════════════════════════════════════════════════
# 1. Workspace + mount registration
# ═══════════════════════════════════════════════════════════


def test_register_workspace_and_mount(
    workspace_store: WorkspaceStore,
    registry: MountBackendRegistry,
    mount_root: Path,
) -> None:
    """Register a local-mount workspace and verify the mount is accessible."""
    ws = workspace_store.create_workspace(
        name="Project NAS",
        mount_type="local",
        mount_target=str(mount_root),
        owner_id="alice",
    )
    assert ws.id
    assert ws.mount_type == "local"
    assert ws.mount_target == str(mount_root)
    assert ws.owner_id == "alice"

    # The registry can build a backend for this workspace.
    backend = registry.get_or_create(ws.id, ws.mount_type, ws.mount_target, ws.mount_options)
    assert isinstance(backend, LocalMountBackend)
    assert backend.root_path == mount_root.resolve()

    # The backend is cached per workspace (same instance on second call).
    again = registry.get_or_create(ws.id, ws.mount_type, ws.mount_target, ws.mount_options)
    assert again is backend

    # The mount is real — a file written through the backend lands on disk.
    import asyncio

    asyncio.run(backend.write_file("README.md", b"hello"))
    assert (mount_root / "README.md").read_bytes() == b"hello"


# ═══════════════════════════════════════════════════════════
# 2. Member management
# ═══════════════════════════════════════════════════════════


def test_add_members_with_roles(workspace_store: WorkspaceStore) -> None:
    """Add members with different roles and verify via ``get_member_role``."""
    ws = workspace_store.create_workspace(
        name="Team WS",
        mount_type="local",
        mount_target="/tmp/x",
        owner_id="alice",
    )
    # Owner is auto-added with role=owner.
    assert workspace_store.get_member_role(ws.id, "alice") == "owner"

    # Add an editor and a viewer.
    workspace_store.add_member(ws.id, "bob", role="editor")
    workspace_store.add_member(ws.id, "carol", role="viewer")

    assert workspace_store.get_member_role(ws.id, "bob") == "editor"
    assert workspace_store.get_member_role(ws.id, "carol") == "viewer"

    members = workspace_store.list_members(ws.id)
    member_ids = {m.member_id for m in members}
    assert member_ids == {"alice", "bob", "carol"}

    # Role upsert: promote carol from viewer to editor.
    workspace_store.add_member(ws.id, "carol", role="editor")
    assert workspace_store.get_member_role(ws.id, "carol") == "editor"
    # No duplicate row.
    assert len(workspace_store.list_members(ws.id)) == 3


# ═══════════════════════════════════════════════════════════
# 3. File lease — acquire + conflict
# ═══════════════════════════════════════════════════════════


def test_file_lease_acquire_and_conflict(lease_store: LeaseStore) -> None:
    """Acquire a file lease; a second holder gets ``LeaseConflictError``."""
    # Alice acquires an exclusive lease.
    lease = lease_store.acquire("ws-1", "config.yaml", "alice", ttl_seconds=60)
    assert lease.holder_id == "alice"
    assert lease.file_path == "config.yaml"
    assert lease.workspace_id == "ws-1"
    assert lease.kind == "exclusive"
    assert lease.expires_at > lease.acquired_at

    # Bob tries to acquire the same file → conflict.
    with pytest.raises(LeaseConflictError) as exc_info:
        lease_store.acquire("ws-1", "config.yaml", "bob", ttl_seconds=60)
    conflict = exc_info.value
    assert conflict.lease.holder_id == "alice"
    assert conflict.lease.file_path == "config.yaml"

    # Different workspace: no conflict (leases are per-workspace).
    other = lease_store.acquire("ws-2", "config.yaml", "bob", ttl_seconds=60)
    assert other.holder_id == "bob"

    # Same holder renews in place (no conflict, same lease_id).
    renewed = lease_store.acquire("ws-1", "config.yaml", "alice", ttl_seconds=120)
    assert renewed.lease_id == lease.lease_id
    assert renewed.expires_at >= lease.expires_at


# ═══════════════════════════════════════════════════════════
# 4. File write with lease
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_file_write_with_lease(lease_store: LeaseStore, mount_root: Path) -> None:
    """A holder with an active lease can write to the file via the backend."""
    backend = LocalMountBackend(mount_root)
    lease = lease_store.acquire("ws-1", "config.yaml", "alice", ttl_seconds=60)

    # Alice writes while holding the lease.
    await backend.write_file("config.yaml", b"version: 1.0\n")

    # The file content is persisted on disk.
    data = await backend.read_file("config.yaml")
    assert data == b"version: 1.0\n"
    assert (mount_root / "config.yaml").read_bytes() == b"version: 1.0\n"

    # The lease is still active (write doesn't release it).
    active = lease_store.get_by_path("ws-1", "config.yaml")
    assert active is not None
    assert active.holder_id == lease.holder_id == "alice"


# ═══════════════════════════════════════════════════════════
# 5. File write blocked when another holder owns the lease
# ═══════════════════════════════════════════════════════════


def test_file_write_blocked_without_lease(
    tmp_path: Path,
    workspace_store: WorkspaceStore,
    lease_store: LeaseStore,
    registry: MountBackendRegistry,
    mount_root: Path,
) -> None:
    """When another holder owns an exclusive lease, the router blocks writes (409).

    The lease gate lives in ``fs_router``: if ``holder_id`` is supplied
    and a different holder owns the lease, the write is rejected with
    ``lease_conflict`` before the backend is touched.
    """
    ws_id = _seed_local_workspace(
        workspace_store, mount_root, workspace_id="ws-block", owner_id="alice"
    )
    workspace_store.add_member(ws_id, "bob", role="editor")
    backend = LocalMountBackend(mount_root)
    registry._instances[ws_id] = backend

    # Bob acquires an exclusive lease on config.yaml.
    lease_store.acquire(ws_id, "config.yaml", "bob", ttl_seconds=600)

    client = _wire_router(workspace_store, lease_store, registry)

    # Alice tries to write with holder_id=alice → 409 conflict.
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:config.yaml",
            "content": "alice-version",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "lease_conflict"
    assert detail["holder_id"] == "bob"

    # The file was NOT written (backend.write_file was never called).
    assert not (mount_root / "config.yaml").exists()


# ═══════════════════════════════════════════════════════════
# 6. Lease release + reacquire
# ═══════════════════════════════════════════════════════════


def test_lease_release_and_reacquire(lease_store: LeaseStore) -> None:
    """Release a lease; another holder can then acquire it."""
    lease = lease_store.acquire("ws-1", "config.yaml", "alice", ttl_seconds=60)

    # Bob still can't acquire while Alice holds the lease.
    with pytest.raises(LeaseConflictError):
        lease_store.acquire("ws-1", "config.yaml", "bob", ttl_seconds=60)

    # Alice releases.
    assert lease_store.release(lease.lease_id) is True
    # Releasing an already-released lease returns False (idempotent).
    assert lease_store.release(lease.lease_id) is False
    assert lease_store.get_by_path("ws-1", "config.yaml") is None

    # Now Bob can acquire a fresh lease.
    bob_lease = lease_store.acquire("ws-1", "config.yaml", "bob", ttl_seconds=60)
    assert bob_lease.holder_id == "bob"
    assert bob_lease.lease_id != lease.lease_id  # new lease, not a renewal


# ═══════════════════════════════════════════════════════════
# 7. ACL — viewer cannot write
# ═══════════════════════════════════════════════════════════


def test_acl_viewer_cannot_write(
    tmp_path: Path,
    workspace_store: WorkspaceStore,
    registry: MountBackendRegistry,
    mount_root: Path,
) -> None:
    """Viewer role is denied write operations (router-level ACL, 403)."""
    ws_id = _seed_local_workspace(
        workspace_store, mount_root, workspace_id="ws-acl-v", owner_id="alice"
    )
    workspace_store.add_member(ws_id, "dave", role="viewer")
    backend = LocalMountBackend(mount_root)
    registry._instances[ws_id] = backend

    lease_store = LeaseStore(db_path=tmp_path / "leases-v.db")
    client = _wire_router(workspace_store, lease_store, registry)

    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:config.yaml",
            "content": "from-dave",
            "user_id": "dave",
            "holder_id": "dave",
        },
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "write_requires_editor"
    assert detail["role"] == "viewer"
    # The file was NOT written.
    assert not (mount_root / "config.yaml").exists()

    # Sanity: the viewer grant maps to summary (read-only) scope.
    assert grant_for_workspace_role("viewer") == ContextGrant(scope="summary")


# ═══════════════════════════════════════════════════════════
# 8. ACL — editor can write
# ═══════════════════════════════════════════════════════════


def test_acl_editor_can_write(
    tmp_path: Path,
    workspace_store: WorkspaceStore,
    registry: MountBackendRegistry,
    mount_root: Path,
) -> None:
    """Editor role is allowed write operations (router-level ACL, 200)."""
    ws_id = _seed_local_workspace(
        workspace_store, mount_root, workspace_id="ws-acl-e", owner_id="alice"
    )
    workspace_store.add_member(ws_id, "bob", role="editor")
    backend = LocalMountBackend(mount_root)
    registry._instances[ws_id] = backend

    lease_store = LeaseStore(db_path=tmp_path / "leases-e.db")
    client = _wire_router(workspace_store, lease_store, registry)

    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:config.yaml",
            "content": "from-bob",
            "user_id": "bob",
            "holder_id": "bob",
        },
    )
    assert r.status_code == 200, r.text
    # The file actually landed on disk.
    assert (mount_root / "config.yaml").read_bytes() == b"from-bob"

    # Sanity: the editor grant maps to full-history (all) scope.
    assert grant_for_workspace_role("editor") == ContextGrant(scope="all")


# ═══════════════════════════════════════════════════════════
# 9. Full collaboration flow
# ═══════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_collaboration_flow(tmp_path: Path) -> None:
    """Full collaboration: register mount → add members → edit → lease
    conflict → release → reacquire → merge → ACL check.

    Walks the entire collaboration lifecycle end-to-end, exercising
    every layer:

      1. WorkspaceStore + LeaseStore + MountBackendRegistry (wired via tmp_path)
      2. Register a local-mount workspace
      3. Add two members (Alice=owner, Bob=editor)
      4. Alice acquires a lease on ``config.yaml``
      5. Bob tries to acquire the same file → ``LeaseConflictError``
      6. Alice writes file content via the backend
      7. Alice releases the lease
      8. Bob acquires the lease + writes a new version
      9. Verify the file content is Bob's version
     10. Verify ACL: viewer role write is rejected (403) via the router
    """
    # ── 1. Create the stores + registry ──────────────────────────────────
    mount_root = tmp_path / "collab_mount"
    mount_root.mkdir()
    ws_store = WorkspaceStore(db_path=tmp_path / "collab_ws.db")
    lease_store = LeaseStore(db_path=tmp_path / "collab_leases.db")
    registry = MountBackendRegistry()
    registry.register("local", LocalMountBackend)

    # ── 2. Register a local-mount workspace ─────────────────────────────
    ws_id = _seed_local_workspace(
        ws_store,
        mount_root,
        workspace_id="ws-collab",
        owner_id="alice",
        name="Collab Project",
    )
    backend = registry.get_or_create(ws_id, "local", str(mount_root), {})
    assert isinstance(backend, LocalMountBackend)
    # Verify the mount is reachable.
    assert await backend.test_connection() is True

    # ── 3. Add members: Alice (owner, auto), Bob (editor) ───────────────
    ws_store.add_member(ws_id, "bob", role="editor")
    assert ws_store.get_member_role(ws_id, "alice") == "owner"
    assert ws_store.get_member_role(ws_id, "bob") == "editor"
    members = ws_store.list_members(ws_id)
    assert {m.member_id for m in members} == {"alice", "bob"}

    # ── 4. Alice acquires a lease on config.yaml ────────────────────────
    lease = lease_store.acquire(ws_id, "config.yaml", "alice", ttl_seconds=600)
    assert lease.holder_id == "alice"
    assert lease_store.get_by_path(ws_id, "config.yaml").holder_id == "alice"

    # ── 5. Bob tries to acquire the same file → LeaseConflictError ──────
    with pytest.raises(LeaseConflictError) as exc_info:
        lease_store.acquire(ws_id, "config.yaml", "bob", ttl_seconds=600)
    assert exc_info.value.lease.holder_id == "alice"

    # ── 6. Alice writes file content while holding the lease ────────────
    await backend.write_file("config.yaml", b"version: 1.0\nauthor: alice")
    data = await backend.read_file("config.yaml")
    assert data == b"version: 1.0\nauthor: alice"

    # ── 7. Alice releases the lease ─────────────────────────────────────
    assert lease_store.release(lease.lease_id) is True
    assert lease_store.get_by_path(ws_id, "config.yaml") is None

    # ── 8. Bob acquires the lease + writes new content ──────────────────
    bob_lease = lease_store.acquire(ws_id, "config.yaml", "bob", ttl_seconds=600)
    assert bob_lease.holder_id == "bob"
    await backend.write_file("config.yaml", b"version: 2.0\nauthor: bob")
    lease_store.release(bob_lease.lease_id)

    # ── 9. Verify the file content is Bob's version ─────────────────────
    final = await backend.read_file("config.yaml")
    assert final == b"version: 2.0\nauthor: bob"
    assert (mount_root / "config.yaml").read_bytes() == b"version: 2.0\nauthor: bob"

    # ── 10. Verify ACL: viewer role write is rejected ───────────────────
    ws_store.add_member(ws_id, "dave", role="viewer")
    client = _wire_router(ws_store, lease_store, registry)
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:config.yaml",
            "content": "from-viewer",
            "user_id": "dave",
            "holder_id": "dave",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "write_requires_editor"
    # The file is still Bob's version (viewer write was blocked).
    assert (mount_root / "config.yaml").read_bytes() == b"version: 2.0\nauthor: bob"

