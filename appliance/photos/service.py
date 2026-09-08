"""Safe NAS photo projection with an isolated Agent semantic-index adapter.

The appliance owns filesystem trust, plan drift detection and job lifecycle.
Agent owns the expensive CLIP/face implementation and SQLite schema.  This
keeps Echo from copying model code while ensuring Agent never gets an
unfiltered view that includes recycle-bin contents, upload internals or links.
"""

from __future__ import annotations

import contextlib
import hashlib
import inspect
import io
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

from appliance.agent_api.images import (
    agent_image_index_readiness,
    load_agent_image_index_module,
    search_agent_image_index,
)
from appliance.photos.job_store import PhotoJobStore, idle_job
from appliance.photos.safe_file import (
    is_link_or_reparse,
    open_safe_photo,
    safe_file_access_available,
)
from echo_runtime.resource_identity import (
    PhotoAssetReference,
    photo_asset_reference,
    photo_library_id,
    photo_source,
)

PHOTO_INDEX_SCHEMA = "echo.photos.index-plan.v1"
PHOTO_LIBRARY_SCHEMA = "echo.photos.library.v1"
PHOTO_STATUS_SCHEMA = "echo.photos.status.v1"
PHOTO_SEARCH_SCHEMA = "echo.photos.search.v1"
DEFAULT_INDEX_MAX_FILES = 4_000
DEFAULT_SCAN_MAX_FILES = 20_000
SCAN_CACHE_SECONDS = 2.0
MAX_SOURCE_BYTES = 256 * 1024 * 1024
HEIF_EXTENSIONS = frozenset({".heic", ".heif"})
IMAGE_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif", *HEIF_EXTENSIONS}
)
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".heic": "image/heic",
    ".heif": "image/heif",
}
_HEIF_PLUGIN_LOCK = threading.Lock()
_HEIF_PLUGIN_AVAILABLE: bool | None = None
_INTERNAL_NAMES = frozenset({".echo-trash", ".echo-upload-sessions"})
_IMAGE_RECORD_TABLES = (
    "image_clip",
    "image_faces",
    "image_meta",
    "image_tags",
    "image_ocr",
    "image_hashes",
    "image_quality",
    "image_fingerprints",
    "image_face_sources",
)


class PhotoPathError(ValueError):
    """A requested photo path is unsafe or outside the NAS root."""


class PhotoIndexConflict(RuntimeError):
    """The index plan changed or another index job already owns the worker."""

    def __init__(self, message: str, *, code: str = "photo_index_conflict") -> None:
        super().__init__(message)
        self.code = code


def _ensure_heif_opener() -> bool:
    """Register the bounded HEIF decoder lazily, without breaking JPEG-only recovery."""
    global _HEIF_PLUGIN_AVAILABLE
    if _HEIF_PLUGIN_AVAILABLE is not None:
        return _HEIF_PLUGIN_AVAILABLE
    with _HEIF_PLUGIN_LOCK:
        if _HEIF_PLUGIN_AVAILABLE is not None:
            return _HEIF_PLUGIN_AVAILABLE
        try:
            from pillow_heif import register_heif_opener

            # Embedded thumbnails are not used by Echo. Disabling them avoids
            # decoding a second untrusted image that will never be returned.
            register_heif_opener(thumbnails=False)
        except (ImportError, OSError, RuntimeError):
            _HEIF_PLUGIN_AVAILABLE = False
        else:
            _HEIF_PLUGIN_AVAILABLE = True
        return _HEIF_PLUGIN_AVAILABLE


def _accepts_keyword(function: Any, name: str) -> bool:
    """Require an explicit protocol parameter; **kwargs alone proves nothing."""
    try:
        parameter = inspect.signature(function).parameters.get(name)
    except (TypeError, ValueError):
        return False
    return parameter is not None and parameter.kind in {
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    }


@dataclass(frozen=True)
class OpenedPhoto:
    stream: BinaryIO
    media_type: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class PhotoFile:
    path: str
    name: str
    size: int
    mtime: float
    mtime_ns: int
    file_type: str

    def to_dict(
        self,
        metadata: dict[str, Any] | None = None,
        *,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        meta = metadata or {}
        result = {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "mtime": self.mtime,
            "fileType": self.file_type,
            "width": meta.get("width"),
            "height": meta.get("height"),
            "capturedAt": meta.get("capturedAt") or None,
            "location": meta.get("location") or None,
            "indexed": bool(meta),
        }
        if source_id:
            reference: PhotoAssetReference = photo_asset_reference(
                source_id,
                self.path,
                size=self.size,
                mtime_ns=self.mtime_ns,
            )
            result.update(reference.to_dict())
        return result


@dataclass(frozen=True)
class PhotoScan:
    files: tuple[PhotoFile, ...]
    unsafe_links: int
    truncated: bool
    errors: int = 0
    root_identity: tuple[int, int] | None = None

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for item in self.files[:DEFAULT_INDEX_MAX_FILES]:
            digest.update(item.path.encode("utf-8", errors="surrogatepass"))
            digest.update(b"\0")
            digest.update(str(item.size).encode("ascii"))
            digest.update(b"\0")
            digest.update(str(item.mtime_ns).encode("ascii"))
            digest.update(b"\n")
        digest.update(f"unsafe={self.unsafe_links};truncated={int(self.truncated)}".encode())
        digest.update(f";errors={self.errors};root={self.root_identity}".encode())
        return digest.hexdigest()


class ImageIndexBackend(Protocol):
    def available(self) -> bool: ...

    def build_index(
        self,
        root: Path,
        db_path: Path,
        image_paths: Sequence[str],
        *,
        include_faces: bool,
        max_files: int,
    ) -> dict[str, Any]: ...

    def search_by_text(
        self,
        query: str,
        *,
        top_k: int,
        db_path: Path,
    ) -> list[dict[str, Any]] | None: ...


class AgentImageIndexAdapter:
    """Narrow compatibility layer around Agent's local image index.

    Current Agent releases discover files internally.  Echo temporarily swaps
    only that iterator while holding a process-wide lock, so the reused model
    code receives the appliance-vetted immutable path list.  If Agent changes
    this seam, the adapter fails closed instead of falling back to an unsafe
    recursive scan.
    """

    _adapter_lock = threading.RLock()

    def _module(self):
        return load_agent_image_index_module()

    def available(self) -> bool:
        return bool(self.readiness()["semantic"]["available"])

    @staticmethod
    def _safe_file_access_available() -> bool:
        return safe_file_access_available()

    def readiness(self) -> dict[str, Any]:
        readiness = agent_image_index_readiness(self._module())
        if not self._safe_file_access_available():
            # The appliance adapter intentionally requires descriptor-relative,
            # no-follow reads. Do not advertise PIL alone as a working preview.
            readiness["previewAvailable"] = False
            readiness["modelDownloadMayBeRequired"] = False
            for name in ("semantic", "faces"):
                capability = readiness[name]
                capability["available"] = False
                capability["modelsLoaded"] = False
                if capability["state"] != "disabled":
                    capability["state"] = "unavailable"
                capability["missingDependencies"].append("secure-file-access")
        return readiness

    def build_index(
        self,
        root: Path,
        db_path: Path,
        image_paths: Sequence[str],
        *,
        include_faces: bool,
        max_files: int,
        job_id: str | None = None,
        plan_id: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        should_pause: Callable[[], bool] | None = None,
        empty_only: bool = False,
    ) -> dict[str, Any]:
        if empty_only:
            if image_paths or not self.supports_empty_cleanup():
                return {"ok": False, "error": "empty_index_cleanup_unavailable"}
        else:
            readiness = self.readiness()
            if not readiness["semantic"]["available"]:
                return {"ok": False, "error": "agent_image_index_unavailable"}
            if include_faces and not readiness["faces"]["available"]:
                return {"ok": False, "error": "face_dependencies_unavailable"}
        module = self._module()
        if module is None or not callable(getattr(module, "build_index", None)):
            return {"ok": False, "error": "agent_image_index_unavailable"}
        original_iterator = getattr(module, "_iter_images", None)
        original_loader = getattr(module, "_load_image", None)
        original_mtime = getattr(module, "_mtime", None)
        if not all(callable(item) for item in (original_iterator, original_loader, original_mtime)):
            return {"ok": False, "error": "agent_image_index_incompatible"}
        bounded = tuple(str(item) for item in image_paths[: max(1, int(max_files))])
        allowed = frozenset(bounded)
        safe_root = Path(os.path.abspath(root))
        owner_thread = threading.get_ident()
        observed_mtimes: dict[str, float] = {}

        def vetted_relative(raw_path: str | Path) -> str | None:
            candidate = Path(raw_path)
            try:
                relative = candidate.relative_to(safe_root).as_posix()
            except ValueError:
                return None
            return relative if relative in allowed else None

        def vetted_iterator(scan_root: Path, max_files: int = DEFAULT_INDEX_MAX_FILES):
            candidate_root = Path(os.path.abspath(scan_root))
            if candidate_root != safe_root:
                if threading.get_ident() == owner_thread:
                    return []
                return original_iterator(scan_root, max_files=max_files)
            return [safe_root.joinpath(*PurePosixPath(item).parts) for item in bounded[:max_files]]

        def vetted_loader(raw_path: str | Path):
            relative = vetted_relative(raw_path)
            if relative is None:
                if threading.get_ident() == owner_thread:
                    return None
                return original_loader(raw_path)
            parts = PurePosixPath(relative).parts
            try:
                from PIL import Image

                if Path(parts[-1]).suffix.lower() in HEIF_EXTENSIONS and not _ensure_heif_opener():
                    return None
                source, info = open_safe_photo(safe_root, parts, max_bytes=MAX_SOURCE_BYTES)
                with source, Image.open(source) as opened:
                    if opened.width * opened.height > 100_000_000:
                        return None
                    image = opened.convert("RGB")
                observed_mtimes[str(Path(raw_path))] = info.st_mtime
                return image
            except (ImportError, OSError, ValueError):
                return None

        def vetted_mtime(raw_path: str | Path) -> float:
            if vetted_relative(raw_path) is None:
                if threading.get_ident() == owner_thread:
                    return 0.0
                return original_mtime(raw_path)
            return observed_mtimes.get(str(Path(raw_path)), 0.0)

        with self._adapter_lock:
            module._iter_images = vetted_iterator
            module._load_image = vetted_loader
            module._mtime = vetted_mtime
            try:
                optional = {
                    name: value
                    for name, value in {
                        "job_id": job_id,
                        "plan_id": plan_id,
                        "should_cancel": should_cancel,
                        "should_pause": should_pause,
                        "empty_only": empty_only,
                    }.items()
                    if _accepts_keyword(module.build_index, name)
                }
                result = module.build_index(
                    root,
                    db_path=db_path,
                    include_faces=include_faces,
                    max_files=max_files,
                    **optional,
                )
            finally:
                module._iter_images = original_iterator
                module._load_image = original_loader
                module._mtime = original_mtime
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_result"}

    def supports_cancellation(self) -> bool:
        module = self._module()
        return _accepts_keyword(getattr(module, "build_index", None), "should_cancel")

    def supports_pause(self) -> bool:
        module = self._module()
        return _accepts_keyword(getattr(module, "build_index", None), "should_pause")

    def supports_empty_cleanup(self) -> bool:
        # An old backend may ignore an empty iterator or load models first.
        # Only the explicit transactional protocol can authorize cleanup.
        module = self._module()
        return all(
            _accepts_keyword(getattr(module, "build_index", None), name)
            for name in ("empty_only", "job_id", "plan_id", "should_cancel")
        ) and callable(getattr(module, "index_job_receipt", None))

    def index_job_receipt(
        self,
        root: Path,
        *,
        db_path: Path,
        job_id: str,
        plan_id: str,
        include_faces: bool,
    ) -> dict[str, Any] | None:
        # Receipt reads do not need models or a writable schema initializer.
        # An old module has no recovery evidence and must remain compatible.
        try:
            info = db_path.lstat()
            if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
                return None
            reader = getattr(self._module(), "index_job_receipt", None)
            if not callable(reader) or not all(
                _accepts_keyword(reader, name)
                for name in ("db_path", "job_id", "plan_id", "include_faces")
            ):
                return None
            result = reader(
                root, db_path=db_path, job_id=job_id, plan_id=plan_id, include_faces=include_faces
            )
        except Exception:  # noqa: BLE001 — unreadable evidence cannot prove a commit
            return None
        return result if isinstance(result, dict) and result.get("ok") is True else None

    def search_by_text(
        self,
        query: str,
        *,
        top_k: int,
        db_path: Path,
    ) -> list[dict[str, Any]] | None:
        if not self.available():
            return None
        return search_agent_image_index(self._module(), query, top_k=top_k, db_path=db_path)

    def search_by_text_in_paths(
        self,
        query: str,
        *,
        top_k: int,
        db_path: Path,
        allowed_paths: Sequence[str],
    ) -> list[dict[str, Any]] | None:
        if not self.available():
            return None
        return search_agent_image_index(
            self._module(), query, top_k=top_k, db_path=db_path, allowed_paths=allowed_paths
        )


class PhotoLibraryService:
    def __init__(
        self,
        root: str | Path,
        data_dir: str | Path,
        *,
        backend: ImageIndexBackend | None = None,
        scan_max_files: int = DEFAULT_SCAN_MAX_FILES,
        index_max_files: int = DEFAULT_INDEX_MAX_FILES,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise OSError(f"NAS root is not a directory: {self.root}")
        # The source id is derived from the canonical library root, matching
        # the Agent image-library identity without publishing the host path.
        self.library_id = photo_library_id(self.root)
        root_info = self.root.stat()
        self._root_identity = (root_info.st_dev, root_info.st_ino)
        data_root = Path(data_dir).expanduser().resolve()
        self.index_dir = data_root / "media"
        if self.index_dir.is_symlink():
            raise OSError("photo index directory must not be a symbolic link")
        self.index_dir.mkdir(parents=True, exist_ok=True)
        index_info = self.index_dir.lstat()
        if not stat.S_ISDIR(index_info.st_mode) or stat.S_ISLNK(index_info.st_mode):
            raise OSError("photo index directory is not a safe directory")
        with contextlib.suppress(OSError):
            self.index_dir.chmod(0o700)
        self.db_path = self.index_dir / "image_index.db"
        self._backend = backend or AgentImageIndexAdapter()
        self._scan_max_files = max(1, min(int(scan_max_files), 100_000))
        self._index_max_files = max(1, min(int(index_max_files), self._scan_max_files))
        self._clock = clock
        self._lock = threading.RLock()
        self._scan_lock = threading.RLock()
        self._scan_cache: tuple[float, PhotoScan] | None = None
        self._job_store = PhotoJobStore(self.index_dir)
        self._job = idle_job()
        self._job_lease: Any = None
        self._job_busy = False
        self._unpersisted_job: dict[str, Any] | None = None
        self._worker: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._pause_event: threading.Event | None = None
        self._worker_job_id: str | None = None
        self._refresh_job()

    def _receipt_for_job(self, job: dict[str, Any]) -> dict[str, Any] | None:
        reader = getattr(self._backend, "index_job_receipt", None)
        if not callable(reader) or not job.get("jobId") or not job.get("planId"):
            return None
        if not all(
            _accepts_keyword(reader, name)
            for name in ("db_path", "job_id", "plan_id", "include_faces")
        ):
            return None
        try:
            if not self._db_revision()["exists"]:
                return None
            result = reader(
                self.root,
                db_path=self.db_path,
                job_id=job["jobId"],
                plan_id=job["planId"],
                include_faces=job["includeFaces"],
            )
        except Exception:  # noqa: BLE001 — no evidence, never infer successful indexing
            return None
        return result if isinstance(result, dict) and result.get("ok") is True else None

    @staticmethod
    def _index_outcome(
        result: dict[str, Any], *, include_faces: bool
    ) -> tuple[str, dict[str, Any] | None, str | None]:
        if result.get("paused") is True:
            return "paused", result, None
        # A committed success wins over a cancellation request arriving too late.
        if result.get("ok") is True:
            if include_faces and result.get("face_capable") is False:
                return "failed", {**result, "partial": True}, "face_model_unavailable"
            return "succeeded", result, None
        if result.get("cancelled") is True:
            return "cancelled", result, "index_cancelled"
        if include_faces and result.get("face_capable") is False:
            return "failed", None, "face_model_unavailable"
        return "failed", None, str(result.get("error") or "index_build_failed")[:160]

    def _load_job_with_lease(self) -> None:
        observed = self._job_store.load()
        if self._unpersisted_job is not None and observed == self._unpersisted_job:
            return
        self._unpersisted_job = None
        if observed["state"] in {"running", "pausing", "cancelling"}:
            receipt = self._receipt_for_job(observed)
            state, result, error = (
                self._index_outcome(receipt, include_faces=observed["includeFaces"])
                if receipt is not None
                else ("failed", None, "service_restart_interrupted")
            )
            observed = self._job_store.save(
                {
                    **observed,
                    "state": state,
                    "completedAt": self._clock(),
                    "result": result,
                    "error": error,
                }
            )
        self._job = observed

    def _refresh_job(self) -> None:
        with self._lock:
            if self._job_lease is not None:
                return
            lease = None
            try:
                lease = self._job_store.try_lease()
                self._job_busy = lease is None
                if lease is None:
                    # Another process owns the writer; reading its atomic
                    # journal must never mark its live task as interrupted.
                    self._job = self._job_store.load()
                else:
                    self._load_job_with_lease()
            except (OSError, ValueError):
                self._job = {**idle_job(), "state": "failed", "error": "job_state_unreadable"}
                self._job_busy = True
            finally:
                if lease is not None:
                    lease.release()

    @staticmethod
    def _is_internal(name: str) -> bool:
        folded = name.casefold()
        return folded in _INTERNAL_NAMES or folded.startswith(".echo-")

    def scan(
        self,
        *,
        fresh: bool = False,
        path_visible: Callable[[str], bool] | None = None,
    ) -> PhotoScan:
        now = time.monotonic()
        with self._scan_lock:
            if (
                path_visible is None
                and not fresh
                and self._scan_cache is not None
                and now - self._scan_cache[0] <= SCAN_CACHE_SECONDS
            ):
                return self._scan_cache[1]
            scan = self._scan_uncached(path_visible=path_visible)
            if path_visible is None:
                self._scan_cache = (time.monotonic(), scan)
            return scan

    def invalidate_scan_cache(self) -> None:
        """Make a newly committed backup visible without forcing an inline scan."""

        with self._scan_lock:
            self._scan_cache = None

    def _scan_uncached(
        self,
        *,
        path_visible: Callable[[str], bool] | None = None,
    ) -> PhotoScan:
        files: list[PhotoFile] = []
        unsafe_links = 0
        truncated = False
        errors = 0
        try:
            root_info = self.root.lstat()
            root_identity = (root_info.st_dev, root_info.st_ino)
            if (
                is_link_or_reparse(root_info)
                or not stat.S_ISDIR(root_info.st_mode)
                or root_identity != self._root_identity
            ):
                return PhotoScan((), 0, False, errors=1)
        except OSError:
            return PhotoScan((), 0, False, errors=1)
        directories = [self.root]
        while directories:
            directory = directories.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name.casefold())
            except OSError:
                errors += 1
                continue
            child_directories: list[Path] = []
            for entry in entries:
                if self._is_internal(entry.name):
                    continue
                try:
                    relative = Path(entry.path).relative_to(self.root).as_posix()
                except ValueError:
                    continue
                if path_visible is not None and not path_visible(relative):
                    continue
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    errors += 1
                    continue
                if is_link_or_reparse(info):
                    if Path(entry.name).suffix.lower() in IMAGE_EXTENSIONS:
                        unsafe_links += 1
                    continue
                if stat.S_ISDIR(info.st_mode):
                    child_directories.append(Path(entry.path))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                suffix = Path(entry.name).suffix.lower()
                if suffix not in IMAGE_EXTENSIONS:
                    continue
                files.append(
                    PhotoFile(
                        path=relative,
                        name=entry.name,
                        size=info.st_size,
                        mtime=info.st_mtime,
                        mtime_ns=info.st_mtime_ns,
                        file_type=suffix.lstrip("."),
                    )
                )
                if len(files) >= self._scan_max_files:
                    truncated = True
                    directories.clear()
                    child_directories.clear()
                    break
            directories.extend(reversed(child_directories))
        files.sort(key=lambda item: (-item.mtime_ns, item.path.casefold()))
        try:
            final_root = self.root.lstat()
            if (
                is_link_or_reparse(final_root)
                or (final_root.st_dev, final_root.st_ino) != root_identity
            ):
                errors += 1
        except OSError:
            errors += 1
        return PhotoScan(tuple(files), unsafe_links, truncated, errors, root_identity)

    def _safe_image_path(self, relative: str) -> Path:
        parts = self._validated_image_parts(relative)
        current = self.root.joinpath(*parts)
        for offset in range(1, len(parts) + 1):
            candidate = self.root.joinpath(*parts[:offset])
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                raise
            if is_link_or_reparse(info):
                raise PhotoPathError("symbolic links are not supported")
        try:
            final_info = current.lstat()
        except FileNotFoundError:
            raise
        if not stat.S_ISREG(final_info.st_mode):
            raise PhotoPathError("photo path is not a regular file")
        if final_info.st_size > MAX_SOURCE_BYTES:
            raise PhotoPathError("image is too large to preview")
        return current

    def _validated_image_parts(self, relative: str) -> tuple[str, ...]:
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative) > 2_048
            or "\x00" in relative
            or "\\" in relative
        ):
            raise PhotoPathError("invalid photo path")
        pure = PurePosixPath(relative)
        if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
            raise PhotoPathError("invalid photo path")
        for part in pure.parts:
            if self._is_internal(part):
                raise PhotoPathError("reserved photo path")
        if Path(pure.name).suffix.lower() not in IMAGE_EXTENSIONS:
            raise PhotoPathError("unsupported image format")
        return tuple(pure.parts)

    def _open_image(self, relative: str) -> OpenedPhoto:
        parts = self._validated_image_parts(relative)
        try:
            stream, info = open_safe_photo(self.root, parts, max_bytes=MAX_SOURCE_BYTES)
            media_type = IMAGE_MEDIA_TYPES[Path(parts[-1]).suffix.lower()]
            return OpenedPhoto(stream, media_type, info.st_size, info.st_mtime_ns)
        except (OSError, ValueError) as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise PhotoPathError("photo path could not be opened safely") from exc

    def _metadata(self, paths: Sequence[str]) -> dict[str, dict[str, Any]]:
        if not paths or not self.db_path.is_file() or self.db_path.is_symlink():
            return {}
        result: dict[str, dict[str, Any]] = {}
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=1.0)
            try:
                for start in range(0, len(paths), 400):
                    batch = paths[start : start + 400]
                    placeholders = ",".join("?" for _ in batch)
                    rows = conn.execute(
                        "SELECT path, width, height, exif_time, file_type, location "
                        f"FROM image_meta WHERE path IN ({placeholders})",
                        tuple(batch),
                    ).fetchall()
                    for path, width, height, captured_at, file_type, location in rows:
                        result[str(path)] = {
                            "width": int(width) if width is not None else None,
                            "height": int(height) if height is not None else None,
                            "capturedAt": str(captured_at or ""),
                            "fileType": str(file_type or ""),
                            "location": str(location or ""),
                        }
            finally:
                conn.close()
        except sqlite3.Error:
            return {}
        return result

    def library(
        self,
        *,
        offset: int = 0,
        limit: int = 120,
        search: str | None = None,
        path_visible: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        scan = self.scan(path_visible=path_visible)
        files = [item for item in scan.files if path_visible is None or path_visible(item.path)]
        needle = (search or "").strip().casefold()
        if needle:
            files = [item for item in files if needle in item.path.casefold()]
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        page = files[offset : offset + limit]
        metadata = self._metadata([item.path for item in page])
        return {
            "schema": PHOTO_LIBRARY_SCHEMA,
            "source": photo_source(self.library_id, revision=scan.fingerprint),
            "total": len(files),
            "offset": offset,
            "limit": limit,
            "scanTruncated": scan.truncated,
            "scanErrors": scan.errors,
            "unsafeLinksSkipped": scan.unsafe_links,
            "items": [
                item.to_dict(metadata.get(item.path), source_id=self.library_id) for item in page
            ],
        }

    def thumbnail(
        self,
        relative: str,
        *,
        size: int = 320,
        if_none_match: str | None = None,
    ) -> tuple[bytes | None, str, str]:
        opened_photo = self._open_image(relative)
        suffix = Path(PurePosixPath(relative).name).suffix.lower()
        size = max(64, min(int(size), 512))
        etag = hashlib.sha256(
            f"{relative}\0{opened_photo.size}\0{opened_photo.mtime_ns}\0{size}".encode()
        ).hexdigest()
        supplied = (if_none_match or "").strip()
        if supplied in {"*", etag, f'"{etag}"', f'W/"{etag}"'}:
            opened_photo.stream.close()
            return None, "image/webp", etag
        if suffix in HEIF_EXTENSIONS and not _ensure_heif_opener():
            opened_photo.stream.close()
            raise PhotoPathError("HEIC/HEIF preview service is unavailable")
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
            opened_photo.stream.close()
            raise PhotoPathError("image preview service is unavailable") from exc

        try:
            with opened_photo.stream as source, Image.open(source) as opened:
                if opened.width * opened.height > 100_000_000:
                    raise PhotoPathError("image dimensions are too large to preview")
                opened.load()
                image = ImageOps.exif_transpose(opened)
                image.thumbnail((size, size), Image.Resampling.LANCZOS)
                if image.mode not in {"RGB", "RGBA"}:
                    image = image.convert("RGBA" if "transparency" in image.info else "RGB")
                output = io.BytesIO()
                image.save(output, format="WEBP", quality=82, method=4)
        except PhotoPathError:
            raise
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError, ValueError) as exc:
            raise PhotoPathError("image preview could not be decoded") from exc
        return output.getvalue(), "image/webp", etag

    def original(self, relative: str) -> OpenedPhoto:
        return self._open_image(relative)

    def _db_revision(self) -> dict[str, Any]:
        try:
            info = self.db_path.lstat()
        except FileNotFoundError:
            return {"exists": False, "size": 0, "mtimeNs": 0}
        if is_link_or_reparse(info) or not stat.S_ISREG(info.st_mode):
            return {"exists": False, "size": 0, "mtimeNs": 0}
        return {"exists": True, "size": info.st_size, "mtimeNs": info.st_mtime_ns}

    def _index_counts(self, paths: Sequence[str] | None = None) -> dict[str, Any]:
        counts: dict[str, Any] = {
            "indexed": 0,
            "faces": 0,
            "duplicateGroups": 0,
            "blurry": 0,
        }
        if not self._db_revision()["exists"]:
            return counts
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=1.0)
            try:
                if paths is not None:
                    hash_counts: dict[str, int] = {}
                    for start in range(0, len(paths), 400):
                        batch = paths[start : start + 400]
                        placeholders = ",".join("?" for _ in batch)
                        if not batch:
                            continue
                        counts["indexed"] += int(
                            conn.execute(
                                f"SELECT COUNT(*) FROM image_clip WHERE path IN ({placeholders})",
                                tuple(batch),
                            ).fetchone()[0]
                        )
                        counts["faces"] += int(
                            conn.execute(
                                f"SELECT COUNT(*) FROM image_faces WHERE path IN ({placeholders})",
                                tuple(batch),
                            ).fetchone()[0]
                        )
                        counts["blurry"] += int(
                            conn.execute(
                                "SELECT COUNT(*) FROM image_quality WHERE sharpness < ? "
                                f"AND path IN ({placeholders})",
                                (50.0, *batch),
                            ).fetchone()[0]
                        )
                        for (digest,) in conn.execute(
                            "SELECT dhash FROM image_hashes WHERE dhash != '' "
                            f"AND path IN ({placeholders})",
                            tuple(batch),
                        ):
                            value = str(digest)
                            hash_counts[value] = hash_counts.get(value, 0) + 1
                    counts["duplicateGroups"] = sum(
                        1 for count in hash_counts.values() if count > 1
                    )
                    return counts
                counts["indexed"] = int(
                    conn.execute("SELECT COUNT(*) FROM image_clip").fetchone()[0]
                )
                counts["faces"] = int(
                    conn.execute("SELECT COUNT(*) FROM image_faces").fetchone()[0]
                )
                counts["duplicateGroups"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM (SELECT dhash FROM image_hashes "
                        "WHERE dhash != '' GROUP BY dhash HAVING COUNT(*) > 1)"
                    ).fetchone()[0]
                )
                counts["blurry"] = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM image_quality WHERE sharpness < ?",
                        (50.0,),
                    ).fetchone()[0]
                )
            finally:
                conn.close()
        except sqlite3.Error:
            counts["error"] = "index_unreadable"
        return counts

    def _cleanup_record_count(self) -> int | None:
        """Count only image-associated rows; never count retained named prototypes."""
        if not self._db_revision()["exists"]:
            return 0
        try:
            with contextlib.closing(
                sqlite3.connect(self.db_path.as_uri() + "?mode=ro", uri=True, timeout=1.0)
            ) as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                return sum(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in _IMAGE_RECORD_TABLES
                    if table in tables
                )
        except (OSError, sqlite3.Error):
            return None

    def _supports_empty_cleanup(self) -> bool:
        supported = getattr(self._backend, "supports_empty_cleanup", None)
        if not callable(supported) or not _accepts_keyword(self._backend.build_index, "empty_only"):
            return False
        try:
            return bool(supported())
        except Exception:  # noqa: BLE001 — do not infer capability from an unavailable adapter
            return False

    def plan_index(
        self, *, include_faces: bool = False, _include_active_job: bool = True
    ) -> dict[str, Any]:
        self._refresh_job()
        scan = self.scan(fresh=True)
        with self._lock:
            running = _include_active_job and (
                self._job["state"] in {"running", "pausing", "paused", "cancelling"}
                or self._job_busy
            )
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        records = self._cleanup_record_count() if not scan.files else 0
        cleanup_only = not scan.files and records is not None and records > 0
        include_faces = bool(include_faces) and not cleanup_only
        if scan.errors or (cleanup_only and scan.truncated):
            blockers.append(
                {
                    "code": "PHOTO_SCAN_INCOMPLETE",
                    "message": "无法完整读取图库，请检查存储连接和读取权限后重新预览；旧索引已保留",
                }
            )
        if not scan.files and not cleanup_only:
            blockers.append({"code": "NO_IMAGES", "message": "NAS 中还没有可索引的照片"})
        if records is None:
            blockers.append(
                {"code": "INDEX_UNREADABLE", "message": "无法读取现有索引，请检查索引数据库后重试"}
            )
        if cleanup_only:
            if not self._supports_empty_cleanup():
                blockers.append(
                    {
                        "code": "EMPTY_INDEX_CLEANUP_UNAVAILABLE",
                        "message": "当前索引组件不支持安全清理空图库，请更新设备组件后重试",
                    }
                )
            warnings.append(
                {
                    "code": "EMPTY_LIBRARY_CLEANUP",
                    "message": "图库已空，本次仅清除失效的图片索引和图片关联记录，保留人物名称、类别与适用库设置，不删除原图",
                }
            )
        readiness = self._readiness()
        semantic = readiness["semantic"]
        if not cleanup_only and not semantic["available"]:
            code = (
                "SEMANTIC_DISABLED"
                if semantic["state"] == "disabled"
                else "SEMANTIC_DEPENDENCIES_MISSING"
                if semantic["missingDependencies"]
                else "AGENT_INDEX_UNAVAILABLE"
            )
            blockers.append(
                {
                    "code": code,
                    "message": (
                        "智能图片索引已在设备配置中关闭"
                        if code == "SEMANTIC_DISABLED"
                        else "智能图片索引组件尚未就绪，照片浏览和文件名搜索仍可使用"
                    ),
                }
            )
        if include_faces and not readiness["faces"]["available"]:
            blockers.append(
                {
                    "code": "FACE_DEPENDENCIES_MISSING",
                    "message": "人物分组组件尚未就绪，可关闭人物分组后建立语义索引",
                }
            )
        if not cleanup_only and (
            readiness["modelDownloadMayBeRequired"]
            or (
                include_faces
                and readiness["faces"]["available"]
                and not readiness["faces"]["modelsLoaded"]
            )
        ):
            warnings.append(
                {
                    "code": "MODEL_DOWNLOAD_MAY_BE_REQUIRED",
                    "message": "模型尚未在当前进程中加载，首次使用可能需要联网下载；加载失败会明确报告",
                }
            )
        if scan.unsafe_links:
            warnings.append(
                {
                    "code": "UNSAFE_LINKS_PRESENT",
                    "message": "已跳过图片符号链接，不会读取链接目标",
                }
            )
        if running:
            blockers.append({"code": "INDEX_RUNNING", "message": "智能索引正在建立"})
        current = self._db_revision()
        counts = self._index_counts()
        changes = [
            {
                "field": "indexedPhotos",
                "before": counts["indexed"],
                "after": min(len(scan.files), self._index_max_files),
            }
        ]
        if cleanup_only:
            changes.extend(
                [
                    {"field": "faceRecords", "before": counts["faces"], "after": 0},
                    {"field": "derivedRecords", "before": records, "after": 0},
                ]
            )
        else:
            changes.append({"field": "faceGrouping", "before": None, "after": bool(include_faces)})
        identity = {
            "schema": PHOTO_INDEX_SCHEMA,
            "operation": "build",
            "cleanupOnly": cleanup_only,
            "libraryFingerprint": scan.fingerprint,
            "imageCount": len(scan.files),
            "unsafeLinks": scan.unsafe_links,
            "scanTruncated": scan.truncated,
            "scanErrors": scan.errors,
            "maxFiles": self._index_max_files,
            "includeFaces": bool(include_faces),
            "current": current,
            "blockers": [item["code"] for item in blockers],
            "changes": changes,
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            **identity,
            "planId": plan_id,
            "source": photo_source(self.library_id, revision=scan.fingerprint),
            "ready": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "readiness": readiness,
            "requiresApproval": True,
            "approvalAction": "photos.index.build",
            "approvalTarget": plan_id,
        }

    def _job_view(self) -> dict[str, Any]:
        self._refresh_job()
        with self._lock:
            return json.loads(json.dumps(self._job))

    def _readiness(self) -> dict[str, Any]:
        probe = getattr(self._backend, "readiness", None)
        if callable(probe):
            return probe()
        # Preserve the small injected-backend contract used by integrations.
        # Only the production Agent adapter claims detailed dependency checks.
        available = bool(self._backend.available())
        capability = {
            "available": available,
            "state": "dependencies-ready" if available else "unavailable",
            "missingDependencies": [],
            "modelsLoaded": False,
        }
        return {
            "schema": "echo.photos.readiness.v1",
            "browseAvailable": True,
            "previewAvailable": True,
            "semantic": dict(capability),
            "faces": dict(capability),
            "modelDownloadMayBeRequired": False,
        }

    def status(
        self,
        *,
        path_visible: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        scan = self.scan(path_visible=path_visible)
        files = [item for item in scan.files if path_visible is None or path_visible(item.path)]
        revision = self._db_revision()
        readiness = self._readiness()
        return {
            "schema": PHOTO_STATUS_SCHEMA,
            "source": photo_source(self.library_id, revision=scan.fingerprint),
            "library": {
                "imageCount": len(files),
                "scanTruncated": scan.truncated,
                "unsafeLinksSkipped": scan.unsafe_links,
                "scanErrors": scan.errors,
            },
            "index": {
                "canManage": path_visible is None,
                "backendAvailable": readiness["semantic"]["available"],
                "cleanupAvailable": (
                    path_visible is None
                    and not scan.files
                    and not scan.errors
                    and not scan.truncated
                    and bool(self._cleanup_record_count())
                    and self._supports_empty_cleanup()
                ),
                "readiness": readiness,
                "databaseExists": revision["exists"],
                "maxFiles": self._index_max_files,
                **self._index_counts(
                    None if path_visible is None else [item.path for item in files]
                ),
            },
            "job": self._job_view() if path_visible is None else idle_job(),
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 24,
        path_visible: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("search query is required")
        limit = max(1, min(int(limit), 50))
        scan = self.scan(path_visible=path_visible)
        safe = {
            item.path: item
            for item in scan.files
            if path_visible is None or path_visible(item.path)
        }
        semantic: list[dict[str, Any]] | None = None
        if self._db_revision()["exists"] and self._backend.available():
            if path_visible is None:
                semantic = self._backend.search_by_text(
                    query, top_k=limit * 2, db_path=self.db_path
                )
            else:
                scoped_search = getattr(self._backend, "search_by_text_in_paths", None)
                # Old backends cannot guarantee scoped ranking. Use the existing
                # filename fallback rather than silently truncate the member's set.
                if safe and callable(scoped_search):
                    semantic = scoped_search(
                        query,
                        top_k=limit * 2,
                        db_path=self.db_path,
                        allowed_paths=tuple(safe),
                    )
        results: list[tuple[PhotoFile, float | None]] = []
        if semantic:
            seen: set[str] = set()
            for raw in semantic:
                path = str(raw.get("path") or "")
                item = safe.get(path)
                if item is None or path in seen:
                    continue
                seen.add(path)
                try:
                    score = float(raw.get("score"))
                except (TypeError, ValueError):
                    score = None
                results.append((item, score))
                if len(results) >= limit:
                    break
            mode = "semantic"
        else:
            needle = query.casefold()
            results = [
                (item, None)
                for item in scan.files
                if (path_visible is None or path_visible(item.path))
                and needle in item.path.casefold()
            ][:limit]
            mode = "filename"
        metadata = self._metadata([item.path for item, _ in results])
        return {
            "schema": PHOTO_SEARCH_SCHEMA,
            "source": photo_source(self.library_id, revision=scan.fingerprint),
            "query": query,
            "mode": mode,
            "total": len(results),
            "items": [
                {
                    **item.to_dict(metadata.get(item.path), source_id=self.library_id),
                    "score": score,
                }
                for item, score in results
            ],
        }

    def start_index(
        self,
        *,
        plan_id: str,
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._job_lease is not None:
                raise PhotoIndexConflict("photo index is already running")
            try:
                lease = self._job_store.try_lease()
            except OSError as exc:
                raise PhotoIndexConflict("photo index job lock is unavailable") from exc
            if lease is None:
                raise PhotoIndexConflict("photo index is already running")
            self._job_lease = lease
            try:
                self._job_busy = False
                try:
                    self._load_job_with_lease()
                except (OSError, ValueError) as exc:
                    raise PhotoIndexConflict("photo index job state is unavailable") from exc
                return self._start_index_claimed(
                    plan_id=plan_id, include_faces=include_faces, on_complete=on_complete
                )
            except BaseException:
                self._job_lease = None
                lease.release()
                raise

    def _start_index_claimed(
        self,
        *,
        plan_id: str,
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> dict[str, Any]:
        plan = self.plan_index(include_faces=include_faces)
        include_faces = plan["includeFaces"]
        if plan["planId"] != plan_id:
            raise PhotoIndexConflict("photo index plan changed; review again")
        if not plan["ready"]:
            raise PhotoIndexConflict("photo index is currently blocked")
        scan = self.scan(fresh=True)
        if scan.fingerprint != plan["libraryFingerprint"]:
            raise PhotoIndexConflict("photo library changed; review a new index plan")
        safe_paths = tuple(item.path for item in scan.files[: self._index_max_files])
        with self._lock:
            if self._job["state"] in {"running", "pausing", "paused", "cancelling"}:
                raise PhotoIndexConflict("photo index is already running")
            job_id = secrets.token_hex(12)
            pending_job = {
                "state": "running",
                "jobId": job_id,
                "planId": plan_id,
                "includeFaces": bool(include_faces),
                "cleanupOnly": plan["cleanupOnly"],
                "startedAt": self._clock(),
                "completedAt": None,
                "result": None,
                "error": None,
            }
            try:
                self._job = self._job_store.save(pending_job)
                self._unpersisted_job = None
            except (OSError, ValueError) as exc:
                self._job = {
                    **pending_job,
                    "state": "failed",
                    "completedAt": self._clock(),
                    "error": "job_state_unavailable",
                }
                with contextlib.suppress(OSError, ValueError):
                    self._unpersisted_job = self._job_store.load()
                raise PhotoIndexConflict("photo index job state could not be saved") from exc
            self._cancel_event = threading.Event()
            self._pause_event = threading.Event()
            self._worker_job_id = job_id
            self._start_index_worker(safe_paths, include_faces, on_complete)
            return self._job_view()

    def _start_index_worker(
        self,
        safe_paths: Sequence[str],
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        worker = threading.Thread(
            target=self._run_index,
            args=(safe_paths, include_faces, on_complete),
            name="echo-photo-index",
            daemon=True,
        )
        self._worker = worker
        try:
            worker.start()
        except RuntimeError as exc:
            self._cancel_event = None
            self._pause_event = None
            self._worker_job_id = None
            self._job = {
                **self._job,
                "state": "failed",
                "completedAt": self._clock(),
                "error": "index_worker_unavailable",
            }
            with contextlib.suppress(OSError, ValueError):
                self._job_store.save(self._job)
            raise PhotoIndexConflict("photo index worker could not start") from exc

    def _supports_pause(self) -> bool:
        supported = getattr(self._backend, "supports_pause", None)
        capable = _accepts_keyword(self._backend.build_index, "should_pause")
        if capable and callable(supported):
            try:
                capable = bool(supported())
            except Exception:  # noqa: BLE001 - unavailable protocol cannot promise a pause
                capable = False
        return capable

    def pause_index(self, job_id: str) -> dict[str, Any]:
        """Request a safe checkpoint pause without discarding the old index."""

        with self._lock:
            self._refresh_job()
            if self._job.get("jobId") != job_id:
                raise PhotoIndexConflict(
                    "photo index job changed; refresh its status", code="photo_index_job_changed"
                )
            if self._job["state"] in {"succeeded", "failed", "cancelled", "paused"}:
                return self._job_view()
            if self._job["state"] == "pausing":
                return self._job_view()
            if self._job["state"] != "running":
                raise PhotoIndexConflict(
                    "photo index job cannot be paused in its current state",
                    code="photo_index_pause_unavailable",
                )
            if (
                self._job_lease is None
                or self._cancel_event is None
                or self._pause_event is None
                or self._worker_job_id != job_id
            ):
                raise PhotoIndexConflict(
                    "photo index job belongs to another worker process",
                    code="photo_index_not_owner",
                )
            if not self._supports_pause():
                raise PhotoIndexConflict(
                    "photo index backend does not support safe pause",
                    code="photo_index_pause_unavailable",
                )
            try:
                self._job = self._job_store.save({**self._job, "state": "pausing"})
            except (OSError, ValueError) as exc:
                raise PhotoIndexConflict(
                    "photo index pause could not be saved",
                    code="photo_index_job_state_unavailable",
                ) from exc
            self._pause_event.set()
            return self._job_view()

    def resume_index(
        self,
        job_id: str,
        *,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Resume a paused job by rebuilding from its last committed snapshot."""

        lease_acquired = False
        with self._lock:
            self._refresh_job()
            if self._job.get("jobId") != job_id:
                raise PhotoIndexConflict(
                    "photo index job changed; refresh its status", code="photo_index_job_changed"
                )
            state = self._job["state"]
            if state in {"succeeded", "failed", "cancelled"}:
                return self._job_view()
            if state == "running":
                return self._job_view()
            if state != "paused":
                raise PhotoIndexConflict(
                    "photo index job cannot be resumed in its current state",
                    code="photo_index_resume_unavailable",
                )

            if self._job_lease is None:
                try:
                    lease = self._job_store.try_lease()
                except OSError as exc:
                    raise PhotoIndexConflict(
                        "photo index job lock is unavailable", code="photo_index_not_owner"
                    ) from exc
                if lease is None:
                    raise PhotoIndexConflict(
                        "photo index is already running", code="photo_index_not_owner"
                    )
                self._job_lease = lease
                lease_acquired = True
                try:
                    self._job_busy = False
                    self._load_job_with_lease()
                except (OSError, ValueError) as exc:
                    self._job_lease = None
                    lease.release()
                    raise PhotoIndexConflict(
                        "photo index job state is unavailable",
                        code="photo_index_job_state_unavailable",
                    ) from exc
                if self._job.get("jobId") != job_id or self._job["state"] != "paused":
                    self._job_lease = None
                    lease.release()
                    raise PhotoIndexConflict(
                        "photo index job changed; refresh its status",
                        code="photo_index_job_changed",
                    )

            try:
                plan = self.plan_index(
                    include_faces=bool(self._job["includeFaces"]),
                    _include_active_job=False,
                )
                if plan["planId"] != self._job["planId"] or not plan["ready"]:
                    raise PhotoIndexConflict(
                        "photo library or index dependencies changed; review a new plan",
                        code="photo_index_resume_unavailable",
                    )
                scan = self.scan(fresh=True)
                if scan.fingerprint != plan["libraryFingerprint"]:
                    raise PhotoIndexConflict(
                        "photo library changed; review a new index plan",
                        code="photo_index_resume_unavailable",
                    )
                safe_paths = tuple(item.path for item in scan.files[: self._index_max_files])
                self._job = self._job_store.save(
                    {
                        **self._job,
                        "state": "running",
                        "completedAt": None,
                        "result": None,
                        "error": None,
                    }
                )
                self._cancel_event = threading.Event()
                self._pause_event = threading.Event()
                self._worker_job_id = job_id
                self._start_index_worker(safe_paths, bool(plan["includeFaces"]), on_complete)
                return self._job_view()
            except BaseException:
                if lease_acquired and self._job_lease is not None:
                    lease = self._job_lease
                    self._job_lease = None
                    self._cancel_event = None
                    self._pause_event = None
                    self._worker_job_id = None
                    lease.release()
                raise

    def cancel_index(self, job_id: str) -> dict[str, Any]:
        """Request cooperative cancellation of exactly this process-owned job."""
        with self._lock:
            self._refresh_job()
            if self._job.get("jobId") != job_id:
                raise PhotoIndexConflict(
                    "photo index job changed; refresh its status", code="photo_index_job_changed"
                )
            if self._job["state"] in {"succeeded", "failed", "cancelled"}:
                return self._job_view()
            if self._job["state"] == "paused" and self._job_lease is None:
                try:
                    lease = self._job_store.try_lease()
                except OSError as exc:
                    raise PhotoIndexConflict(
                        "photo index job lock is unavailable", code="photo_index_not_owner"
                    ) from exc
                if lease is None:
                    raise PhotoIndexConflict(
                        "photo index is already running", code="photo_index_not_owner"
                    )
                self._job_lease = lease
                try:
                    self._job_busy = False
                    self._load_job_with_lease()
                    if self._job.get("jobId") != job_id or self._job["state"] != "paused":
                        raise PhotoIndexConflict(
                            "photo index job changed; refresh its status",
                            code="photo_index_job_changed",
                        )
                    self._job = self._job_store.save(
                        {
                            **self._job,
                            "state": "cancelled",
                            "completedAt": self._clock(),
                            "result": {
                                "cancelled": True,
                                "retained_previous": True,
                            },
                            "error": "index_cancelled",
                        }
                    )
                    return self._job_view()
                finally:
                    lease = self._job_lease
                    self._job_lease = None
                    if lease is not None:
                        lease.release()
            if (
                self._job_lease is None
                or self._cancel_event is None
                or self._worker_job_id != job_id
            ):
                raise PhotoIndexConflict(
                    "photo index job belongs to another worker process",
                    code="photo_index_not_owner",
                )
            supports = getattr(self._backend, "supports_cancellation", None)
            cancellable = _accepts_keyword(self._backend.build_index, "should_cancel")
            if cancellable and callable(supports):
                try:
                    cancellable = bool(supports())
                except Exception:  # noqa: BLE001 — unavailable protocol cannot promise a stop
                    cancellable = False
            if not cancellable:
                raise PhotoIndexConflict(
                    "photo index backend does not support cooperative cancellation",
                    code="photo_index_cancel_unavailable",
                )
            if self._job["state"] != "cancelling":
                try:
                    self._job = self._job_store.save({**self._job, "state": "cancelling"})
                except (OSError, ValueError) as exc:
                    raise PhotoIndexConflict(
                        "photo index cancellation could not be saved",
                        code="photo_index_job_state_unavailable",
                    ) from exc
            self._cancel_event.set()
            if self._pause_event is not None:
                self._pause_event.clear()
            return self._job_view()

    def _run_index(
        self,
        safe_paths: Sequence[str],
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        try:
            self._run_index_owned(safe_paths, include_faces, on_complete)
        finally:
            with self._lock:
                lease = self._job_lease
                self._job_lease = None
                self._cancel_event = None
                self._pause_event = None
                self._worker_job_id = None
                self._worker = None
                if lease is not None:
                    lease.release()

    def _run_index_owned(
        self,
        safe_paths: Sequence[str],
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        state = "failed"
        result: dict[str, Any] | None = None
        error: str | None = None
        with self._lock:
            running_job = dict(self._job)
            cancel_event = self._cancel_event
            pause_event = self._pause_event
        try:
            try:
                index_info = self.db_path.lstat()
            except FileNotFoundError:
                index_info = None
            if index_info is not None and (
                is_link_or_reparse(index_info) or not stat.S_ISREG(index_info.st_mode)
            ):
                raise OSError("unsafe photo index database")
            cleanup_only = running_job.get("cleanupOnly") is True
            if cleanup_only:
                # Recheck after worker acquisition too: a dropped/replaced root,
                # unreadable directory or newly arrived photo is not an empty library.
                scan = self.scan(fresh=True)
                if scan.files or scan.errors or scan.truncated or safe_paths:
                    raise PhotoIndexConflict("empty photo library changed before cleanup")
                if not self._supports_empty_cleanup():
                    raise PhotoIndexConflict("empty photo index cleanup is unavailable")
            optional = {
                name: value
                for name, value in {
                    "job_id": running_job["jobId"],
                    "plan_id": running_job["planId"],
                    "should_cancel": cancel_event.is_set if cancel_event is not None else None,
                    "should_pause": pause_event.is_set if pause_event is not None else None,
                    "empty_only": cleanup_only,
                }.items()
                if _accepts_keyword(self._backend.build_index, name)
            }
            raw_result = self._backend.build_index(
                self.root,
                self.db_path,
                safe_paths,
                include_faces=include_faces,
                max_files=self._index_max_files,
                **optional,
            )
            if not isinstance(raw_result, dict):
                raise ValueError("invalid index result")
            state, result, error = self._index_outcome(raw_result, include_faces=include_faces)
            if state == "succeeded":
                with contextlib.suppress(OSError):
                    self.db_path.chmod(0o600)
        except Exception as exc:  # noqa: BLE001 - worker must publish a safe failure state
            error = type(exc).__name__
        if state != "succeeded":
            receipt = self._receipt_for_job(running_job)
            if receipt is not None:
                state, result, error = self._index_outcome(receipt, include_faces=include_faces)
        with self._lock:
            self._job = {
                **self._job,
                "state": state,
                "completedAt": None if state == "paused" else self._clock(),
                "result": result,
                "error": error,
            }
            try:
                self._job = self._job_store.save(self._job)
            except (OSError, ValueError):
                self._job = {
                    **self._job,
                    "state": "failed",
                    "result": None,
                    "error": "job_state_unavailable",
                }
                with contextlib.suppress(OSError, ValueError):
                    self._unpersisted_job = self._job_store.load()
            completed = self._job_view()
        if on_complete is not None and completed["state"] != "paused":
            with contextlib.suppress(Exception):
                on_complete(completed)

    def wait_for_idle(self, timeout: float = 5.0) -> dict[str, Any]:
        """Test/deployment helper; the HTTP API never blocks on model work."""

        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=max(0.0, timeout))
        return self._job_view()


__all__ = [
    "AgentImageIndexAdapter",
    "DEFAULT_INDEX_MAX_FILES",
    "IMAGE_EXTENSIONS",
    "IMAGE_MEDIA_TYPES",
    "OpenedPhoto",
    "PhotoIndexConflict",
    "PhotoLibraryService",
    "PhotoPathError",
]
