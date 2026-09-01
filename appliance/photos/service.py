"""Safe NAS photo projection with an isolated Agent semantic-index adapter.

The appliance owns filesystem trust, plan drift detection and job lifecycle.
Agent owns the expensive CLIP/face implementation and SQLite schema.  This
keeps Echo from copying model code while ensuring Agent never gets an
unfiltered view that includes recycle-bin contents, upload internals or links.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import sqlite3
import stat
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Protocol

from appliance.agent_api.images import load_agent_image_index_module

PHOTO_INDEX_SCHEMA = "echo.photos.index-plan.v1"
PHOTO_LIBRARY_SCHEMA = "echo.photos.library.v1"
PHOTO_STATUS_SCHEMA = "echo.photos.status.v1"
PHOTO_SEARCH_SCHEMA = "echo.photos.search.v1"
DEFAULT_INDEX_MAX_FILES = 4_000
DEFAULT_SCAN_MAX_FILES = 20_000
SCAN_CACHE_SECONDS = 2.0
MAX_SOURCE_BYTES = 256 * 1024 * 1024
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"})
IMAGE_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
}
_INTERNAL_NAMES = frozenset({".echo-trash", ".echo-upload-sessions"})


class PhotoPathError(ValueError):
    """A requested photo path is unsafe or outside the NAS root."""


class PhotoIndexConflict(RuntimeError):
    """The index plan changed or another index job already owns the worker."""


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

    def to_dict(self, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        meta = metadata or {}
        return {
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


@dataclass(frozen=True)
class PhotoScan:
    files: tuple[PhotoFile, ...]
    unsafe_links: int
    truncated: bool

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
        module = self._module()
        return bool(
            module is not None
            and callable(getattr(module, "build_index", None))
            and callable(getattr(module, "search_by_text", None))
            and callable(getattr(module, "_iter_images", None))
            and callable(getattr(module, "_load_image", None))
            and callable(getattr(module, "_mtime", None))
        )

    def build_index(
        self,
        root: Path,
        db_path: Path,
        image_paths: Sequence[str],
        *,
        include_faces: bool,
        max_files: int,
    ) -> dict[str, Any]:
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
        safe_root = root.resolve()
        observed_mtimes: dict[str, float] = {}

        def vetted_relative(raw_path: str | Path) -> str | None:
            candidate = Path(raw_path)
            try:
                relative = candidate.relative_to(safe_root).as_posix()
            except ValueError:
                return None
            return relative if relative in allowed else None

        def vetted_iterator(scan_root: Path, max_files: int = DEFAULT_INDEX_MAX_FILES):
            candidate_root = Path(scan_root).resolve()
            if candidate_root != safe_root:
                return original_iterator(scan_root, max_files=max_files)
            return [safe_root.joinpath(*PurePosixPath(item).parts) for item in bounded[:max_files]]

        def vetted_loader(raw_path: str | Path):
            relative = vetted_relative(raw_path)
            if relative is None:
                return original_loader(raw_path)
            parts = PurePosixPath(relative).parts
            directory_fd = os.open(
                safe_root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            file_fd: int | None = None
            try:
                for part in parts[:-1]:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    os.close(directory_fd)
                    directory_fd = next_fd
                file_fd = os.open(
                    parts[-1],
                    os.O_RDONLY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_SOURCE_BYTES:
                    return None
                from PIL import Image

                with os.fdopen(file_fd, "rb") as source:
                    file_fd = None
                    with Image.open(source) as opened:
                        if opened.width * opened.height > 100_000_000:
                            return None
                        image = opened.convert("RGB")
                observed_mtimes[str(Path(raw_path))] = info.st_mtime
                return image
            except (ImportError, OSError, ValueError):
                return None
            finally:
                if file_fd is not None:
                    os.close(file_fd)
                os.close(directory_fd)

        def vetted_mtime(raw_path: str | Path) -> float:
            if vetted_relative(raw_path) is None:
                return original_mtime(raw_path)
            return observed_mtimes.get(str(Path(raw_path)), 0.0)

        with self._adapter_lock:
            module._iter_images = vetted_iterator
            module._load_image = vetted_loader
            module._mtime = vetted_mtime
            try:
                result = module.build_index(
                    root,
                    db_path=db_path,
                    include_faces=include_faces,
                    max_files=max_files,
                )
            finally:
                module._iter_images = original_iterator
                module._load_image = original_loader
                module._mtime = original_mtime
        return result if isinstance(result, dict) else {"ok": False, "error": "invalid_result"}

    def search_by_text(
        self,
        query: str,
        *,
        top_k: int,
        db_path: Path,
    ) -> list[dict[str, Any]] | None:
        module = self._module()
        search = getattr(module, "search_by_text", None) if module is not None else None
        if not callable(search):
            return None
        result = search(query, top_k=top_k, db_path=db_path)
        return result if isinstance(result, list) else None


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
        self._job: dict[str, Any] = {
            "state": "idle",
            "jobId": None,
            "planId": None,
            "includeFaces": False,
            "startedAt": None,
            "completedAt": None,
            "result": None,
            "error": None,
        }
        self._worker: threading.Thread | None = None

    @staticmethod
    def _is_internal(name: str) -> bool:
        return name in _INTERNAL_NAMES or name.startswith(".echo-")

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
        directories = [self.root]
        while directories:
            directory = directories.pop()
            try:
                with os.scandir(directory) as iterator:
                    entries = sorted(iterator, key=lambda item: item.name.casefold())
            except OSError:
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
                    continue
                if stat.S_ISLNK(info.st_mode):
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
        return PhotoScan(tuple(files), unsafe_links, truncated)

    def _safe_image_path(self, relative: str) -> Path:
        parts = self._validated_image_parts(relative)
        current = self.root.joinpath(*parts)
        for offset in range(1, len(parts) + 1):
            candidate = self.root.joinpath(*parts[:offset])
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                raise
            if stat.S_ISLNK(info.st_mode):
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
        directory_fd: int | None = None
        file_fd: int | None = None
        try:
            directory_fd = os.open(
                self.root,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            )
            for part in parts[:-1]:
                next_fd = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            file_fd = os.open(
                parts[-1],
                os.O_RDONLY | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode):
                raise PhotoPathError("photo path is not a regular file")
            if info.st_size > MAX_SOURCE_BYTES:
                raise PhotoPathError("image is too large to preview")
            media_type = IMAGE_MEDIA_TYPES[Path(parts[-1]).suffix.lower()]
            stream = os.fdopen(file_fd, "rb")
            file_fd = None
            return OpenedPhoto(stream, media_type, info.st_size, info.st_mtime_ns)
        except OSError as exc:
            if isinstance(exc, FileNotFoundError):
                raise
            raise PhotoPathError("photo path could not be opened safely") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if directory_fd is not None:
                os.close(directory_fd)

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
            "total": len(files),
            "offset": offset,
            "limit": limit,
            "scanTruncated": scan.truncated,
            "unsafeLinksSkipped": scan.unsafe_links,
            "items": [item.to_dict(metadata.get(item.path)) for item in page],
        }

    def thumbnail(
        self,
        relative: str,
        *,
        size: int = 320,
        if_none_match: str | None = None,
    ) -> tuple[bytes | None, str, str]:
        opened_photo = self._open_image(relative)
        size = max(64, min(int(size), 512))
        etag = hashlib.sha256(
            f"{relative}\0{opened_photo.size}\0{opened_photo.mtime_ns}\0{size}".encode()
        ).hexdigest()
        supplied = (if_none_match or "").strip()
        if supplied in {"*", etag, f'"{etag}"', f'W/"{etag}"'}:
            opened_photo.stream.close()
            return None, "image/webp", etag
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError
        except ImportError as exc:
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
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
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

    def plan_index(self, *, include_faces: bool = False) -> dict[str, Any]:
        scan = self.scan(fresh=True)
        with self._lock:
            running = self._job["state"] == "running"
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        if not scan.files:
            blockers.append({"code": "NO_IMAGES", "message": "NAS 中还没有可索引的照片"})
        if not self._backend.available():
            blockers.append(
                {"code": "AGENT_INDEX_UNAVAILABLE", "message": "Agent 图片索引组件尚未安装"}
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
        identity = {
            "schema": PHOTO_INDEX_SCHEMA,
            "operation": "build",
            "libraryFingerprint": scan.fingerprint,
            "imageCount": len(scan.files),
            "unsafeLinks": scan.unsafe_links,
            "scanTruncated": scan.truncated,
            "maxFiles": self._index_max_files,
            "includeFaces": bool(include_faces),
            "current": current,
            "blockers": [item["code"] for item in blockers],
        }
        canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        plan_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return {
            **identity,
            "planId": plan_id,
            "ready": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "requiresApproval": True,
            "approvalAction": "photos.index.build",
            "approvalTarget": plan_id,
            "changes": [
                {
                    "field": "indexedPhotos",
                    "before": self._index_counts()["indexed"],
                    "after": min(len(scan.files), self._index_max_files),
                },
                {"field": "faceGrouping", "before": None, "after": bool(include_faces)},
            ],
        }

    def _job_view(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._job))

    def status(
        self,
        *,
        path_visible: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        scan = self.scan(path_visible=path_visible)
        files = [item for item in scan.files if path_visible is None or path_visible(item.path)]
        revision = self._db_revision()
        return {
            "schema": PHOTO_STATUS_SCHEMA,
            "library": {
                "imageCount": len(files),
                "scanTruncated": scan.truncated,
                "unsafeLinksSkipped": scan.unsafe_links,
            },
            "index": {
                "backendAvailable": self._backend.available(),
                "databaseExists": revision["exists"],
                "maxFiles": self._index_max_files,
                **self._index_counts(
                    None if path_visible is None else [item.path for item in files]
                ),
            },
            "job": self._job_view(),
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
            semantic = self._backend.search_by_text(query, top_k=limit * 2, db_path=self.db_path)
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
            "query": query,
            "mode": mode,
            "total": len(results),
            "items": [
                {**item.to_dict(metadata.get(item.path)), "score": score} for item, score in results
            ],
        }

    def start_index(
        self,
        *,
        plan_id: str,
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        plan = self.plan_index(include_faces=include_faces)
        if plan["planId"] != plan_id:
            raise PhotoIndexConflict("photo index plan changed; review again")
        if not plan["ready"]:
            raise PhotoIndexConflict("photo index is currently blocked")
        scan = self.scan(fresh=True)
        if scan.fingerprint != plan["libraryFingerprint"]:
            raise PhotoIndexConflict("photo library changed; review a new index plan")
        safe_paths = tuple(item.path for item in scan.files[: self._index_max_files])
        with self._lock:
            if self._job["state"] == "running":
                raise PhotoIndexConflict("photo index is already running")
            job_id = hashlib.sha256(
                f"{plan_id}\0{self._clock()}\0{os.getpid()}".encode("ascii")
            ).hexdigest()[:24]
            self._job = {
                "state": "running",
                "jobId": job_id,
                "planId": plan_id,
                "includeFaces": bool(include_faces),
                "startedAt": self._clock(),
                "completedAt": None,
                "result": None,
                "error": None,
            }
            worker = threading.Thread(
                target=self._run_index,
                args=(safe_paths, include_faces, on_complete),
                name="echo-photo-index",
                daemon=True,
            )
            self._worker = worker
            worker.start()
            return self._job_view()

    def _run_index(
        self,
        safe_paths: Sequence[str],
        include_faces: bool,
        on_complete: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        state = "failed"
        result: dict[str, Any] | None = None
        error: str | None = None
        try:
            if self.db_path.is_symlink():
                raise OSError("unsafe photo index database")
            result = self._backend.build_index(
                self.root,
                self.db_path,
                safe_paths,
                include_faces=include_faces,
                max_files=self._index_max_files,
            )
            if result.get("ok") is True:
                state = "succeeded"
                with contextlib.suppress(OSError):
                    self.db_path.chmod(0o600)
            else:
                error = str(result.get("error") or "index_build_failed")[:160]
        except Exception as exc:  # noqa: BLE001 - worker must publish a safe failure state
            error = type(exc).__name__
        with self._lock:
            self._job = {
                **self._job,
                "state": state,
                "completedAt": self._clock(),
                "result": result if state == "succeeded" else None,
                "error": error,
            }
            completed = self._job_view()
        if on_complete is not None:
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
