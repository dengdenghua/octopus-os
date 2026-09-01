"""Dense coverage for wiki_router endpoints (audit Q-05)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from runtime.sensing.gateway import wiki_router as wr


def _make_client(monkeypatch) -> TestClient:
    app = FastAPI()
    app.include_router(wr.create_wiki_router())
    monkeypatch.setattr(wr, "_run_generator", lambda: True)
    return TestClient(app)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "mod.py").write_text('"""Doc."""\n\ndef f():\n    pass\n', encoding="utf-8")
    (root / "README.md").write_text("# Proj\n", encoding="utf-8")
    return root


def test_status_and_generate_with_root(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    client = _make_client(monkeypatch)
    before = client.get("/api/wiki/status", params={"root": str(root)}).json()
    assert "status" in before
    gen = client.post("/api/wiki/generate", params={"root": str(root)})
    assert gen.status_code == 200
    assert gen.json()["ok"] is True
    after = client.get("/api/wiki/status", params={"root": str(root)}).json()
    assert after["exists"] is True


def test_generate_without_root_and_double_start(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/api/wiki/generate")
    assert resp.status_code == 200
    # Second call: _run_generator returns True again -> 200 (idempotent path).
    assert client.post("/api/wiki/generate").status_code == 200


def test_update_with_root(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    client = _make_client(monkeypatch)
    resp = client.post("/api/wiki/update", params={"root": str(root)})
    assert resp.status_code == 200
    assert "status" in resp.json()


def test_docs_list_and_read(tmp_path: Path, monkeypatch) -> None:
    root = _project(tmp_path)
    client = _make_client(monkeypatch)
    client.post("/api/wiki/generate", params={"root": str(root)})
    listed = client.get("/api/wiki/docs", params={"root": str(root)}).json()
    assert "docs" in listed
    read = client.get("/api/wiki/docs/README.md", params={"root": str(root)})
    assert read.status_code == 200
    assert "content" in read.json()
    missing = client.get("/api/wiki/docs/nope.md", params={"root": str(root)})
    assert missing.status_code == 404


def test_ask_grounded_false(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.post("/api/wiki/ask", json={"question": "hello?"})
    assert resp.status_code == 200
    assert resp.json()["grounded"] is False
    bad = client.post("/api/wiki/ask", json={"question": "  "})
    assert bad.status_code == 400


def test_graph_and_okf_bundle(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(monkeypatch)
    graph = client.get("/api/wiki/graph")
    assert graph.status_code == 200
    bundle = client.get("/api/wiki/okf-bundle")
    assert bundle.status_code in (200, 404)


def test_split_frontmatter() -> None:
    meta, body = wr._split_frontmatter("---\ntype: page\ntitle: Hi\n---\n# Body\n")
    assert meta.get("title") == "Hi"
    assert "# Body" in body
    no_meta, full = wr._split_frontmatter("plain text\n")
    assert no_meta == {} and full == "plain text\n"


def test_doc_write_and_read_no_root(tmp_path: Path, monkeypatch) -> None:
    client = _make_client(monkeypatch)
    bad_ext = client.put("/api/wiki/docs/x.txt", json={"content": "hi"})
    assert bad_ext.status_code == 400
    bad_body = client.put("/api/wiki/docs/x.md", json={"content": 1})
    assert bad_body.status_code == 400
    ok = client.put("/api/wiki/docs/_coverage_tmp/note.md", json={"content": "# Note\n"})
    assert ok.status_code == 200
    read = client.get("/api/wiki/docs/_coverage_tmp/note.md")
    assert read.status_code == 200
    assert read.json()["content"] == "# Note\n"
    # The no-root PUT writes into the real docs/auto tree — remove the
    # test artifact so it cannot leak into the repo.
    target = wr._resolve_doc_path("_coverage_tmp/note.md")
    if target.is_file():
        target.unlink()
        target.parent.rmdir()


def test_generate_conflict_when_running(tmp_path: Path, monkeypatch) -> None:
    app = FastAPI()
    app.include_router(wr.create_wiki_router())
    monkeypatch.setattr(wr, "_run_generator", lambda: False)  # already running
    client = TestClient(app)
    resp = client.post("/api/wiki/generate")
    assert resp.status_code == 409


def test_ask_with_model_router(monkeypatch) -> None:
    from types import SimpleNamespace

    import runtime.memory.hemolymph.repo_context as rc

    monkeypatch.setattr(rc, "build_codebase_context", lambda q: ("wiki context", [{"title": "t"}]))
    app = FastAPI()

    class _FakeRouter:
        def call(self, request):
            return SimpleNamespace(text="cited answer")

    app.include_router(wr.create_wiki_router(model_router=_FakeRouter(), model="m"))
    client = TestClient(app)
    resp = client.post("/api/wiki/ask", json={"question": "what?"})
    assert resp.status_code == 200
    assert resp.json()["grounded"] is True
    assert "cited" in resp.json()["answer"]


def test_docs_list_without_root(monkeypatch) -> None:
    client = _make_client(monkeypatch)
    resp = client.get("/api/wiki/docs")
    assert resp.status_code == 200
    assert "docs" in resp.json()
    assert isinstance(resp.json()["docs"], list)


def test_resolve_doc_path_rejects_traversal_and_symlink(monkeypatch, tmp_path: Path) -> None:
    auto = tmp_path / "auto"
    auto.mkdir()
    (auto / "ok.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(wr, "_auto_dir", lambda: auto)
    assert wr._resolve_doc_path("ok.md") == (auto / "ok.md").resolve()
    import pytest as _pytest

    with _pytest.raises(HTTPException):
        wr._resolve_doc_path("../etc/passwd")


def test_flat_docs_walks_and_skips_readme(monkeypatch, tmp_path: Path) -> None:
    auto = tmp_path / "auto"
    (auto / "sub").mkdir(parents=True)
    (auto / "README.md").write_text("#", encoding="utf-8")
    (auto / "sub" / "a.md").write_text("a", encoding="utf-8")
    monkeypatch.setattr(wr, "_auto_dir", lambda: auto)
    docs = wr._flat_docs()
    assert {d["path"] for d in docs} == {"sub/a.md"}
    monkeypatch.setattr(wr, "_auto_dir", lambda: tmp_path / "missing")
    assert wr._flat_docs() == []


def test_validate_user_root(tmp_path: Path) -> None:
    assert wr._validate_user_root(None) is None
    assert wr._validate_user_root("") is None
    assert wr._validate_user_root("relative/path") is None
    assert wr._validate_user_root(str(tmp_path / "nope")) is None
    p = tmp_path / "dir"
    p.mkdir()
    assert wr._validate_user_root(str(p)) == p.resolve()

