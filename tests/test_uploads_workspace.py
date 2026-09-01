from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from runtime.sensing.gateway.uploads_router import create_uploads_router


class _ThreadStore:
    def ensure_thread(self, thread_id: str) -> None:
        self.thread_id = thread_id


def _client(workspace_root: Path, legacy_root: Path | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_uploads_router(
            thread_store=_ThreadStore(),
            workspace_root=workspace_root,
            legacy_upload_root=legacy_root,
        )
    )
    return TestClient(app)


def test_uploads_write_to_workspace_upload_dir(tmp_path: Path) -> None:
    client = _client(tmp_path / "workspaces")

    response = client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("brief.md", b"# brief\n", "text/markdown")},
    )

    assert response.status_code == 200
    upload_path = tmp_path / "workspaces" / "th-1" / "upload" / "brief.md"
    assert upload_path.read_bytes() == b"# brief\n"
    assert Path(response.json()["files"][0]["path"]) == upload_path.resolve()
    assert (tmp_path / "workspaces" / "th-1" / "workspace.json").is_file()


def test_upload_listing_reads_workspace_before_legacy(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspaces"
    legacy_root = tmp_path / "thread_uploads"
    (legacy_root / "th-1").mkdir(parents=True)
    (legacy_root / "th-1" / "legacy.txt").write_text("old", encoding="utf-8")
    client = _client(workspace_root, legacy_root)
    client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("new.txt", b"new", "text/plain")},
    )

    response = client.get("/api/threads/th-1/uploads/list")

    assert response.status_code == 200
    files = response.json()["files"]
    assert [item["filename"] for item in files] == ["new.txt", "legacy.txt"]


def test_artifact_serves_legacy_upload_during_migration(tmp_path: Path) -> None:
    legacy_root = tmp_path / "thread_uploads"
    (legacy_root / "th-1").mkdir(parents=True)
    (legacy_root / "th-1" / "old.txt").write_text("old", encoding="utf-8")
    client = _client(tmp_path / "workspaces", legacy_root)

    response = client.get("/api/threads/th-1/artifacts/old.txt")

    assert response.status_code == 200
    assert response.content == b"old"
