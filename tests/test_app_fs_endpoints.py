"""
Integration tests for the filesystem endpoint group (``/api/fs/*``).

Pre-split baseline for moving these out of ``app.py`` into
``fs_router.py``. Same pattern as the previous extraction rounds
(config / meta / mcp).

Covered
-------

    GET  /api/fs/tree   · directory tree walk with depth cap
    GET  /api/fs/read   · read file text with line cap + truncation
    POST /api/fs/write  · write file text

Sandbox: when a request carries no per-thread workspace scope, the
endpoints now fail CLOSED to the process-wide allowed fs roots (data
dir / home / project / ECHO_FS_ALLOWED_ROOTS) instead of serving any
absolute path the caller named — see ``_assert_in_scope`` and
``TestFailClosed`` below. Auth is enforced once at the router level when
an identity store is wired and ``require_auth`` is set (``TestFsAuth``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.platform.ui.app import create_app
from runtime.sensing.gateway.fs_router import create_fs_router


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Direct every runtime-state dir (data/, threads/, logs/, etc.)
    # at a sibling path so the fs endpoint, which is asked to walk
    # ``tmp_path`` in tests below, sees only the files the test wrote.
    # Without this, ``create_app()``'s on-startup file writes —
    # prompt templates, thread JSONLs, feature flag overrides —
    # would land alongside the test's ``a.txt`` / ``sub/`` and break
    # exact-match assertions.
    runtime_state = tmp_path.parent / f"{tmp_path.name}.runtime"
    runtime_state.mkdir(exist_ok=True)
    monkeypatch.setenv("ECHO_HOME", str(runtime_state))
    monkeypatch.chdir(tmp_path)
    return TestClient(create_app())


class _ThreadStore:
    def __init__(self, metadata: dict[str, object]) -> None:
        self.metadata = metadata

    def get(self, thread_id: str) -> dict[str, object] | None:
        if thread_id != "thread-scope":
            return None
        return {"metadata": self.metadata}


def scoped_client(metadata: dict[str, object]) -> TestClient:
    app = FastAPI()
    app.include_router(create_fs_router(thread_store=_ThreadStore(metadata)))
    return TestClient(app)


class TestFsTree:
    def test_returns_filesystem_roots(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        r = client.get("/api/fs/roots")

        assert r.status_code == 200
        entries = r.json()["entries"]
        paths = {Path(e["path"]).resolve(strict=False) for e in entries}
        assert tmp_path.resolve(strict=False) in paths
        assert all(e["type"] == "dir" for e in entries)

    def test_import_directory_preserves_relative_tree(
        self,
        client: TestClient,
    ) -> None:
        r = client.post(
            "/api/fs/import-directory",
            files=[
                ("files", ("project/package.json", b"{}", "application/json")),
                ("files", ("project/src/app.ts", b"export {}", "text/plain")),
            ],
            data={
                "relative_paths": [
                    "project/package.json",
                    "project/src/app.ts",
                ],
            },
        )

        assert r.status_code == 200
        data = r.json()
        imported = Path(data["path"])
        assert data["files"] == 2
        assert (imported / "package.json").read_text(encoding="utf-8") == "{}"
        assert (imported / "src" / "app.ts").read_text(encoding="utf-8") == "export {}"

    def test_returns_entries_for_valid_dir(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "a.txt").write_text("hi")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("hello")

        r = client.get(f"/api/fs/tree?path={tmp_path}")
        assert r.status_code == 200
        entries = r.json()["entries"]
        names = {e["name"] for e in entries}
        # Subset, not equality. The ``client`` fixture ``chdir``s into
        # ``tmp_path`` before constructing the app, and downstream
        # subsystems (sqlite indices, prompt registry, etc.) lazily
        # materialise a ``data/`` tree under cwd. The test's contract
        # is "the files the test wrote show up", not "no other dirs
        # exist anywhere in tmp".
        assert {"a.txt", "sub", "b.txt"}.issubset(names)
        # Dirs sorted before files among the test's own writes.
        own_top = [e["name"] for e in entries if e["depth"] == 0 and e["name"] in {"a.txt", "sub"}]
        assert own_top == ["sub", "a.txt"]

    def test_nonexistent_dir_returns_404(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        r = client.get(f"/api/fs/tree?path={tmp_path / 'nope'}")
        assert r.status_code == 404

    def test_file_instead_of_dir_returns_404(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "x.txt"
        f.write_text("x")
        r = client.get(f"/api/fs/tree?path={f}")
        assert r.status_code == 404

    def test_depth_cap_respected(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        """Deeply nested dirs beyond ``depth`` should not appear."""
        deep = tmp_path / "l0" / "l1" / "l2" / "l3"
        deep.mkdir(parents=True)
        r = client.get(f"/api/fs/tree?path={tmp_path}&depth=1")
        assert r.status_code == 200
        entries = r.json()["entries"]
        depths = {e["depth"] for e in entries}
        # depth=1 sees top-level + one level inside → max depth 1
        assert max(depths) <= 1

    def test_ignores_heavy_project_dirs_by_default(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".git" / "objects" / "aa").mkdir(parents=True)
        (tmp_path / ".git" / "objects" / "aa" / "pack").write_text("x")
        (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
        (tmp_path / "node_modules" / "pkg" / "index.js").write_text("x")
        (tmp_path / ".echo" / "memory.db").parent.mkdir()
        (tmp_path / ".echo" / "memory.db").write_text("x")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "server.log").write_text("x")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("print('ok')")

        r = client.get(f"/api/fs/tree?path={tmp_path}&depth=3")

        assert r.status_code == 200
        names = {e["name"] for e in r.json()["entries"]}
        assert ".git" not in names
        assert "node_modules" not in names
        assert ".echo" not in names
        assert "logs" not in names
        assert names >= {"src", "app.py"}

    def test_can_include_ignored_dirs_when_requested(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        (tmp_path / ".git" / "objects").mkdir(parents=True)

        r = client.get(
            f"/api/fs/tree?path={tmp_path}&depth=1&include_ignored=true",
        )

        assert r.status_code == 200
        names = {e["name"] for e in r.json()["entries"]}
        assert ".git" in names
        assert "objects" in names


class TestFsRead:
    def test_read_small_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "x.txt"
        f.write_text("line1\nline2\nline3\n")
        r = client.get(f"/api/fs/read?path={f}")
        assert r.status_code == 200
        data = r.json()
        assert data["lines"] == ["line1", "line2", "line3"]
        assert data["truncated"] is False

    def test_read_respects_max_lines(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "big.txt"
        f.write_text("\n".join(f"row{i}" for i in range(100)))
        r = client.get(f"/api/fs/read?path={f}&max_lines=5")
        data = r.json()
        assert len(data["lines"]) == 5
        assert data["truncated"] is True

    def test_missing_file_returns_404(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        r = client.get(f"/api/fs/read?path={tmp_path / 'ghost.txt'}")
        assert r.status_code == 404

    def test_directory_returns_404(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        r = client.get(f"/api/fs/read?path={tmp_path}")
        assert r.status_code == 404

    def test_thread_workspace_rejects_outside_file(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        inside = workspace / "inside.txt"
        outside = tmp_path / "outside.txt"
        inside.write_text("inside", encoding="utf-8")
        outside.write_text("outside", encoding="utf-8")

        client = scoped_client({"workspace_path": str(workspace)})

        ok = client.get(
            "/api/fs/read",
            params={"path": str(inside), "thread_id": "thread-scope"},
        )
        blocked = client.get(
            "/api/fs/read",
            params={"path": str(outside), "thread_id": "thread-scope"},
        )

        assert ok.status_code == 200
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["error"] == "path_outside_workspace"


class TestFsWrite:
    def test_write_creates_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "out" / "nested" / "file.txt"
        r = client.post(
            "/api/fs/write",
            json={"path": str(target), "content": "hello world"},
        )
        assert r.status_code == 200
        assert target.read_text(encoding="utf-8") == "hello world"
        data = r.json()
        assert data["success"] is True
        assert data["bytes"] == len(b"hello world")

    def test_write_missing_path_rejected(
        self,
        client: TestClient,
    ) -> None:
        r = client.post("/api/fs/write", json={"content": "x"})
        assert r.status_code == 400

    def test_write_empty_path_rejected(
        self,
        client: TestClient,
    ) -> None:
        r = client.post(
            "/api/fs/write",
            json={"path": "   ", "content": "x"},
        )
        assert r.status_code == 400

    def test_write_non_string_content_rejected(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        r = client.post(
            "/api/fs/write",
            json={"path": str(tmp_path / "x.txt"), "content": [1, 2]},
        )
        assert r.status_code == 400

    def test_write_overwrites(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "x.txt"
        f.write_text("first")
        client.post(
            "/api/fs/write",
            json={"path": str(f), "content": "second"},
        )
        assert f.read_text(encoding="utf-8") == "second"

    def test_write_with_matching_digest_preserves_optimistic_lock(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "shared.html"
        original = "<h1>old</h1>"
        f.write_text(original, encoding="utf-8")

        r = client.post(
            "/api/fs/write",
            json={
                "path": str(f),
                "content": "<h1>new</h1>",
                "expected_sha256": hashlib.sha256(original.encode()).hexdigest(),
            },
        )

        assert r.status_code == 200
        assert f.read_text(encoding="utf-8") == "<h1>new</h1>"

    def test_write_rejects_stale_digest_without_overwriting(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        f = tmp_path / "shared.html"
        f.write_text("agent version", encoding="utf-8")

        r = client.post(
            "/api/fs/write",
            json={
                "path": str(f),
                "content": "human stale version",
                "expected_sha256": hashlib.sha256(b"older version").hexdigest(),
            },
        )

        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "file_changed"
        assert f.read_text(encoding="utf-8") == "agent version"

    def test_workspace_path_rejects_outside_write(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = tmp_path / "outside.txt"
        client = scoped_client({})

        r = client.post(
            "/api/fs/write",
            json={
                "path": str(target),
                "content": "nope",
                "workspace_path": str(workspace),
            },
        )

        assert r.status_code == 403
        assert not target.exists()


class TestFsRevertDiff:
    def test_revert_diff_restores_file_content(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nnew\nomega\n", encoding="utf-8")
        diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,3 +1,3 @@\n alpha\n-old\n+new\n omega\n"

        r = client.post(
            "/api/fs/revert-diff",
            json={"path": str(target), "diff": diff},
        )

        assert r.status_code == 200
        assert r.json()["reverted"] is True
        assert target.read_text(encoding="utf-8") == "alpha\nold\nomega\n"

    def test_revert_diff_can_reject_one_hunk_only(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nnew\nomega\nkeep\n", encoding="utf-8")
        diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,3 +1,3 @@\n alpha\n-old\n+new\n omega\n"

        r = client.post(
            "/api/fs/revert-diff",
            json={"path": str(target), "diff": diff},
        )

        assert r.status_code == 200
        assert target.read_text(encoding="utf-8") == "alpha\nold\nomega\nkeep\n"

    def test_revert_diff_conflict_returns_409(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "sample.txt"
        target.write_text("alpha\nchanged-again\nomega\n", encoding="utf-8")
        diff = "--- a/sample.txt\n+++ b/sample.txt\n@@ -1,3 +1,3 @@\n alpha\n-old\n+new\n omega\n"

        r = client.post(
            "/api/fs/revert-diff",
            json={"path": str(target), "diff": diff},
        )

        assert r.status_code == 409
        assert target.read_text(encoding="utf-8") == "alpha\nchanged-again\nomega\n"

    def test_revert_diff_can_delete_created_file(
        self,
        client: TestClient,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "created.txt"
        target.write_text("created\n", encoding="utf-8")
        diff = "--- a/created.txt\n+++ b/created.txt\n@@ -0,0 +1 @@\n+created\n"

        r = client.post(
            "/api/fs/revert-diff",
            json={
                "path": str(target),
                "diff": diff,
                "delete_empty": True,
            },
        )

        assert r.status_code == 200
        assert r.json()["deleted"] is True
        assert not target.exists()

    def test_revert_diff_respects_workspace_scope(
        self,
        tmp_path: Path,
    ) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("new\n", encoding="utf-8")
        client = scoped_client({})
        diff = "--- a/outside.txt\n+++ b/outside.txt\n@@ -1 +1 @@\n-old\n+new\n"

        r = client.post(
            "/api/fs/revert-diff",
            json={
                "path": str(outside),
                "diff": diff,
                "workspace_path": str(workspace),
            },
        )

        assert r.status_code == 403
        assert outside.read_text(encoding="utf-8") == "new\n"


class TestFailClosed:
    """No per-thread workspace scope → the endpoints must fall back to
    the allowed fs roots and reject an arbitrary absolute path, instead
    of the old behaviour of serving any path the caller named.
    """

    def test_unscoped_read_outside_roots_is_403(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        forbidden = tmp_path / "forbidden"
        forbidden.mkdir()
        secret = forbidden / "secret.txt"
        secret.write_text("top secret", encoding="utf-8")
        monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

        app = FastAPI()
        app.include_router(create_fs_router())
        client = TestClient(app)

        # No thread_id / workspace_path → unscoped → fail-closed.
        r = client.get("/api/fs/read", params={"path": str(secret)})
        assert r.status_code == 403

    def test_unscoped_write_outside_roots_is_403(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden" / "evil.txt"
        monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

        app = FastAPI()
        app.include_router(create_fs_router())
        client = TestClient(app)

        r = client.post("/api/fs/write", json={"path": str(target), "content": "x"})
        assert r.status_code == 403
        assert not target.exists()

    def test_unscoped_read_inside_roots_is_allowed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        f = allowed / "ok.txt"
        f.write_text("fine", encoding="utf-8")
        monkeypatch.setenv("ECHO_FS_ALLOWED_ROOTS", str(allowed))

        app = FastAPI()
        app.include_router(create_fs_router())
        client = TestClient(app)

        r = client.get("/api/fs/read", params={"path": str(f)})
        assert r.status_code == 200
        assert r.json()["content"] == "fine"


class TestFsAuth:
    """Router-level auth: a no-op by default (require_auth False), but
    when an identity store is wired with require_auth the whole fs router
    rejects unauthenticated requests.
    """

    def _client(self, require_auth: bool, *, allow_local_workspace_access: bool = False):
        from runtime.safety.auth import Identity, IdentityStore

        store = IdentityStore()
        store.add(Identity(actor_id="alice"), api_key_plaintext="sk-alice")
        app = FastAPI()
        app.include_router(
            create_fs_router(
                identity_store=store,
                require_auth=require_auth,
                allow_local_workspace_access=allow_local_workspace_access,
            )
        )
        return TestClient(app)

    def test_no_auth_required_by_default(self) -> None:
        # require_auth False → endpoints reachable without credentials.
        client = self._client(require_auth=False)
        r = client.get("/api/fs/roots")
        assert r.status_code == 200

    def test_missing_token_rejected_when_required(self) -> None:
        client = self._client(require_auth=True)
        r = client.get("/api/fs/roots")
        assert r.status_code == 401

    def test_valid_token_accepted_when_required(self) -> None:
        client = self._client(require_auth=True)
        r = client.get("/api/fs/roots", headers={"Authorization": "Bearer sk-alice"})
        # Authentication succeeds, but shared-mode roots also require a
        # server-owned thread scope.  The direct router has no thread store,
        # so it must fail closed after the 401 boundary.
        assert r.status_code == 403

    def test_authenticated_loopback_mode_can_pick_before_thread_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from runtime.sensing.gateway import _fs_router_endpoints as endpoints

        selected = "/Users/alice/project"
        monkeypatch.setattr(endpoints, "_pick_directory_macos", lambda _default: selected)
        monkeypatch.setattr(endpoints, "_pick_directory_tk", lambda _default: selected)
        monkeypatch.setattr(endpoints, "_pick_directory_windows", lambda _default: selected)
        client = self._client(require_auth=True, allow_local_workspace_access=True)

        roots = client.get(
            "/api/fs/roots",
            headers={"Authorization": "Bearer sk-alice"},
        )
        picked = client.get(
            "/api/fs/pick-directory",
            headers={"Authorization": "Bearer sk-alice"},
        )

        assert roots.status_code == 200
        assert picked.status_code == 200
        assert picked.json() == {
            "success": True,
            "path": selected,
            "canceled": False,
            "error": None,
        }

    def test_shared_mode_still_rejects_picker_without_thread_scope(self) -> None:
        client = self._client(require_auth=True)

        picked = client.get(
            "/api/fs/pick-directory",
            headers={"Authorization": "Bearer sk-alice"},
        )

        assert picked.status_code == 403
        assert picked.json()["detail"]["error"] == "thread_scope_required"

