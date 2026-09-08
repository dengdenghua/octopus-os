from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runtime.execution.suckers.builtins import _file_stats, _list_cwd, _read_file
from runtime.execution.suckers.fs_search_skills import (
    _glob_files,
    _grep_text,
    _read_file_range,
    _tree,
)
from runtime.execution.suckers.resource_refs import with_workspace_resource
from runtime.platform.process.session import Session, session_scope


def _session(root: Path) -> Session:
    return Session(
        thread_id="thread-resource",
        metadata={"mode": "code", "workspace_path": str(root)},
    )


def test_file_tools_attach_one_portable_identity_for_workspace_files(tmp_path: Path):
    target = tmp_path / "output" / "final" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello\nworld\n", encoding="utf-8")
    session = _session(tmp_path)

    with session_scope(session):
        read = _read_file(str(target))
        ranged = _read_file_range(str(target), offset=1, limit=1)
        stats = _file_stats(str(target))

    expected = "workspace-file:v1:dGhyZWFkLXJlc291cmNl:ZmluYWw:cmVwb3J0Lm1k"
    assert read["resource_id"] == expected
    assert ranged["resource_id"] == expected
    assert stats["resource_id"] == expected


def test_search_tools_attach_identity_without_exposing_it_outside_workspace(tmp_path: Path):
    target = tmp_path / "upload" / "source.txt"
    target.parent.mkdir(parents=True)
    target.write_text("source", encoding="utf-8")
    outside = tmp_path.parent / "outside-resource.txt"
    outside.write_text("outside", encoding="utf-8")
    session = _session(tmp_path)

    with session_scope(session):
        listing = _list_cwd(str(tmp_path / "upload"))
        matches = _glob_files("*.txt", root=str(tmp_path / "upload"))
        grep = _grep_text("source", root=str(tmp_path / "upload"))
        tree = _tree(root=str(tmp_path / "upload"))
        outside_result = _read_file(str(outside))

    assert listing["items"][0]["resource_id"].startswith("workspace-file:v1:")
    assert matches["files"][0]["resource_id"].startswith("workspace-file:v1:")
    assert grep["matches"][0]["resource_id"].startswith("workspace-file:v1:")
    assert tree["tree"]["children"][0]["resource_id"].startswith("workspace-file:v1:")
    assert "resource_id" not in outside_result


def test_glob_does_not_identify_directories_as_files(tmp_path: Path):
    folder = tmp_path / "output" / "final" / "folder"
    folder.mkdir(parents=True)
    session = _session(tmp_path)

    with session_scope(session):
        result = _glob_files("*", root=str(tmp_path / "output" / "final"), include_dirs=True)

    assert result["files"][0]["is_dir"] is True
    assert "resource_id" not in result["files"][0]


def test_existing_server_identity_is_preserved(tmp_path: Path):
    target = tmp_path / "output" / "final" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    result = {"path": str(target), "resource_id": "storage-file:v1:server:ref"}

    with session_scope(_session(tmp_path)):
        assert with_workspace_resource(result, target) is result


def test_existing_camel_case_identity_is_not_duplicated(tmp_path: Path):
    target = tmp_path / "output" / "final" / "report.md"
    target.parent.mkdir(parents=True)
    target.write_text("hello", encoding="utf-8")
    result = {"path": str(target), "resourceId": "storage-file:v1:server:ref"}

    with session_scope(_session(tmp_path)):
        assert with_workspace_resource(result, target) is result
        assert "resource_id" not in result


def test_openai_turn_binds_workspace_root_for_file_identity(tmp_path: Path, monkeypatch):
    from runtime.sensing.gateway.openai_gateway import turn_context

    monkeypatch.setattr(
        turn_context,
        "app_paths",
        lambda: SimpleNamespace(data_dir=tmp_path),
    )
    prepared = turn_context.prepare_chat_turn(
        None,
        turn_id="turn-resource",
        actor=None,
        agent=None,
        conversation_id="thread-resource",
        tenant_id=None,
    )

    assert prepared.session.metadata["workspace_path"] == str(prepared.workspace.root)
    target = prepared.workspace.final / "gateway.md"
    target.write_text("gateway", encoding="utf-8")
    with session_scope(prepared.session):
        result = _read_file(str(target))
    assert result["resource_id"].startswith("workspace-file:v1:")


def test_legacy_artifact_root_is_enough_for_final_identity(tmp_path: Path):
    final = tmp_path / "thread" / "output" / "final"
    final.mkdir(parents=True)
    target = final / "legacy.md"
    target.write_text("legacy", encoding="utf-8")
    session = Session(
        thread_id="thread-resource",
        metadata={"_artifact_output_root": str(final)},
    )

    with session_scope(session):
        result = _read_file(str(target))
    assert result["resource_id"].startswith("workspace-file:v1:")
