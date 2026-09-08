from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import runtime.sensing.gateway.uploads_router as uploads_router
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


def test_risky_upload_preview_uses_isolated_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, str]] = []

    def isolated(data: bytes, extension: str) -> dict[str, object]:
        calls.append((data, extension))
        return {"outcome": "ok", "text": "worker preview"}

    def direct_parser(*args: object, **kwargs: object) -> str:
        raise AssertionError("risky upload used the in-process parser")

    monkeypatch.setattr(uploads_router, "extract_document_isolated", isolated)
    monkeypatch.setattr(uploads_router, "extract_text_from_upload", direct_parser)
    client = _client(tmp_path / "workspaces")

    response = client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("brief.pdf", b"pdf bytes", "application/pdf")},
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["extracted_text"] == "worker preview"
    assert calls == [(b"pdf bytes", "pdf")]


def test_upload_succeeds_when_isolated_preview_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        uploads_router,
        "extract_document_isolated",
        lambda data, extension: {"outcome": "unavailable", "text": None},
    )
    client = _client(tmp_path / "workspaces")

    response = client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("brief.docx", b"not a real docx", "application/vnd.openxmlformats")},
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["extracted_text"] is None
    assert (tmp_path / "workspaces" / "th-1" / "upload" / "brief.docx").read_bytes() == (
        b"not a real docx"
    )


def test_plain_text_upload_keeps_lightweight_preview_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def should_not_run(data: bytes, extension: str) -> dict[str, object]:
        raise AssertionError("plain text worker call")

    monkeypatch.setattr(
        uploads_router,
        "extract_text_from_upload",
        lambda data, extension: "plain preview",
    )
    monkeypatch.setattr(uploads_router, "extract_document_isolated", should_not_run)
    client = _client(tmp_path / "workspaces")

    response = client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("brief.txt", b"plain", "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["files"][0]["extracted_text"] == "plain preview"


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


@pytest.mark.parametrize("kind", ["absolute", "missing", "other-thread", "relative", "windows"])
def test_qualified_artifact_never_falls_back_to_same_name_upload(tmp_path: Path, kind: str) -> None:
    workspace_root = tmp_path / "workspaces"
    client = _client(workspace_root)
    client.post(
        "/api/threads/th-1/uploads",
        files={"files": ("invoice.txt", b"older uploaded invoice", "text/plain")},
    )
    original = tmp_path / "documents" / "invoice.txt"
    original.parent.mkdir()
    original.write_bytes(b"current original invoice")
    if kind == "absolute":
        reference = str(original)
    elif kind == "missing":
        reference = str(tmp_path / "missing" / "invoice.txt")
    elif kind == "other-thread":
        client.post(
            "/api/threads/th-2/uploads",
            files={"files": ("invoice.txt", b"different thread invoice", "text/plain")},
        )
        reference = str(workspace_root / "th-2" / "upload" / "invoice.txt")
    elif kind == "windows":
        reference = "Z:\\other-device\\documents\\invoice.txt"
    else:
        reference = "documents/invoice.txt"

    response = client.get(f"/api/threads/th-1/artifacts/{quote(reference, safe='/')}")

    assert response.status_code == 404
    assert (
        client.get("/api/threads/th-1/artifacts/invoice.txt").content == b"older uploaded invoice"
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_artifact_preserves_exact_absolute_upload_reference(tmp_path: Path, legacy: bool) -> None:
    workspace_root = tmp_path / "workspaces"
    legacy_root = tmp_path / "thread_uploads"
    client = _client(workspace_root, legacy_root)
    target = (legacy_root / "th-1") if legacy else (workspace_root / "th-1" / "upload")
    target.mkdir(parents=True)
    original = target / "invoice.txt"
    original.write_bytes(b"exact uploaded invoice")

    response = client.get(f"/api/threads/th-1/artifacts/{quote(str(original), safe='/')}")

    assert response.status_code == 200
    assert response.content == b"exact uploaded invoice"
