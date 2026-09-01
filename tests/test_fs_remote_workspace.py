"""Tests for the remote-workspace file operations on ``fs_router``.

Covers Task 6:

  - ``/api/fs/read`` with a ``workspace_id:`` prefix routes through the
    MountBackend and returns its bytes.
  - ``/api/fs/write`` with a ``workspace_id:`` prefix routes through the
    MountBackend; the optional ``holder_id`` triggers a pre-write lease
    check (409 on conflict, auto-acquire on success).
  - ``/api/fs/tree`` with a ``workspace_id:`` prefix routes through the
    MountBackend and returns ``FsTreeEntry``-shaped entries, with the
    ``.git`` / ``node_modules`` / ``.echo`` / ``logs`` noise filtered.
  - ``broadcast_file_written`` lands on the bound cowork group's
    blackboard when a ``thread_id`` is supplied.
  - Local-path requests keep their existing behaviour when no remote
    stores are wired (backward-compat).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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

# ═══════════════════════════════════════════════════════════
# In-memory mock MountBackend
# ═══════════════════════════════════════════════════════════


class _MockMountBackend(MountBackend):
    """In-memory MountBackend that records every call.

    The backing store is a plain dict ``{path: bytes}``; list_dir
    synthesises DirEntry rows from the keys so the tree endpoint has
    something concrete to return.
    """

    def __init__(self, **_kwargs: Any) -> None:
        self.files: dict[str, bytes] = {}
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self._ignored_dirs: set[str] = set()

    # — helpers —
    def seed(self, path: str, content: bytes | str) -> None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[path] = content

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    # — MountBackend impl —
    async def read_file(self, path: str) -> bytes:
        self._record("read_file", path)
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    async def write_file(self, path: str, content: bytes) -> None:
        self._record("write_file", path, content)
        self.files[path] = content

    async def list_dir(self, path: str, depth: int = 1) -> list[DirEntry]:
        self._record("list_dir", path, depth)
        prefix = path.strip("/")
        entries: list[DirEntry] = []
        seen: set[str] = set()
        for key in sorted(self.files):
            stripped = key.strip("/")
            if prefix:
                if not stripped.startswith(prefix + "/"):
                    continue
                rest = stripped[len(prefix) + 1 :]
            else:
                rest = stripped
            if not rest:
                continue
            parts = rest.split("/")
            # Top-level entry name.
            top = parts[0]
            if top in seen:
                continue
            seen.add(top)
            is_dir = len(parts) > 1
            entries.append(
                DirEntry(
                    name=top,
                    path=(prefix + "/" + top) if prefix else top,
                    is_dir=is_dir,
                    size=0 if is_dir else len(self.files[key]),
                    modified=0.0,
                )
            )
        return entries

    async def stat(self, path: str) -> Any:  # pragma: no cover — not used here
        raise FileNotFoundError(path)

    async def mkdir(self, path: str) -> None:
        self._record("mkdir", path)

    async def remove(self, path: str) -> None:
        self._record("remove", path)
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


def _make_backend_registry(backend: _MockMountBackend) -> MountBackendRegistry:
    """Fresh registry that returns ``backend`` for any workspace_id.

    The registry caches by workspace_id, so the same backend instance
    is reused across calls for the same workspace.
    """
    reg = MountBackendRegistry()
    # Register a dummy class whose constructor returns our mock — the
    # factory doesn't matter since get_or_create caches by workspace_id.
    reg.register("local", _MockMountBackend)
    # Pre-seed the cache so get_or_create returns our mock instance.
    return reg


def _seed_workspace(
    store: WorkspaceStore,
    *,
    mount_target: str = "/tmp/mock",
    workspace_id: str = "ws-mock",
    owner_id: str = "alice",
) -> Any:
    return store.create_workspace(
        name="mock",
        mount_type="local",
        mount_target=mount_target,
        mount_options={},
        owner_id=owner_id,
        workspace_id=workspace_id,
    )


def _client(
    *,
    tmp_path: Path,
    workspace_store: WorkspaceStore | None = None,
    lease_store: LeaseStore | None = None,
    registry: MountBackendRegistry | None = None,
    group_store: GroupStore | None = None,
) -> TestClient:
    ws_store = workspace_store or WorkspaceStore(db_path=tmp_path / "workspaces.db")
    leases = lease_store or LeaseStore(db_path=tmp_path / "leases.db")
    reg = registry or _make_backend_registry(_MockMountBackend())
    gstore = group_store or GroupStore(base_dir=tmp_path / "cowork")
    app = FastAPI()
    app.include_router(
        create_fs_router(
            workspace_store=ws_store,
            lease_store=leases,
            mount_registry=reg,
            group_store=gstore,
        )
    )
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# Read
# ═══════════════════════════════════════════════════════════


def test_read_routes_through_backend_for_workspace_prefix(tmp_path: Path) -> None:
    backend = _MockMountBackend()
    backend.seed("src/main.py", "print('hello')")
    reg = _make_backend_registry(backend)
    # Pre-seed the registry cache so get_or_create returns ``backend``.
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        registry=reg,
    )

    r = client.get(
        "/api/fs/read",
        params={"path": "ws-1:src/main.py", "user_id": "alice"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["content"] == "print('hello')"
    assert body["lines"] == ["print('hello')"]
    assert body["path"] == "ws-1:src/main.py"
    # Backend was actually called.
    assert any(c[0] == "read_file" for c in backend.calls)


def test_read_returns_404_when_backend_file_missing(tmp_path: Path) -> None:
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(tmp_path=tmp_path, workspace_store=store, registry=reg)

    r = client.get(
        "/api/fs/read",
        params={"path": "ws-1:does/not/exist.py", "user_id": "alice"},
    )
    assert r.status_code == 404


def test_read_unknown_workspace_prefix_falls_through_to_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``workspace_id:`` prefix for an unknown workspace must NOT be
    treated as a remote-workspace path — it should fall through to the
    existing local-path resolver, which then fails closed to the
    allowed fs roots (403) or returns 404 if the resolved local file
    does not exist. The key contract: the response must NOT carry a
    remote-routing error (e.g. ``mount_backend_unavailable``), which
    would indicate remote resolution was attempted."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    # No workspace with id 'ws-ghost' is registered.
    client = _client(tmp_path=tmp_path, workspace_store=store)

    r = client.get(
        "/api/fs/read",
        params={"path": "ws-ghost:/etc/passwd", "user_id": "alice"},
    )
    # Resolved as a local path → either outside allowed roots (403) or
    # missing file (404); never 500 with a backend error.
    assert r.status_code in (403, 404)
    if r.status_code == 500:
        assert r.json().get("detail", {}).get("error") != "mount_backend_unavailable"


# ═══════════════════════════════════════════════════════════
# Write
# ═══════════════════════════════════════════════════════════


def test_write_routes_through_backend_for_workspace_prefix(tmp_path: Path) -> None:
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(tmp_path=tmp_path, workspace_store=store, registry=reg)

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "print('hi')",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["path"] == "ws-1:src/app.py"
    assert body["bytes"] == len(b"print('hi')")
    # Backend was actually called.
    assert ("write_file", ("src/app.py", b"print('hi')"), {}) in [
        (c[0], c[1], c[2]) for c in backend.calls
    ] or any(c[0] == "write_file" for c in backend.calls)
    # File landed in the backend's in-memory store.
    assert backend.files["src/app.py"] == b"print('hi')"


def test_remote_write_rejects_stale_digest_as_conflict(tmp_path: Path) -> None:
    backend = _MockMountBackend()
    backend.seed("src/app.py", "agent version")
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend
    client = _client(tmp_path=tmp_path, workspace_store=store, registry=reg)

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "stale human version",
            "user_id": "alice",
            "holder_id": "alice",
            "expected_sha256": hashlib.sha256(b"older version").hexdigest(),
        },
    )

    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "file_changed"
    assert backend.files["src/app.py"] == b"agent version"
    assert not any(call[0] == "write_file" for call in backend.calls)


def test_write_409_when_other_holder_owns_lease(tmp_path: Path) -> None:
    """If a different holder already owns an exclusive lease on the file,
    write returns 409 and does NOT call backend.write_file."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    lease_store = LeaseStore(db_path=tmp_path / "leases.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    # Bob acquires an exclusive lease on src/app.py.
    lease_store.acquire(
        workspace_id="ws-1",
        file_path="src/app.py",
        holder_id="bob",
        ttl_seconds=600,
    )

    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        lease_store=lease_store,
        registry=reg,
    )

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "print('hi')",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "lease_conflict"
    assert detail["holder_id"] == "bob"
    # Backend was NOT called.
    assert not any(c[0] == "write_file" for c in backend.calls)
    # File was not written.
    assert "src/app.py" not in backend.files


def test_write_auto_acquires_lease_when_holder_id_supplied(tmp_path: Path) -> None:
    """When ``holder_id`` is supplied and no conflict exists, the write
    auto-acquires an exclusive lease so the file is locked against
    concurrent writers for the TTL window."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    lease_store = LeaseStore(db_path=tmp_path / "leases.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        lease_store=lease_store,
        registry=reg,
    )

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "print('hi')",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 200
    # The lease should now exist.
    lease = lease_store.get_by_path("ws-1", "src/app.py")
    assert lease is not None
    assert lease.holder_id == "alice"


def test_write_same_holder_renews_lease(tmp_path: Path) -> None:
    """A second write by the same holder renews the existing lease
    instead of conflict — the underlying LeaseStore.acquire handles this
    in place, but the fs endpoint must not surface it as a 409."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    lease_store = LeaseStore(db_path=tmp_path / "leases.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        lease_store=lease_store,
        registry=reg,
    )

    first = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "v1",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert first.status_code == 200
    second = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "v2",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert second.status_code == 200
    assert backend.files["src/app.py"] == b"v2"


def test_write_without_holder_id_skips_lease_check(tmp_path: Path) -> None:
    """When no holder_id is supplied the lease check is skipped — back-compat
    for callers that haven't adopted leases."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    lease_store = LeaseStore(db_path=tmp_path / "leases.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    # Bob owns a lease on the file.
    lease_store.acquire(
        workspace_id="ws-1",
        file_path="src/app.py",
        holder_id="bob",
        ttl_seconds=600,
    )

    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        lease_store=lease_store,
        registry=reg,
    )

    # Alice writes without holder_id — lease gate is skipped, write succeeds.
    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "ignored-lease",
            "user_id": "alice",
        },
    )
    assert r.status_code == 200
    assert backend.files["src/app.py"] == b"ignored-lease"


# ═══════════════════════════════════════════════════════════
# Tree
# ═══════════════════════════════════════════════════════════


def test_tree_routes_through_backend_for_workspace_prefix(tmp_path: Path) -> None:
    backend = _MockMountBackend()
    backend.seed("src/app.py", b"x")
    backend.seed("src/lib/util.py", b"y")
    backend.seed("README.md", b"z")
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(tmp_path=tmp_path, workspace_store=store, registry=reg)

    r = client.get(
        "/api/fs/tree",
        params={"path": "ws-1:/", "depth": 1, "user_id": "alice"},
    )
    assert r.status_code == 200, r.text
    names = {e["name"] for e in r.json()["entries"]}
    assert {"src", "README.md"} <= names


def test_tree_filters_ignored_remote_dirs(tmp_path: Path) -> None:
    """``.git`` / ``node_modules`` / ``.echo`` / ``logs`` are filtered
    from remote tree listings to mirror the local behaviour."""
    backend = _MockMountBackend()
    backend.seed(".git/config", b"")
    backend.seed("node_modules/pkg/index.js", b"")
    backend.seed(".echo/state", b"")
    backend.seed("logs/run.log", b"")
    backend.seed("src/app.py", b"print('hi')")
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    client = _client(tmp_path=tmp_path, workspace_store=store, registry=reg)

    r = client.get(
        "/api/fs/tree",
        params={"path": "ws-1:/", "depth": 1, "user_id": "alice"},
    )
    assert r.status_code == 200
    names = {e["name"] for e in r.json()["entries"]}
    assert ".git" not in names
    assert "node_modules" not in names
    assert ".echo" not in names
    assert "logs" not in names
    assert "src" in names


# ═══════════════════════════════════════════════════════════
# Broadcast file_written
# ═══════════════════════════════════════════════════════════


def test_write_broadcasts_file_written_when_thread_id_supplied(tmp_path: Path) -> None:
    """When a ``thread_id`` is supplied and a ``group_store`` is wired,
    a successful remote write lands a ``file_written`` entry on the
    thread's shared blackboard."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    group_store = GroupStore(base_dir=tmp_path / "cowork")
    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        registry=reg,
        group_store=group_store,
    )

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "print('hi')",
            "user_id": "alice",
            "holder_id": "alice",
            "thread_id": "t1",
        },
    )
    assert r.status_code == 200

    # The blackboard should now have a file_written entry.
    board = group_store.blackboard("t1")
    entries = board.read("file_written", default=[])
    assert isinstance(entries, list)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["file_path"] == "src/app.py"
    assert entry["writer_id"] == "alice"
    assert entry["workspace_id"] == "ws-1"


def test_write_skips_broadcast_when_no_thread_id(tmp_path: Path) -> None:
    """No ``thread_id`` → no broadcast — the write succeeds but the
    blackboard stays empty."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    group_store = GroupStore(base_dir=tmp_path / "cowork")
    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        registry=reg,
        group_store=group_store,
    )

    r = client.post(
        "/api/fs/write",
        json={
            "path": "ws-1:src/app.py",
            "content": "print('hi')",
            "user_id": "alice",
            "holder_id": "alice",
        },
    )
    assert r.status_code == 200
    board = group_store.blackboard("t1")
    assert board.read("file_written", default=[]) == []


def test_multiple_writes_accumulate_blackboard_entries(tmp_path: Path) -> None:
    """Each successful write appends to the ``file_written`` list — later
    collaborators can poll the key and see the full history."""
    backend = _MockMountBackend()
    reg = _make_backend_registry(backend)
    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    _seed_workspace(store, workspace_id="ws-1", owner_id="alice")
    reg._instances["ws-1"] = backend

    group_store = GroupStore(base_dir=tmp_path / "cowork")
    client = _client(
        tmp_path=tmp_path,
        workspace_store=store,
        registry=reg,
        group_store=group_store,
    )

    for path, content in [("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3")]:
        r = client.post(
            "/api/fs/write",
            json={
                "path": f"ws-1:{path}",
                "content": content,
                "user_id": "alice",
                "holder_id": "alice",
                "thread_id": "t1",
            },
        )
        assert r.status_code == 200

    board = group_store.blackboard("t1")
    entries = board.read("file_written", default=[])
    assert len(entries) == 3
    paths = [e["file_path"] for e in entries]
    assert paths == ["a.txt", "b.txt", "c.txt"]


# ═══════════════════════════════════════════════════════════
# Backward-compat
# ═══════════════════════════════════════════════════════════


def test_local_path_request_unchanged_when_remote_stores_not_wired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``workspace_store`` is None (the default), ``workspace_id:``
    prefixes are not treated as remote-workspace paths — the existing
    local-path resolver runs and either accepts (200/404) or rejects
    (403) the local interpretation of the path. The key contract: the
    response must NOT carry a remote-routing error (e.g.
    ``mount_backend_unavailable``), which would indicate remote
    resolution was attempted."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

    app = FastAPI()
    app.include_router(create_fs_router())  # no remote stores wired
    client = TestClient(app)

    # A path with a "ws-foo:" prefix for an unknown workspace should fall
    # through to the local-path resolver — i.e., 4xx (403 outside allowed
    # roots OR 404 file-not-found), never 500 with a backend error.
    r = client.get(
        "/api/fs/read",
        params={"path": "ws-foo:/nonexistent/file.txt"},
    )
    assert r.status_code in (403, 404)
    if r.status_code == 500:
        body = r.json()
        assert body.get("detail", {}).get("error") != "mount_backend_unavailable"


def test_local_path_inside_allowed_roots_still_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity: a local path inside the allowed roots is served normally
    even when the remote stores ARE wired (no workspace_id prefix →
    fall through to local resolver)."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    f = allowed / "ok.txt"
    f.write_text("fine", encoding="utf-8")
    monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

    store = WorkspaceStore(db_path=tmp_path / "ws.db")
    app = FastAPI()
    app.include_router(create_fs_router(workspace_store=store))
    client = TestClient(app)

    r = client.get("/api/fs/read", params={"path": str(f)})
    assert r.status_code == 200
    assert r.json()["content"] == "fine"

