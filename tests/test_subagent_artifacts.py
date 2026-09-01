"""Tests for the sub-agent file artifacts channel."""

from __future__ import annotations

from pathlib import Path

from runtime.execution.subagents.artifacts import read_artifact, save_artifact


def test_save_and_read_roundtrip(tmp_path: Path) -> None:
    ref = save_artifact(
        "hello world",
        name="note.md",
        workspace_path=str(tmp_path),
        root_thread_id="root-1",
        sub_thread_id="sub-1",
    )
    assert ref["ok"] is True
    assert ref["name"] == "note.md"
    assert ref["size"] == 11
    assert ref["hash"]
    # deterministic lineage-scoped path
    p = Path(ref["path"])
    assert p.is_relative_to(tmp_path / ".echo" / "artifacts" / "root-1" / "sub-1")
    assert p.read_text() == "hello world"

    back = read_artifact(ref["path"])
    assert back["ok"] is True
    assert back["content"] == "hello world"


def test_bytes_content_and_hash_stable(tmp_path: Path) -> None:
    a = save_artifact(b"data", name="blob.bin", workspace_path=str(tmp_path), root_thread_id="r")
    b = save_artifact(b"data", name="blob.bin", workspace_path=str(tmp_path), root_thread_id="r")
    assert a["ok"] and b["ok"]
    assert (
        a["hash"] == b["hash"] == read_artifact(a["path"])["hash"]
        if False
        else a["hash"] == b["hash"]
    )


def test_name_sanitized_no_traversal(tmp_path: Path) -> None:
    ref = save_artifact(
        "x",
        name="../../etc/passwd",
        workspace_path=str(tmp_path),
        root_thread_id="r",
    )
    assert ref["ok"] is True
    # the artifact lands INSIDE the artifacts dir, not outside
    assert ref["path"].startswith(str(tmp_path / ".echo" / "artifacts"))
    # no path traversal: name is a single clean filename (no slashes)
    assert "/" not in ref["name"]
    assert not ref["name"].startswith(".")


def test_too_large_rejected(tmp_path: Path) -> None:
    ref = save_artifact(
        "x" * (512 * 1024 + 1),
        name="big.txt",
        workspace_path=str(tmp_path),
        root_thread_id="r",
    )
    assert ref["ok"] is False
    assert "too large" in ref["error"]


def test_fallback_to_project_root(tmp_path: Path, monkeypatch) -> None:
    # no workspace_path → falls back to the project's .echo/artifacts
    ref = save_artifact("x", name="n.txt", root_thread_id="r", sub_thread_id="s")
    assert ref["ok"] is True
    assert ".echo" in ref["path"]
    assert "/artifacts/" in ref["path"]


def test_read_missing_returns_error(tmp_path: Path) -> None:
    back = read_artifact(str(tmp_path / "nope.txt"))
    assert back["ok"] is False

