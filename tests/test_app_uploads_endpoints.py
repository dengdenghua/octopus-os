"""
Integration tests for the thread-uploads endpoint group.

Baseline for extracting the 4 uploads/artifacts endpoints out of
``runtime/platform/ui/app.py`` into their own
``runtime/sensing/siphon/uploads_router.py``. Same pattern as the
previous 4 extractions.

Covered
-------

    POST   /api/threads/{tid}/uploads                  · multipart upload
    GET    /api/threads/{tid}/uploads/list             · list stored files
    DELETE /api/threads/{tid}/uploads/{filename}       · remove one file
    GET    /api/threads/{tid}/artifacts/{artifact:path} · serve file content

Hermetic isolation
------------------

``create_app`` must be built with ``journal`` / ``registry`` /
``stack`` so ``thread_store`` wires up (the upload endpoints 503 when
it's None). The fastest way to get that is ``build_from_config`` with
a minimal AgentConfig · identical to what other integration tests use.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from runtime.platform.config import AgentConfig, PlannerConfig, build_from_config
from runtime.platform.ui.app import create_app


@pytest.fixture
def isolated_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Path]:
    monkeypatch.chdir(tmp_path)
    yield tmp_path


@pytest.fixture
def stack(isolated_cwd: Path):
    """Full stack so ``thread_store`` binds inside create_app."""
    cfg = AgentConfig(
        planner=PlannerConfig(
            type="llm",
            model="mock/up",
            mock_response='{"reasoning":"r","nodes":[]}',
        ),
    )
    return build_from_config(cfg)


@pytest.fixture
def client(stack, isolated_cwd: Path) -> TestClient:
    app = create_app(
        journal=stack.journal,
        registry=stack.registry,
        stack=stack,
    )
    return TestClient(app)


# ═══════════════════════════════════════════════════════════
# POST /api/threads/{tid}/uploads
# ═══════════════════════════════════════════════════════════


class TestUploadPost:
    def test_single_file_persists(self, client: TestClient) -> None:
        r = client.post(
            "/api/threads/t1/uploads",
            files={"files": ("hello.txt", b"hi there", "text/plain")},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert len(data["files"]) == 1
        entry = data["files"][0]
        assert entry["filename"] == "hello.txt"
        assert entry["size"] == len(b"hi there")
        assert "artifact_url" in entry

    def test_multiple_files_in_one_request(
        self,
        client: TestClient,
    ) -> None:
        r = client.post(
            "/api/threads/t2/uploads",
            files=[
                ("files", ("a.txt", b"aaa", "text/plain")),
                ("files", ("b.bin", b"bbbb", "application/octet-stream")),
            ],
        )
        assert r.status_code == 200
        data = r.json()
        assert len(data["files"]) == 2
        names = {f["filename"] for f in data["files"]}
        assert names == {"a.txt", "b.bin"}

    def test_filename_path_traversal_stripped(
        self,
        client: TestClient,
    ) -> None:
        """Malicious filename like ``../../etc/passwd`` must be
        sanitized to its basename · otherwise uploads could land
        outside the thread's dir."""
        r = client.post(
            "/api/threads/t3/uploads",
            files={
                "files": (
                    "../../evil.txt",
                    b"X",
                    "text/plain",
                ),
            },
        )
        assert r.status_code == 200
        stored = r.json()["files"][0]["filename"]
        # basename only · no traversal segments
        assert stored == "evil.txt"


# ═══════════════════════════════════════════════════════════
# GET /api/threads/{tid}/uploads/list
# ═══════════════════════════════════════════════════════════


class TestUploadsList:
    def test_empty_thread(self, client: TestClient) -> None:
        r = client.get("/api/threads/empty_t/uploads/list")
        assert r.status_code == 200
        data = r.json()
        assert data == {"files": [], "count": 0}

    def test_lists_uploaded_files(self, client: TestClient) -> None:
        client.post(
            "/api/threads/list_t/uploads",
            files={"files": ("one.txt", b"111", "text/plain")},
        )
        client.post(
            "/api/threads/list_t/uploads",
            files={"files": ("two.txt", b"222", "text/plain")},
        )
        r = client.get("/api/threads/list_t/uploads/list")
        data = r.json()
        names = {f["filename"] for f in data["files"]}
        assert names == {"one.txt", "two.txt"}
        assert data["count"] == 2


# ═══════════════════════════════════════════════════════════
# DELETE /api/threads/{tid}/uploads/{filename}
# ═══════════════════════════════════════════════════════════


class TestUploadsDelete:
    def test_delete_existing(self, client: TestClient) -> None:
        client.post(
            "/api/threads/del_t/uploads",
            files={"files": ("drop.txt", b"x", "text/plain")},
        )
        r = client.delete("/api/threads/del_t/uploads/drop.txt")
        assert r.status_code == 200
        assert r.json()["success"] is True
        # Gone from list
        listing = client.get("/api/threads/del_t/uploads/list").json()
        assert listing["count"] == 0

    def test_delete_missing_returns_404(
        self,
        client: TestClient,
    ) -> None:
        r = client.delete(
            "/api/threads/del_t/uploads/never_existed.txt",
        )
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════
# GET /api/threads/{tid}/artifacts/{artifact}
# ═══════════════════════════════════════════════════════════


class TestArtifactServe:
    def test_serves_uploaded_file_content(
        self,
        client: TestClient,
    ) -> None:
        client.post(
            "/api/threads/art_t/uploads",
            files={"files": ("a.txt", b"hello from artifact", "text/plain")},
        )
        r = client.get("/api/threads/art_t/artifacts/a.txt")
        assert r.status_code == 200
        assert r.content == b"hello from artifact"

    def test_missing_artifact_returns_404(
        self,
        client: TestClient,
    ) -> None:
        r = client.get("/api/threads/art_t/artifacts/ghost.txt")
        assert r.status_code == 404

    def test_download_param_sets_filename(
        self,
        client: TestClient,
    ) -> None:
        """Implementation note."""
        client.post(
            "/api/threads/dl_t/uploads",
            files={"files": ("note.md", b"# x\n", "text/markdown")},
        )
        r = client.get(
            "/api/threads/dl_t/artifacts/note.md?download=true",
        )
        assert r.status_code == 200
        assert r.content == b"# x\n"


# ═══════════════════════════════════════════════════════════
# Degraded-mode behavior · no stack → 503 everywhere
# ═══════════════════════════════════════════════════════════


class TestNoStackDegraded:
    def test_returns_503_when_thread_store_missing(
        self,
        isolated_cwd: Path,
    ) -> None:
        """A minimal ``create_app()`` with no stack leaves
        ``thread_store=None``. The upload endpoints must 503
        rather than AttributeError · matches the pre-split
        contract that lets demo apps launch without wiring a
        full stack."""
        app = create_app()
        c = TestClient(app)
        r = c.post(
            "/api/threads/t/uploads",
            files={"files": ("x.txt", b"x", "text/plain")},
        )
        assert r.status_code == 503
        r = c.get("/api/threads/t/uploads/list")
        assert r.status_code == 503
        r = c.delete("/api/threads/t/uploads/x.txt")
        assert r.status_code == 503
        r = c.get("/api/threads/t/artifacts/x.txt")
        assert r.status_code == 503
