"""Tests for ``runtime.sensing.gateway.workspace_api_router``.

Covers:
  - Workspace CRUD (create / list / get / delete)
  - Mount-type validation + ``test_connection`` failure → 400
  - Member management (list / add / remove, role validation, 404s)
  - File lease acquire / release / renew / list (incl. 409 conflict)
  - Health endpoint
  - Feature flag gating (403 when ``ui.remote_workspace`` is off)
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform import feature_flags as ff
from runtime.platform.io.lease import LeaseStore
from runtime.sensing.gateway.workspace_api_router import (
    create_workspace_api_router,
)
from runtime.sensing.server.mount_backend import (
    LocalMountBackend,
    MountBackendRegistry,
)
from runtime.workspace import WorkspaceStore

# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_flags() -> Iterator[None]:
    """Snapshot/restore the feature-flag registry around each test."""
    original = dict(ff._SPECS)
    original_snapshot = ff._SNAPSHOT
    original_file = ff._FILE_PATH
    yield
    ff._SPECS.clear()
    ff._SPECS.update(original)
    ff._SNAPSHOT = original_snapshot
    ff._FILE_PATH = original_file


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Default the flag to ON for the duration of each test.

    Tests that need the flag OFF call ``_flag_off`` inline.
    """
    monkeypatch.setenv("ECHO_FF_UI_REMOTE_WORKSPACE", "1")
    ff.reload()
    yield
    monkeypatch.delenv("ECHO_FF_UI_REMOTE_WORKSPACE", raising=False)
    ff.reload()


def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ECHO_FF_UI_REMOTE_WORKSPACE", raising=False)
    ff.reload()


def _fresh_registry() -> MountBackendRegistry:
    """Fresh registry with only the local backend registered.

    Using a fresh registry per client avoids the module-level
    ``default_registry`` caching backends across tests.
    """
    r = MountBackendRegistry()
    r.register("local", LocalMountBackend)
    return r


def _client(
    tmp_path: Path,
    *,
    workspace_store: WorkspaceStore | None = None,
    lease_store: LeaseStore | None = None,
    registry: MountBackendRegistry | None = None,
) -> TestClient:
    store = workspace_store or WorkspaceStore(db_path=tmp_path / "workspaces.db")
    leases = lease_store or LeaseStore(db_path=tmp_path / "leases.db")
    reg = registry or _fresh_registry()
    app = FastAPI()
    app.include_router(
        create_workspace_api_router(
            workspace_store=store,
            lease_store=leases,
            registry=reg,
        )
    )
    return TestClient(app)


def _create_workspace(
    client: TestClient,
    *,
    mount_target: str | Path,
    name: str = "ws",
    owner_id: str = "alice",
    mount_type: str = "local",
    mount_options: dict | None = None,
) -> dict:
    r = client.post(
        "/api/workspaces",
        json={
            "name": name,
            "mount_type": mount_type,
            "mount_target": str(mount_target),
            "mount_options": mount_options or {},
            "owner_id": owner_id,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()["workspace"]


# ═══════════════════════════════════════════════════════════
# Workspace CRUD
# ═══════════════════════════════════════════════════════════


def test_create_workspace_with_local_mount(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    ws = _create_workspace(client=_client(tmp_path), mount_target=mount, name="my-ws")

    assert ws["id"]
    assert ws["name"] == "my-ws"
    assert ws["mount_type"] == "local"
    assert ws["mount_target"] == str(mount.resolve())
    assert ws["owner_id"] == "alice"
    assert ws["created_at"] > 0


def test_create_workspace_rejects_invalid_mount_type(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    r = client.post(
        "/api/workspaces",
        json={
            "name": "bad",
            "mount_type": "ftp",  # not in VALID_MOUNT_TYPES
            "mount_target": str(mount),
            "mount_options": {},
            "owner_id": "alice",
        },
    )
    assert r.status_code == 400
    assert "ftp" in r.text


def test_create_workspace_unreachable_mount_returns_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.post(
        "/api/workspaces",
        json={
            "name": "missing",
            "mount_type": "local",
            "mount_target": str(tmp_path / "does-not-exist"),
            "mount_options": {},
            "owner_id": "alice",
        },
    )
    assert r.status_code == 400
    body = r.json()
    detail = body["detail"]
    assert detail["error"] == "mount_unreachable"
    assert detail["mount_type"] == "local"


def test_create_workspace_owner_auto_added_as_member(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount, owner_id="alice")

    members = client.get(f"/api/workspaces/{ws['id']}/members").json()["members"]
    assert len(members) == 1
    assert members[0]["member_id"] == "alice"
    assert members[0]["role"] == "owner"


def test_list_workspaces_for_user(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    _create_workspace(client=client, mount_target=mount, name="a", owner_id="alice")
    _create_workspace(client=client, mount_target=mount, name="b", owner_id="bob")

    alice_ws = client.get("/api/workspaces?user_id=alice").json()["workspaces"]
    bob_ws = client.get("/api/workspaces?user_id=bob").json()["workspaces"]

    assert {w["name"] for w in alice_ws} == {"a"}
    assert {w["name"] for w in bob_ws} == {"b"}


def test_list_workspaces_all_when_no_user_id(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    _create_workspace(client=client, mount_target=mount, name="a", owner_id="alice")
    _create_workspace(client=client, mount_target=mount, name="b", owner_id="bob")

    all_ws = client.get("/api/workspaces").json()["workspaces"]
    assert {w["name"] for w in all_ws} == {"a", "b"}


def test_get_workspace_returns_details(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount, name="ws")

    r = client.get(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 200
    assert r.json()["workspace"]["id"] == ws["id"]


def test_get_workspace_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/workspaces/nope").status_code == 404


def test_delete_workspace_cascades_members(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount, name="ws")

    # Add another member so we can verify cascade
    client.post(
        f"/api/workspaces/{ws['id']}/members",
        json={"member_id": "carol", "role": "editor"},
    )

    r = client.delete(f"/api/workspaces/{ws['id']}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # Workspace is gone
    assert client.get(f"/api/workspaces/{ws['id']}").status_code == 404
    # Members listing also 404s (workspace gone)
    assert client.get(f"/api/workspaces/{ws['id']}/members").status_code == 404


def test_delete_workspace_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.delete("/api/workspaces/nope").status_code == 404


# ═══════════════════════════════════════════════════════════
# Members
# ═══════════════════════════════════════════════════════════


def test_add_member_with_role(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    r = client.post(
        f"/api/workspaces/{ws['id']}/members",
        json={"member_id": "bob", "role": "editor"},
    )
    assert r.status_code == 200
    member = r.json()["member"]
    assert member["member_id"] == "bob"
    assert member["role"] == "editor"


def test_add_member_invalid_role_returns_400(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    r = client.post(
        f"/api/workspaces/{ws['id']}/members",
        json={"member_id": "bob", "role": "admin"},
    )
    assert r.status_code == 400
    assert "admin" in r.text


def test_add_member_workspace_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    r = client.post(
        "/api/workspaces/nope/members",
        json={"member_id": "bob", "role": "viewer"},
    )
    assert r.status_code == 404


def test_list_members_includes_owner(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount, owner_id="alice")
    client.post(
        f"/api/workspaces/{ws['id']}/members",
        json={"member_id": "bob", "role": "viewer"},
    )

    members = client.get(f"/api/workspaces/{ws['id']}/members").json()["members"]
    by_id = {m["member_id"]: m for m in members}
    assert by_id["alice"]["role"] == "owner"
    assert by_id["bob"]["role"] == "viewer"


def test_remove_member(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)
    client.post(
        f"/api/workspaces/{ws['id']}/members",
        json={"member_id": "bob", "role": "viewer"},
    )

    r = client.delete(f"/api/workspaces/{ws['id']}/members/bob")
    assert r.status_code == 200
    members = client.get(f"/api/workspaces/{ws['id']}/members").json()["members"]
    assert all(m["member_id"] != "bob" for m in members)


def test_remove_member_404(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    r = client.delete(f"/api/workspaces/{ws['id']}/members/ghost")
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# File leases
# ═══════════════════════════════════════════════════════════


def test_acquire_lease_returns_lease(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    r = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={
            "file_path": "src/main.py",
            "holder_id": "alice",
            "ttl_seconds": 600,
        },
    )
    assert r.status_code == 200
    lease = r.json()["lease"]
    assert lease["lease_id"]
    assert lease["workspace_id"] == ws["id"]
    assert lease["file_path"] == "src/main.py"
    assert lease["holder_id"] == "alice"
    assert lease["kind"] == "exclusive"
    assert lease["expires_at"] > lease["acquired_at"]


def test_acquire_lease_conflict_returns_409(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    first = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "doc.md", "holder_id": "alice", "ttl_seconds": 600},
    )
    assert first.status_code == 200

    second = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "doc.md", "holder_id": "bob", "ttl_seconds": 600},
    )
    assert second.status_code == 409
    body = second.json()
    detail = body["detail"]
    assert detail["error"] == "lease_conflict"
    assert detail["holder_id"] == "alice"
    assert detail["file_path"] == "doc.md"
    assert "conflict" in detail


def test_acquire_lease_same_holder_renews_in_place(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    first = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "doc.md", "holder_id": "alice", "ttl_seconds": 60},
    ).json()["lease"]
    second = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "doc.md", "holder_id": "alice", "ttl_seconds": 600},
    ).json()["lease"]

    assert second["lease_id"] == first["lease_id"]
    assert second["expires_at"] >= first["expires_at"]


def test_release_lease(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)
    lease = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "f.txt", "holder_id": "alice"},
    ).json()["lease"]

    r = client.delete(f"/api/workspaces/{ws['id']}/lease/{lease['lease_id']}")
    assert r.status_code == 200
    assert client.get(f"/api/workspaces/{ws['id']}/leases").json()["leases"] == []


def test_release_lease_404(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)
    r = client.delete(f"/api/workspaces/{ws['id']}/lease/nope")
    assert r.status_code == 404


def test_renew_lease_extends_expiry(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)
    lease = client.post(
        f"/api/workspaces/{ws['id']}/lease",
        json={"file_path": "f.txt", "holder_id": "alice", "ttl_seconds": 60},
    ).json()["lease"]

    r = client.post(
        f"/api/workspaces/{ws['id']}/lease/{lease['lease_id']}/renew",
        json={"ttl_seconds": 3600},
    )
    assert r.status_code == 200
    renewed = r.json()["lease"]
    assert renewed["lease_id"] == lease["lease_id"]
    assert renewed["expires_at"] > lease["expires_at"]


def test_renew_lease_404(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)
    r = client.post(
        f"/api/workspaces/{ws['id']}/lease/nope/renew",
        json={"ttl_seconds": 3600},
    )
    assert r.status_code == 404


def test_list_leases_filters_by_workspace(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws_a = _create_workspace(client=client, mount_target=mount, name="a")
    ws_b = _create_workspace(client=client, mount_target=mount, name="b")

    client.post(
        f"/api/workspaces/{ws_a['id']}/lease",
        json={"file_path": "a.txt", "holder_id": "alice"},
    )
    client.post(
        f"/api/workspaces/{ws_b['id']}/lease",
        json={"file_path": "b.txt", "holder_id": "bob"},
    )

    a_leases = client.get(f"/api/workspaces/{ws_a['id']}/leases").json()["leases"]
    b_leases = client.get(f"/api/workspaces/{ws_b['id']}/leases").json()["leases"]

    assert len(a_leases) == 1
    assert a_leases[0]["file_path"] == "a.txt"
    assert len(b_leases) == 1
    assert b_leases[0]["file_path"] == "b.txt"


# ═══════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════


def test_health_ok_for_reachable_mount(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    r = client.post(f"/api/workspaces/{ws['id']}/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["workspace_id"] == ws["id"]


def test_health_reports_unreachable_after_mount_gone(tmp_path: Path) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    ws = _create_workspace(client=client, mount_target=mount)

    # Remove the mount directory → test_connection should return False.
    import shutil

    shutil.rmtree(mount)

    r = client.post(f"/api/workspaces/{ws['id']}/health")
    assert r.status_code == 200
    assert r.json()["ok"] is False


def test_health_workspace_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/workspaces/nope/health").status_code == 404


# ═══════════════════════════════════════════════════════════
# Feature flag gating
# ═══════════════════════════════════════════════════════════


def test_endpoints_return_403_when_flag_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "data"
    mount.mkdir()
    client = _client(tmp_path)
    _flag_off(monkeypatch)

    # POST create
    r = client.post(
        "/api/workspaces",
        json={
            "name": "x",
            "mount_type": "local",
            "mount_target": str(mount),
            "mount_options": {},
            "owner_id": "alice",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "remote_workspace_disabled"

    # GET list
    assert client.get("/api/workspaces").status_code == 403
    # GET single
    assert client.get("/api/workspaces/anything").status_code == 403
    # DELETE
    assert client.delete("/api/workspaces/anything").status_code == 403
    # Members
    assert client.get("/api/workspaces/anything/members").status_code == 403
    assert (
        client.post(
            "/api/workspaces/anything/members",
            json={"member_id": "x", "role": "viewer"},
        ).status_code
        == 403
    )
    # Lease
    assert (
        client.post(
            "/api/workspaces/anything/lease",
            json={"file_path": "f", "holder_id": "x"},
        ).status_code
        == 403
    )
    assert client.get("/api/workspaces/anything/leases").status_code == 403
    # Health
    assert client.post("/api/workspaces/anything/health").status_code == 403

