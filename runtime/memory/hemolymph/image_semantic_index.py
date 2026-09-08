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

Persistence: a caller-selected SQLite library (legacy default
``data/image_index.db``), created lazily. It stores CLIP/face vectors,
metadata, OCR/tags, perceptual hashes, sharpness, trained category and named
person prototypes, and the library root. Names survive rebuilds; numeric
face-group IDs describe only the current snapshot.

Self-gating: no images, no CLIP tower, or ``ECHO_IMAGE_SEMANTIC=0`` →
``None`` / empty results so callers degrade to filesystem listing.
"""

from __future__ import annotations

import contextlib
import errno
import math
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from runtime.platform.process.paths import app_paths

from ._image_index_state import index_job_receipt as index_job_receipt
from ._image_model_runtime import (
    ImageModelConfigurationError,
    clip_options,
    mark_failed,
    mark_loaded,
    mark_loading,
    model_states,
)
from ._image_model_runtime import (
    model_index_identity as _model_index_identity,
)
from ._image_prototype_identity import (
    bind_prototype,
    face_identity,
    forget_prototype,
    read_category_prototypes,
    read_face_snapshot,
)
from ._image_semantic_vectors import (
    _blob_to_vec,
    _cosine,
    _ham_dist,
    _vec_to_blob,
)
from ._image_semantic_vectors import _compute_dhash as _compute_dhash
from ._image_semantic_vectors import _decoded_image_fingerprint as _decoded_image_fingerprint
from ._image_semantic_vectors import _laplacian_sharpness as _laplacian_sharpness

_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"})
_DEFAULT_DB = Path("data/image_index.db")

# Lazy, module-level singletons (loaded once, never unloaded).
_CLIP_TEXT: Any = None
_CLIP_IMAGE: Any = None
_FACE_APP: Any = None
_LOCK = threading.Lock()


class ImageInferenceResourceBusy(RuntimeError):
    """The bounded local inference budget is currently occupied."""

    code = "image_inference_busy"


class ImageInferenceCancelled(RuntimeError):
    """A waiting inference request was cancelled before it acquired a slot."""

    code = "image_inference_cancelled"


class ImageInferencePaused(RuntimeError):
    """A waiting inference request was paused before it acquired a slot."""

    code = "image_inference_paused"


class _InferenceSlotBusy(Exception):
    """One cross-process inference slot is held by another worker."""


class _InferenceGateUnavailable(Exception):
    """The optional cross-process inference gate cannot be used safely."""


class _InferenceProcessLease:
    """An advisory OS lock held for the duration of one native model call."""

    __slots__ = ("_descriptor", "_windows")

    def __init__(self, descriptor: int, *, windows: bool) -> None:
        self._descriptor = descriptor
        self._windows = windows

    def release(self) -> None:
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        try:
            if self._windows:
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except (ImportError, OSError):
            # Closing the descriptor still releases the kernel-owned lock.
            pass
        finally:
            with contextlib.suppress(OSError):
                os.close(descriptor)


_INFERENCE_CONDITION = threading.Condition()
_INFERENCE_ACTIVE = 0
_INFERENCE_WAITERS = 0
_DEFAULT_INFERENCE_LIMIT = 1
_MAX_INFERENCE_LIMIT = 4
_DEFAULT_INFERENCE_WAIT_SECONDS = 2.0
_MAX_INFERENCE_WAIT_SECONDS = 30.0
_DEFAULT_IMAGE_EMBED_BATCH_SIZE = 8
_MAX_IMAGE_EMBED_BATCH_SIZE = 64


def _inference_limit() -> int:
    raw = os.environ.get("ECHO_IMAGE_MAX_CONCURRENT_INFERENCE", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_INFERENCE_LIMIT
    except ValueError:
        value = _DEFAULT_INFERENCE_LIMIT
    return max(1, min(value, _MAX_INFERENCE_LIMIT))


def _inference_wait_seconds() -> float:
    raw = os.environ.get("ECHO_IMAGE_INFERENCE_WAIT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_INFERENCE_WAIT_SECONDS
    except ValueError:
        value = _DEFAULT_INFERENCE_WAIT_SECONDS
    if not math.isfinite(value):
        value = _DEFAULT_INFERENCE_WAIT_SECONDS
    return max(0.0, min(value, _MAX_INFERENCE_WAIT_SECONDS))


def image_embed_batch_size() -> int:
    """Return the bounded number of images sent to one CLIP call.

    FastEmbed accepts a sequence of images and performs substantially less
    Python/ONNX setup work when a rebuild sends a small batch.  The bound keeps
    decoded PIL images from turning a large library scan into an unbounded
    memory spike; deployments with very little RAM can set the value to ``1``.
    Invalid values use the conservative default rather than failing an index
    job before it has a chance to report a useful result.
    """

    raw = os.environ.get("ECHO_IMAGE_EMBED_BATCH_SIZE", "").strip()
    try:
        value = int(raw) if raw else _DEFAULT_IMAGE_EMBED_BATCH_SIZE
    except ValueError:
        value = _DEFAULT_IMAGE_EMBED_BATCH_SIZE
    return max(1, min(value, _MAX_IMAGE_EMBED_BATCH_SIZE))


def _inference_gate_directory(*, create: bool = True) -> Path | None:
    """Return the trusted local gate directory, or ``None`` to fail open.

    The gate is deliberately a set of advisory lock files under the runtime's
    data directory.  Kernel locks disappear when a worker crashes, so a stale
    process cannot strand the image model budget.  Deployments can point the
    directory at a shared local filesystem with ``ECHO_IMAGE_INFERENCE_GATE_DIR``.
    """

    raw = os.environ.get("ECHO_IMAGE_INFERENCE_GATE_DIR", "").strip()
    try:
        directory = Path(raw).expanduser() if raw else app_paths().data_dir / "image-inference-gate"
        if directory.is_symlink():
            return None
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        elif not directory.exists():
            return None
        if not directory.is_dir() or directory.is_symlink():
            return None
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(directory, 0o700)
        return directory
    except (OSError, RuntimeError, ValueError):
        return None


def _lock_error_is_busy(exc: OSError) -> bool:
    return exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(
        exc, "winerror", None
    ) in {33, 36}


def _acquire_process_slot(path: Path) -> _InferenceProcessLease:
    if path.is_symlink():
        raise _InferenceGateUnavailable("unsafe inference gate slot")
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise _InferenceGateUnavailable("inference gate slot unavailable") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _InferenceGateUnavailable("inference gate slot is not a file")
        if os.name == "nt":
            import msvcrt

            # msvcrt.locking starts at the current offset and accepts a byte
            # beyond EOF, so the file never needs to be rewritten by holders.
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except _InferenceGateUnavailable:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise
    except ImportError as exc:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        raise _InferenceGateUnavailable("inference gate locking unavailable") from exc
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.close(descriptor)
        if _lock_error_is_busy(exc):
            raise _InferenceSlotBusy from exc
        raise _InferenceGateUnavailable("inference gate locking failed") from exc
    return _InferenceProcessLease(descriptor, windows=os.name == "nt")


def _try_process_slot(limit: int) -> tuple[bool, _InferenceProcessLease | None]:
    """Try each local OS slot; return ``(shared, lease)``."""

    directory = _inference_gate_directory()
    if directory is None:
        return False, None
    for index in range(limit):
        try:
            return True, _acquire_process_slot(directory / f"slot-{index}.lock")
        except _InferenceSlotBusy:
            continue
        except _InferenceGateUnavailable:
            return False, None
    return True, None


def image_inference_status() -> dict[str, int | float | bool]:
    """Return bounded, non-secret local inference resource observations."""

    with _INFERENCE_CONDITION:
        active = _INFERENCE_ACTIVE
        waiting = _INFERENCE_WAITERS
    limit = _inference_limit()
    return {
        "maxConcurrent": limit,
        "active": active,
        "waiting": waiting,
        "available": max(0, limit - active),
        # Readiness is deliberately side-effect free: the first actual model
        # call creates the gate directory if the data volume permits it.
        "processShared": _inference_gate_directory(create=False) is not None,
    }


@contextlib.contextmanager
def inference_slot(
    *,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    timeout: float | None = None,
):
    """Acquire one bounded model-inference slot with cooperative backoff.

    A slot is held only around the native model call, never around image
    decoding or SQLite work. This keeps a paused/cancelled index from holding
    the model budget and lets a foreground search make progress after the
    current native call returns.
    """

    global _INFERENCE_ACTIVE, _INFERENCE_WAITERS
    wait_seconds = _inference_wait_seconds() if timeout is None else max(0.0, float(timeout))
    wait_seconds = min(wait_seconds, _MAX_INFERENCE_WAIT_SECONDS)
    deadline = time.monotonic() + wait_seconds
    process_lease: _InferenceProcessLease | None = None
    with _INFERENCE_CONDITION:
        _INFERENCE_WAITERS += 1
        try:
            while True:
                if should_cancel is not None and should_cancel():
                    raise ImageInferenceCancelled(ImageInferenceCancelled.code)
                if should_pause is not None and should_pause():
                    raise ImageInferencePaused(ImageInferencePaused.code)
                limit = _inference_limit()
                if limit > _INFERENCE_ACTIVE:
                    process_shared, candidate = _try_process_slot(limit)
                    if not process_shared or candidate is not None:
                        process_lease = candidate
                        _INFERENCE_ACTIVE += 1
                        break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ImageInferenceResourceBusy(ImageInferenceResourceBusy.code)
                _INFERENCE_CONDITION.wait(min(0.25, remaining))
        finally:
            _INFERENCE_WAITERS -= 1
    try:
        yield
    finally:
        if process_lease is not None:
            process_lease.release()
        with _INFERENCE_CONDITION:
            _INFERENCE_ACTIVE -= 1
            _INFERENCE_CONDITION.notify_all()


def _embed(
    model: Any,
    values: Sequence[Any],
    *,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
):
    with inference_slot(should_cancel=should_cancel, should_pause=should_pause):
        return list(model.embed(values))


def _detect_faces(
    app: Any,
    value: Any,
    *,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
):
    with inference_slot(should_cancel=should_cancel, should_pause=should_pause):
        return app.get(value)


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
    build is installed. The locked FastEmbed implementation rejects unavailable
    providers; a GPU request is not evidence that inference ran on a GPU.
    """
    raw = os.environ.get("ECHO_ORT_PROVIDERS", "").strip()
    if not raw:
        return ["CPUExecutionProvider"]
    providers = [p.strip() for p in raw.split(",") if p.strip()]
    return providers or ["CPUExecutionProvider"]


def embed_quantization() -> str | None:
    """Quantization mode for the CLIP ONNX models, or ``None`` to keep default.

    Read from ``ECHO_EMBED_QUANTIZE``. The locked CLIP wrappers do not implement
    int8/uint8 quantization; explicit requests are rejected during loading,
    rather than silently running full precision. Empty/float32 uses the default.
    """
    raw = os.environ.get("ECHO_EMBED_QUANTIZE", "").strip().lower()
    if raw in ("int8", "uint8", "float32"):
        return raw
    if not raw:
        return None
    raise ImageModelConfigurationError("invalid_model_configuration")


def image_model_status() -> dict[str, dict[str, str]]:
    """Observe prior loading attempts; never initialize a model from status."""
    return model_states(
        {
            "text": _CLIP_TEXT is not None,
            "vision": _CLIP_IMAGE is not None,
            "faces": _FACE_APP is not None,
        }
    )


def _text_model() -> Any:
    global _CLIP_TEXT
    if _CLIP_TEXT is not None:
        return _CLIP_TEXT
    with _LOCK:
        if _CLIP_TEXT is not None:
            return _CLIP_TEXT
        mark_loading("text")
        try:
            options = clip_options(providers=ort_providers(), quantization=embed_quantization())
            from fastembed import TextEmbedding

            _CLIP_TEXT = TextEmbedding(model_name="Qdrant/clip-ViT-B-32-text", **options)
            mark_loaded("text")
        except Exception as exc:  # noqa: BLE001 - optional models remain unavailable
            _CLIP_TEXT = None
            mark_failed("text", exc)
        return _CLIP_TEXT


def _image_model() -> Any:
    global _CLIP_IMAGE
    if _CLIP_IMAGE is not None:
        return _CLIP_IMAGE
    with _LOCK:
        if _CLIP_IMAGE is not None:
            return _CLIP_IMAGE
        mark_loading("vision")
        try:
            options = clip_options(providers=ort_providers(), quantization=embed_quantization())
            from fastembed import ImageEmbedding

            _CLIP_IMAGE = ImageEmbedding(model_name="Qdrant/clip-ViT-B-32-vision", **options)
            _model_index_identity(_CLIP_IMAGE, kind="vision")
            mark_loaded("vision")
        except Exception as exc:  # noqa: BLE001 - optional models remain unavailable
            _CLIP_IMAGE = None
            mark_failed("vision", exc)
        return _CLIP_IMAGE


def _face_app() -> Any:
    """Lazy insightface FaceAnalysis (``buffalo_l``); ``None`` when unavailable."""
    global _FACE_APP
    if _FACE_APP is not None:
        return _FACE_APP
    with _LOCK:
        if _FACE_APP is not None:
            return _FACE_APP
        mark_loading("faces")
        try:
            from runtime.safety.privacy import privacy_enabled

            if privacy_enabled() and not (Path.home() / ".insightface" / "models" / "buffalo_l").is_dir():
                raise RuntimeError("privacy_model_assets_missing")
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(name="buffalo_l", providers=ort_providers())
            app.prepare(ctx_id=0, det_size=(640, 640))
            _FACE_APP = app
            _model_index_identity(app, kind="faces")
            mark_loaded("faces")
        except Exception as exc:  # noqa: BLE001 - optional models remain unavailable
            _FACE_APP = None
            mark_failed("faces", exc)
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
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_people "
        "(name TEXT PRIMARY KEY, prototype BLOB, threshold REAL NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_index_settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_fingerprints (path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS image_face_sources "
        "(path TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, model_identity TEXT NOT NULL)"
    )
    return conn


def _iter_images(root: Path, max_files: int = 4000) -> list[Path]:
    def scan_error(error: OSError) -> None:
        raise error

    out: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root, onerror=scan_error):
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
    job_id: str | None = None,
    plan_id: str | None = None,
    should_cancel: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    empty_only: bool = False,
) -> dict[str, Any]:
    """Atomically index a snapshot with content/model-verified reuse and cancellation."""
    from ._image_index_builder import build_index as build_snapshot

    return build_snapshot(
        root,
        db_path=db_path,
        include_faces=include_faces,
        max_files=max_files,
        job_id=job_id,
        plan_id=plan_id,
        should_cancel=should_cancel,
        should_pause=should_pause,
        empty_only=empty_only,
    )


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


def _load_clip_rows(
    db_path: Path, *, allowed_paths: Sequence[str] | None = None
) -> list[tuple[str, list[float]]]:
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            if allowed_paths is None:
                rows = conn.execute("SELECT path, clip_embedding FROM image_clip").fetchall()
            else:
                paths = sorted(set(allowed_paths))
                rows = []
                # Keep within SQLite's bind limit without widening the candidate set.
                for start in range(0, len(paths), 400):
                    batch = paths[start : start + 400]
                    placeholders = ",".join("?" for _ in batch)
                    rows.extend(
                        conn.execute(
                            "SELECT path, clip_embedding FROM image_clip "
                            f"WHERE path IN ({placeholders})",
                            batch,
                        ).fetchall()
                    )
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
    allowed_paths: Sequence[str] | None = None,
) -> list[dict[str, Any]] | None:
    """Top-k images semantically closest to a text description. ``None`` when
    the semantic layer is unavailable (no index / no text tower).

    When supplied, ``allowed_paths`` restricts candidates before ranking; the
    caller owns path authorization. An empty scope never expands to the full index.
    """
    query = (query or "").strip()
    if not query or _disabled():
        return None
    if allowed_paths is not None and not allowed_paths:
        return []
    text_model = _text_model()
    if text_model is None:
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_clip_rows(path, allowed_paths=allowed_paths)
    if not rows:
        return None
    try:
        q = _embed(text_model, [query])[0]
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
    pil = _load_image(_index_source_path(image_path, db_path))
    if pil is None:
        return None
    try:
        q = _embed(img_model, [pil])[0]
    except Exception:  # noqa: BLE001
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    rows = _load_clip_rows(path)
    if not rows:
        return None
    scored = [(_cosine(q, vec), p) for p, vec in rows]
    scored.sort(key=lambda t: -t[0])
    return [{"path": p, "score": round(s, 4)} for s, p in scored[: max(1, int(top_k))]]


def _face_groups(db_path, threshold: float) -> list[dict[str, Any]]:
    snapshot = read_face_snapshot(Path(db_path) if db_path is not None else _DEFAULT_DB)
    return _cluster_face_rows(snapshot[0] if snapshot is not None else [], threshold)


def _cluster_face_rows(rows, threshold: float) -> list[dict[str, Any]]:
    if not math.isfinite(threshold) or not -1.0 <= threshold <= 1.0:
        raise ValueError("face threshold must be between -1 and 1")
    faces = []
    for p, fi, blob in rows:
        with contextlib.suppress(TypeError, ValueError):
            vec = _valid_vector(_blob_to_vec(blob))
            if vec:
                faces.append((str(p), int(fi), vec))
    if not faces:
        return []

    # Greedy incremental clustering: each face joins the first group whose
    # running centroid is within ``threshold`` (cosine), else starts a new one.
    groups: list[list[tuple[str, int]]] = []
    centers: list[list[float]] = []
    for path, fi, vec in faces:
        placed = False
        for gi, center in enumerate(centers):
            if len(vec) == len(center) and _cosine(vec, center) >= threshold:
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
        {
            "person": idx,
            "faces": len(g),
            "images": sorted({p for p, _ in g}),
            "prototype": centers[idx],
        }
        for idx, g in enumerate(groups)
        if g
    ]


def group_faces(
    db_path: str | Path | None = None,
    *,
    threshold: float = 0.45,
) -> list[dict[str, Any]] | None:
    """Group faces; numeric person IDs identify only the current sorted snapshot.

    Names are durable and match saved prototypes, not these transient IDs.
    """
    if _disabled():
        return None
    snapshot = read_face_snapshot(Path(db_path) if db_path is not None else _DEFAULT_DB)
    if snapshot is None:
        return None
    face_rows, people = snapshot
    groups = _cluster_face_rows(face_rows, threshold)
    for group in groups:
        matches = []
        for name, blob, cutoff in people:
            with contextlib.suppress(TypeError, ValueError):
                score = _cosine(group["prototype"], _blob_to_vec(blob))
                if score >= float(cutoff):
                    matches.append((score, str(name)))
        group.pop("prototype")
        if matches:
            group["name"] = max(matches)[1]
    return groups


def name_face_group(
    person: int | str = 0,
    name: str = "",
    *,
    db_path: str | Path | None = None,
    threshold: float = 0.45,
) -> dict[str, Any] | None:
    """Save a current group's name/prototype; return None if its ID is absent.

    Repeating a name replaces its prototype. Naming needs cached vectors only.
    IDs are temporary; persist and query the name across future index rebuilds.
    """
    name = name.strip()
    if not name or len(name) > 120 or name.casefold() in {"*", "any"} or name.isdecimal():
        raise ValueError("person name must be 1-120 characters and not a group ID or wildcard")
    raw_id = str(person).strip()
    if not raw_id.isdecimal():
        raise ValueError("person must be a non-negative integer group ID")
    if _disabled():
        return None
    person_id = int(raw_id)
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    if not path.is_file():
        return None
    conn = _open(path)
    try:
        # The selected group, model identity and saved prototype belong to one
        # snapshot. A concurrent rebuild must not bind old vectors to a new model.
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT path,face_index,face_embedding FROM image_faces ORDER BY path,face_index"
        ).fetchall()
        groups = _cluster_face_rows(rows, threshold)
        if person_id >= len(groups):
            return None
        group = groups[person_id]
        for (existing,) in conn.execute("SELECT name FROM image_people").fetchall():
            if existing.casefold() == name.casefold():
                conn.execute("DELETE FROM image_people WHERE name=?", (existing,))
                forget_prototype(conn, kind="person", name=existing)
        conn.execute(
            "INSERT OR REPLACE INTO image_people VALUES (?, ?, ?)",
            (name, _vec_to_blob(group["prototype"]), threshold),
        )
        bind_prototype(conn, kind="person", name=name, identity=face_identity(conn))
        conn.commit()
    finally:
        conn.close()
    return {key: value for key, value in group.items() if key != "prototype"} | {"name": name}


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
    pil = _load_image(_index_source_path(image_path, db_path))
    if pil is None:
        return None
    try:
        import numpy as np

        query_faces = _detect_faces(app, np.asarray(pil))
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
    ("image_categories", "name, prototype"): "SELECT name, prototype FROM image_categories",
    (
        "image_people",
        "name, prototype, threshold",
    ): "SELECT name, prototype, threshold FROM image_people",
    ("image_index_settings", "root"): "SELECT value FROM image_index_settings WHERE key='root'",
    ("image_hashes", "path, dhash"): "SELECT path, dhash FROM image_hashes",
    ("image_quality", "path, sharpness"): "SELECT path, sharpness FROM image_quality",
    ("image_clip", "path, clip_embedding"): "SELECT path, clip_embedding FROM image_clip",
    (
        "image_meta",
        "path, width, height, mtime, exif_time, file_type, location",
    ): "SELECT path, width, height, mtime, exif_time, file_type, location FROM image_meta",
    ("image_faces", "path"): "SELECT path FROM image_faces",
    ("image_faces", "path, face_embedding"): "SELECT path, face_embedding FROM image_faces",
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


def _valid_vector(value) -> list[float] | None:
    try:
        vector = [float(item) for item in value]
        return vector if vector and all(map(math.isfinite, vector)) and any(vector) else None
    except (TypeError, ValueError):
        return None


def _index_source_path(image_path: str, db_path) -> Path:
    path = Path(image_path)
    root = _read_db(db_path, table="image_index_settings", columns="root")
    if not path.is_absolute() and root:
        return Path(root[0][0]) / path
    return path


def classify_image(
    image_path: str = "",
    labels: list[str] | None = None,
    *,
    db_path: str | Path | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]] | None:
    """Rank labels by text similarity and trained categories by their prototypes.

    A trained prototype takes precedence over a label of the same name. Cached
    prototypes can still be used if the optional text encoder is unavailable.
    """
    if not image_path or _disabled():
        return None
    img_model = _image_model()
    text_model = _text_model()
    if img_model is None:
        return None
    pil = _load_image(_index_source_path(image_path, db_path))
    if pil is None:
        return None
    try:
        img_vec = _valid_vector(_embed(img_model, [pil])[0])
    except Exception:  # noqa: BLE001
        return None
    if img_vec is None:
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
    prototypes: dict[str, list[float]] = {}
    for name, blob in read_category_prototypes(
        Path(db_path) if db_path is not None else _DEFAULT_DB,
        _model_index_identity(img_model, kind="vision"),
    ):
        with contextlib.suppress(TypeError, ValueError):
            vector = _valid_vector(_blob_to_vec(blob))
            if vector and len(vector) == len(img_vec):
                prototypes[str(name)] = vector
    scores = {name: _cosine(img_vec, vector) for name, vector in prototypes.items()}
    text_labels = list(dict.fromkeys(label for label in label_list if label not in prototypes))
    if text_model is not None and text_labels:
        try:
            text_vecs = _embed(text_model, text_labels)
            for label, value in zip(text_labels, text_vecs, strict=False):
                vector = _valid_vector(value)
                if vector and len(vector) == len(img_vec):
                    scores[label] = _cosine(img_vec, vector)
        except Exception:  # noqa: BLE001 - valid trained prototypes remain usable
            pass
    if not scores:
        return None
    scored = sorted(
        ((score, label) for label, score in scores.items()), key=lambda t: (-t[0], t[1])
    )
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
        source_path = _index_source_path(image_path, db_path)
        out = engine(str(source_path))
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
        cache_key = image_path
        root = _read_db(db_path, table="image_index_settings", columns="root")
        if root:
            with contextlib.suppress(ValueError):
                cache_key = source_path.resolve().relative_to(Path(root[0][0])).as_posix()
        try:
            conn = _open(path)
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO image_ocr VALUES (?, ?)",
                    (cache_key, text),
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
    if rows is None:
        return None
    entries = [(str(p), str(h)) for p, h in rows if h]
    if not entries:
        return []
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
    return out


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
    if rows is None:
        return None
    out = [
        {"path": str(p), "sharpness": round(float(s), 4)}
        for p, s in rows
        if s is not None and float(s) < float(threshold)
    ]
    out.sort(key=lambda d: d["sharpness"])
    return out


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
        label_vecs = _embed(text_model, labels)
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


def _person_paths(person: str, db_path, threshold: float = 0.45) -> set[str]:
    selector = str(person).strip()
    snapshot = read_face_snapshot(Path(db_path) if db_path is not None else _DEFAULT_DB)
    if snapshot is None:
        return set()
    face_rows, people = snapshot
    if selector.casefold() in {"*", "any"}:
        return {str(row[0]) for row in face_rows}
    if selector.isdecimal():
        groups = _cluster_face_rows(face_rows, threshold)
        idx = int(selector)
        return set(groups[idx]["images"]) if idx < len(groups) else set()
    for name, blob, threshold in people:
        if str(name).casefold() != selector.casefold():
            continue
        with contextlib.suppress(TypeError, ValueError):
            prototype = _valid_vector(_blob_to_vec(blob))
            if prototype:
                paths = set()
                for path, _face_index, face_blob in face_rows:
                    with contextlib.suppress(TypeError, ValueError):
                        vec = _valid_vector(_blob_to_vec(face_blob))
                        if (
                            vec
                            and len(vec) == len(prototype)
                            and _cosine(vec, prototype) >= threshold
                        ):
                            paths.add(str(path))
                return paths
    return set()


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
    person_threshold: float = 0.45,
    scene: str | None = None,
) -> list[dict[str, Any]] | None:
    """Filter the image library by metadata (all conditions must match).

    Supports ``year``/``month`` (from ``exif_time``), ``file_type``,
    ``location`` substring, ``min_width``/``min_height``, ``person`` (a saved
    name, current numeric group ID, or ``*`` for any face). ``person_threshold``
    must match the clustering threshold used to obtain numeric IDs; saved
    names use their persisted threshold. ``scene`` matches the top label.
    Returns ``[{"path", "width", "height", "mtime", "exif_time",
    "file_type", "location"}]``. ``None`` when there is no meta or no DB."""
    rows = _read_db(
        db_path,
        table="image_meta",
        columns="path, width, height, mtime, exif_time, file_type, location",
    )
    if rows is None:
        return None
    face_paths: set[str] | None = None
    if person is not None:
        face_paths = _person_paths(person, db_path, person_threshold)

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
        if file_type is not None and ftype.lower().lstrip(".") != str(file_type).lower().lstrip(
            "."
        ):
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
    return out


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
    name = name.strip()
    if not name or not image_paths or _disabled():
        return None
    img_model = _image_model()
    if img_model is None:
        return None
    acc: list[list[float]] = []
    for p in image_paths:
        pil = _load_image(_index_source_path(p, db_path))
        if pil is None:
            continue
        try:
            vector = _valid_vector(_embed(img_model, [pil])[0])
            if vector is None or (acc and len(vector) != len(acc[0])):
                return None
            acc.append(vector)
        except Exception:  # noqa: BLE001
            continue
    if not acc:
        return None
    dim = len(acc[0])
    proto = [sum(v[i] for v in acc) / len(acc) for i in range(dim)]
    if _valid_vector(proto) is None:
        return None
    path = Path(db_path) if db_path is not None else _DEFAULT_DB
    try:
        conn = _open(path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO image_categories VALUES (?, ?)",
                (name, _vec_to_blob(proto)),
            )
            bind_prototype(
                conn,
                kind="category",
                name=name,
                identity=_model_index_identity(img_model, kind="vision"),
            )
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return {"name": name, "examples": len(acc), "vector_dim": dim}
