"""Media (video understanding) web API.

Exposes the local video semantic index (keyframe extraction + CLIP embedding +
face grouping + optional speech search) as REST endpoints for the frontend
"本地数据库 → 视频" surface. All capabilities are self-gating: when the
underlying model / index is unavailable, endpoints return a clear ``ok: False``
message instead of raising — the UI degrades gracefully.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from fastapi import APIRouter, Depends, HTTPException, Request
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except ImportError:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    APIRouter = None  # type: ignore[assignment, misc]
    HTTPException = None  # type: ignore[assignment, misc]
    Depends = None  # type: ignore[assignment, misc]
    Request = None  # type: ignore[assignment, misc]
    BaseModel = None  # type: ignore[assignment, misc]
    Field = None  # type: ignore[assignment, misc]

from runtime.sensing._fastapi_guard import require_fastapi


class VideoIndexRequest(BaseModel):
    directory: str = "."
    include_faces: bool = True
    include_transcript: bool = False
    max_files: int = Field(default=100, ge=1, le=1000)
    incremental: bool = False
    watch: bool = False
    interval_sec: float = Field(default=60.0, ge=10.0, le=3600.0)


class VideoWatchRequest(BaseModel):
    directory: str = "."
    interval_sec: float = Field(default=60.0, ge=10.0, le=3600.0)


class VideoSearchRequest(BaseModel):
    query: str = ""
    directory: str = "."
    top_k: int = Field(default=10, ge=1, le=100)


class VideoFaceSearchRequest(BaseModel):
    image_path: str = ""
    directory: str = "."
    top_k: int = Field(default=10, ge=1, le=100)


class VideoClassifyRequest(BaseModel):
    directory: str = "."
    top_k: int = Field(default=5, ge=1, le=50)


class VideoSpeechSearchRequest(BaseModel):
    query: str = ""
    directory: str = "."


class VideoImageSearchRequest(BaseModel):
    image_path: str = ""
    directory: str = "."
    top_k: int = Field(default=10, ge=1, le=100)


class VideoOcrRequest(BaseModel):
    query: str = ""
    directory: str = "."
    top_k: int = Field(default=20, ge=1, le=100)


def create_media_router(
    *,
    identity_store: Any = None,
    require_auth: bool = False,
    jwt_secret: str | None = None,
    jwt_issuer: str | None = None,
    jwt_audience: str | None = None,
) -> Any:
    require_fastapi(__name__)

    def _auth_dep(request: Request) -> None:
        if require_auth and identity_store is None:
            raise HTTPException(401, "identity store required for media auth")
        from runtime.safety.auth.principal import resolve_principal

        principal = resolve_principal(
            request,
            identity_store,
            require_auth,
            jwt_secret=jwt_secret,
            jwt_issuer=jwt_issuer,
            jwt_audience=jwt_audience,
        )
        from runtime.safety.auth.scope import scope_from_principal

        request.state.media_scope = scope_from_principal(principal)

    router = APIRouter(tags=["media"], dependencies=[Depends(_auth_dep)])

    def _load_iidx() -> Any:
        from runtime.memory.hemolymph import video_semantic_index as _vidx

        return _vidx

    def _media_roots(request: Request) -> list[Path]:
        scope = getattr(request.state, "media_scope", None)
        if scope is None:
            return []
        import hashlib
        import os

        from runtime.platform.process.paths import app_paths

        tenant_key = hashlib.sha256(f"{scope.tenant_id}:{scope.actor_id}".encode()).hexdigest()[:32]
        default_root = app_paths().data_dir / "tenants" / tenant_key / "media"
        raw = os.environ.get("ECHO_MEDIA_ALLOWED_ROOTS", "").strip()
        values = [item.strip() for item in raw.split(os.pathsep) if item.strip()]
        principal = getattr(request.state, "principal", None)
        elevated = bool(
            principal is not None
            and set(getattr(principal, "roles", ())).intersection({"operator", "admin"})
        )
        # A shared allowlist is appropriate for an operator library, but it
        # must never become a cross-tenant escape hatch for ordinary users.
        roots = (
            [Path(item).expanduser().resolve() for item in values]
            if values and elevated
            else [default_root]
        )
        for root in roots:
            root.mkdir(parents=True, exist_ok=True)
        return roots

    def _resolve_media_path(request: Request, value: str, *, must_exist: bool = False) -> Path:
        raw = str(value or ".").strip()
        roots = _media_roots(request)
        if not roots:
            path = Path(raw).expanduser().resolve()
            if must_exist and not path.exists():
                raise HTTPException(404, "media path not found")
            return path
        # Relative paths are resolved under the first configured tenant root;
        # absolute paths must still fall under an allowlisted root.
        candidate = (
            (roots[0] / raw if not Path(raw).is_absolute() else Path(raw)).expanduser().resolve()
        )
        for root in roots:
            try:
                candidate.relative_to(root)
                if must_exist and not candidate.exists():
                    raise HTTPException(404, "media path not found")
                return candidate
            except ValueError:
                continue
        raise HTTPException(403, "media path is outside the tenant allowlist")

    def _resolve_media_dir(request: Request, value: str) -> Path:
        path = _resolve_media_path(request, value, must_exist=True)
        if not path.is_dir():
            raise HTTPException(400, "media directory is not a directory")
        return path

    def _db_path(request: Request, directory: str) -> str | None:
        """Resolve the video index DB under the given directory (or default)."""
        scope = getattr(request.state, "media_scope", None)
        if scope is not None:
            from runtime.memory.hemolymph.video_semantic_index import tenant_video_db_path

            return str(tenant_video_db_path(scope))
        if not directory or directory.strip() in (".", ""):
            return None
        return str(Path(directory).expanduser() / "data" / "video_index.db")

    def _indexed_video_paths(db_path: str | None) -> list[str]:
        """List ``video_path`` values from the video_meta table (or [])."""
        import sqlite3
        from pathlib import Path

        path = db_path or "data/video_index.db"
        if not Path(path).exists():
            return []
        try:
            conn = sqlite3.connect(path)
            try:
                rows = conn.execute("SELECT video_path FROM video_meta").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            return []
        return [str(r[0]) for r in rows]

    @router.post("/video/index")
    def video_index(request: Request, req: VideoIndexRequest) -> dict[str, Any]:
        """Build (or rebuild) the video keyframe index for a directory.

        With ``incremental=true`` only new/changed files are processed; with
        ``watch=true`` a background watcher keeps the directory up to date.
        """
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        root = _resolve_media_dir(request, req.directory)
        db_path = _db_path(request, req.directory)
        if req.watch:
            from runtime.memory.hemolymph.video_watchdog import start_watching

            start_watching(
                root,
                interval_sec=req.interval_sec,
                include_faces=req.include_faces,
                db_path=db_path,
                max_files=req.max_files,
            )
            return {"ok": True, "watching": True, "directory": req.directory}
        result = _vidx.build_video_index(
            root,
            db_path=db_path,
            include_faces=req.include_faces,
            include_transcript=req.include_transcript,
            max_files=req.max_files,
            incremental=req.incremental,
        )
        if result is None:
            return {"ok": False, "message": "video indexing unavailable"}
        return result

    @router.post("/video/watch")
    def video_watch(request: Request, req: VideoWatchRequest) -> dict[str, Any]:
        """Start a background watcher for a directory (auto incremental index)."""
        from runtime.memory.hemolymph.video_watchdog import start_watching

        root = _resolve_media_dir(request, req.directory)
        start_watching(
            root,
            interval_sec=req.interval_sec,
            db_path=_db_path(request, req.directory),
        )
        return {"ok": True, "watching": True, "directory": str(root)}

    @router.delete("/video/watch")
    def video_unwatch(request: Request, directory: str = ".") -> dict[str, Any]:
        """Stop the background watcher for a directory."""
        from runtime.memory.hemolymph.video_watchdog import stop_watching

        root = _resolve_media_dir(request, directory)
        stopped = stop_watching(root, db_path=_db_path(request, directory))
        return {"ok": True, "stopped": stopped, "directory": str(root)}

    @router.get("/video/hardware")
    def video_hardware(request: Request) -> dict[str, Any]:
        """Report configured hardware-acceleration settings (ORT providers, whisper)."""
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return {"ok": True, "hardware": _vidx.hardware_accel()}

    @router.post("/video/search")
    def video_search(request: Request, req: VideoSearchRequest) -> dict[str, Any]:
        """Find video keyframes semantically closest to a text query."""
        if not req.query.strip():
            return {"ok": False, "message": "missing query"}
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        results = _vidx.search_video_by_text(
            req.query,
            db_path=_db_path(request, req.directory),
            top_k=req.top_k,
        )
        return {"ok": True, "query": req.query, "hits": results or []}

    @router.post("/video/search/face")
    def video_search_face(request: Request, req: VideoFaceSearchRequest) -> dict[str, Any]:
        """Find video keyframes containing the same face as an image."""
        if not req.image_path.strip():
            return {"ok": False, "message": "missing image_path"}
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        results = _vidx.search_face_in_videos(
            str(_resolve_media_path(request, req.image_path, must_exist=True)),
            db_path=_db_path(request, req.directory),
            top_k=req.top_k,
        )
        return {"ok": True, "hits": results or []}

    @router.get("/video/faces")
    def video_faces(
        request: Request, directory: str = ".", threshold: float = 0.45
    ) -> dict[str, Any]:
        """Group indexed faces into person clusters across videos."""
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        try:
            result = _vidx.group_video_faces(
                db_path=_db_path(request, directory), threshold=threshold
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        if result is None:
            return {"ok": False, "message": "video indexing unavailable"}
        return {"ok": True, "groups": result or []}

    @router.post("/video/classify")
    def video_classify(request: Request, req: VideoClassifyRequest) -> dict[str, Any]:
        """Zero-shot tag every indexed video in a directory."""
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        db_path = _db_path(request, req.directory)
        try:
            root = _resolve_media_dir(request, req.directory)
            paths = _indexed_video_paths(db_path)
            results = []
            for vp in paths:
                candidate = _resolve_media_path(request, str(root / vp), must_exist=True)
                tags = _vidx.classify_video(candidate, db_path=db_path, top_k=req.top_k)
                results.append({"video_path": str(candidate), "tags": tags or []})
            return {"ok": True, "results": results}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}

    @router.post("/video/search/speech")
    def video_search_speech(request: Request, req: VideoSpeechSearchRequest) -> dict[str, Any]:
        """Find video transcript segments containing a text query."""
        if not req.query.strip():
            return {"ok": False, "message": "missing query"}
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        results = _vidx.search_video_by_speech(req.query, db_path=_db_path(request, req.directory))
        return {"ok": True, "hits": results or []}

    @router.post("/video/search/image")
    def video_search_image(request: Request, req: VideoImageSearchRequest) -> dict[str, Any]:
        """Find video keyframes visually closest to an image file."""
        if not req.image_path.strip():
            return {"ok": False, "message": "missing image_path"}
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        results = _vidx.search_video_by_image(
            str(_resolve_media_path(request, req.image_path, must_exist=True)),
            db_path=_db_path(request, req.directory),
            top_k=req.top_k,
        )
        return {"ok": True, "hits": results or []}

    @router.post("/video/ocr")
    def video_ocr(request: Request, req: VideoOcrRequest) -> dict[str, Any]:
        """OCR video keyframes and match a text query against the text."""
        if not req.query.strip():
            return {"ok": False, "message": "missing query"}
        try:
            _vidx = _load_iidx()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc)}
        results = _vidx.ocr_video_keyframes(
            req.query,
            root=str(_resolve_media_dir(request, req.directory)),
            db_path=_db_path(request, req.directory),
            top_k=req.top_k,
        )
        return {"ok": True, "hits": results or []}

    @router.get("/video/cover")
    def video_cover(
        request: Request, video_path: str = "", time_sec: float = 0.0, directory: str = "."
    ):
        """Return a JPEG frame of a video at a given time (as image/jpeg)."""
        from fastapi import Response

        if not video_path:
            return Response(status_code=404)
        try:
            _vidx = _load_iidx()
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        try:
            root = _resolve_media_dir(request, directory)
            candidate = Path(video_path)
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = _resolve_media_path(request, str(candidate), must_exist=True)
            data = _vidx.extract_frame_jpeg(candidate, time_sec)
        except Exception:  # noqa: BLE001
            return Response(status_code=404)
        if not data:
            return Response(status_code=404)
        return Response(content=data, media_type="image/jpeg")

    return router


__all__ = [
    "VideoIndexRequest",
    "VideoWatchRequest",
    "VideoSearchRequest",
    "VideoFaceSearchRequest",
    "VideoClassifyRequest",
    "VideoSpeechSearchRequest",
    "VideoImageSearchRequest",
    "VideoOcrRequest",
    "create_media_router",
]
