"""Tests for the Workspace-level ACL on ``fs_router`` endpoints.

Covers Task 7:

  - Read ops (``/api/fs/read``, ``/api/fs/tree``) require any member role.
  - Write ops (``/api/fs/write``) require ``owner`` or ``editor``.
  - ``viewer`` / ``reviewer`` are denied writes (403).
  - Non-members are denied everything (403).
  - Missing ``user_id`` (no query param, no header, no body field) → 403.
  - ``user_id`` resolution order: query param → header → body field.
  - ``ContextGrant`` per role on ``link_workspace_to_group``:
      owner/editor → scope=all
      reviewer     → scope=from_join
      viewer       → scope=summary
  - ``broadcast_file_written`` appends to the group blackboard.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.memory.cowork.group import ContextGrant
from runtime.memory.cowork.group_store import GroupStore
from runtime.platform.io.lease import LeaseStore
from runtime.sensing.gateway.fs_router import create_fs_router
from runtime.sensing.server.mount_backend import (
    DirEntry,
    MountBackend,
    MountBackendRegistry,
)
from runtime.workspace import WorkspaceStore
from runtime.workspace import crypto as crypto_mod
from runtime.workspace.cowork_bridge import (
    broadcast_file_written,
    grant_for_workspace_role,
    link_workspace_to_group,
)

# ═══════════════════════════════════════════════════════════
# Mock backend
# ═══════════════════════════════════════════════════════════


class _StubBackend(MountBackend):
    """Trivial MountBackend — files stored in a dict."""

    def __init__(self, **_kwargs: Any) -> None:
        self.files: dict[str, bytes] = {}

    async def read_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        return [DirEntry(name="dummy.txt", path="dummy.txt", is_dir=False, size=0, modified=0.0)]

    async def stat(self, path: str) -> Any:  # pragma: no cover — unused
        raise FileNotFoundError(path)

    async def mkdir(self, path: str) -> None:
        pass

    async def remove(self, path: str) -> None:
        self.files.pop(path, None)

    async def test_connection(self) -> bool:
        return True


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_crypto_cache() -> None:
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None
    yield
    crypto_mod._CIPHER_CACHE = None
    crypto_mod._CIPHER_KEY_CACHE = None
    crypto_mod._MACHINE_ID_CACHE = None


def _client_with_workspace(
    tmp_path: Path,
    *,
    workspace_id: str = "ws-acl",
    owner_id: str = "alice",
    members: dict[str, str] | None = None,
    seed_files: dict[str, bytes] | None = None,
) -> tuple[TestClient, WorkspaceStore, LeaseStore, _StubBackend, str]:
    """Build a TestClient wired to a workspace with the given members.

    Returns ``(client, workspace_store, lease_store, backend, workspace_id)``.
    """
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    store.create_workspace(
        name="acl-test",
        mount_type="local",
        mount_target="/tmp/stub",
        mount_options={},
        owner_id=owner_id,
        workspace_id=workspace_id,
    )
    # Add additional members.
    for member_id, role in (members or {}).items():
        store.add_member(workspace_id, member_id, role=role)

    backend = _StubBackend()
    if seed_files:
        for path, content in seed_files.items():
            backend.files[path] = content

    reg = MountBackendRegistry()
    reg.register("local", _StubBackend)
    reg._instances[workspace_id] = backend

    leases = LeaseStore(db_path=tmp_path / "leases.db")
    gstore = GroupStore(base_dir=tmp_path / "cowork")

    app = FastAPI()
    app.include_router(
        create_fs_router(
            workspace_store=store,
            lease_store=leases,
            mount_registry=reg,
            group_store=gstore,
        )
    )
    return TestClient(app), store, leases, backend, workspace_id


# ═══════════════════════════════════════════════════════════
# Read ACL
# ═══════════════════════════════════════════════════════════


def test_owner_can_read(tmp_path: Path) -> None:
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        seed_files={"src/main.py": b"print('hi')"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:src/main.py", "user_id": "alice"},
    )
    assert r.status_code == 200
    assert r.json()["content"] == "print('hi')"


def test_editor_can_read(tmp_path: Path) -> None:
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"bob": "editor"},
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt", "user_id": "bob"},
    )
    assert r.status_code == 200


def test_reviewer_can_read(tmp_path: Path) -> None:
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"carol": "reviewer"},
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt", "user_id": "carol"},
    )
    assert r.status_code == 200


def test_viewer_can_read(tmp_path: Path) -> None:
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"dave": "viewer"},
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt", "user_id": "dave"},
    )
    assert r.status_code == 200


def test_non_member_cannot_read(tmp_path: Path) -> None:
    """A user with no role on the workspace is rejected (403)."""
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt", "user_id": "stranger"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "not_a_member"
    assert detail["user_id"] == "stranger"


def test_missing_user_id_returns_403(tmp_path: Path) -> None:
    """No user_id in any of (query, header, body) → 403."""
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "user_id_required"


def test_user_id_from_header(tmp_path: Path) -> None:
    """``X-User-Id`` header is accepted as a fallback when no query param."""
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"bob": "editor"},
        seed_files={"a.txt": b"hello"},
    )
    r = client.get(
        "/api/fs/read",
        params={"path": f"{ws_id}:a.txt"},
        headers={"X-User-Id": "bob"},
    )
    assert r.status_code == 200


def test_tree_requires_membership(tmp_path: Path) -> None:
    """Tree listing on a remote workspace also enforces ACL."""
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        seed_files={"a.txt": b"x"},
    )
    # Owner can list.
    r = client.get(
        "/api/fs/tree",
        params={"path": f"{ws_id}:/", "user_id": "alice"},
    )
    assert r.status_code == 200
    # Stranger cannot list.
    r = client.get(
        "/api/fs/tree",
        params={"path": f"{ws_id}:/", "user_id": "stranger"},
    )
    assert r.status_code == 403


# ═══════════════════════════════════════════════════════════
# Write ACL
# ═══════════════════════════════════════════════════════════


def test_owner_can_write(tmp_path: Path) -> None:
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "hello",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 200, r.text
    assert backend.files["a.txt"] == b"hello"


def test_editor_can_write(tmp_path: Path) -> None:
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"bob": "editor"},
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "from-bob",
            "user_id": "bob",
            "holder_id": "bob",
        },
    )
    assert r.status_code == 200, r.text
    assert backend.files["a.txt"] == b"from-bob"


def test_reviewer_cannot_write(tmp_path: Path) -> None:
    """Reviewer is read-only — write returns 403 and the file is NOT written."""
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"carol": "reviewer"},
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "from-carol",
            "user_id": "carol",
            "holder_id": "carol",
        },
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["error"] == "write_requires_editor"
    assert detail["role"] == "reviewer"
    assert "a.txt" not in backend.files


def test_viewer_cannot_write(tmp_path: Path) -> None:
    """Viewer is read-only — write returns 403."""
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"dave": "viewer"},
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "from-dave",
            "user_id": "dave",
            "holder_id": "dave",
        },
    )
    assert r.status_code == 403
    assert "a.txt" not in backend.files


def test_non_member_cannot_write(tmp_path: Path) -> None:
    """Non-member write is rejected with 403."""
    client, _, _, backend, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "from-stranger",
            "user_id": "stranger",
            "holder_id": "stranger",
        },
    )
    assert r.status_code == 403
    assert "a.txt" not in backend.files


def test_write_with_user_id_in_body(tmp_path: Path) -> None:
    """``user_id`` can also be supplied in the request body (POST only)."""
    client, _, _, _, ws_id = _client_with_workspace(
        tmp_path,
        owner_id="alice",
        members={"bob": "editor"},
    )
    r = client.post(
        "/api/fs/write",
        json={
            "path": f"{ws_id}:a.txt",
            "content": "hello",
            "user_id": "bob",
            "holder_id": "bob",
        },
    )
    assert r.status_code == 200


# ═══════════════════════════════════════════════════════════
# grant_for_workspace_role
# ═══════════════════════════════════════════════════════════


def test_grant_for_owner_is_all() -> None:
    assert grant_for_workspace_role("owner") == ContextGrant(scope="all")


def test_grant_for_editor_is_all() -> None:
    assert grant_for_workspace_role("editor") == ContextGrant(scope="all")


def test_grant_for_reviewer_is_from_join() -> None:
    assert grant_for_workspace_role("reviewer") == ContextGrant(scope="from_join")


def test_grant_for_viewer_is_summary() -> None:
    assert grant_for_workspace_role("viewer") == ContextGrant(scope="summary")


def test_grant_for_unknown_role_is_summary() -> None:
    """Unknown roles default to summary (fail-safe — least access)."""
    assert grant_for_workspace_role("intern") == ContextGrant(scope="summary")
    assert grant_for_workspace_role("") == ContextGrant(scope="summary")


# ═══════════════════════════════════════════════════════════
# link_workspace_to_group sets ContextGrant per role
# ═══════════════════════════════════════════════════════════


def test_link_workspace_sets_grant_per_role(
    tmp_path: Path,
) -> None:
    """``link_workspace_to_group`` mirrors each workspace member into the
    group with a ContextGrant matching their role."""
    ws_store = WorkspaceStore(db_path=tmp_path / "ws.db")
    ws = ws_store.create_workspace(
        name="t",
        mount_type="local",
        mount_target="/tmp/x",
        mount_options={},
        owner_id="alice",
        workspace_id="ws-1",
    )
    ws_store.add_member(ws.id, "bob", role="editor")
    ws_store.add_member(ws.id, "carol", role="reviewer")
    ws_store.add_member(ws.id, "dave", role="viewer")

    group_store = GroupStore(base_dir=tmp_path / "cowork")
    link_workspace_to_group(ws_store, group_store, ws.id, "t1")

    state = group_store.state("t1")
    by_id = {m.id: m for m in state.roster}
    # owner / editor → scope=all
    assert by_id["alice"].grant.scope == "all"
    assert by_id["bob"].grant.scope == "all"
    # reviewer → scope=from_join
    assert by_id["carol"].grant.scope == "from_join"
    # viewer → scope=summary
    assert by_id["dave"].grant.scope == "summary"


# ═══════════════════════════════════════════════════════════
# broadcast_file_written
# ═══════════════════════════════════════════════════════════


def test_broadcast_file_written_appends_to_blackboard(tmp_path: Path) -> None:
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    broadcast_file_written(
        group_store,
        "t1",
        "src/app.py",
        "alice",
        workspace_id="ws-1",
    )
    board = group_store.blackboard("t1")
    entries = board.read("file_written", default=[])
    assert len(entries) == 1
    assert entries[0]["file_path"] == "src/app.py"
    assert entries[0]["writer_id"] == "alice"
    assert entries[0]["workspace_id"] == "ws-1"
    assert entries[0]["ts"] > 0


def test_broadcast_file_written_accumulates(tmp_path: Path) -> None:
    """Successive calls append to the same key — readers see full history."""
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    broadcast_file_written(group_store, "t1", "a.txt", "alice", workspace_id="ws-1")
    broadcast_file_written(group_store, "t1", "b.txt", "bob", workspace_id="ws-1")
    broadcast_file_written(group_store, "t1", "c.txt", "alice", workspace_id="ws-1")

    board = group_store.blackboard("t1")
    entries = board.read("file_written", default=[])
    assert len(entries) == 3
    paths = [e["file_path"] for e in entries]
    writers = [e["writer_id"] for e in entries]
    assert paths == ["a.txt", "b.txt", "c.txt"]
    assert writers == ["alice", "bob", "alice"]


def test_broadcast_file_written_noop_without_thread_id(tmp_path: Path) -> None:
    """No thread_id → no-op — the blackboard stays empty."""
    group_store = GroupStore(base_dir=tmp_path / "cowork")
    broadcast_file_written(
        group_store,
        "",
        "src/app.py",
        "alice",
        workspace_id="ws-1",
    )
    # No blackboard writes for any thread.
    board = group_store.blackboard("t1")
    assert board.read("file_written", default=[]) == []


def test_broadcast_file_written_noop_without_group_store() -> None:
    """No group_store → no-op (defensive)."""
    # Pass None as group_store — the function must handle this gracefully.
    broadcast_file_written(
        None,  # type: ignore[arg-type]
        "t1",
        "src/app.py",
        "alice",
        workspace_id="ws-1",
    )
    # No assertion needed — the function must not raise.

