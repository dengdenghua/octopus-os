"""Image-library persistence regressions using deterministic vectors, not model accuracy."""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from runtime.memory.hemolymph import image_semantic_index as index


class _ImageModel:
    def embed(self, images):
        return [[1.0, 0.0] for _ in images]


@pytest.fixture(autouse=True)
def _local_models(monkeypatch):
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(index, "_image_model", lambda: _ImageModel())
    monkeypatch.setattr(index, "_face_app", lambda: None)


def _photo(root: Path, name: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), (30, 50, 90)).save(path)
    return path


def _faces(db: Path, rows):
    conn = index._open(db)
    try:
        for path, vector in rows:
            conn.execute(
                "INSERT OR REPLACE INTO image_meta VALUES (?, 8, 8, 0, '', '.jpg', '')",
                (path,),
            )
            conn.execute(
                "INSERT INTO image_faces VALUES (?, 0, ?)",
                (path, index._vec_to_blob(vector)),
            )
        conn.commit()
    finally:
        conn.close()


def _paths(rows):
    return [row["path"] for row in rows]


def test_people_filter_uses_group_identity_and_persistent_names(tmp_path, monkeypatch):
    db = tmp_path / "photos.db"
    _faces(db, [("b.jpg", [1, 0]), ("c.jpg", [1, 0.1]), ("d.jpg", [0, 1])])
    assert _paths(index.filter_meta(db_path=db, person="0")) == ["b.jpg", "c.jpg"]
    assert _paths(index.filter_meta(db_path=db, person="1")) == ["d.jpg"]
    assert index.filter_meta(db_path=db, person="unknown") == []
    assert len(index.filter_meta(db_path=db, person="*")) == 3
    named = index.name_face_group("0", " Alice ", db_path=db)
    assert named["name"] == "Alice"
    assert named["images"] == ["b.jpg", "c.jpg"]

    # A newly indexed earlier path shifts every numeric group ID. The saved
    # name must still match Alice's vectors, including her newly added photo.
    _faces(db, [("a.jpg", [-1, 0]), ("e.jpg", [1, 0.2])])
    assert _paths(index.filter_meta(db_path=db, person="0")) == ["a.jpg"]
    assert _paths(index.filter_meta(db_path=db, person="alice")) == ["b.jpg", "c.jpg", "e.jpg"]
    monkeypatch.setattr(index, "face_capable", lambda: True)
    groups = index.group_faces(db)
    alice = next(group for group in groups if group.get("name") == "Alice")
    assert alice["person"] == 1
    assert alice["images"] == ["b.jpg", "c.jpg", "e.jpg"]
    assert index.name_face_group(99, "Missing", db_path=db) is None


@pytest.mark.parametrize("name", ["", "*", "any", "0", "x" * 121])
def test_person_names_reject_ambiguous_selectors(tmp_path, name):
    with pytest.raises(ValueError):
        index.name_face_group(0, name, db_path=tmp_path / "unused.db")


def test_trained_prototype_overrides_same_name_text_and_survives_text_failure(
    tmp_path, monkeypatch
):
    class _TextModel:
        def embed(self, labels):
            assert "Alice's collection" not in labels
            return [[0.0, 1.0] for _ in labels]

    db = tmp_path / "photos.db"
    image = _photo(tmp_path, "example.jpg")
    trained = index.train_category("Alice's collection", [str(image)], db_path=db)
    assert trained == {"name": "Alice's collection", "examples": 1, "vector_dim": 2}
    monkeypatch.setattr(index, "_text_model", lambda: _TextModel())
    results = index.classify_image(str(image), ["Alice's collection", "other"], db_path=db)
    assert results == [
        {"label": "Alice's collection", "score": 1.0},
        {"label": "other", "score": 0.0},
    ]
    monkeypatch.setattr(index, "_text_model", lambda: None)
    assert index.classify_image(str(image), db_path=db) == results[:1]


def test_invalid_training_vectors_do_not_replace_previous_category(tmp_path, monkeypatch):
    image = _photo(tmp_path, "example.jpg")
    db = tmp_path / "photos.db"
    assert index.train_category("known", [str(image)], db_path=db)

    class _InvalidModel:
        def embed(self, _images):
            return [[float("nan"), 0.0]]

    monkeypatch.setattr(index, "_image_model", lambda: _InvalidModel())
    assert index.train_category("known", [str(image)], db_path=db) is None
    with sqlite3.connect(db) as conn:
        blob = conn.execute("SELECT prototype FROM image_categories WHERE name='known'").fetchone()[
            0
        ]
    assert index._blob_to_vec(blob) == [1.0, 0.0]


def test_rebuild_keeps_unchanged_annotations_and_discards_stale_ones(tmp_path, monkeypatch):
    root = tmp_path / "library"
    for name in ("same.jpg", "changed.jpg", "deleted.jpg"):
        _photo(root, name)
    db = tmp_path / "index.db"
    mtimes = {"same.jpg": 1.0, "changed.jpg": 1.0, "deleted.jpg": 1.0}
    monkeypatch.setattr(index, "_mtime", lambda path: mtimes[Path(path).name])
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        for name in mtimes:
            conn.execute("INSERT INTO image_ocr VALUES (?, ?)", (name, "recognized text"))
            conn.execute("INSERT INTO image_tags VALUES (?, 'document', 0.8)", (name,))
    mtimes["changed.jpg"] = 2.0
    (root / "deleted.jpg").unlink()
    assert index.build_index(root, db_path=db, include_faces=False)["indexed"] == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT path FROM image_ocr").fetchall() == [("same.jpg",)]
        assert conn.execute("SELECT path FROM image_tags").fetchall() == [("same.jpg",)]


def test_ocr_uses_library_relative_key_and_survives_rebuild(tmp_path, monkeypatch):
    root = tmp_path / "library"
    image = _photo(root, "nested/document.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)

    class _OCR:
        def __call__(self, path):
            assert Path(path) == image
            return ([[[[0, 0], [8, 8]], "invoice", 0.9]], 0.01)

    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", SimpleNamespace(RapidOCR=_OCR))
    assert index.ocr_image(str(image), db_path=db)["text"] == "invoice"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT path, text FROM image_ocr").fetchall() == [
            ("nested/document.jpg", "invoice")
        ]


def test_replacing_pixels_with_identical_mtime_and_dimensions_invalidates_annotations(tmp_path):
    root = tmp_path / "library"
    replaced = _photo(root, "replaced.png")
    _photo(root, "unchanged.png")
    db = tmp_path / "index.db"
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        previous = conn.execute(
            "SELECT fingerprint FROM image_fingerprints WHERE path='replaced.png'"
        ).fetchone()[0]
        for name in ("replaced.png", "unchanged.png"):
            conn.execute("INSERT INTO image_ocr VALUES (?, 'old recognized content')", (name,))
            conn.execute("INSERT INTO image_tags VALUES (?, 'old category', 0.9)", (name,))
            conn.execute(
                "INSERT INTO image_faces VALUES (?, 0, ?)", (name, index._vec_to_blob([1, 0]))
            )
    stat = replaced.stat()
    Image.new("RGB", (8, 8), (210, 10, 40)).save(replaced)
    os.utime(replaced, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        for table in ("image_ocr", "image_tags", "image_faces"):
            assert conn.execute(f"SELECT path FROM {table}").fetchall() == [("unchanged.png",)]
        assert (
            conn.execute(
                "SELECT fingerprint FROM image_fingerprints WHERE path='replaced.png'"
            ).fetchone()[0]
            != previous
        )


def test_legacy_annotations_without_source_fingerprint_are_not_trusted(tmp_path):
    root = tmp_path / "library"
    _photo(root, "old.png")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE image_fingerprints")
        conn.execute("INSERT INTO image_ocr VALUES ('old.png', 'unverified old text')")
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM image_ocr").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM image_fingerprints").fetchone()[0] == 1


def test_empty_library_clears_old_index_without_loading_models(tmp_path, monkeypatch):
    root = tmp_path / "library"
    photo = _photo(root, "last.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    photo.unlink()
    monkeypatch.setattr(index, "_image_model", lambda: pytest.fail("empty library needs no model"))
    result = index.build_index(root, db_path=db)
    assert result["ok"] and result["indexed"] == 0
    assert index._load_clip_rows(db) == []
    assert index.filter_meta(db_path=db) == []


def test_partial_embedding_failure_rolls_back_entire_previous_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _photo(root, "old.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO image_ocr VALUES ('old.jpg', 'keep me')")
    _photo(root, "new.jpg")

    class _FailingModel:
        calls = 0

        def embed(self, _images):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("temporary model failure")
            return [[0.0, 1.0]]

    model = _FailingModel()
    monkeypatch.setattr(index, "_image_model", lambda: model)
    result = index.build_index(root, db_path=db, include_faces=False)
    assert result["error"] == "image_embedding_failed"
    assert result["retained_previous"] is True
    assert index._load_clip_rows(db) == [("old.jpg", [1.0, 0.0])]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM image_ocr").fetchall() == [("old.jpg", "keep me")]
    assert (
        index.build_index(tmp_path / "missing", db_path=db)["error"]
        == "image_directory_unavailable"
    )
    assert index._load_clip_rows(db) == [("old.jpg", [1.0, 0.0])]


def test_empty_results_are_distinct_from_missing_library(tmp_path):
    db = tmp_path / "index.db"
    index._open(db).close()
    assert index.find_duplicates(db_path=db) == []
    assert index.find_blurry(db_path=db) == []
    assert index.filter_meta(db_path=db, person="not named") == []
    assert index.filter_meta(db_path=tmp_path / "missing.db") is None
    _faces(db, [("photo.jpg", [1.0, 0.0])])
    assert _paths(index.filter_meta(db_path=db, file_type="jpg")) == ["photo.jpg"]
    assert _paths(index.filter_meta(db_path=db, file_type=".jpg")) == ["photo.jpg"]
    assert index.filter_meta(db_path=db, file_type="png") == []


def test_relative_source_prefers_registered_library_over_cwd(tmp_path, monkeypatch):
    root = tmp_path / "library"
    actual = _photo(root, "same.jpg")
    unrelated = _photo(tmp_path / "cwd", "same.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    monkeypatch.chdir(unrelated.parent)
    assert index._index_source_path("same.jpg", db) == actual


def test_reusing_database_for_new_root_clears_old_annotations(tmp_path, monkeypatch):
    first = tmp_path / "first"
    second = tmp_path / "second"
    _photo(first, "same.jpg")
    _photo(second, "same.jpg")
    monkeypatch.setattr(index, "_mtime", lambda _path: 100.0)
    db = tmp_path / "index.db"
    index.build_index(first, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO image_ocr VALUES ('same.jpg', 'first root text')")
        conn.execute("INSERT INTO image_tags VALUES ('same.jpg', 'first root tag', 1.0)")
    assert index.build_index(second, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM image_ocr").fetchall() == []
        assert conn.execute("SELECT * FROM image_tags").fetchall() == []


def test_cached_face_groups_do_not_load_detector(tmp_path, monkeypatch):
    db = tmp_path / "index.db"
    _faces(db, [("a.jpg", [1.0, 0.0]), ("b.jpg", [0.0, 1.0])])
    monkeypatch.setattr(
        index, "face_capable", lambda: pytest.fail("cached vectors need no detector")
    )
    groups = index.group_faces(db)
    assert [group["images"] for group in groups] == [["a.jpg"], ["b.jpg"]]


@pytest.mark.parametrize("include_faces", [False, True])
def test_rebuild_without_face_detector_keeps_unchanged_faces(tmp_path, monkeypatch, include_faces):
    root = tmp_path / "library"
    _photo(root, "same.jpg")
    _photo(root, "changed.jpg")
    db = tmp_path / "index.db"
    mtimes = {"same.jpg": 1.0, "changed.jpg": 1.0}
    monkeypatch.setattr(index, "_mtime", lambda path: mtimes[Path(path).name])
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        for name in mtimes:
            conn.execute(
                "INSERT INTO image_faces VALUES (?, 0, ?)", (name, index._vec_to_blob([1, 0]))
            )
    index.name_face_group(0, "Alice", db_path=db)
    mtimes["changed.jpg"] = 2.0
    result = index.build_index(root, db_path=db, include_faces=include_faces)
    assert result["ok"] and result["faces"] == 1
    assert _paths(index.filter_meta(db_path=db, person="Alice")) == ["same.jpg"]


def test_face_extraction_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _photo(root, "same.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO image_faces VALUES ('same.jpg', 0, ?)", (index._vec_to_blob([1, 0]),)
        )

    class _FaceFailure:
        def get(self, _pixels):
            raise RuntimeError("temporary face detector failure")

    # ndarray conversion is incidental to this failure/transaction test.
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(asarray=lambda value: value))
    monkeypatch.setattr(index, "_face_app", lambda: _FaceFailure())
    result = index.build_index(root, db_path=db, include_faces=True)
    assert result["error"] == "face_embedding_failed"
    assert result["retained_previous"] is True
    assert index._load_clip_rows(db) == [("same.jpg", [1.0, 0.0])]
    assert _paths(index.filter_meta(db_path=db, person="*")) == ["same.jpg"]


def test_numeric_person_filter_respects_selected_grouping_threshold(tmp_path):
    db = tmp_path / "index.db"
    _faces(db, [("a.jpg", [1, 0]), ("b.jpg", [0.8, 0.6])])
    groups = index.group_faces(db, threshold=0.9)
    assert len(groups) == 2
    assert _paths(index.filter_meta(db_path=db, person="1", person_threshold=0.9)) == ["b.jpg"]
    assert len(index.group_faces(db, threshold=0.45)) == 1


def test_image_and_face_queries_resolve_paths_from_registered_library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    actual = _photo(root, "same.jpg")
    unrelated = _photo(tmp_path / "cwd", "same.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO image_faces VALUES ('same.jpg', 0, ?)", (index._vec_to_blob([1, 0]),)
        )
    load_image = index._load_image

    def checked_loader(path):
        assert path == actual
        return load_image(path)

    monkeypatch.setattr(index, "_load_image", checked_loader)
    monkeypatch.chdir(unrelated.parent)
    assert index.search_by_image("same.jpg", db_path=db)[0]["path"] == "same.jpg"
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(asarray=lambda value: value))
    monkeypatch.setattr(index, "face_capable", lambda: True)
    monkeypatch.setattr(
        index,
        "_face_app",
        lambda: SimpleNamespace(get=lambda _pixels: [SimpleNamespace(normed_embedding=[1, 0])]),
    )
    assert index.search_face("same.jpg", db_path=db)[0]["path"] == "same.jpg"


def test_existing_temporarily_unreadable_photo_does_not_lose_cached_data(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _photo(root, "old.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO image_ocr VALUES ('old.jpg', 'keep text')")
    _photo(root, "new.jpg")
    load_image = index._load_image
    monkeypatch.setattr(
        index, "_load_image", lambda path: None if path.name == "old.jpg" else load_image(path)
    )
    result = index.build_index(root, db_path=db, include_faces=False)
    assert result["error"] == "images_unreadable"
    assert result["retained_previous"] is True
    assert index._load_clip_rows(db) == [("old.jpg", [1.0, 0.0])]
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT * FROM image_ocr").fetchall() == [("old.jpg", "keep text")]


def test_scan_failure_is_not_published_as_an_empty_library(tmp_path, monkeypatch):
    root = tmp_path / "library"
    _photo(root, "old.jpg")
    db = tmp_path / "index.db"
    index.build_index(root, db_path=db, include_faces=False)

    def inaccessible_walk(_root, *, onerror):
        onerror(PermissionError("temporarily inaccessible directory"))
        return []

    monkeypatch.setattr(index.os, "walk", inaccessible_walk)
    result = index.build_index(root, db_path=db, include_faces=False)
    assert result["error"] == "image_scan_failed"
    assert index._load_clip_rows(db) == [("old.jpg", [1.0, 0.0])]
