"""Local video semantic search + face grouping + speech search over a persisted index.

Mirrors the read-only self-gating pattern of :mod:`image_semantic_index` — but
for a **local video library**. Reuses the CLIP dual-tower and the insightface
face detector directly from :mod:`image_semantic_index` (no duplicate loading):

  * ``_image_model`` / ``_text_model``   (Qdrant clip-ViT-B-32 dual tower, 512-dim)
  * ``_face_app``                        (insightface ``buffalo_l``)
  * ``_cosine`` / ``_blob_to_vec`` / ``_vec_to_blob`` / ``_load_image``

A video is reduced to a small set of **keyframes** (one every ``min_interval_sec``
seconds, capped at ``max_frames``), each of which is embedded with the CLIP image
tower just like a still image. Faces on those keyframes are embedded with ArcFace
for person grouping across videos. Optionally the audio track is transcribed with
``faster-whisper`` for speech search.

Persistence: a single SQLite file ``data/video_index.db`` with five tables
(built lazily on first index):
  * ``video_meta``       (video_path, duration, width, height, fps, format, mtime)
  * ``video_keyframes``  (id, video_path, time_sec, clip_embedding BLOB)
  * ``video_faces``      (id, video_path, kf_time, face_index, face_embedding BLOB)
  * ``video_transcript`` (id, video_path, start_sec, end_sec, text, confidence)
  * ``video_tags``       (video_path, tag, score)  -- reserved for future use

Self-gating: no ``av``, no CLIP tower, or ``ECHO_VIDEO_SEMANTIC=0`` →
``None`` / empty results so callers degrade gracefully. Every tool function
never raises — it returns ``None`` / ``{"ok": False, ...}`` on any miss.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

from . import image_semantic_index as _img

# Re-export / reuse the underlying towers and helpers from the image index.
from .image_semantic_index import (  # noqa: F401  (re-exported for convenience)
    _blob_to_vec,
    _cosine,
    _face_app,
    _image_model,
    _load_image,
    _text_model,
    _vec_to_blob,
)

_VIDEO_EXTS = frozenset({".mp4", ".avi", ".mov", ".mkv", ".flv", ".wmv", ".webm"})
_DEFAULT_DB = Path("data/video_index.db")


def tenant_video_db_path(scope: Any | None = None) -> Path:
    """Return an index path isolated to one verified tenant/actor scope.

    The legacy process-local path is retained only for single-user mode.  A
    shared deployment must pass a resolved ``TenantScope``; callers cannot
    select this path from a request body or query parameter.
    """

    if scope is None:
        return _DEFAULT_DB
    tenant_id = str(getattr(scope, "tenant_id", "") or "").strip()
    actor_id = str(getattr(scope, "actor_id", "") or "").strip()
    if not tenant_id or not actor_id:
        return _DEFAULT_DB
    from runtime.platform.process.paths import app_paths

    suffix = hashlib.sha256(f"{tenant_id}:{actor_id}".encode()).hexdigest()[:32]
    return app_paths().data_dir / "tenants" / suffix / "video_index.db"


# Disabled flag is read from the *video* env var (independent of the image one).
def _disabled() -> bool:
    return os.environ.get("ECHO_VIDEO_SEMANTIC", "auto").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def face_capable() -> bool:
    """True when a face detector is loaded (face grouping is available)."""
    return _face_app() is not None


def hardware_accel() -> dict[str, Any]:
    """Report the configured hardware-acceleration settings.

    Returns the ONNX Runtime execution providers requested for the CLIP towers
    and face model, plus the faster-whisper device / compute type. This is the
    *configured* intent (from env); whether a provider is actually active on the
    host depends on the installed ``onnxruntime`` build supporting it.
    """
    ort = _img.ort_providers()
    return {
        "ort_providers": ort,
        "gpu_requested": any("CUDA" in p or "TensorRT" in p or "GPU" in p for p in ort),
        "embed_quantization": _img.embed_quantization(),
        "whisper_device": os.environ.get("ECHO_WHISPER_DEVICE", "cpu").strip().lower() or "cpu",
        "whisper_compute": os.environ.get("ECHO_WHISPER_COMPUTE", "int8").strip() or "int8",
        "whisper_model": os.environ.get("ECHO_WHISPER_MODEL", "small").strip() or "small",
    }


def _open(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_meta (video_path TEXT PRIMARY KEY, "
        "duration REAL, width INTEGER, height INTEGER, fps REAL, format TEXT, mtime REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_keyframes (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "video_path TEXT, time_sec REAL, clip_embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_faces (id INTEGER, video_path TEXT, "
        "kf_time REAL, face_index INTEGER, face_embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_transcript (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "video_path TEXT, start_sec REAL, end_sec REAL, text TEXT, confidence REAL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS video_tags (video_path TEXT, tag TEXT, score REAL, "
        "PRIMARY KEY (video_path, tag))"
    )
    return conn


def _iter_videos(root: Path, max_files: int = 100) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if Path(name).suffix.lower() in _VIDEO_EXTS:
                out.append(Path(dirpath) / name)
                if len(out) >= max_files:
                    return out
    return out


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.as_posix()


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _extract_keyframes(
    video_path: str | Path,
    max_frames: int = 50,
    min_interval_sec: float = 2.0,
) -> list[tuple[float, Any]]:
    """Sample keyframes from a video at a fixed interval.

    Returns a list of ``(time_sec, PIL.Image)`` (RGB). Silent, fixed-interval
    sampling keeps the frame count low; no scene-change detection for now.
    ``[]`` when ``av`` is unavailable or the video cannot be opened."""
    try:
        import av
    except ImportError:
        return []
    out: list[tuple[float, Any]] = []
    try:
        with av.open(str(video_path)) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                return []
            stream.thread_type = "AUTO"
            last_sec = -1e9
            for frame in container.decode(stream):
                t = float(frame.time)
                if t - last_sec < float(min_interval_sec):
                    continue
                img = frame.to_image().convert("RGB")
                out.append((t, img))
                last_sec = t
                if len(out) >= max_frames:
                    break
    except Exception:  # noqa: BLE001
        return []
    return out


def _video_meta(video_path: Path) -> dict[str, Any] | None:
    """Read duration / resolution / fps / format from a video via PyAV."""
    try:
        import av
    except ImportError:
        return None
    try:
        with av.open(str(video_path)) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                return None
            duration = float(stream.duration or 0.0) / float(stream.time_base or 1.0)
            if container.duration and duration <= 0:
                duration = float(container.duration) / 1e6
            return {
                "duration": duration,
                "width": int(stream.width or 0),
                "height": int(stream.height or 0),
                "fps": float(stream.average_rate or 0.0),
                "format": str(container.format.name) if container.format else "",
            }
    except Exception:  # noqa: BLE001
        return None


def build_video_index(
    root: str | Path = ".",
    *,
    db_path: str | Path | None = None,
    include_faces: bool = True,
    include_transcript: bool = False,
    max_files: int = 100,
    max_frames_per_video: int = 50,
    min_interval_sec: float = 2.0,
    incremental: bool = False,
) -> dict[str, Any]:
    """Scan ``root`` for videos and (re)build the persisted keyframe index.

    Each video is sampled into keyframes (interval ``min_interval_sec``, capped
    at ``max_frames_per_video``), each keyframe is CLIP-embedded, and optionally
    face-detected (``include_faces``) and transcribed (``include_transcript``).

    When ``incremental`` is True, only videos that are new or whose mtime
    changed since the last index are processed; stale rows for files that no
    longer exist are pruned. This avoids re-keyframing the whole library on
    every scan (the auto-index / watchdog path).

    Returns a summary dict. Self-gated: ``av`` or CLIP unavailable →
    ``{"ok": False, "error": ...}``."""
    if _disabled():
        return {"ok": False, "error": "disabled", "semantic": False, "faces": False}
    try:
        import av  # noqa: F401
    except ImportError:
        return {"ok": False, "error": "av_unavailable", "semantic": False}
    img_model = _image_model()
    if img_model is None:
        return {"ok": False, "error": "clip_vision_unavailable", "semantic": False}
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    root_path = Path(root)
    videos = _iter_videos(root_path, max_files=max_files)
    if not videos:
        return {"ok": True, "videos_indexed": 0, "semantic": True, "faces": face_capable()}

    face_app = _face_app() if include_faces else None
    conn = _open(path)
    vids = 0
    kfs = 0
    faces = 0
    try:
        if incremental:
            # Only (re)index files that are new or whose mtime changed.
            known: dict[str, float] = {}
            for row in conn.execute("SELECT video_path, mtime FROM video_meta").fetchall():
                known[row[0]] = row[1]
            pending: list[Path] = []
            for video_path in videos:
                rel = _rel(video_path, root_path)
                if rel not in known or abs(known[rel] - _mtime(video_path)) > 1e-6:
                    pending.append(video_path)
            # Prune rows for files that disappeared from the scan root.
            active = {_rel(v, root_path) for v in videos}
            for stale in [k for k in known if k not in active]:
                conn.execute("DELETE FROM video_meta WHERE video_path = ?", (stale,))
                conn.execute("DELETE FROM video_keyframes WHERE video_path = ?", (stale,))
                conn.execute("DELETE FROM video_faces WHERE video_path = ?", (stale,))
                conn.execute("DELETE FROM video_transcript WHERE video_path = ?", (stale,))
            to_index = pending
            full_reset = False
        else:
            conn.execute("DELETE FROM video_meta")
            conn.execute("DELETE FROM video_keyframes")
            conn.execute("DELETE FROM video_faces")
            conn.execute("DELETE FROM video_transcript")
            conn.execute("DELETE FROM video_tags")
            to_index = videos
            full_reset = True

        for video_path in to_index:
            rel = _rel(video_path, root_path)
            meta = _video_meta(video_path)
            if meta is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO video_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rel,
                        meta["duration"],
                        meta["width"],
                        meta["height"],
                        meta["fps"],
                        meta["format"],
                        _mtime(video_path),
                    ),
                )
            if not full_reset:
                # Drop stale rows for this file before re-adding keyframes.
                conn.execute("DELETE FROM video_keyframes WHERE video_path = ?", (rel,))
                conn.execute("DELETE FROM video_faces WHERE video_path = ?", (rel,))
                conn.execute("DELETE FROM video_transcript WHERE video_path = ?", (rel,))
            keyframes = _extract_keyframes(
                video_path, max_frames=max_frames_per_video, min_interval_sec=min_interval_sec
            )
            if not keyframes:
                continue
            vids += 1
            for time_sec, pil in keyframes:
                try:
                    vec = list(img_model.embed([pil]))[0]
                except Exception:  # noqa: BLE001
                    continue
                cur = conn.execute(
                    "INSERT INTO video_keyframes (video_path, time_sec, clip_embedding) "
                    "VALUES (?, ?, ?)",
                    (rel, time_sec, _vec_to_blob(vec)),
                )
                kf_id = cur.lastrowid
                kfs += 1
                if face_app is not None:
                    try:
                        import numpy as np

                        det = face_app.get(np.asarray(pil))
                        for fi, face in enumerate(det):
                            conn.execute(
                                "INSERT INTO video_faces VALUES (?, ?, ?, ?, ?)",
                                (kf_id, rel, time_sec, fi, _vec_to_blob(face.normed_embedding)),
                            )
                            faces += 1
                    except Exception:  # noqa: BLE001
                        continue
                # keyframe image is done with — drop the reference before next frame
                del pil
        if include_transcript and to_index:
            transcripts = _transcribe_videos(to_index, root_path, conn)
        else:
            transcripts = 0
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "videos_indexed": vids,
        "keyframes_indexed": kfs,
        "faces_indexed": faces,
        "transcripts_done": transcripts if include_transcript else 0,
        "incremental": incremental,
        "skipped": len(videos) - len(to_index) if incremental else 0,
        "semantic": True,
        "face_capable": face_app is not None,
    }


def _transcribe_videos(videos: list[Path], root: Path, conn) -> int:
    """Transcribe ``videos`` with ``faster-whisper`` and persist segments.

    Returns the number of segments written. Self-gated: ``faster-whisper``
    unavailable or any failure is silenced (returns 0).

    Device / compute / model are configurable via env so GPU acceleration and
    INT8/INT8_FLOAT16 quantization can be enabled on capable hardware:
      * ``ECHO_WHISPER_DEVICE``  (``cpu`` | ``cuda`` | ``auto``; default ``cpu``)
      * ``ECHO_WHISPER_COMPUTE`` (``int8`` | ``int8_float16`` | ``float16``; default ``int8``)
      * ``ECHO_WHISPER_MODEL``   (default ``small``)
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return 0
    device = os.environ.get("ECHO_WHISPER_DEVICE", "cpu").strip().lower() or "cpu"
    compute_type = os.environ.get("ECHO_WHISPER_COMPUTE", "int8").strip() or "int8"
    model_name = os.environ.get("ECHO_WHISPER_MODEL", "small").strip() or "small"
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception:  # noqa: BLE001
        return 0
    done = 0
    for video_path in videos:
        rel = _rel(video_path, root)
        try:
            segments, _info = model.transcribe(str(video_path))
            for seg in segments:
                conn.execute(
                    "INSERT INTO video_transcript (video_path, start_sec, end_sec, text, confidence) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (rel, float(seg.start), float(seg.end), str(seg.text), float(seg.avg_logprob)),
                )
                done += 1
        except Exception:  # noqa: BLE001
            continue
    return done


def _load_keyframe_rows(db_path: Path) -> list[tuple[int, str, float, list[float]]]:
    """Read ``(id, video_path, time_sec, clip_embedding)`` from the DB."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                "SELECT id, video_path, time_sec, clip_embedding FROM video_keyframes"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[tuple[int, str, float, list[float]]] = []
    for kf_id, video, ts, blob in rows:
        try:
            out.append((int(kf_id), str(video), float(ts), _blob_to_vec(blob)))
        except (TypeError, ValueError):
            continue
    return out


def search_video_by_text(
    query: str,
    *,
    db_path: str | Path | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]] | None:
    """Top-k keyframes semantically closest to a text description.

    Returns ``[{"video_path", "time_sec", "score"}]``. ``None`` when the
    semantic layer is unavailable (no index / no text tower)."""
    query = (query or "").strip()
    if not query or _disabled():
        return None
    text_model = _text_model()
    if text_model is None:
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_keyframe_rows(path)
    if not rows:
        return None
    try:
        q = list(text_model.embed([query]))[0]
    except Exception:  # noqa: BLE001
        return None
    scored = [(_cosine(q, vec), video, ts) for _id, video, ts, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [
        {"video_path": video, "time_sec": round(ts, 3), "score": round(s, 4)}
        for s, video, ts in scored[: max(1, int(top_k))]
    ]


def search_video_by_image(
    image_path: str = "",
    *,
    db_path: str | Path | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]] | None:
    """Top-k keyframes visually closest to a given image file.

    Returns ``[{"video_path", "time_sec", "score"}]``. ``None`` when the
    semantic layer is unavailable."""
    if not image_path or _disabled():
        return None
    img_model = _image_model()
    if img_model is None:
        return None
    pil = _load_image(Path(image_path))
    if pil is None:
        return None
    try:
        q = list(img_model.embed([pil]))[0]
    except Exception:  # noqa: BLE001
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_keyframe_rows(path)
    if not rows:
        return None
    scored = [(_cosine(q, vec), video, ts) for _id, video, ts, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [
        {"video_path": video, "time_sec": round(ts, 3), "score": round(s, 4)}
        for s, video, ts in scored[: max(1, int(top_k))]
    ]


def search_face_in_videos(
    image_path: str = "",
    *,
    db_path: str | Path | None = None,
    top_k: int = 10,
) -> list[dict[str, Any]] | None:
    """Find video keyframes containing the same face(s) as a given image.

    Returns ``[{"video_path", "time_sec", "score"}]``. ``None`` when face
    capability is off or no faces are indexed."""
    if not image_path or _disabled() or not face_capable():
        return None
    app = _face_app()
    pil = _load_image(Path(image_path))
    if pil is None:
        return None
    try:
        import numpy as np

        query_faces = app.get(np.asarray(pil))
    except Exception:  # noqa: BLE001
        return None
    if not query_faces:
        return []
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT video_path, kf_time, face_embedding FROM video_faces"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    best: dict[tuple[str, float], float] = {}
    for qf in query_faces:
        qv = list(qf.normed_embedding)
        for video, ts, blob in rows:
            try:
                iv = _blob_to_vec(blob)
            except (ValueError, TypeError):
                continue
            sim = _cosine(qv, iv)
            key = (str(video), float(ts))
            best[key] = max(best.get(key, 0.0), sim)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [
        {"video_path": video, "time_sec": round(ts, 3), "score": round(s, 4)}
        for (video, ts), s in ranked[: max(1, int(top_k))]
    ]


def group_video_faces(
    *,
    db_path: str | Path | None = None,
    threshold: float = 0.45,
) -> list[dict[str, Any]] | None:
    """Cluster face embeddings into person groups across videos.

    Returns ``[{"person", "count_faces", "appearances": [{"video_path",
    "time_sec"}]}]``. ``None`` when face capability is off or no faces are
    indexed. Mirrors :func:`image_semantic_index.group_faces` but records each
    face's ``video_path`` + ``time_sec``."""
    if _disabled() or not face_capable():
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT video_path, kf_time, face_embedding FROM video_faces"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    faces = [
        (str(video), float(ts), v) for video, ts, blob in rows if len(v := _blob_to_vec(blob)) > 0
    ]
    if not faces:
        return None

    # Greedy incremental clustering: each face joins the first group whose
    # running centroid is within ``threshold`` (cosine), else starts a new one.
    groups: list[list[tuple[str, float]]] = []
    centers: list[list[float]] = []
    for video, ts, vec in faces:
        placed = False
        for gi, center in enumerate(centers):
            if _cosine(vec, center) >= threshold:
                groups[gi].append((video, ts))
                n = len(groups[gi])
                centers[gi] = [(c * (n - 1) + x) / n for c, x in zip(center, vec, strict=False)]
                placed = True
                break
        if not placed:
            groups.append([(video, ts)])
            centers.append(list(vec))
    return [
        {
            "person": idx,
            "count_faces": len(g),
            "appearances": [{"video_path": v, "time_sec": round(ts, 3)} for v, ts in g],
        }
        for idx, g in enumerate(groups)
        if g
    ]


def search_video_by_speech(
    query: str,
    *,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """Simple text match over the transcribed speech of indexed videos.

    Returns ``[{"video_path", "start_sec", "end_sec", "text", "score"}]`` for
    every transcript segment whose text contains ``query`` (case-insensitive).
    ``None`` when ``faster-whisper`` is missing or there is no transcript."""
    query = (query or "").strip()
    if not query or _disabled():
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT video_path, start_sec, end_sec, text, confidence FROM video_transcript"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    q = query.lower()
    out: list[dict[str, Any]] = []
    for video, start, end, text, conf in rows:
        text = str(text or "")
        if q in text.lower():
            out.append(
                {
                    "video_path": str(video),
                    "start_sec": round(float(start), 3),
                    "end_sec": round(float(end), 3),
                    "text": text.strip(),
                    "score": round(float(conf), 4),
                }
            )
    return out or None


def classify_video(
    video_path: str = "",
    labels: list[str] | None = None,
    *,
    db_path: str | Path | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | None:
    """Zero-shot classify a video by averaging label scores over its keyframes.

    Scores every keyframe of ``video_path`` (from the index) against each
    ``labels`` (or a default set) with the CLIP towers and averages per label.
    Returns the top ``top_k`` ``[{"label", "score"}]`` sorted by descending
    score. ``None`` when the video has no indexed keyframes or the towers are
    unavailable (self-gated — never raises)."""
    if not video_path or _disabled():
        return None
    img_model = _image_model()
    text_model = _text_model()
    if img_model is None or text_model is None:
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT clip_embedding FROM video_keyframes WHERE video_path = ?",
                (str(video_path),),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    vecs: list[list[float]] = []
    for (blob,) in rows:
        try:
            vecs.append(_blob_to_vec(blob))
        except (TypeError, ValueError):
            continue
    if not vecs:
        return None
    label_list = (
        list(labels)
        if labels
        else [
            "风景",
            "人物",
            "城市",
            "旅行",
            "美食",
            "夜景",
            "运动",
            "会议",
            "户外",
            "室内",
            "其他",
        ]
    )
    try:
        text_vecs = list(text_model.embed(label_list))
    except Exception:  # noqa: BLE001
        return None
    agg = [0.0] * len(label_list)
    for vec in vecs:
        for i, tv in enumerate(text_vecs):
            agg[i] += _cosine(vec, tv)
    agg = [s / len(vecs) for s in agg]
    scored = sorted(zip(label_list, agg, strict=False), key=lambda t: -t[1])
    return [{"label": label, "score": round(s, 4)} for label, s in scored[: max(1, int(top_k))]]


def extract_frame_jpeg(video_path: str | Path, time_sec: float = 0.0) -> bytes | None:
    """Decode a single frame of a video and return it as JPEG bytes.

    Seeks to ``time_sec`` via PyAV's ``AVFormatContext.seek`` (backward) and
    decodes forward until reaching that timestamp; the nearest frame is encoded
    as ``image/jpeg``. Returns ``None`` on any failure (self-gated — never
    raises; ``av`` import is guarded)."""
    try:
        from io import BytesIO

        import av
    except ImportError:
        return None
    target = max(0.0, float(time_sec))
    try:
        with av.open(str(video_path)) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                return None
            stream.thread_type = "AUTO"
            container.seek(int(target * 1_000_000), backward=True)
            frame = None
            for f in container.decode(stream):
                if float(f.time) >= target:
                    frame = f
                    break
            if frame is None:
                # Seek landed past the last frame (or decode produced nothing);
                # fall back to the first decodable frame.
                container.seek(0)
                frame = next(iter(container.decode(stream)), None)
            if frame is None:
                return None
            img = frame.to_image().convert("RGB")
            buf = BytesIO()
            img.save(buf, "JPEG", quality=85)
            return buf.getvalue()
    except Exception:  # noqa: BLE001
        return None


def ocr_video_keyframes(
    query: str,
    root: str = ".",
    db_path: str | Path | None = None,
    top_k: int = 20,
    min_interval_sec: float = 2.0,
    max_frames: int = 30,
) -> list[dict[str, Any]] | None:
    """OCR the keyframes of every video under ``root`` and text-match ``query``.

    For each video, samples keyframes with :func:`_extract_keyframes`, writes
    each keyframe to a temp PNG, runs :func:`image_semantic_index.ocr_image` on
    it, and records a hit whenever the lowercased OCR text contains the
    lowercased ``query``. Returns a list of ``{"video_path", "time_sec",
    "text", "score"}`` sorted by descending ``score`` truncated to ``top_k``,
    or ``None`` when there are no hits (self-gated — never raises)."""
    if _disabled():
        return None
    q = (query or "").strip().lower()
    if not q:
        return None
    try:
        import tempfile

        root_path = Path(root)
        hits: list[dict[str, Any]] = []
        for video in _iter_videos(root_path, max_files=100):
            keyframes = _extract_keyframes(
                video, max_frames=max_frames, min_interval_sec=min_interval_sec
            )
            for time_sec, pil in keyframes:
                tmp_path = ""
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp_path = tmp.name
                    pil.save(tmp_path, "PNG")
                    ocr = _img.ocr_image(tmp_path, db_path=db_path)
                    if ocr is None:
                        continue
                    text = str(ocr.get("text") or "")
                    if q in text.lower():
                        hits.append(
                            {
                                "video_path": _rel(video, root_path),
                                "time_sec": round(time_sec, 3),
                                "text": text.strip(),
                                "score": round(float(ocr.get("confidence") or 0.0), 4),
                            }
                        )
                except Exception:  # noqa: BLE001
                    continue
                finally:
                    if tmp_path:
                        with contextlib.suppress(OSError):
                            os.unlink(tmp_path)
        if not hits:
            return None
        hits.sort(key=lambda h: -h["score"])
        return hits[: max(1, int(top_k))]
    except Exception:  # noqa: BLE001
        return None
