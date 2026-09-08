"""Dense coverage for image_semantic_index pure helpers (audit Q-05)."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from runtime.memory.hemolymph import image_semantic_index as isi


def test_disabled_flag(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    assert isi._disabled() is False
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "0")
    assert isi._disabled() is True
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "off")
    assert isi._disabled() is True


def test_iter_images_finds_and_caps(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")
    (tmp_path / "b.JPG").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("no", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "d.jpeg").write_bytes(b"x")
    found = isi._iter_images(tmp_path, max_files=100)
    assert len(found) == 3
    capped = isi._iter_images(tmp_path, max_files=2)
    assert len(capped) == 2


def test_load_image_and_dhash(tmp_path: Path) -> None:
    img = Image.new("RGB", (16, 16), (0, 0, 0))
    p = tmp_path / "x.png"
    img.save(p)
    loaded = isi._load_image(p)
    assert loaded is not None
    dhash = isi._compute_dhash(loaded)
    assert dhash.startswith("0x")
    assert len(dhash) >= 2
    assert isi._load_image(tmp_path / "nope.png") is None


def test_cosine_similarity() -> None:
    assert isi._cosine([1, 0], [1, 0]) == 1.0
    assert isi._cosine([1, 0], [0, 1]) == 0.0
    assert math.isclose(isi._cosine([1, 1], [1, 1]), 1.0)
    assert isi._cosine([], [1]) == 0.0
    assert isi._cosine([1, 2], [1, 2, 3]) == 0.0


def test_blob_vec_roundtrip() -> None:
    vec = [0.5, -1.25, 3.0]
    blob = isi._vec_to_blob(vec)
    assert isi._blob_to_vec(blob) == vec


def test_read_exif_and_gps(tmp_path: Path) -> None:
    img = Image.new("RGB", (4, 4))
    exif = img.getexif()
    exif[36867] = "2026:08:17 12:00:00"
    p = tmp_path / "x.jpg"
    img.save(p, exif=exif)
    loaded = Image.open(p)
    exif_time, location = isi._read_exif(loaded)
    assert exif_time == "2026:08:17 12:00:00"
    assert location == ""
    # _read_gps_coord decodes hand-built GPSInfo dicts (no PIL round-trip).
    north = isi._read_gps_coord({1: "N", 2: (30.0, 30.0, 0.0)}, "lat")
    assert north is not None and 30.0 < north < 31.0
    south = isi._read_gps_coord({1: "S", 2: (10.0, 0.0, 0.0), 3: "W", 4: (20.0, 0.0, 0.0)}, "lat")
    assert south < 0
    assert isi._read_gps_coord({}, "lat") is None
    assert isi._read_gps_coord({2: (1.0, 2.0)}, "lat") is None


def test_ham_dist() -> None:
    assert isi._ham_dist("ff", "ff") == 0
    assert isi._ham_dist("ff", "fe") == 1
    assert isi._ham_dist("00", "ff") == 8
    assert isi._ham_dist("nope", "ff") == 1 << 30


def test_find_duplicates_and_blurry(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "idx.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE image_hashes (path TEXT, dhash TEXT)")
    # 1-bit apart -> duplicate group; far apart -> separate.
    conn.executemany(
        "INSERT INTO image_hashes VALUES (?, ?)",
        [("a.png", "ff"), ("b.png", "fe"), ("c.png", "00")],
    )
    conn.execute("CREATE TABLE image_quality (path TEXT, sharpness REAL)")
    conn.executemany(
        "INSERT INTO image_quality VALUES (?, ?)",
        [("blur.png", 10.0), ("sharp.png", 200.0)],
    )
    conn.commit()
    conn.close()

    dups = isi.find_duplicates(db_path=db, hash_threshold=4)
    assert dups is not None
    assert any(len(g["images"]) >= 2 for g in dups)

    blurry = isi.find_blurry(db_path=db, threshold=50.0)
    assert blurry is not None
    assert blurry[0]["path"] == "blur.png"
    assert isi.find_duplicates(db_path=tmp_path / "nope.db") is None
    assert isi.find_blurry(db_path=tmp_path / "nope.db") is None


class _FakeEmbed:
    def embed(self, items):
        return [[1.0, 0.0, 0.5] for _ in items]


def test_inference_budget_is_bounded_and_released(monkeypatch):
    monkeypatch.setenv("ECHO_IMAGE_MAX_CONCURRENT_INFERENCE", "1")
    monkeypatch.setenv("ECHO_IMAGE_INFERENCE_WAIT_SECONDS", "0.01")
    entered = threading.Event()
    release = threading.Event()

    def hold_slot():
        with isi.inference_slot():
            entered.set()
            release.wait(timeout=1)

    worker = threading.Thread(target=hold_slot)
    worker.start()
    assert entered.wait(timeout=1)
    for _ in range(100):
        if isi.image_inference_status()["active"] == 1:
            break
        time.sleep(0.001)
    assert isi.image_inference_status()["active"] == 1
    with pytest.raises(isi.ImageInferenceResourceBusy), isi.inference_slot():
        pass
    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert isi.image_inference_status()["active"] == 0


def test_inference_wait_honors_cancellation(monkeypatch):
    monkeypatch.setenv("ECHO_IMAGE_MAX_CONCURRENT_INFERENCE", "1")
    entered = threading.Event()
    release = threading.Event()

    def hold_slot():
        with isi.inference_slot():
            entered.set()
            release.wait(timeout=1)

    worker = threading.Thread(target=hold_slot)
    worker.start()
    assert entered.wait(timeout=1)
    with pytest.raises(isi.ImageInferenceCancelled), isi.inference_slot(
        should_cancel=lambda: True, timeout=1
    ):
        pass
    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_inference_wait_honors_pause(monkeypatch):
    monkeypatch.setenv("ECHO_IMAGE_MAX_CONCURRENT_INFERENCE", "1")
    entered = threading.Event()
    release = threading.Event()

    def hold_slot():
        with isi.inference_slot():
            entered.set()
            release.wait(timeout=1)

    worker = threading.Thread(target=hold_slot)
    worker.start()
    assert entered.wait(timeout=1)
    with pytest.raises(isi.ImageInferencePaused), isi.inference_slot(
        should_pause=lambda: True, timeout=1
    ):
        pass
    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()


def test_inference_budget_is_shared_across_processes(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ECHO_IMAGE_MAX_CONCURRENT_INFERENCE", "1")
    gate = tmp_path / "gate"
    monkeypatch.setenv("ECHO_IMAGE_INFERENCE_GATE_DIR", str(gate))
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    child_code = """
import os
import time
from pathlib import Path

from runtime.memory.hemolymph import image_semantic_index as isi

ready = Path(os.environ["ECHO_TEST_INFERENCE_READY"])
release = Path(os.environ["ECHO_TEST_INFERENCE_RELEASE"])
with isi.inference_slot(timeout=2):
    ready.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 8
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
"""
    child_env = os.environ.copy()
    child_env.update(
        {
            "ECHO_IMAGE_INFERENCE_GATE_DIR": str(gate),
            "ECHO_TEST_INFERENCE_READY": str(ready),
            "ECHO_TEST_INFERENCE_RELEASE": str(release),
        }
    )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        if not ready.exists():
            stdout, stderr = child.communicate(timeout=2)
            pytest.fail(f"child did not acquire inference slot: {stdout}\n{stderr}")

        with pytest.raises(isi.ImageInferenceResourceBusy), isi.inference_slot(timeout=0.1):
            pass

        release.write_text("release", encoding="utf-8")
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, f"{stdout}\n{stderr}"
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_build_index_and_search(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(isi, "_image_model", lambda: _FakeEmbed())
    monkeypatch.setattr(isi, "_text_model", lambda: _FakeEmbed())
    monkeypatch.setattr(isi, "_face_app", lambda: None)

    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    Image.new("RGB", (8, 8), (10, 10, 10)).save(img_dir / "a.png")

    db = tmp_path / "idx.db"
    summary = isi.build_index(str(img_dir), db_path=db, include_faces=True)
    assert summary["ok"] is True
    assert summary["indexed"] == 1
    assert summary["semantic"] is True

    hits = isi.search_by_text("a cat", top_k=5, db_path=db)
    assert hits is not None and len(hits) == 1
    assert hits[0]["path"] == "a.png"

    img_hits = isi.search_by_image(str(img_dir / "a.png"), top_k=5, db_path=db)
    assert img_hits is not None and len(img_hits) == 1

    # disabled -> None / error
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "0")
    assert isi.search_by_text("x", db_path=db) is None

    disabled = isi.build_index(str(img_dir), db_path=db)
    assert disabled["ok"] is False and "disabled" in disabled["error"]

    # no model -> None / error
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(isi, "_image_model", lambda: None)
    no_model = isi.build_index(str(img_dir), db_path=db)
    assert no_model["ok"] is False and "clip_vision_unavailable" in no_model["error"]
    monkeypatch.setattr(isi, "_text_model", lambda: None)
    assert isi.search_by_text("x", db_path=db) is None


def test_build_index_pause_rolls_back_to_previous_snapshot(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    model = _FakeEmbed()
    monkeypatch.setattr(isi, "_image_model", lambda: model)
    monkeypatch.setattr(isi, "_face_app", lambda: None)

    img_dir = tmp_path / "imgs"
    img_dir.mkdir()
    Image.new("RGB", (8, 8), (20, 20, 20)).save(img_dir / "a.png")
    db = tmp_path / "idx.db"
    assert isi.build_index(str(img_dir), db_path=db, include_faces=False)["ok"] is True

    import sqlite3

    with sqlite3.connect(db) as conn:
        before = conn.execute("SELECT path, clip_embedding FROM image_clip").fetchall()

    paused = False
    original_embed = isi._embed

    def embed_and_pause(*args, **kwargs):
        nonlocal paused
        result = original_embed(*args, **kwargs)
        paused = True
        return result

    monkeypatch.setattr(isi, "_embed", embed_and_pause)
    result = isi.build_index(
        str(img_dir),
        db_path=db,
        include_faces=False,
        should_pause=lambda: paused,
    )
    assert result["paused"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT path, clip_embedding FROM image_clip").fetchall() == before


def test_group_faces_clusters(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(isi, "face_capable", lambda: True)
    db = tmp_path / "f.db"
    conn = isi._open(db)
    for img, vec in [("a.png", [1.0, 0.0]), ("b.png", [1.0, 0.1]), ("c.png", [0.0, 1.0])]:
        conn.execute(
            "INSERT INTO image_faces (path, face_index, face_embedding) VALUES (?, 0, ?)",
            (img, isi._vec_to_blob(vec)),
        )
    conn.commit()
    conn.close()
    groups = isi.group_faces(db, threshold=0.5)
    assert groups is not None and len(groups) == 2  # a+b together, c alone
    assert any("a.png" in g["images"] and "b.png" in g["images"] for g in groups)

    monkeypatch.setattr(isi, "face_capable", lambda: False)
    # Existing vectors can be grouped without loading a face detector.
    assert isi.group_faces(db) == groups
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "0")
    assert isi.group_faces(db) is None
    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    assert isi.group_faces(tmp_path / "nope.db") is None


def test_search_face(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    monkeypatch.setenv("ECHO_IMAGE_SEMANTIC", "auto")
    monkeypatch.setattr(isi, "face_capable", lambda: True)
    monkeypatch.setattr(
        isi,
        "_face_app",
        lambda: type(
            "A",
            (),
            {"get": staticmethod(lambda arr: [type("F", (), {"normed_embedding": [1.0, 0.0]})()])},
        )(),
    )

    db = tmp_path / "sf.db"
    conn = isi._open(db)
    conn.execute(
        "INSERT INTO image_faces (path, face_index, face_embedding) VALUES (?, 0, ?)",
        ("who.png", isi._vec_to_blob([1.0, 0.0])),
    )
    conn.commit()
    conn.close()

    img = tmp_path / "q.png"
    Image.new("RGB", (8, 8), (3, 3, 3)).save(img)
    hits = isi.search_face(str(img), db_path=db)
    assert hits is not None and len(hits) == 1
    assert hits[0]["path"] == "who.png"
    assert isi.search_face("", db_path=db) is None
