"""Local image semantic search + face grouping over a persisted index.

Mirrors the read-only self-gating pattern of :mod:`semantic_code_index` —
but for a **local image library**. Built on the CLIP dual-tower:

  * ``Qdrant/clip-ViT-B-32-text``   (text encoder, dim 512)
  * ``Qdrant/clip-ViT-B-32-vision`` (image encoder, dim 512)

Both towers share the same 512-dim latent space, so a text query and an image
query can be compared by cosine against the same stored image vectors — giving
"text→image" and "image→image" search without any external service.

Face grouping is layered on top via insightface (``buffalo_l``): when a face
detector is available, each indexed image also records its face embeddings
(512-dim ArcFace), and grouping clusters those embeddings into "people" — the
same idea as a NAS AI-album's person grouping. Face tagging is optional and
self-gated: if insightface isn't installed or the model can't load, the module
still does CLIP semantic search and simply reports face capability as off.

Persistence: a single SQLite file ``data/image_index.db`` with three tables
(built lazily on first index):
  * ``image_clip``  (path, clip_embedding BLOB)         — semantic search
  * ``image_faces`` (path, face_index, face_embedding BLOB) — person grouping
  * ``image_meta``  (path, width, height, mtime)        — cheap dedup

Self-gating: no images, no CLIP tower, or ``ECHO_IMAGE_SEMANTIC=0`` →
``None`` / empty results so callers degrade to filesystem listing.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any

from ._image_semantic_vectors import (
    _blob_to_vec,
    _compute_dhash,
    _cosine,
    _ham_dist,
    _laplacian_sharpness,
    _vec_to_blob,
)

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"})
_DEFAULT_DB = Path("data/image_index.db")

# Lazy, module-level singletons (loaded once, never unloaded).
_CLIP_TEXT: Any = None
_CLIP_IMAGE: Any = None
_FACE_APP: Any = None
_LOCK = threading.Lock()


def _disabled() -> bool:
    return os.environ.get("ECHO_IMAGE_SEMANTIC", "auto").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def ort_providers() -> list[str]:
    """ONNX Runtime execution providers for the CLIP towers & face model.

    Read from ``ECHO_ORT_PROVIDERS`` (comma-separated provider names, e.g.
    ``CUDAExecutionProvider,TensorrtExecutionProvider,CPUExecutionProvider``).
    Falls back to ``["CPUExecutionProvider"]`` when unset. GPU acceleration
    (CUDA / TensorRT) is only exercised when the matching ``onnxruntime-gpu``
    build is installed; otherwise onnxruntime silently ignores the unsupported
    provider and returns CPU results — so this is safe to leave configured even
    on machines without a GPU.
    """
    raw = os.environ.get("ECHO_ORT_PROVIDERS", "").strip()
    if not raw:
        return ["CPUExecutionProvider"]
    providers = [p.strip() for p in raw.split(",") if p.strip()]
    return providers or ["CPUExecutionProvider"]


def embed_quantization() -> str | None:
    """Quantization mode for the CLIP ONNX models, or ``None`` to keep default.

    Read from ``ECHO_EMBED_QUANTIZE`` (``int8`` / ``uint8`` / ``float32`` /
    empty). On low-power NAS-like hardware, ``int8`` shrinks the model and
    speeds inference at a small accuracy cost. Passed to fastembed's
    ``quantization`` kwarg (supported since fastembed>=0.3).
    """
    raw = os.environ.get("ECHO_EMBED_QUANTIZE", "").strip().lower()
    if raw in ("int8", "uint8", "float32"):
        return raw
    return None


def _text_model() -> Any:
    global _CLIP_TEXT
    if _CLIP_TEXT is not None:
        return _CLIP_TEXT
    with _LOCK:
        if _CLIP_TEXT is not None:
            return _CLIP_TEXT
        try:
            from fastembed import TextEmbedding

            _kwargs: dict[str, Any] = {
                "model_name": "Qdrant/clip-ViT-B-32-text",
                "providers": ort_providers(),
            }
            _q = embed_quantization()
            if _q:
                _kwargs["quantization"] = _q
            _CLIP_TEXT = TextEmbedding(**_kwargs)
        except Exception:  # noqa: BLE001
            _CLIP_TEXT = None
        return _CLIP_TEXT


def _image_model() -> Any:
    global _CLIP_IMAGE
    if _CLIP_IMAGE is not None:
        return _CLIP_IMAGE
    with _LOCK:
        if _CLIP_IMAGE is not None:
            return _CLIP_IMAGE
        try:
            from fastembed import ImageEmbedding

            _kwargs: dict[str, Any] = {
                "model_name": "Qdrant/clip-ViT-B-32-vision",
                "providers": ort_providers(),
            }
            _q = embed_quantization()
            if _q:
                _kwargs["quantization"] = _q
            _CLIP_IMAGE = ImageEmbedding(**_kwargs)
        except Exception:  # noqa: BLE001
            _CLIP_IMAGE = None
        return _CLIP_IMAGE


def _face_app() -> Any:
    """Lazy insightface FaceAnalysis (``buffalo_l``); ``None`` when unavailable."""
    global _FACE_APP
    if _FACE_APP is not None:
        return _FACE_APP
    with _LOCK:
        if _FACE_APP is not None:
            return _FACE_APP
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name="buffalo_l", providers=ort_providers())
            app.prepare(ctx_id=0, det_size=(640, 640))
            _FACE_APP = app
        except Exception:  # noqa: BLE001
            _FACE_APP = None
        return _FACE_APP


def face_capable() -> bool:
    """True when a face detector is loaded (face grouping is available)."""
    return _face_app() is not None


def _open(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_clip (path TEXT PRIMARY KEY, clip_embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_faces (path TEXT, face_index INTEGER, face_embedding BLOB)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_meta (path TEXT PRIMARY KEY, width INTEGER, height INTEGER, "
        "mtime REAL, exif_time TEXT, file_type TEXT, location TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_tags (path TEXT, tag TEXT, score REAL, "
        "PRIMARY KEY (path, tag))"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS image_ocr (path TEXT PRIMARY KEY, text TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS image_hashes (path TEXT PRIMARY KEY, dhash TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS image_quality (path TEXT PRIMARY KEY, sharpness REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_categories (name TEXT PRIMARY KEY, prototype BLOB)"
    )
    return conn


def _iter_images(root: Path, max_files: int = 4000) -> list[Path]:
    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if Path(name).suffix.lower() in _IMAGE_EXTS:
                out.append(Path(dirpath) / name)
                if len(out) >= max_files:
                    return out
    return out


def _load_image(path: Path):
    try:
        from PIL import Image

        return Image.open(str(path)).convert("RGB")
    except Exception:  # noqa: BLE001
        return None


def _read_exif(pil) -> tuple[str, str]:
    """Extract ``(exif_time, location)`` from a PIL image's EXIF metadata.

    ``exif_time`` is the DateTimeOriginal tag (36867) as ``YYYY:MM:DD HH:MM:SS``.
    ``location`` is a rough ``"lat,lon"`` string parsed from GPSInfo (34853).
    Missing values fall back to empty strings — never raises."""
    exif_time = ""
    location = ""
    try:
        exif = pil.getexif()
        if exif:
            dt = exif.get(36867)
            if dt:
                exif_time = str(dt).strip()
            gps = exif.get(34853)
            if gps:
                lat = _read_gps_coord(gps, "lat")
                lon = _read_gps_coord(gps, "lon")
                if lat is not None and lon is not None:
                    location = f"{lat},{lon}"
    except Exception:  # noqa: BLE001
        pass
    return exif_time, location


def _read_gps_coord(gps, axis: str):
    """Decode a single GPS latitude/longitude coordinate from a GPSInfo dict.

    ``axis`` is ``"lat"`` or ``"lon"``. Returns a float or ``None``."""
    try:
        if axis == "lat":
            tag_ref, tag_val = 1, 2
        else:
            tag_ref, tag_val = 3, 4
        ref = (gps.get(tag_ref) or "").strip().upper()
        val = gps.get(tag_val)
        if not val or len(val) != 3:
            return None
        deg, mn, sec = (float(v) for v in val)
        coord = deg + mn / 60.0 + sec / 3600.0
        if ref in ("S", "W"):
            coord = -coord
        return round(coord, 5)
    except Exception:  # noqa: BLE001
        return None


def build_index(
    root: str | Path = ".",
    *,
    db_path: str | Path | None = None,
    include_faces: bool = True,
    max_files: int = 4000,
) -> dict[str, Any]:
    """Scan ``root`` for images and (re)build the persisted index. Returns a
    summary dict. Face embedding is optional — skipped when the detector is
    unavailable or ``include_faces`` is False."""
    if _disabled():
        return {"ok": False, "error": "disabled", "semantic": False, "faces": False}
    img_model = _image_model()
    if img_model is None:
        return {"ok": False, "error": "clip_vision_unavailable", "semantic": False}
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    root_path = Path(root)
    images = _iter_images(root_path, max_files=max_files)
    if not images:
        return {"ok": True, "indexed": 0, "semantic": True, "faces": face_capable()}

    face_app = _face_app() if include_faces else None
    conn = _open(path)
    try:
        conn.execute("DELETE FROM image_clip")
        conn.execute("DELETE FROM image_faces")
        conn.execute("DELETE FROM image_meta")
        conn.execute("DELETE FROM image_tags")
        conn.execute("DELETE FROM image_ocr")
        conn.execute("DELETE FROM image_hashes")
        conn.execute("DELETE FROM image_quality")
        indexed = 0
        face_rows = 0
        for img_path in images:
            pil = _load_image(img_path)
            if pil is None:
                continue
            try:
                vec = list(img_model.embed([pil]))[0]
            except Exception:  # noqa: BLE001
                continue
            rel = _rel(img_path, root_path)
            conn.execute(
                "INSERT OR REPLACE INTO image_clip VALUES (?, ?)",
                (rel, _vec_to_blob(vec)),
            )
            exif_time, location = _read_exif(pil)
            conn.execute(
                "INSERT OR REPLACE INTO image_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rel,
                    pil.width,
                    pil.height,
                    _mtime(img_path),
                    exif_time,
                    Path(img_path).suffix.lower(),
                    location,
                ),
            )
            dhash = _compute_dhash(pil)
            if dhash:
                conn.execute(
                    "INSERT OR REPLACE INTO image_hashes VALUES (?, ?)",
                    (rel, dhash),
                )
            conn.execute(
                "INSERT OR REPLACE INTO image_quality VALUES (?, ?)",
                (rel, _laplacian_sharpness(pil)),
            )
            indexed += 1
            if face_app is not None:
                try:
                    import numpy as np

                    faces = face_app.get(np.asarray(pil))
                    for fi, face in enumerate(faces):
                        conn.execute(
                            "INSERT INTO image_faces VALUES (?, ?, ?)",
                            (rel, fi, _vec_to_blob(face.normed_embedding)),
                        )
                        face_rows += 1
                except Exception:  # noqa: BLE001
                    continue
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "indexed": indexed,
        "faces": face_rows,
        "semantic": True,
        "face_capable": face_app is not None,
    }


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


def _load_clip_rows(db_path: Path) -> list[tuple[str, list[float]]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute("SELECT path, clip_embedding FROM image_clip").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    out: list[tuple[str, list[float]]] = []
    for path, blob in rows:
        try:
            out.append((str(path), _blob_to_vec(blob)))
        except (TypeError, ValueError):
            continue
    return out


def search_by_text(
    query: str,
    *,
    top_k: int = 10,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """Top-k images semantically closest to a text description. ``None`` when
    the semantic layer is unavailable (no index / no text tower)."""
    query = (query or "").strip()
    if not query or _disabled():
        return None
    text_model = _text_model()
    if text_model is None:
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_clip_rows(path)
    if not rows:
        return None
    try:
        q = list(text_model.embed([query]))[0]
    except Exception:  # noqa: BLE001
        return None
    scored = [(_cosine(q, vec), p) for p, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [{"path": p, "score": round(s, 4)} for s, p in scored[: max(1, int(top_k))]]


def search_by_image(
    image_path: str = "",
    *,
    top_k: int = 10,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """Top-k images visually closest to a given image file. ``None`` when the
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
    rows = _load_clip_rows(path)
    if not rows:
        return None
    scored = [(_cosine(q, vec), p) for p, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [{"path": p, "score": round(s, 4)} for s, p in scored[: max(1, int(top_k))]]


def group_faces(
    db_path: str | Path | None = None,
    *,
    threshold: float = 0.45,
) -> list[dict[str, Any]] | None:
    """Cluster face embeddings into person groups. Returns ``None`` when face
    capability is off or no faces are indexed. Each group lists image paths
    containing that person (with per-image face index)."""
    if _disabled() or not face_capable():
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute(
                "SELECT path, face_index, face_embedding FROM image_faces"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    faces = [(str(p), int(fi), v) for p, fi, blob in rows if len(v := _blob_to_vec(blob)) > 0]
    if not faces:
        return None

    # Greedy incremental clustering: each face joins the first group whose
    # running centroid is within ``threshold`` (cosine), else starts a new one.
    groups: list[list[tuple[str, int]]] = []
    centers: list[list[float]] = []
    for path, fi, vec in faces:
        placed = False
        for gi, center in enumerate(centers):
            if _cosine(vec, center) >= threshold:
                groups[gi].append((path, fi))
                # nudge centroid toward the new member (running mean)
                n = len(groups[gi])
                centers[gi] = [(c * (n - 1) + x) / n for c, x in zip(center, vec, strict=False)]
                placed = True
                break
        if not placed:
            groups.append([(path, fi)])
            centers.append(list(vec))
    return [
        {"person": idx, "faces": len(g), "images": sorted({p for p, _ in g})}
        for idx, g in enumerate(groups)
        if g
    ]


def search_face(
    image_path: str = "",
    *,
    top_k: int = 10,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """Find indexed images that contain the same face(s) as a given image.
    ``None`` when face capability is off."""
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
                "SELECT path, face_embedding FROM image_faces ORDER BY path"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    # For each query face, find the best-matching indexed face across all images.
    best: dict[str, float] = {}
    for qf in query_faces:
        qv = list(qf.normed_embedding)
        for im_path, blob in rows:
            try:
                iv = _blob_to_vec(blob)
            except (ValueError, TypeError):
                continue
            sim = _cosine(qv, iv)
            best[im_path] = max(best.get(im_path, 0.0), sim)
    ranked = sorted(best.items(), key=lambda kv: -kv[1])
    return [{"path": p, "score": round(s, 4)} for p, s in ranked[: max(1, int(top_k))]]


# ---------------------------------------------------------------------------
# Local AI-album data layer helpers (self-gating: return None / empty on miss)
# ---------------------------------------------------------------------------


_READ_DB_QUERIES = {
    ("image_categories", "name"): "SELECT name FROM image_categories",
    ("image_hashes", "path, dhash"): "SELECT path, dhash FROM image_hashes",
    ("image_quality", "path, sharpness"): "SELECT path, sharpness FROM image_quality",
    ("image_clip", "path, clip_embedding"): "SELECT path, clip_embedding FROM image_clip",
    (
        "image_meta",
        "path, width, height, mtime, exif_time, file_type, location",
    ): "SELECT path, width, height, mtime, exif_time, file_type, location FROM image_meta",
    ("image_faces", "path"): "SELECT path FROM image_faces",
}


def _read_db(db_path, *, table: str, columns: str):
    """Read ``columns`` from ``table`` in a DB file, or ``None`` on any miss."""
    query = _READ_DB_QUERIES.get((table, columns))
    if query is None:
        raise ValueError(f"unsupported image index query: {table}.{columns}")
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(str(path))
        try:
            return conn.execute(query).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def classify_image(
    image_path: str = "",
    labels: list[str] | None = None,
    *,
    db_path: str | Path | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | None:
    """Zero-shot classify an image with the CLIP text tower.

    Scores the image against the given ``labels`` (or a default set) plus any
    user-defined categories stored in ``image_categories``. Returns the top
    ``top_k`` ``[{"label", "score"}]`` sorted by descending score. ``None`` when
    the image or text tower is unavailable (self-gated — never raises)."""
    if not image_path or _disabled():
        return None
    img_model = _image_model()
    text_model = _text_model()
    if img_model is None or text_model is None:
        return None
    pil = _load_image(Path(image_path))
    if pil is None:
        return None
    try:
        img_vec = list(img_model.embed([pil]))[0]
    except Exception:  # noqa: BLE001
        return None
    label_list = (
        list(labels)
        if labels
        else [
            "风景",
            "人物",
            "食物",
            "动物",
            "文档",
            "截图",
            "建筑",
            "夜景",
            "旅行",
            "其他",
        ]
    )
    rows = _read_db(db_path, table="image_categories", columns="name")
    if rows:
        for (name,) in rows:
            name = str(name)
            if name not in label_list:
                label_list.append(name)
    try:
        text_vecs = list(text_model.embed(label_list))
    except Exception:  # noqa: BLE001
        return None
    scored = [
        (_cosine(img_vec, tv), label) for label, tv in zip(label_list, text_vecs, strict=False)
    ]
    scored.sort(key=lambda t: -t[0])
    return [{"label": label, "score": round(s, 4)} for s, label in scored[: max(1, int(top_k))]]


def ocr_image(
    image_path: str = "",
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """OCR an image via ``rapidocr_onnxruntime`` and persist the text.

    Returns ``{"text", "boxes", "confidence"}`` where ``text`` joins all
    recognized lines with ``\\n``. When OCR succeeds, the text is written to the
    ``image_ocr`` table (keyed by ``image_path``). Returns ``None`` when the
    OCR package is missing or recognition fails (self-gated — never raises)."""
    if not image_path or _disabled():
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        return None
    try:
        engine = RapidOCR()
        out = engine(str(image_path))
    except Exception:  # noqa: BLE001
        return None
    # RapidOCR returns ``(result, elapse)``; result is the list of
    # ``[box, text, score]`` detections (or None when no text found).
    if not out:
        return None
    result = out[0] if isinstance(out, (tuple, list)) and out and isinstance(out[0], list) else out
    if not result:
        return None
    pieces: list[str] = []
    boxes: list[Any] = []
    confs: list[float] = []
    for item in result:
        if not item or len(item) < 3:
            continue
        boxes.append(item[0])
        text = str(item[1])
        if text:
            pieces.append(text)
        with contextlib.suppress(TypeError, ValueError):
            confs.append(float(item[2]))
    text = "\n".join(pieces)
    confidence = sum(confs) / len(confs) if confs else 0.0
    if text:
        path = Path(db_path) if db_path is not None else _DEFAULT_DB
        try:
            conn = sqlite3.connect(str(path))
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO image_ocr VALUES (?, ?)",
                    (image_path, text),
                )
                conn.commit()
            finally:
                conn.close()
        except sqlite3.Error:  # noqa: BLE001 — OCR result remains valid if cache persistence fails
            pass
    return {"text": text, "boxes": boxes, "confidence": round(confidence, 4)}


def find_duplicates(
    *,
    db_path: str | Path | None = None,
    hash_threshold: int = 4,
) -> list[dict[str, Any]] | None:
    """Group near-duplicate images by dHash Hamming distance.

    Two images are duplicates when their dHash bit distance is
    ``<= hash_threshold``. Returns ``[{"group", "images", "representative"}]``
    only for groups of at least 2 images. Listing only — never deletes.
    ``None`` when the hashes table is missing or unavailable."""
    rows = _read_db(db_path, table="image_hashes", columns="path, dhash")
    if not rows:
        return None
    entries = [(str(p), str(h)) for p, h in rows if h]
    if not entries:
        return None
    groups: list[list[str]] = []
    for path, h in entries:
        placed = False
        for g in groups:
            if _ham_dist(
                h, entries[next(i for i, (p, _) in enumerate(entries) if p == g[0])][1]
            ) <= int(hash_threshold):
                g.append(path)
                placed = True
                break
        if not placed:
            groups.append([path])
    out: list[dict[str, Any]] = []
    for idx, g in enumerate(groups):
        if len(g) >= 2:
            out.append({"group": idx, "images": g, "representative": g[0]})
    return out or None


def find_blurry(
    *,
    db_path: str | Path | None = None,
    threshold: float = 50.0,
) -> list[dict[str, Any]] | None:
    """List images whose Laplacian sharpness is below ``threshold``.

    Returns ``[{"path", "sharpness"}]`` sorted ascending by sharpness (blurriest
    first). Listing only — never deletes. ``None`` when the quality table is
    missing or unavailable."""
    rows = _read_db(db_path, table="image_quality", columns="path, sharpness")
    if not rows:
        return None
    out = [
        {"path": str(p), "sharpness": round(float(s), 4)}
        for p, s in rows
        if s is not None and float(s) < float(threshold)
    ]
    out.sort(key=lambda d: d["sharpness"])
    return out or None


def sensitive_scan(
    *,
    db_path: str | Path | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | None:
    """Zero-shot score all indexed images against NSFW semantic labels.

    Uses the CLIP text tower to flag potentially sensitive content. Returns
    ``[{"path", "score", "label"}]`` with the highest-scoring NSFW label per
    image, sorted by score. Flagging only — never deletes or modifies anything.
    ``None`` when the text tower is unavailable or no index exists."""
    if _disabled():
        return None
    text_model = _text_model()
    if text_model is None:
        return None
    rows = _read_db(db_path, table="image_clip", columns="path, clip_embedding")
    if not rows:
        return None
    try:
        vecs = [(str(p), _blob_to_vec(blob)) for p, blob in rows]
    except (TypeError, ValueError):
        return None
    if not vecs:
        return None
    labels = ["nsfw", "explicit", "violence", "gore", "drugs", "blood", "nudity"]
    try:
        label_vecs = list(text_model.embed(labels))
    except Exception:  # noqa: BLE001
        return None
    out: list[dict[str, Any]] = []
    for path, vec in vecs:
        best_score = 0.0
        best_label = ""
        for label, lv in zip(labels, label_vecs, strict=False):
            s = _cosine(vec, lv)
            if s > best_score:
                best_score = s
                best_label = label
        if best_score > 0:
            out.append({"path": path, "score": round(best_score, 4), "label": best_label})
    out.sort(key=lambda d: -d["score"])
    return out[: max(1, int(top_k))] or None


def filter_meta(
    *,
    db_path: str | Path | None = None,
    year: int | None = None,
    month: int | None = None,
    file_type: str | None = None,
    location: str | None = None,
    min_width: int | None = None,
    min_height: int | None = None,
    person: str | None = None,
    scene: str | None = None,
) -> list[dict[str, Any]] | None:
    """Filter the image library by metadata (all conditions must match).

    Supports ``year``/``month`` (from ``exif_time``), ``file_type``,
    ``location`` substring, ``min_width``/``min_height``, ``person`` (path has a
    face record) and ``scene`` (CLIP-classified, top label containing the
    substring). Returns ``[{"path", "width", "height", "mtime", "exif_time",
    "file_type", "location"}]``. ``None`` when there is no meta or no DB."""
    rows = _read_db(
        db_path,
        table="image_meta",
        columns="path, width, height, mtime, exif_time, file_type, location",
    )
    if not rows:
        return None
    face_paths: set[str] | None = None
    if person is not None:
        face_rows = _read_db(db_path, table="image_faces", columns="path")
        face_paths = {str(p) for p, _ in face_rows} if face_rows else set()

    scene_cache: dict[str, bool] = {}
    month = None if month is None else int(month)
    year = None if year is None else int(year)
    out: list[dict[str, Any]] = []
    for row in rows:
        path, width, height, mtime, exif_time, ftype, loc = row
        path = str(path)
        width = int(width) if width is not None else None
        height = int(height) if height is not None else None
        exif_time = str(exif_time) if exif_time is not None else ""
        ftype = str(ftype) if ftype else ""
        loc = str(loc) if loc else ""
        if year is not None and not exif_time.startswith(str(year)):
            continue
        if month is not None:
            part = exif_time[5:7] if len(exif_time) >= 7 else ""
            if part.isdigit() and int(part) != month:
                continue
            if not part.isdigit():
                continue
        if file_type is not None and ftype.lower() != str(file_type).lower():
            continue
        if location is not None and location.lower() not in loc.lower():
            continue
        if min_width is not None and (width is None or width < int(min_width)):
            continue
        if min_height is not None and (height is None or height < int(min_height)):
            continue
        if person is not None and (face_paths is None or path not in face_paths):
            continue
        if scene is not None:
            if path not in scene_cache:
                scene_cache[path] = _scene_matches(path, scene, db_path)
            if not scene_cache[path]:
                continue
        out.append(
            {
                "path": path,
                "width": width,
                "height": height,
                "mtime": mtime,
                "exif_time": exif_time,
                "file_type": ftype,
                "location": loc,
            }
        )
    return out or None


def _scene_matches(path: str, scene: str, db_path) -> bool:
    """Helper: does the CLIP top label for ``path`` contain ``scene``?"""
    try:
        top = classify_image(path, top_k=1, db_path=db_path)
        if not top:
            return False
        label = str(top[0]["label"]).lower()
        return scene.lower() in label
    except Exception:  # noqa: BLE001
        return False


def train_category(
    name: str = "",
    image_paths: list[str] | None = None,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Few-shot train a custom category from example images.

    Averages the CLIP image vectors of ``image_paths`` into a prototype vector
    (center of the class) and stores it in ``image_categories``. Returns
    ``{"name", "examples", "vector_dim"}``. ``None`` when the image tower is
    unavailable or no valid examples embed (self-gated — never raises)."""
    if not name or not image_paths or _disabled():
        return None
    img_model = _image_model()
    if img_model is None:
        return None
    acc: list[list[float]] = []
    for p in image_paths:
        pil = _load_image(Path(p))
        if pil is None:
            continue
        try:
            acc.append(list(img_model.embed([pil]))[0])
        except Exception:  # noqa: BLE001
            continue
    if not acc:
        return None
    dim = len(acc[0])
    proto = [sum(v[i] for v in acc) / len(acc) for i in range(dim)]
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    try:
        conn = _open(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO image_categories VALUES (?, ?)",
                (name, _vec_to_blob(proto)),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {"name": name, "examples": len(acc), "vector_dim": dim}
