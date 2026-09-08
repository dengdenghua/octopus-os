"""Dense coverage for video_semantic_index model-free helpers (audit Q-05)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from runtime.memory.hemolymph import video_semantic_index as vsi


def test_tenant_db_path(monkeypatch, tmp_path: Path) -> None:
    import runtime.platform.process.paths as pp

    assert vsi.tenant_video_db_path(None) == vsi._DEFAULT_DB
    assert vsi.tenant_video_db_path(SimpleNamespace(tenant_id="", actor_id="")) == vsi._DEFAULT_DB
    monkeypatch.setattr(pp, "app_paths", lambda: type("P", (), {"data_dir": tmp_path / "d"})())
    scoped = vsi.tenant_video_db_path(SimpleNamespace(tenant_id="t1", actor_id="alice"))
    assert str(scoped).startswith(str(tmp_path / "d" / "tenants"))
    assert scoped.name == "video_index.db"


def test_video_disabled_and_accel(monkeypatch) -> None:
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "auto")
    assert vsi._disabled() is False
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "off")
    assert vsi._disabled() is True
    monkeypatch.setenv("ECHO_WHISPER_DEVICE", "gpu")
    monkeypatch.setenv("ECHO_WHISPER_MODEL", "large")
    accel = vsi.hardware_accel()
    assert accel["whisper_device"] == "gpu"
    assert accel["whisper_model"] == "large"


def test_iter_videos_and_rel_mtime(tmp_path: Path) -> None:
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "b.MOV").write_bytes(b"x")
    (tmp_path / "c.txt").write_text("no", encoding="utf-8")
    found = vsi._iter_videos(tmp_path)
    assert len(found) == 2
    assert len(vsi._iter_videos(tmp_path, max_files=1)) == 1

    rel = vsi._rel(tmp_path / "a.mp4", tmp_path)
    assert rel == "a.mp4"
    outside = vsi._rel(Path("/elsewhere/x.mp4"), tmp_path)
    assert outside == "/elsewhere/x.mp4"

    assert vsi._mtime(tmp_path / "a.mp4") > 0
    assert vsi._mtime(tmp_path / "missing.mp4") == 0.0


def test_extract_frame_jpeg_without_av(tmp_path: Path) -> None:
    # av is not installed in CI -> self-gated None.
    p = tmp_path / "x.mp4"
    p.write_bytes(b"not-a-real-video")
    out = vsi.extract_frame_jpeg(p, time_sec=0.5)
    assert out is None or isinstance(out, bytes)


class _FakeEmbed:
    def embed(self, items):
        return [[1.0, 0.0, 0.5] for _ in items]


def test_build_video_index_error_paths(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "0")
    assert vsi.build_video_index(str(tmp_path))["error"] == "disabled"
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "auto")
    # av missing -> av_unavailable
    import sys

    monkeypatch.setitem(sys.modules, "av", None)
    out = vsi.build_video_index(str(tmp_path))
    assert out["error"] == "av_unavailable"
    # av present but no clip model
    fake_av = type("Av", (), {})()
    monkeypatch.setitem(sys.modules, "av", fake_av)
    monkeypatch.setattr(vsi, "_image_model", lambda: None)
    out2 = vsi.build_video_index(str(tmp_path))
    assert "clip_vision_unavailable" in out2["error"]


def test_video_resource_limit_rolls_back_the_previous_snapshot(monkeypatch, tmp_path: Path) -> None:
    import sys

    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "auto")
    monkeypatch.setitem(sys.modules, "av", SimpleNamespace())
    video = tmp_path / "new.mp4"
    video.write_bytes(b"synthetic")
    db = tmp_path / "video.db"
    conn = vsi._open(db)
    conn.execute(
        "INSERT INTO video_keyframes (video_path, time_sec, clip_embedding) VALUES (?, ?, ?)",
        ("old.mp4", 1.0, vsi._vec_to_blob([1.0, 0.0])),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(vsi, "_image_model", lambda: _FakeEmbed())
    monkeypatch.setattr(vsi, "_iter_videos", lambda *_args, **_kwargs: [video])
    monkeypatch.setattr(
        vsi,
        "_video_meta",
        lambda *_args, **_kwargs: {
            "duration": 1.0,
            "width": 8,
            "height": 8,
            "fps": 1.0,
            "format": "mp4",
        },
    )
    monkeypatch.setattr(
        vsi,
        "_extract_keyframes",
        lambda *_args, **_kwargs: [(0.0, Image.new("RGB", (8, 8), "red"))],
    )
    monkeypatch.setattr(
        vsi,
        "_embed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(vsi._img.ImageInferenceResourceBusy()),
    )

    result = vsi.build_video_index(tmp_path, db_path=db, include_faces=False)

    assert result == {
        "ok": False,
        "error": "image_inference_busy",
        "resource_limited": True,
        "retained_previous": True,
    }
    with vsi._open(db) as conn:
        assert conn.execute("SELECT video_path FROM video_keyframes").fetchall() == [("old.mp4",)]


def test_search_video_by_text_with_index(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "auto")
    monkeypatch.setattr(vsi, "_text_model", lambda: _FakeEmbed())
    db = tmp_path / "v.db"
    conn = vsi._open(db)
    conn.execute(
        "INSERT INTO video_keyframes (video_path, time_sec, clip_embedding) VALUES (?, ?, ?)",
        ("clip.mp4", 1.5, vsi._vec_to_blob([1.0, 0.0, 0.5])),
    )
    conn.commit()
    conn.close()

    hits = vsi.search_video_by_text("a scene", db_path=db, top_k=5)
    assert hits is not None and len(hits) == 1
    assert hits[0]["video_path"] == "clip.mp4"
    assert abs(hits[0]["time_sec"] - 1.5) < 0.01
    assert vsi.search_video_by_text("", db_path=db) is None
    monkeypatch.setattr(vsi, "_text_model", lambda: None)
    assert vsi.search_video_by_text("x", db_path=db) is None
    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "0")
    assert vsi.search_video_by_text("x", db_path=db) is None
    assert vsi.search_video_by_text("x", db_path=tmp_path / "nope.db") is None


def test_search_video_by_image_with_index(monkeypatch, tmp_path: Path) -> None:
    from PIL import Image

    monkeypatch.setenv("ECHO_VIDEO_SEMANTIC", "auto")
    monkeypatch.setattr(vsi, "_image_model", lambda: _FakeEmbed())
    db = tmp_path / "v2.db"
    conn = vsi._open(db)
    conn.execute(
        "INSERT INTO video_keyframes (video_path, time_sec, clip_embedding) VALUES (?, ?, ?)",
        ("clip2.mp4", 2.5, vsi._vec_to_blob([1.0, 0.0, 0.5])),
    )
    conn.commit()
    conn.close()

    img = tmp_path / "query.png"
    Image.new("RGB", (8, 8), (5, 5, 5)).save(img)

    hits = vsi.search_video_by_image(str(img), db_path=db, top_k=3)
    assert hits is not None and len(hits) == 1
    assert hits[0]["video_path"] == "clip2.mp4"
    assert abs(hits[0]["time_sec"] - 2.5) < 0.01

    # error paths
    assert vsi.search_video_by_image("", db_path=db) is None
    monkeypatch.setattr(vsi, "_image_model", lambda: None)
    assert vsi.search_video_by_image(str(img), db_path=db) is None
    assert vsi.search_video_by_image(str(tmp_path / "missing.png"), db_path=db) is None
