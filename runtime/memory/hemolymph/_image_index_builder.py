"""Atomic incremental snapshot construction for the local photo library.

Access model and file hooks through image_semantic_index so the appliance's
verified-file adapter and explicitly injected test providers remain in force.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import image_semantic_index as index
from ._image_index_state import job_identity, write_receipt


def _embed_batch(
    model: Any,
    images: list[Any],
    *,
    expected_dim: int | None,
    should_cancel: Callable[[], bool] | None,
    should_pause: Callable[[], bool] | None,
) -> list[list[float] | None]:
    """Embed a bounded batch and preserve per-image failure semantics.

    FastEmbed and the test/runtime adapters accept a sequence.  A legacy or
    provider-specific wrapper may still only support one image, or may reject
    a whole batch.  In that case retrying each image keeps the old behavior:
    one bad image does not discard otherwise usable images from the snapshot.
    """

    if not images:
        return []
    try:
        vectors = index._embed(
            model,
            images,
            should_cancel=should_cancel,
            should_pause=should_pause,
        )
        if len(vectors) != len(images):
            raise ValueError("embedding count mismatch")
        normalized: list[list[float]] = []
        for vector in vectors:
            valid = index._valid_vector(vector)
            if valid is None or (expected_dim is not None and len(valid) != expected_dim):
                raise ValueError("invalid image embedding")
            normalized.append(valid)
        return normalized
    except (
        index.ImageInferenceCancelled,
        index.ImageInferencePaused,
        index.ImageInferenceResourceBusy,
    ):
        raise
    except Exception:  # noqa: BLE001 - retry per image to isolate provider failures
        normalized = []
        for image in images:
            try:
                vectors = index._embed(
                    model,
                    [image],
                    should_cancel=should_cancel,
                    should_pause=should_pause,
                )
                if len(vectors) != 1:
                    raise ValueError("embedding count mismatch")
                valid = index._valid_vector(vectors[0])
                if valid is None or (expected_dim is not None and len(valid) != expected_dim):
                    raise ValueError("invalid image embedding")
            except (
                index.ImageInferenceCancelled,
                index.ImageInferencePaused,
                index.ImageInferenceResourceBusy,
            ):
                raise
            except Exception:  # noqa: BLE001 - record this image as failed
                valid = None
            normalized.append(valid)
        return normalized


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
    """Atomically index the selected snapshot, reusing verified encodings.

    All files are safely decoded and fingerprinted. Only matching content and
    encoder identities can reuse vectors; unknown/legacy identities recompute.
    Cancellation is cooperative between operations, including after a model
    call returns. It never interrupts a native inference call mid-execution.
    A pause request follows the same safe checkpoints, rolls back the temporary
    snapshot and returns the previous committed index to the caller.
    """

    def check_cancelled() -> None:
        if should_cancel is not None and should_cancel():
            raise _IndexCancelled

    def check_paused() -> None:
        if should_pause is not None and should_pause():
            raise _IndexPaused

    try:
        check_cancelled()
        check_paused()
    except _IndexCancelled:
        return _cancelled_result()
    except _IndexPaused:
        return _paused_result()
    if (job_id is not None or plan_id is not None) and not job_identity(job_id, plan_id):
        return {"ok": False, "error": "invalid_job_identity", "retained_previous": True}
    if index._disabled() and not empty_only:
        return {"ok": False, "error": "disabled", "semantic": False, "faces": False}
    path = Path(db_path) if db_path is not None else index._DEFAULT_DB
    root_path = Path(root)
    if not root_path.is_dir():
        return {"ok": False, "error": "image_directory_unavailable", "semantic": False}
    try:
        images = index._iter_images(root_path, max_files=max_files)
    except OSError:
        return {"ok": False, "error": "image_scan_failed", "retained_previous": True}
    if empty_only and images:
        return {"ok": False, "error": "image_library_not_empty", "retained_previous": True}
    img_model = index._image_model() if images else None
    try:
        check_cancelled()
        check_paused()
    except _IndexCancelled:
        return _cancelled_result()
    except _IndexPaused:
        return _paused_result()
    if images and img_model is None:
        model_error = index.image_model_status()["vision"].get("code")
        return {
            "ok": False,
            "error": "clip_vision_unavailable",
            "semantic": False,
            **({"model_error": model_error} if model_error else {}),
        }
    face_app = index._face_app() if images and include_faces else None
    vision_identity = index._model_index_identity(img_model, kind="vision") if images else None
    face_identity = index._model_index_identity(face_app, kind="faces") if images else None
    expected_dim = getattr(img_model, "embedding_size", None)
    if type(expected_dim) is not int or expected_dim <= 0:
        expected_dim = None
    conn = index._open(path)
    try:
        check_cancelled()
        check_paused()
        conn.execute("BEGIN IMMEDIATE")
        previous_root = conn.execute(
            "SELECT value FROM image_index_settings WHERE key='root'"
        ).fetchone()
        same_root = previous_root is not None and Path(previous_root[0]) == root_path.resolve()
        previous_identity = conn.execute(
            "SELECT value FROM image_index_settings WHERE key='vision_identity'"
        ).fetchone()
        reuse_vision = (
            same_root and bool(vision_identity) and previous_identity == (vision_identity,)
        )
        tables = (
            "image_meta",
            "image_faces",
            "image_fingerprints",
            "image_clip",
            "image_hashes",
            "image_quality",
            "image_face_sources",
        )
        for table in tables:
            conn.execute(f"CREATE TEMP TABLE previous_{table} AS SELECT * FROM {table}")
            conn.execute(f"DELETE FROM {table}")
        previous_paths = {row[0] for row in conn.execute("SELECT path FROM previous_image_meta")}
        previous_vectors = (
            {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT c.path,c.clip_embedding,f.fingerprint FROM previous_image_clip c "
                    "JOIN previous_image_fingerprints f ON f.path=c.path"
                )
            }
            if reuse_vision
            else {}
        )
        indexed = 0
        reused = 0
        embedded = 0
        face_rows = 0
        embedding_failures = 0
        face_failures = 0
        read_failures = 0
        resource_limited = False
        embed_batch_size = index.image_embed_batch_size()
        pending: list[tuple[Path, str, Any, str]] = []

        def store_image(img_path: Path, rel: str, pil: Any, fingerprint: str, vec: list[float]) -> bool:
            """Write one decoded image and its optional face-derived rows."""

            nonlocal face_failures, face_rows, indexed, resource_limited
            check_cancelled()
            check_paused()
            conn.execute(
                "INSERT OR REPLACE INTO image_clip VALUES (?, ?)",
                (rel, index._vec_to_blob(vec)),
            )
            conn.execute("INSERT INTO image_fingerprints VALUES (?, ?)", (rel, fingerprint))
            exif_time, location = index._read_exif(pil)
            conn.execute(
                "INSERT OR REPLACE INTO image_meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    rel,
                    pil.width,
                    pil.height,
                    index._mtime(img_path),
                    exif_time,
                    Path(img_path).suffix.lower(),
                    location,
                ),
            )
            dhash = index._compute_dhash(pil)
            if dhash:
                conn.execute("INSERT OR REPLACE INTO image_hashes VALUES (?, ?)", (rel, dhash))
            conn.execute(
                "INSERT OR REPLACE INTO image_quality VALUES (?, ?)",
                (rel, index._laplacian_sharpness(pil)),
            )
            indexed += 1
            if face_app is not None:
                face_failed = False
                cached_faces = _cached_index_faces(
                    conn,
                    rel,
                    fingerprint,
                    face_identity if same_root else None,
                )
                if cached_faces is not None:
                    conn.executemany("INSERT INTO image_faces VALUES (?, ?, ?)", cached_faces)
                    face_rows += len(cached_faces)
                else:
                    try:
                        import numpy as np

                        faces = index._detect_faces(
                            face_app,
                            np.asarray(pil),
                            should_cancel=should_cancel,
                            should_pause=should_pause,
                        )
                        for fi, face in enumerate(faces):
                            face_vector = index._valid_vector(face.normed_embedding)
                            if face_vector is None:
                                raise ValueError("invalid face embedding")
                            conn.execute(
                                "INSERT INTO image_faces VALUES (?, ?, ?)",
                                (rel, fi, index._vec_to_blob(face_vector)),
                            )
                            face_rows += 1
                    except index.ImageInferenceCancelled:
                        raise _IndexCancelled from None
                    except index.ImageInferencePaused:
                        raise _IndexPaused from None
                    except index.ImageInferenceResourceBusy:
                        resource_limited = True
                        return False
                    except Exception:  # noqa: BLE001
                        face_failures += 1
                        face_failed = True
                if face_identity and not face_failed:
                    conn.execute(
                        "INSERT INTO image_face_sources VALUES (?, ?, ?)",
                        (rel, fingerprint, face_identity),
                    )
            check_cancelled()
            check_paused()
            return True

        def flush_pending() -> bool:
            """Embed a bounded batch, falling back per image for old wrappers."""

            nonlocal embedded, embedding_failures, resource_limited
            if not pending:
                return True
            batch = list(pending)
            pending.clear()
            try:
                try:
                    vectors = _embed_batch(
                        img_model,
                        [item[2] for item in batch],
                        expected_dim=expected_dim,
                        should_cancel=should_cancel,
                        should_pause=should_pause,
                    )
                except index.ImageInferenceCancelled:
                    raise _IndexCancelled from None
                except index.ImageInferencePaused:
                    raise _IndexPaused from None
                except index.ImageInferenceResourceBusy:
                    resource_limited = True
                    return False
                for item, vec in zip(batch, vectors, strict=True):
                    img_path, rel, pil, fingerprint = item
                    if vec is None:
                        embedding_failures += 1
                        continue
                    embedded += 1
                    if not store_image(img_path, rel, pil, fingerprint, vec):
                        return False
                return True
            finally:
                for _img_path, _rel, pil, _fingerprint in batch:
                    pil.close()

        try:
            for img_path in images:
                check_cancelled()
                check_paused()
                rel = index._rel(img_path, root_path)
                pil = index._load_image(img_path)
                if pil is None:
                    if same_root and rel in previous_paths:
                        read_failures += 1
                    continue
                keep_open = False
                try:
                    try:
                        fingerprint = index._decoded_image_fingerprint(pil)
                    except Exception:  # noqa: BLE001 - cannot reuse unverified source content
                        read_failures += 1
                        continue
                    check_cancelled()
                    check_paused()
                    old = previous_vectors.get(rel)
                    vec = (
                        _cached_index_vector(old[0], expected_dim)
                        if old and old[1] == fingerprint
                        else None
                    )
                    if vec is not None:
                        reused += 1
                        if not store_image(img_path, rel, pil, fingerprint, vec):
                            break
                    else:
                        pending.append((img_path, rel, pil, fingerprint))
                        keep_open = True
                        if len(pending) >= embed_batch_size and not flush_pending():
                            break
                finally:
                    if not keep_open:
                        pil.close()
            if not resource_limited:
                flush_pending()
        finally:
            for _img_path, _rel, pil, _fingerprint in pending:
                pil.close()
            pending.clear()
        check_cancelled()
        check_paused()
        if resource_limited:
            conn.rollback()
            return {
                "ok": False,
                "error": "image_inference_busy",
                "resource_limited": True,
                "retained_previous": True,
            }
        if embedding_failures or face_failures or read_failures or (images and not indexed):
            conn.rollback()
            return {
                "ok": False,
                "error": (
                    "image_embedding_failed"
                    if embedding_failures
                    else "face_embedding_failed"
                    if face_failures
                    else "images_unreadable"
                ),
                "failed": embedding_failures + face_failures + read_failures or len(images),
                "retained_previous": True,
            }
        if face_app is None and same_root:
            conn.execute(
                "INSERT INTO image_faces SELECT f.path, f.face_index, f.face_embedding "
                "FROM previous_image_faces f JOIN image_meta m ON m.path=f.path "
                "JOIN previous_image_meta p ON p.path=m.path "
                "JOIN image_fingerprints n ON n.path=m.path "
                "JOIN previous_image_fingerprints o ON o.path=m.path "
                "WHERE m.mtime=p.mtime AND m.width=p.width AND m.height=p.height "
                "AND n.fingerprint=o.fingerprint"
            )
            face_rows = conn.execute("SELECT COUNT(*) FROM image_faces").fetchone()[0]
            conn.execute(
                "INSERT INTO image_face_sources SELECT s.* FROM previous_image_face_sources s "
                "JOIN image_fingerprints n ON n.path=s.path "
                "JOIN image_meta m ON m.path=s.path JOIN previous_image_meta p ON p.path=s.path "
                "WHERE s.fingerprint=n.fingerprint AND m.mtime=p.mtime "
                "AND m.width=p.width AND m.height=p.height"
            )
        # Keep derived annotations only while their source is still indexed and
        # has the same decoded content and metadata; mtimes alone are mutable.
        # Legacy rows have no fingerprint and are conservatively invalidated.
        for table in ("image_tags", "image_ocr"):
            if not same_root:
                conn.execute(f"DELETE FROM {table}")
                continue
            conn.execute(
                f"DELETE FROM {table} WHERE NOT EXISTS ("
                "SELECT 1 FROM image_meta m JOIN previous_image_meta p ON p.path=m.path "
                "JOIN image_fingerprints n ON n.path=m.path "
                "JOIN previous_image_fingerprints o ON o.path=m.path "
                f"WHERE m.path={table}.path AND m.mtime=p.mtime "
                "AND m.width=p.width AND m.height=p.height AND n.fingerprint=o.fingerprint)"
            )
        conn.execute(
            "INSERT OR REPLACE INTO image_index_settings VALUES ('root', ?)",
            (str(root_path.resolve()),),
        )
        if images or not same_root:
            conn.execute(
                "INSERT OR REPLACE INTO image_index_settings VALUES ('vision_identity', ?)",
                (vision_identity or "",),
            )
        if face_app is not None or not same_root:
            conn.execute(
                "INSERT OR REPLACE INTO image_index_settings VALUES ('faces_identity', ?)",
                (face_identity or "",),
            )
        current_paths = {row[0] for row in conn.execute("SELECT path FROM image_meta")}
        result = {
            "ok": True,
            "indexed": indexed,
            "reused": reused,
            "embedded": embedded,
            "removed": len(previous_paths - current_paths) if same_root else len(previous_paths),
            "skipped": len(images) - indexed,
            "faces": face_rows,
            "semantic": True,
            "face_capable": face_app is not None,
        }
        write_receipt(
            conn,
            root=root_path,
            job_id=job_id,
            plan_id=plan_id,
            include_faces=include_faces,
            result=result,
        )
        check_cancelled()
        conn.commit()
        # Once committed, cancellation cannot retroactively roll back this
        # snapshot. The receipt is authoritative if the worker dies here.
        return result
    except _IndexCancelled:
        conn.rollback()
        return _cancelled_result()
    except _IndexPaused:
        conn.rollback()
        return _paused_result()
    finally:
        conn.close()


class _IndexCancelled(Exception):
    pass


class _IndexPaused(Exception):
    pass


def _cancelled_result() -> dict[str, Any]:
    return {"ok": False, "error": "index_cancelled", "cancelled": True, "retained_previous": True}


def _paused_result() -> dict[str, Any]:
    return {"ok": False, "error": "index_paused", "paused": True, "retained_previous": True}


def _cached_index_vector(blob, expected_dim: int | None = None) -> list[float] | None:
    try:
        vector = index._valid_vector(index._blob_to_vec(blob))
        if vector is not None and (expected_dim is None or len(vector) == expected_dim):
            return vector
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _cached_index_faces(conn, path: str, fingerprint: str, identity: str | None):
    if not identity:
        return None
    source = conn.execute(
        "SELECT fingerprint,model_identity FROM previous_image_face_sources WHERE path=?",
        (path,),
    ).fetchone()
    if source != (fingerprint, identity):
        return None
    rows = conn.execute("SELECT * FROM previous_image_faces WHERE path=?", (path,)).fetchall()
    if any(_cached_index_vector(row[2]) is None for row in rows):
        return None
    # The source marker records a completed face pass even when no faces exist.
    return rows
