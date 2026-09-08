"""Permission-scoped semantic recall using SQLite and deterministic vectors."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from appliance.photos import AgentImageIndexAdapter, PhotoLibraryService
from runtime.memory.hemolymph import image_semantic_index as image_index


class _TextEncoder:
    def embed(self, _queries):
        return [[1.0, 0.0]]


def _library(tmp_path, monkeypatch):
    root = tmp_path / "nas"
    root.mkdir()
    adapter = AgentImageIndexAdapter()
    monkeypatch.setattr(adapter, "available", lambda: True)
    monkeypatch.setattr(adapter, "_module", lambda: image_index)
    monkeypatch.setattr(image_index, "_text_model", lambda: _TextEncoder())
    monkeypatch.delenv("ECHO_IMAGE_SEMANTIC", raising=False)
    service = PhotoLibraryService(root, tmp_path / "state", backend=adapter)
    rows = [(f"private/{number:02d}.jpg", [1.0, 0.0]) for number in range(48)]
    rows += [("shared/own.jpg", [0.6, 0.8]), ("shared/second.jpg", [0.3, 0.95])]
    for path, _vector in rows:
        photo = root / path
        photo.parent.mkdir(exist_ok=True)
        photo.write_bytes(b"scanning does not decode images")
    with image_index._open(service.db_path) as connection:
        connection.executemany(
            "INSERT INTO image_clip VALUES (?, ?)",
            [(path, image_index._vec_to_blob(vector)) for path, vector in rows],
        )
    return service


def test_member_ranks_visible_candidates_before_global_top_k(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)
    global_results = image_index.search_by_text("beach", top_k=48, db_path=service.db_path)
    assert global_results is not None
    assert len(global_results) == 48
    assert all(item["path"].startswith("private/") for item in global_results)

    member = service.search("beach", limit=24, path_visible=lambda path: path.startswith("shared"))

    assert member["mode"] == "semantic"
    assert [item["path"] for item in member["items"]] == ["shared/own.jpg", "shared/second.jpg"]
    assert all(not Path(item["path"]).is_absolute() for item in member["items"])


def test_admin_keeps_global_ranking_and_filename_fallback(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)

    result = service.search("beach", limit=2)

    assert result["mode"] == "semantic"
    assert len(result["items"]) == 2
    assert all(item["path"].startswith("private/") for item in result["items"])
    monkeypatch.setattr(image_index, "_text_model", lambda: None)
    fallback = service.search("own", path_visible=lambda path: path.startswith("shared"))
    assert fallback["mode"] == "filename"
    assert [item["path"] for item in fallback["items"]] == ["shared/own.jpg"]


def test_scoped_candidates_exclude_unsafe_deleted_and_hidden_paths(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)
    invalid = ["../escape.jpg", str(tmp_path / "outside.jpg"), "shared/deleted.jpg"]
    with sqlite3.connect(service.db_path) as connection:
        connection.executemany(
            "INSERT INTO image_clip VALUES (?, ?)",
            [(path, image_index._vec_to_blob([1.0, 0.0])) for path in invalid],
        )

    member = service.search("beach", limit=1, path_visible=lambda path: path.startswith("shared"))

    assert [item["path"] for item in member["items"]] == ["shared/own.jpg"]
    empty = service.search("beach", path_visible=lambda _path: False)
    assert empty["items"] == []


def test_backend_results_still_pass_the_final_safe_projection(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)

    def untrusted_result(_query, *, top_k, db_path, allowed_paths):
        assert set(allowed_paths) == {"shared/own.jpg", "shared/second.jpg"}
        return [
            {"path": "private/00.jpg", "score": 1.0},
            {"path": str(service.root / "shared/own.jpg"), "score": 1.0},
            {"path": "../escape.jpg", "score": 1.0},
            {"path": "shared/own.jpg", "score": 0.6},
            {"path": "shared/own.jpg", "score": 0.6},
        ]

    monkeypatch.setattr(service._backend, "search_by_text_in_paths", untrusted_result)

    result = service.search("beach", path_visible=lambda path: path.startswith("shared"))

    assert [item["path"] for item in result["items"]] == ["shared/own.jpg"]


def test_runtime_candidate_batches_rank_together_and_bind_literal_paths(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)
    paths = [f"batch/{number:04d}.jpg" for number in range(805)]
    literal = "shared/' OR 1=1 --.jpg"
    with sqlite3.connect(service.db_path) as connection:
        connection.executemany(
            "INSERT INTO image_clip VALUES (?, ?)",
            [(path, image_index._vec_to_blob([0.1, 0.99])) for path in paths]
            + [(literal, image_index._vec_to_blob([0.9, 0.1]))],
        )

    result = image_index.search_by_text(
        "beach", top_k=1, db_path=service.db_path, allowed_paths=[*paths, literal, literal]
    )

    assert result is not None
    assert [item["path"] for item in result] == [literal]
    monkeypatch.setattr(
        image_index, "_text_model", lambda: pytest.fail("empty scope loaded a model")
    )
    assert image_index.search_by_text("beach", db_path=service.db_path, allowed_paths=[]) == []


@pytest.mark.parametrize("accepts_extra_kwargs", [False, True])
def test_old_agent_abi_falls_back_for_members_but_remains_usable_for_admin(
    tmp_path, monkeypatch, accepts_extra_kwargs
):
    service = _library(tmp_path, monkeypatch)
    calls = []

    def legacy(query, *, top_k, db_path):
        calls.append(query)
        return [{"path": "private/00.jpg", "score": 1.0}]

    def ignoring_kwargs(query, *, top_k, db_path, **_kwargs):
        return legacy(query, top_k=top_k, db_path=db_path)

    module = SimpleNamespace(search_by_text=ignoring_kwargs if accepts_extra_kwargs else legacy)
    monkeypatch.setattr(service._backend, "_module", lambda: module)

    member = service.search("own", path_visible=lambda path: path.startswith("shared"))

    assert member["mode"] == "filename"
    assert [item["path"] for item in member["items"]] == ["shared/own.jpg"]
    assert calls == []
    admin = service.search("beach", limit=1)
    assert admin["mode"] == "semantic"
    assert [item["path"] for item in admin["items"]] == ["private/00.jpg"]
    assert calls == ["beach"]


def test_old_backend_without_scoped_method_keeps_filename_fallback(tmp_path, monkeypatch):
    service = _library(tmp_path, monkeypatch)
    backend = SimpleNamespace(
        available=lambda: True,
        search_by_text=lambda *_args, **_kwargs: pytest.fail("member used global backend search"),
    )
    service._backend = backend

    result = service.search("own", path_visible=lambda path: path.startswith("shared"))

    assert result["mode"] == "filename"
    assert [item["path"] for item in result["items"]] == ["shared/own.jpg"]
