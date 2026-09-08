"""Persistent incremental-index and cancellation behavior, using fixed local vectors.

These tests exercise real PNG decoding and SQLite transactions. The fake models
measure control flow and cache identity, not model quality or inference speed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from runtime.memory.hemolymph import image_semantic_index as index

_JOB = "a" * 24
_PLAN = "b" * 64
_NEXT_JOB = "c" * 24
_NEXT_PLAN = "d" * 64


class _VisionModel:
    def __init__(self):
        self.calls: list[int] = []
        self.batch_sizes: list[int] = []
        self.after_embed = None

    def embed(self, images):
        self.batch_sizes.append(len(images))
        vectors = []
        for image in images:
            color = image.getpixel((0, 0))[0]
            self.calls.append(color)
            vectors.append([float(color), 1.0])
        if self.after_embed:
            self.after_embed()
        return vectors


@pytest.fixture
def models(monkeypatch):
    state = SimpleNamespace(
        vision=_VisionModel(),
        identities={"vision": "fixed-vision-v1", "faces": "fixed-faces-v1"},
    )
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(index, "_image_model", lambda: state.vision)
    monkeypatch.setattr(index, "_face_app", lambda: None)
    monkeypatch.setattr(index, "_laplacian_sharpness", lambda _image: 12.0)
    monkeypatch.setattr(
        index,
        "_model_index_identity",
        lambda _model, kind: state.identities[kind],
        raising=False,
    )
    return state


def _photo(root: Path, name: str, color: int = 10) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (9, 13), (color, 20, 30)).save(path)
    return path


def _snapshot(db: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(db) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {
            table: sorted(
                conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"').fetchall(),
                key=repr,
            )
            for (table,) in tables
        }


def _counts(result, *, indexed: int, reused: int, embedded: int, removed: int):
    assert result["ok"] is True
    assert {key: result[key] for key in ("indexed", "reused", "embedded", "removed")} == {
        "indexed": indexed,
        "reused": reused,
        "embedded": embedded,
        "removed": removed,
    }


def _receipt(root, db, *, job=_JOB, plan=_PLAN, faces=False):
    return index.index_job_receipt(root, db_path=db, job_id=job, plan_id=plan, include_faces=faces)


def _seed_job(root, db):
    result = index.build_index(root, db_path=db, include_faces=False, job_id=_JOB, plan_id=_PLAN)
    assert result["ok"], result
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO image_ocr VALUES ('a.png', 'retained OCR')")
        conn.execute("INSERT INTO image_tags VALUES ('a.png', 'retained tag', 0.9)")
        conn.execute(
            "INSERT INTO image_faces VALUES ('a.png', 0, ?)", (index._vec_to_blob([1, 0]),)
        )
    return result


def test_unchanged_add_modify_delete_and_empty_library_update_only_required_vectors(
    tmp_path, models
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    for name, color in (("a.png", 10), ("b.png", 20), ("c.png", 30)):
        _photo(root, name, color)

    def build(expected, *, indexed, reused, removed=0):
        models.vision.calls.clear()
        result = index.build_index(root, db_path=db, include_faces=False)
        _counts(result, indexed=indexed, reused=reused, embedded=len(expected), removed=removed)
        assert sorted(models.vision.calls) == sorted(expected)

    build([10, 20, 30], indexed=3, reused=0)
    build([], indexed=3, reused=3)
    _photo(root, "d.png", 40)
    build([40], indexed=4, reused=3)
    _photo(root, "b.png", 90)
    build([90], indexed=4, reused=3)
    assert dict(index._load_clip_rows(db))["b.png"] == [90.0, 1.0]
    (root / "c.png").unlink()
    build([], indexed=3, reused=3, removed=1)
    assert set(dict(index._load_clip_rows(db))) == {"a.png", "b.png", "d.png"}
    for image in root.iterdir():
        image.unlink()
    build([], indexed=0, reused=0, removed=3)
    assert index._load_clip_rows(db) == []
    assert _snapshot(db)["image_fingerprints"] == []


def test_embedding_batches_are_bounded_and_only_include_uncached_images(
    tmp_path, models, monkeypatch
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    for number in range(5):
        _photo(root, f"{number}.png", number + 10)
    monkeypatch.setenv("ECHO_IMAGE_EMBED_BATCH_SIZE", "2")

    result = index.build_index(root, db_path=db, include_faces=False)

    assert result["ok"] is True
    assert result["embedded"] == 5
    assert models.vision.batch_sizes == [2, 2, 1]

    models.vision.batch_sizes.clear()
    assert index.build_index(root, db_path=db, include_faces=False)["reused"] == 5
    assert models.vision.batch_sizes == []

    _photo(root, "new.png", 99)
    assert index.build_index(root, db_path=db, include_faces=False)["embedded"] == 1
    assert models.vision.batch_sizes == [1]


def test_replacing_pixels_with_same_mtime_and_size_recomputes_only_that_photo(tmp_path, models):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    replaced = _photo(root, "a.png", 10)
    _photo(root, "b.png", 20)
    _seed_job(root, db)
    previous = replaced.stat()
    _photo(root, "a.png", 77)
    os.utime(replaced, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    models.vision.calls.clear()
    result = index.build_index(root, db_path=db, include_faces=False)
    _counts(result, indexed=2, reused=1, embedded=1, removed=0)
    assert models.vision.calls == [77]
    assert dict(index._load_clip_rows(db))["a.png"] == [77.0, 1.0]
    with sqlite3.connect(db) as conn:
        for table in ("image_ocr", "image_tags", "image_faces"):
            assert conn.execute(f"SELECT path FROM {table} WHERE path='a.png'").fetchall() == []


def test_changed_model_identity_recomputes_all_cached_vectors(tmp_path, models):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    _photo(root, "b.png", 20)
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    models.identities["vision"] = "fixed-vision-v2"
    models.vision.calls.clear()
    result = index.build_index(root, db_path=db, include_faces=False)
    _counts(result, indexed=2, reused=0, embedded=2, removed=0)
    assert sorted(models.vision.calls) == [10, 20]
    models.vision.calls.clear()
    _counts(
        index.build_index(root, db_path=db, include_faces=False),
        indexed=2,
        reused=2,
        embedded=0,
        removed=0,
    )
    assert models.vision.calls == []


@pytest.mark.parametrize(
    "invalid_blob",
    [b"", b"malformed", index._vec_to_blob([0, 0]), index._vec_to_blob([float("nan"), 1])],
    ids=["empty", "malformed-bytes", "zero-vector", "non-finite"],
)
def test_invalid_cached_vector_recomputes_only_its_source(tmp_path, models, invalid_blob):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    _photo(root, "b.png", 20)
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE image_clip SET clip_embedding=? WHERE path='a.png'", (invalid_blob,))
    models.vision.calls.clear()
    result = index.build_index(root, db_path=db, include_faces=False)
    _counts(result, indexed=2, reused=1, embedded=1, removed=0)
    assert models.vision.calls == [10]
    assert dict(index._load_clip_rows(db))["a.png"] == [10.0, 1.0]


def test_unknown_model_identity_never_reuses_vectors(tmp_path, models):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    models.identities["vision"] = None
    for _ in range(2):
        models.vision.calls.clear()
        _counts(
            index.build_index(root, db_path=db, include_faces=False),
            indexed=1,
            reused=0,
            embedded=1,
            removed=0,
        )
        assert models.vision.calls == [10]


def test_legacy_vectors_without_model_identity_are_recomputed(tmp_path, models):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    source = _photo(root, "a.png", 10)
    conn = index._open(db)
    try:
        with index._load_image(source) as image:
            fingerprint = index._decoded_image_fingerprint(image)
        conn.execute("INSERT INTO image_clip VALUES ('a.png', ?)", (index._vec_to_blob([99, 1]),))
        conn.execute(
            "INSERT INTO image_meta VALUES ('a.png', 9, 13, ?, '', '.png', '')",
            (source.stat().st_mtime,),
        )
        conn.execute("INSERT INTO image_fingerprints VALUES ('a.png', ?)", (fingerprint,))
        conn.execute("INSERT INTO image_index_settings VALUES ('root', ?)", (str(root.resolve()),))
        conn.commit()
    finally:
        conn.close()
    result = index.build_index(root, db_path=db, include_faces=False)
    _counts(result, indexed=1, reused=0, embedded=1, removed=0)
    assert models.vision.calls == [10]
    assert dict(index._load_clip_rows(db))["a.png"] == [10.0, 1.0]


@pytest.mark.parametrize("found_faces", [True, False], ids=["with-face", "without-face"])
def test_face_cache_tracks_detector_identity_including_empty_detections(
    tmp_path, models, monkeypatch, found_faces
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    face_calls = []

    def detect(image):
        face_calls.append(image.getpixel((0, 0))[0])
        return [SimpleNamespace(normed_embedding=[1, 0])] if found_faces else []

    monkeypatch.setattr(index, "_face_app", lambda: SimpleNamespace(get=detect))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(asarray=lambda image: image))
    assert index.build_index(root, db_path=db, include_faces=True)["ok"]
    assert face_calls == [10]
    face_calls.clear()
    models.vision.calls.clear()
    result = index.build_index(root, db_path=db, include_faces=True)
    _counts(result, indexed=1, reused=1, embedded=0, removed=0)
    assert face_calls == []
    assert models.vision.calls == []
    models.identities["faces"] = "fixed-faces-v2"
    result = index.build_index(root, db_path=db, include_faces=True)
    _counts(result, indexed=1, reused=1, embedded=0, removed=0)
    assert face_calls == [10]
    assert models.vision.calls == []
    assert result["faces"] == int(found_faces)


def test_pre_cancel_preserves_all_tables_and_avoids_model_loading(tmp_path, models, monkeypatch):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    previous_result = _seed_job(root, db)
    before = _snapshot(db)
    _photo(root, "b.png", 20)
    monkeypatch.setattr(index, "_image_model", lambda: pytest.fail("pre-cancel needs no model"))
    result = index.build_index(
        root,
        db_path=db,
        include_faces=False,
        job_id=_NEXT_JOB,
        plan_id=_NEXT_PLAN,
        should_cancel=lambda: True,
    )
    assert result["ok"] is False and result["error"] == "index_cancelled"
    assert _snapshot(db) == before
    assert _receipt(root, db) == previous_result
    assert _receipt(root, db, job=_NEXT_JOB, plan=_NEXT_PLAN) is None


def test_cancel_after_model_returns_preserves_vectors_annotations_fingerprints_and_receipt(
    tmp_path, models
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    previous_result = _seed_job(root, db)
    before = _snapshot(db)
    _photo(root, "a.png", 90)
    _photo(root, "b.png", 20)
    cancelled = threading.Event()
    models.vision.calls.clear()
    models.vision.after_embed = cancelled.set
    result = index.build_index(
        root,
        db_path=db,
        include_faces=False,
        job_id=_NEXT_JOB,
        plan_id=_NEXT_PLAN,
        should_cancel=cancelled.is_set,
    )
    assert models.vision.calls, "cancellation must happen after an actual encoder return"
    assert result["ok"] is False and result["error"] == "index_cancelled"
    assert _snapshot(db) == before
    assert _receipt(root, db) == previous_result
    assert _receipt(root, db, job=_NEXT_JOB, plan=_NEXT_PLAN) is None


def test_committed_receipt_matches_exact_job_plan_root_and_face_choice_without_models(
    tmp_path, models, monkeypatch
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    result = index.build_index(root, db_path=db, include_faces=False, job_id=_JOB, plan_id=_PLAN)
    before = _snapshot(db)
    monkeypatch.setattr(index, "_image_model", lambda: pytest.fail("receipt must be read-only"))
    monkeypatch.setattr(index, "_face_app", lambda: pytest.fail("receipt must be read-only"))
    assert _receipt(root, db) == result
    assert dict(index._load_clip_rows(db)) == {"a.png": [10.0, 1.0]}
    assert _receipt(root, db, job=_NEXT_JOB) is None
    assert _receipt(root, db, plan=_NEXT_PLAN) is None
    assert _receipt(root, db, faces=True) is None
    assert _receipt(tmp_path / "other-root", db) is None
    assert _snapshot(db) == before
    absent = tmp_path / "never-created" / "index.db"
    assert _receipt(root, absent) is None
    assert not absent.parent.exists()


def test_changed_face_encoder_never_assigns_an_old_person_name_to_another_photo(
    tmp_path, models, monkeypatch
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    _photo(root, "a.png", 10)
    _photo(root, "b.png", 20)

    def detect(image):
        first_person = image.getpixel((0, 0))[0] == 10
        if models.identities["faces"] == "fixed-faces-v2":
            first_person = not first_person
        vector = [1, 0] if first_person else [0, 1]
        return [SimpleNamespace(normed_embedding=vector)]

    monkeypatch.setattr(index, "_face_app", lambda: SimpleNamespace(get=detect))
    monkeypatch.setitem(sys.modules, "numpy", SimpleNamespace(asarray=lambda image: image))
    assert index.build_index(root, db_path=db, include_faces=True)["ok"]
    assert index.name_face_group(0, "Alice", db_path=db)["images"] == ["a.png"]
    assert [row["path"] for row in index.filter_meta(db_path=db, person="Alice")] == ["a.png"]
    with sqlite3.connect(db) as conn:
        previous_person = conn.execute(
            "SELECT name,prototype,threshold FROM image_people WHERE name='Alice'"
        ).fetchone()

    # A different recognition encoder can use a different coordinate system.
    # Its valid, equal-length vectors are not comparable to the old prototype.
    models.identities["faces"] = "fixed-faces-v2"
    assert index.build_index(root, db_path=db, include_faces=True)["ok"]
    selected = {row["path"] for row in index.filter_meta(db_path=db, person="Alice")}
    assert selected == set(), "an incompatible old prototype must wait for a new binding"
    groups = index.group_faces(db)
    assert all(group.get("name") != "Alice" for group in groups)
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute(
                "SELECT name,prototype,threshold FROM image_people WHERE name='Alice'"
            ).fetchone()
            == previous_person
        )

    # A deliberate name confirmation binds the new prototype while retaining
    # the user's existing name. Both readers must then identify the same photo.
    alice_group = next(group for group in groups if group["images"] == ["a.png"])
    assert index.name_face_group(alice_group["person"], "Alice", db_path=db)["images"] == ["a.png"]
    assert [row["path"] for row in index.filter_meta(db_path=db, person="Alice")] == ["a.png"]
    named_groups = [group for group in index.group_faces(db) if group.get("name") == "Alice"]
    assert [group["images"] for group in named_groups] == [["a.png"]]
    with sqlite3.connect(db) as conn:
        current_person = conn.execute(
            "SELECT name,prototype,threshold FROM image_people WHERE name='Alice'"
        ).fetchone()
    assert current_person[0] == previous_person[0]
    assert current_person[1] != previous_person[1]


def test_changed_vision_encoder_does_not_apply_old_trained_category_in_new_vector_space(
    tmp_path, models, monkeypatch
):
    root, db = tmp_path / "photos", tmp_path / "index.db"
    positive = _photo(root, "a.png", 10)
    negative = _photo(root, "b.png", 20)

    def embed(images):
        vectors = []
        for image in images:
            positive_example = image.getpixel((0, 0))[0] == 10
            if models.identities["vision"] == "fixed-vision-v2":
                positive_example = not positive_example
            vectors.append([1.0, 0.0] if positive_example else [0.0, 1.0])
        return vectors

    monkeypatch.setattr(models.vision, "embed", embed)
    monkeypatch.setattr(index, "_text_model", lambda: None)
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    assert index.train_category("My collection", [str(positive)], db_path=db)["examples"] == 1
    assert index.classify_image(str(positive), ["My collection"], db_path=db) == [
        {"label": "My collection", "score": 1.0}
    ]
    with sqlite3.connect(db) as conn:
        previous_category = conn.execute(
            "SELECT name,prototype FROM image_categories WHERE name='My collection'"
        ).fetchone()
    models.identities["vision"] = "fixed-vision-v2"
    # The current loaded encoder changes query coordinates immediately; waiting
    # for a library rebuild must not be required to stop using an old prototype.
    assert index.classify_image(str(negative), ["My collection"], db_path=db) is None
    assert index.build_index(root, db_path=db, include_faces=False)["ok"]
    assert index.classify_image(str(negative), ["My collection"], db_path=db) is None
    with sqlite3.connect(db) as conn:
        assert (
            conn.execute(
                "SELECT name,prototype FROM image_categories WHERE name='My collection'"
            ).fetchone()
            == previous_category
        )
    assert index.train_category("My collection", [str(positive)], db_path=db)["examples"] == 1
    assert index.classify_image(str(positive), ["My collection"], db_path=db) == [
        {"label": "My collection", "score": 1.0}
    ]
    assert index.classify_image(str(negative), ["My collection"], db_path=db) == [
        {"label": "My collection", "score": 0.0}
    ]
    with sqlite3.connect(db) as conn:
        current_category = conn.execute(
            "SELECT name,prototype FROM image_categories WHERE name='My collection'"
        ).fetchone()
    assert current_category[0] == previous_category[0]
    assert current_category[1] != previous_category[1]
