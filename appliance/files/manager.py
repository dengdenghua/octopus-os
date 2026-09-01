"""NAS 文件管理器核心逻辑(无 HTTP,便于单测)。

路径安全是第一要务:所有相对路径解析后必须落在 root 内,拒绝 `..` 越权与
符号链接逃逸。删除走回收站语义,物理删除仅 empty_trash。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import stat
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

TRASH_DIRNAME = ".echo-trash"
_MANIFEST = "manifest.json"
_UPLOAD_TEMP_PREFIX = ".echo-upload-"
_UPLOAD_TEMP_SUFFIX = ".part"
_UPLOAD_SESSION_DIRNAME = ".echo-upload-sessions"
_UPLOAD_SESSION_VERSION = 1
DEFAULT_UPLOAD_RESERVE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024**3
DEFAULT_STALE_UPLOAD_SECONDS = 24 * 3600
DEFAULT_UPLOAD_CHUNK_BYTES = 8 * 1024**2
DEFAULT_MAX_UPLOAD_SESSIONS = 64
MAX_SHARE_QUOTAS = 256
DEFAULT_USAGE_MAX_ENTRIES = 200_000
USAGE_CACHE_SECONDS = 10.0

_USAGE_EXTENSIONS = {
    "photos": frozenset(
        {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
    ),
    "videos": frozenset({".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mts", ".m2ts", ".ts"}),
    "audio": frozenset({".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg", ".opus", ".wma"}),
    "documents": frozenset(
        {
            ".txt",
            ".md",
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".ppt",
            ".pptx",
            ".csv",
            ".epub",
        }
    ),
    "archives": frozenset(
        {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso", ".dmg", ".pkg"}
    ),
}


class PathEscape(ValueError):
    """相对路径解析后逃出了 root。"""


class UploadTooLarge(ValueError):
    """Declared or streamed upload exceeds the appliance safety limit."""


class InsufficientStorage(RuntimeError):
    """The NAS would cross its reserved free-space floor."""


class UploadOffsetMismatch(ValueError):
    """A resumable chunk did not start at the server's committed offset."""

    def __init__(self, expected_offset: int) -> None:
        super().__init__(f"upload offset must be {expected_offset}")
        self.expected_offset = expected_offset


class UploadHashMismatch(ValueError):
    """The completed upload does not match its declared SHA-256."""


class UploadSessionLimit(RuntimeError):
    """Too many resumable uploads are retaining storage reservations."""


class ShareQuotaExceeded(InsufficientStorage):
    """A managed shared directory would exceed its logical byte quota."""

    def __init__(self, report: dict[str, Any]) -> None:
        path = str(report["path"])
        super().__init__(f"share quota exceeded for {path!r}")
        self.report = report


@dataclass(frozen=True)
class ShareQuota:
    path: str
    limit_bytes: int


@dataclass
class FileEntry:
    name: str
    path: str  # root 相对路径(posix 风格)
    kind: str  # "dir" | "file"
    size: int
    mtime: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mtime": self.mtime,
        }


class FileManager:
    def __init__(
        self,
        root: str | Path,
        *,
        upload_reserve_bytes: int | None = None,
        max_upload_bytes: int | None = None,
        stale_upload_seconds: int | None = None,
        max_upload_sessions: int | None = None,
        share_quotas: Mapping[str, int] | str | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.upload_reserve_bytes = self._setting(
            "ECHO_UPLOAD_RESERVE_BYTES",
            upload_reserve_bytes,
            DEFAULT_UPLOAD_RESERVE_BYTES,
            minimum=0,
        )
        self.max_upload_bytes = self._setting(
            "ECHO_UPLOAD_MAX_BYTES",
            max_upload_bytes,
            DEFAULT_MAX_UPLOAD_BYTES,
            minimum=1,
        )
        self.stale_upload_seconds = self._setting(
            "ECHO_UPLOAD_STALE_SECONDS",
            stale_upload_seconds,
            DEFAULT_STALE_UPLOAD_SECONDS,
            minimum=60,
        )
        self.max_upload_sessions = self._setting(
            "ECHO_UPLOAD_MAX_SESSIONS",
            max_upload_sessions,
            DEFAULT_MAX_UPLOAD_SESSIONS,
            minimum=1,
        )
        self.share_quotas = self._share_quota_setting(share_quotas)
        self._upload_lock = threading.RLock()
        self._active_uploads: set[Path] = set()
        self._multipart_uploads: dict[Path, dict[str, Any]] = {}
        self._upload_sessions: dict[str, dict[str, Any]] = {}
        self._upload_session_locks: dict[str, threading.Lock] = {}
        self._quota_blocked_sessions: set[str] = set()
        self._usage_lock = threading.RLock()
        self._usage_cache: tuple[float, dict[str, Any]] | None = None
        self._load_upload_sessions()
        self.cleanup_expired_upload_sessions()
        self._refresh_upload_session_quota_flags()
        self.cleanup_stale_uploads("")

    @staticmethod
    def _setting(
        environment_name: str,
        explicit: int | None,
        default: int,
        *,
        minimum: int,
    ) -> int:
        raw: int | str = (
            explicit if explicit is not None else os.environ.get(environment_name, default)
        )
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{environment_name} must be an integer") from exc
        if value < minimum:
            raise ValueError(f"{environment_name} must be at least {minimum}")
        return value

    def _share_quota_setting(
        self,
        explicit: Mapping[str, int] | str | None,
    ) -> tuple[ShareQuota, ...]:
        raw: object = (
            explicit if explicit is not None else os.environ.get("ECHO_SHARE_QUOTAS_JSON", "")
        )
        if raw is None or raw == "":
            return ()
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("ECHO_SHARE_QUOTAS_JSON must be valid JSON") from exc
        if not isinstance(raw, Mapping):
            raise ValueError("ECHO_SHARE_QUOTAS_JSON must be a JSON object")
        if len(raw) > MAX_SHARE_QUOTAS:
            raise ValueError(f"at most {MAX_SHARE_QUOTAS} share quotas are supported")
        normalized: dict[str, int] = {}
        for raw_path, raw_limit in raw.items():
            if not isinstance(raw_path, str):
                raise ValueError("share quota paths must be strings")
            if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit < 0:
                raise ValueError("share quota values must be non-negative integers")
            path = self._normalize_share_quota_path(raw_path)
            if path in normalized:
                raise ValueError(f"duplicate normalized share quota path: {path!r}")
            normalized[path] = raw_limit
        return tuple(
            ShareQuota(path=path, limit_bytes=limit)
            for path, limit in sorted(
                normalized.items(),
                key=lambda item: (len(PurePosixPath(item[0]).parts), item[0]),
            )
        )

    def _normalize_share_quota_path(self, raw_path: str) -> str:
        value = raw_path.strip()
        if value in {"", "."}:
            return ""
        if value.startswith("/") or "\\" in value or "\x00" in value:
            raise ValueError(f"invalid share quota path: {raw_path!r}")
        parts = PurePosixPath(value).parts
        if (
            not parts
            or any(part in {"", ".", "..", TRASH_DIRNAME} for part in parts)
            or any(part.startswith(_UPLOAD_TEMP_PREFIX) for part in parts)
        ):
            raise ValueError(f"invalid share quota path: {raw_path!r}")
        normalized = "/".join(parts)
        candidate = self.root.joinpath(*parts)
        resolved = candidate.resolve()
        if resolved != candidate or (resolved != self.root and self.root not in resolved.parents):
            raise ValueError(f"share quota path must not traverse symbolic links: {raw_path!r}")
        return normalized

    # ── 路径安全 ────────────────────────────────────────────────
    def _resolve(self, rel: str) -> Path:
        # 归一化:去掉前导斜杠,空/"."视为 root。
        rel = (rel or "").strip().lstrip("/")
        if any(
            part in (TRASH_DIRNAME, _UPLOAD_SESSION_DIRNAME) or part.startswith(_UPLOAD_TEMP_PREFIX)
            for part in Path(rel).parts
        ):
            raise PathEscape("reserved internal path")
        target = (self.root / rel).resolve()
        if target != self.root and self.root not in target.parents:
            raise PathEscape(f"path escapes root: {rel!r}")
        return target

    def _rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    @staticmethod
    def _quota_contains(quota_path: str, relative_path: str) -> bool:
        return (
            not quota_path
            or relative_path == quota_path
            or relative_path.startswith(f"{quota_path}/")
        )

    @staticmethod
    def _is_internal_name(name: str) -> bool:
        return name in (TRASH_DIRNAME, _UPLOAD_SESSION_DIRNAME) or name.startswith(
            _UPLOAD_TEMP_PREFIX
        )

    def _logical_size(self, path: Path) -> int:
        """Return logical user-data bytes without following links or counting internals."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            return 0
        if stat.S_ISLNK(info.st_mode):
            return 0
        if stat.S_ISREG(info.st_mode):
            return info.st_size
        if not stat.S_ISDIR(info.st_mode):
            return 0
        total = 0
        with os.scandir(path) as entries:
            for entry in entries:
                if self._is_internal_name(entry.name):
                    continue
                total += self._logical_size(Path(entry.path))
        return total

    @staticmethod
    def _existing_file_size(target: Path) -> int:
        try:
            info = target.lstat()
        except FileNotFoundError:
            return 0
        return info.st_size if stat.S_ISREG(info.st_mode) else 0

    def _quota_reserved_bytes(
        self,
        quota: ShareQuota,
        *,
        exclude_session_id: str | None = None,
        exclude_temp: Path | None = None,
    ) -> int:
        reserved = 0
        for session_id, session in self._upload_sessions.items():
            if session_id == exclude_session_id:
                continue
            target = self._resolve(str(session["target"]))
            target_rel = self._rel(target)
            if not self._quota_contains(quota.path, target_rel):
                continue
            reserved += max(
                0,
                int(session["expectedBytes"]) - self._existing_file_size(target),
            )
        for temp, reservation in self._multipart_uploads.items():
            if exclude_temp is not None and temp == exclude_temp:
                continue
            target = self._resolve(str(reservation["target"]))
            target_rel = self._rel(target)
            if not self._quota_contains(quota.path, target_rel):
                continue
            expected_bytes = reservation["expectedBytes"]
            final_bytes = int(expected_bytes) if expected_bytes is not None else temp.stat().st_size
            reserved += max(0, final_bytes - self._existing_file_size(target))
        return reserved

    def _share_quota_reports(
        self,
        target: Path,
        final_bytes: int,
        *,
        source: Path | None = None,
        exclude_session_id: str | None = None,
        exclude_temp: Path | None = None,
    ) -> list[dict[str, Any]]:
        if final_bytes < 0:
            raise ValueError("quota projection size must not be negative")
        target_rel = self._rel(target)
        source_rel = self._rel(source) if source is not None else None
        reports: list[dict[str, Any]] = []
        for quota in self.share_quotas:
            if not self._quota_contains(quota.path, target_rel):
                continue
            try:
                used_bytes = self._logical_size(self.root / quota.path)
                reserved_bytes = self._quota_reserved_bytes(
                    quota,
                    exclude_session_id=exclude_session_id,
                    exclude_temp=exclude_temp,
                )
            except OSError as exc:
                raise InsufficientStorage(
                    f"share quota usage is unavailable for {(quota.path or '.')!r}"
                ) from exc
            source_already_counted = source_rel is not None and self._quota_contains(
                quota.path,
                source_rel,
            )
            requested_growth = (
                0
                if source_already_counted
                else max(0, final_bytes - self._existing_file_size(target))
            )
            available_bytes = max(0, quota.limit_bytes - used_bytes - reserved_bytes)
            report = {
                "path": quota.path or ".",
                "limitBytes": quota.limit_bytes,
                "usedBytes": used_bytes,
                "reservedBytes": reserved_bytes,
                "availableBytes": available_bytes,
                "requestedGrowthBytes": requested_growth,
                "projectedBytes": used_bytes + reserved_bytes + requested_growth,
            }
            if requested_growth > available_bytes:
                raise ShareQuotaExceeded(report)
            reports.append(report)
        return reports

    @staticmethod
    def _copy_ignored_internal(_directory: str, names: list[str]) -> list[str]:
        return [name for name in names if FileManager._is_internal_name(name)]

    def _existing_parent(self, path: Path) -> Path:
        candidate = path
        while not candidate.exists() and candidate != self.root:
            candidate = candidate.parent
        if candidate != self.root and self.root not in candidate.parents:
            raise PathEscape("path escapes root")
        return candidate

    def _assert_no_active_uploads(self, path: Path) -> None:
        if any(temp == path or path in temp.parents for temp in self._active_uploads):
            raise ValueError("cannot move or trash a directory with active uploads")

    @property
    def _trash_dir(self) -> Path:
        d = self.root / TRASH_DIRNAME
        d.mkdir(exist_ok=True)
        return d

    def _read_manifest(self) -> list[dict[str, Any]]:
        # 读清单需与写加同一锁，避免竞态截断
        with self._upload_lock:
            f = self._trash_dir / _MANIFEST
            try:
                return json.loads(f.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                return []

    def _write_manifest(self, entries: list[dict[str, Any]]) -> None:
        with self._upload_lock:
            trash_dir = self._trash_dir
            payload = json.dumps(entries, indent=2)
            tmp = trash_dir / f".{_MANIFEST}.{uuid.uuid4().hex}.tmp"
            descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                tmp.chmod(0o600)
                os.replace(tmp, trash_dir / _MANIFEST)
                with contextlib.suppress(OSError):
                    directory_fd = os.open(trash_dir, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                with contextlib.suppress(FileNotFoundError):
                    tmp.unlink(missing_ok=True)

    # ── 浏览 ────────────────────────────────────────────────────
    def list_dir(self, rel: str = "") -> list[FileEntry]:
        target = self._resolve(rel)
        if not target.is_dir():
            raise NotADirectoryError(rel)
        self._cleanup_stale_in(target)
        entries: list[FileEntry] = []
        for child in target.iterdir():
            # 隐藏回收站目录本身(仅在 root 层)。
            if child.parent == self.root and child.name == TRASH_DIRNAME:
                continue
            if child.name.startswith(_UPLOAD_TEMP_PREFIX):
                continue
            try:
                st = child.stat()
                child_rel = self._rel(child)
            except (OSError, ValueError):
                continue
            entries.append(
                FileEntry(
                    name=child.name,
                    path=child_rel,
                    kind="dir" if child.is_dir() else "file",
                    size=st.st_size,
                    mtime=st.st_mtime,
                )
            )
        entries.sort(key=lambda e: (e.kind != "dir", e.name.lower()))
        return entries

    @staticmethod
    def _usage_category(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        for category, extensions in _USAGE_EXTENSIONS.items():
            if suffix in extensions:
                return category
        return "other"

    def storage_usage(
        self,
        *,
        fresh: bool = False,
        max_entries: int = DEFAULT_USAGE_MAX_ENTRIES,
        path_visible: Callable[[str], bool] | None = None,
    ) -> dict[str, Any]:
        """Return a bounded, read-only view of NAS usage without following links."""
        now = time.monotonic()
        with self._usage_lock:
            if (
                path_visible is None
                and not fresh
                and self._usage_cache is not None
                and now - self._usage_cache[0] <= USAGE_CACHE_SECONDS
            ):
                return self._usage_cache[1]

            limit = max(1, min(int(max_entries), 1_000_000))
            categories = {
                category: {"bytes": 0, "files": 0} for category in (*_USAGE_EXTENSIONS, "other")
            }
            top_folders: dict[str, dict[str, int]] = {}
            quota_usage = {quota.path: 0 for quota in self.share_quotas}
            trash_bytes = 0
            trash_files = 0
            file_count = 0
            directory_count = 0
            skipped_links = 0
            scanned_entries = 0
            truncated = False
            stack: list[tuple[Path, str | None, bool]] = [(self.root, None, False)]

            while stack and not truncated:
                directory, top_folder, in_trash = stack.pop()
                try:
                    iterator = os.scandir(directory)
                except OSError:
                    continue
                with iterator:
                    for entry in iterator:
                        scanned_entries += 1
                        if scanned_entries > limit:
                            truncated = True
                            break
                        is_root_entry = directory == self.root
                        if is_root_entry and entry.name == TRASH_DIRNAME:
                            if path_visible is not None:
                                continue
                            try:
                                info = entry.stat(follow_symlinks=False)
                            except OSError:
                                continue
                            if stat.S_ISLNK(info.st_mode):
                                skipped_links += 1
                            elif stat.S_ISDIR(info.st_mode):
                                stack.append((Path(entry.path), None, True))
                            continue
                        if self._is_internal_name(entry.name) or entry.name.startswith(".echo-"):
                            continue
                        if path_visible is not None:
                            try:
                                visible_path = self._rel(Path(entry.path))
                            except ValueError:
                                continue
                            if not path_visible(visible_path):
                                continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if stat.S_ISLNK(info.st_mode):
                            skipped_links += 1
                            continue
                        next_top = top_folder
                        if is_root_entry and stat.S_ISDIR(info.st_mode):
                            next_top = entry.name
                        if stat.S_ISDIR(info.st_mode):
                            if not in_trash:
                                directory_count += 1
                            stack.append((Path(entry.path), next_top, in_trash))
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        if in_trash:
                            if entry.name != _MANIFEST:
                                trash_bytes += info.st_size
                                trash_files += 1
                            continue

                        file_count += 1
                        category = self._usage_category(entry.name)
                        categories[category]["bytes"] += info.st_size
                        categories[category]["files"] += 1
                        if next_top:
                            folder = top_folders.setdefault(
                                next_top,
                                {"bytes": 0, "files": 0},
                            )
                            folder["bytes"] += info.st_size
                            folder["files"] += 1

                        if quota_usage:
                            try:
                                relative = Path(entry.path).relative_to(self.root)
                            except ValueError:
                                continue
                            if "" in quota_usage:
                                quota_usage[""] += info.st_size
                            prefix = ""
                            for part in relative.parts[:-1]:
                                prefix = f"{prefix}/{part}" if prefix else part
                                if prefix in quota_usage:
                                    quota_usage[prefix] += info.st_size

            logical_bytes = sum(item["bytes"] for item in categories.values())
            disk = shutil.disk_usage(self.root)
            with self._upload_lock:
                if path_visible is None:
                    reserved_upload_bytes = self._reserved_upload_bytes(self.root)
                    active_uploads = len(self._upload_sessions) + len(self._multipart_uploads)
                else:
                    reserved_upload_bytes = 0
                    active_uploads = 0
                    for session in self._upload_sessions.values():
                        target = str(session["target"])
                        if not path_visible(target):
                            continue
                        active_uploads += 1
                        reserved_upload_bytes += max(
                            0,
                            int(session["expectedBytes"]) - int(session["uploadedBytes"]),
                        )
                    for temp, reservation in self._multipart_uploads.items():
                        target = str(reservation["target"])
                        if not path_visible(target):
                            continue
                        active_uploads += 1
                        expected = reservation["expectedBytes"]
                        if expected is not None:
                            try:
                                stored_bytes = temp.stat().st_size
                            except FileNotFoundError:
                                stored_bytes = 0
                            reserved_upload_bytes += max(0, int(expected) - stored_bytes)
                quotas = []
                for quota in self.share_quotas:
                    if path_visible is not None and not path_visible(quota.path):
                        continue
                    reserved = self._quota_reserved_bytes(quota)
                    used = quota_usage.get(quota.path, 0)
                    quotas.append(
                        {
                            "path": quota.path or ".",
                            "limitBytes": quota.limit_bytes,
                            "usedBytes": used,
                            "reservedBytes": reserved,
                            "availableBytes": max(0, quota.limit_bytes - used - reserved),
                            "estimated": truncated,
                        }
                    )
            result = {
                "schema": "echo.storage.usage.v1",
                "readOnly": True,
                "generatedAt": time.time(),
                "disk": {
                    "totalBytes": disk.total,
                    "usedBytes": disk.used,
                    "freeBytes": disk.free,
                    "reserveBytes": self.upload_reserve_bytes,
                    "availableForUploadsBytes": max(
                        0,
                        disk.free - self.upload_reserve_bytes - reserved_upload_bytes,
                    ),
                    "usedPercent": (round((disk.used / disk.total) * 100, 1) if disk.total else 0),
                },
                "library": {
                    "logicalBytes": logical_bytes,
                    "files": file_count,
                    "directories": directory_count,
                    "scannedEntries": min(scanned_entries, limit),
                    "maxEntries": limit,
                    "truncated": truncated,
                    "skippedLinks": skipped_links,
                },
                "categories": [
                    {"id": category, **values} for category, values in categories.items()
                ],
                "topFolders": [
                    {"name": name, **values}
                    for name, values in sorted(
                        top_folders.items(),
                        key=lambda item: (-item[1]["bytes"], item[0].casefold()),
                    )[:12]
                ],
                "trash": {"bytes": trash_bytes, "files": trash_files},
                "uploads": {
                    "reservedBytes": reserved_upload_bytes,
                    "active": active_uploads,
                },
                "quotas": quotas,
            }
            if path_visible is not None:
                trash_bytes = 0
                trash_files = 0
                for record in self.list_trash():
                    original = str(record.get("original") or "")
                    if not path_visible(original):
                        continue
                    stored = self._trash_dir / str(record.get("id") or "")
                    try:
                        trash_bytes += self._logical_size(stored)
                    except OSError:
                        continue
                    trash_files += 1
                result["trash"] = {"bytes": trash_bytes, "files": trash_files}
            else:
                self._usage_cache = (time.monotonic(), result)
            return result

    def mkdir(self, rel: str) -> FileEntry:
        target = self._resolve(rel)
        if target == self.root:
            raise ValueError("cannot mkdir root")
        target.mkdir(parents=True, exist_ok=False)
        return self._entry(target)

    def move(self, src_rel: str, dst_rel: str) -> FileEntry:
        with self._upload_lock:
            src = self._resolve(src_rel)
            dst = self._resolve(dst_rel)
            if src == self.root:
                raise ValueError("cannot move root")
            if not src.exists():
                raise FileNotFoundError(src_rel)
            self._assert_no_active_uploads(src)
            # dst 是已存在目录 → 移动进该目录;否则视为重命名目标。
            if dst.is_dir():
                dst = dst / src.name
            if dst.exists():
                raise FileExistsError(self._rel(dst))
            if src.is_dir() and (dst == src or src in dst.parents):
                raise ValueError("cannot move a directory into itself")
            logical_bytes = self._logical_size(src)
            self._share_quota_reports(dst, logical_bytes, source=src)
            destination_device = self._existing_parent(dst.parent).stat().st_dev
            if src.stat().st_dev != destination_device:
                self._assert_capacity(
                    self._existing_parent(dst.parent),
                    self._reserved_upload_bytes(self._existing_parent(dst.parent)) + logical_bytes,
                )
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return self._entry(dst)

    def copy(self, src_rel: str, dst_rel: str) -> FileEntry:
        """复制文件或目录;不覆盖目标,也不复制符号链接树。"""
        with self._upload_lock:
            src = self._resolve(src_rel)
            dst = self._resolve(dst_rel)
            if src == self.root:
                raise ValueError("cannot copy root")
            if not src.exists():
                raise FileNotFoundError(src_rel)
            if dst.is_dir():
                dst = dst / src.name
            if dst.exists():
                raise FileExistsError(self._rel(dst))
            if src.is_dir() and (dst == src or src in dst.parents):
                raise ValueError("cannot copy a directory into itself")
            if src.is_symlink() or (src.is_dir() and any(p.is_symlink() for p in src.rglob("*"))):
                raise ValueError("copying symbolic links is not supported")
            logical_bytes = self._logical_size(src)
            destination_parent = self._existing_parent(dst.parent)
            self._share_quota_reports(dst, logical_bytes)
            self._assert_capacity(
                destination_parent,
                self._reserved_upload_bytes(destination_parent) + logical_bytes,
            )
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                shutil.copytree(src, dst, ignore=self._copy_ignored_internal)
            else:
                shutil.copy2(src, dst)
            return self._entry(dst)

    # ── 上传 / 下载 ────────────────────────────────────────────
    def prepare_upload(
        self,
        directory_rel: str,
        filename: str,
        overwrite: bool = False,
        expected_bytes: int | None = None,
    ) -> tuple[Path, Path]:
        """返回同目录临时路径与最终路径;调用方流式写临时文件后再 finalize。"""
        directory, target = self._upload_destination(
            directory_rel,
            filename,
            overwrite=overwrite,
        )
        self._check_expected_size(expected_bytes)
        if self.share_quotas and expected_bytes is None:
            raise ValueError("upload size is required when share quotas are configured")
        with self._upload_lock:
            self._assert_capacity(
                directory,
                self._reserved_upload_bytes(directory) + (expected_bytes or 0),
            )
            self._share_quota_reports(target, expected_bytes or 0)
            self._cleanup_stale_in(directory)
            temp = directory / f"{_UPLOAD_TEMP_PREFIX}{uuid.uuid4().hex}{_UPLOAD_TEMP_SUFFIX}"
            self._active_uploads.add(temp)
            self._multipart_uploads[temp] = {
                "target": self._rel(target),
                "expectedBytes": expected_bytes,
            }
        return temp, target

    def _upload_destination(
        self,
        directory_rel: str,
        filename: str,
        *,
        overwrite: bool,
    ) -> tuple[Path, Path]:
        safe_name = Path((filename or "").replace("\\", "/")).name.strip()
        if safe_name in {"", ".", "..", TRASH_DIRNAME} or safe_name.startswith(_UPLOAD_TEMP_PREFIX):
            raise ValueError("invalid upload filename")
        directory = self._resolve(directory_rel)
        if not directory.is_dir():
            raise NotADirectoryError(directory_rel)
        target = self._resolve(self._rel(directory / safe_name))
        if target.exists() and not overwrite:
            raise FileExistsError(self._rel(target))
        if target.is_dir():
            raise FileExistsError(self._rel(target))
        return directory, target

    def _check_expected_size(self, expected_bytes: int | None) -> None:
        if expected_bytes is None:
            return
        if expected_bytes < 0:
            raise ValueError("upload size must not be negative")
        if expected_bytes > self.max_upload_bytes:
            raise UploadTooLarge("upload exceeds the configured maximum size")

    def _assert_capacity(self, directory: Path, incoming_bytes: int) -> dict[str, int]:
        usage = shutil.disk_usage(directory)
        available = max(0, usage.free - self.upload_reserve_bytes)
        if incoming_bytes > available:
            raise InsufficientStorage("upload would cross the reserved free-space floor")
        return {
            "freeBytes": usage.free,
            "reserveBytes": self.upload_reserve_bytes,
            "availableBytes": available,
            "maxUploadBytes": self.max_upload_bytes,
        }

    def preflight_upload(
        self,
        directory_rel: str,
        filename: str,
        expected_bytes: int,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        directory, target = self._upload_destination(
            directory_rel,
            filename,
            overwrite=overwrite,
        )
        self._check_expected_size(expected_bytes)
        with self._upload_lock:
            capacity = self._assert_capacity(
                directory,
                self._reserved_upload_bytes(directory) + expected_bytes,
            )
            quota_reports = self._share_quota_reports(target, expected_bytes)
        return {
            **capacity,
            "target": self._rel(target),
            "expectedBytes": expected_bytes,
            "shareQuotas": quota_reports,
        }

    @property
    def _upload_session_dir(self) -> Path:
        return self.root / _UPLOAD_SESSION_DIRNAME

    def _ensure_upload_session_dir(self) -> Path:
        directory = self._upload_session_dir
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.resolve() != directory:
            raise PathEscape("invalid upload session directory")
        return directory

    def _upload_session_meta_path(self, session_id: str) -> Path:
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("invalid upload session id")
        return self._upload_session_dir / f"{session_id}.json"

    def _upload_session_temp(self, session_id: str, target: Path) -> Path:
        temp = target.parent / f"{_UPLOAD_TEMP_PREFIX}{session_id}{_UPLOAD_TEMP_SUFFIX}"
        self._assert_upload_temp(temp)
        return temp

    def _write_upload_session(self, session: dict[str, Any]) -> None:
        directory = self._ensure_upload_session_dir()
        destination = self._upload_session_meta_path(str(session["id"]))
        temporary = directory / f".{session['id']}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(session, separators=(",", ":"), sort_keys=True)
        try:
            with temporary.open("x", encoding="utf-8") as output:
                os.chmod(temporary, 0o600)
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            directory_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_upload_session(self, raw: object) -> tuple[dict[str, Any], Path]:
        if not isinstance(raw, dict) or raw.get("version") != _UPLOAD_SESSION_VERSION:
            raise ValueError("unsupported upload session metadata")
        session_id = str(raw.get("id", ""))
        self._upload_session_meta_path(session_id)
        directory_rel = str(raw.get("path", ""))
        filename = str(raw.get("filename", ""))
        expected_bytes = raw.get("expectedBytes")
        uploaded_bytes = raw.get("uploadedBytes")
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or not isinstance(uploaded_bytes, int)
            or isinstance(uploaded_bytes, bool)
        ):
            raise ValueError("invalid upload session size")
        self._check_expected_size(expected_bytes)
        if uploaded_bytes < 0 or uploaded_bytes > expected_bytes:
            raise ValueError("invalid upload session offset")
        expected_digest = raw.get("sha256")
        if expected_digest is not None and (
            not isinstance(expected_digest, str)
            or len(expected_digest) != 64
            or any(c not in "0123456789abcdef" for c in expected_digest)
        ):
            raise ValueError("invalid upload session SHA-256")
        fingerprint = raw.get("fingerprint")
        if fingerprint is not None and (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
        ):
            raise ValueError("invalid upload session fingerprint")
        overwrite = raw.get("overwrite")
        if not isinstance(overwrite, bool):
            raise ValueError("invalid upload session overwrite flag")
        directory = self._resolve(directory_rel)
        if not directory.is_dir():
            raise NotADirectoryError(directory_rel)
        safe_name = Path(filename.replace("\\", "/")).name.strip()
        if safe_name != filename or safe_name in {"", ".", "..", TRASH_DIRNAME}:
            raise ValueError("invalid upload session filename")
        target = self._resolve(self._rel(directory / safe_name))
        temp = self._upload_session_temp(session_id, target)
        info = temp.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > expected_bytes:
            raise ValueError("invalid upload session temporary file")
        if uploaded_bytes > info.st_size:
            raise ValueError("upload session metadata is ahead of stored data")
        session = {
            "version": _UPLOAD_SESSION_VERSION,
            "id": session_id,
            "path": directory_rel,
            "filename": filename,
            "target": self._rel(target),
            "expectedBytes": expected_bytes,
            "uploadedBytes": info.st_size,
            "sha256": expected_digest,
            "fingerprint": fingerprint,
            "overwrite": overwrite,
            "createdAt": float(raw.get("createdAt", info.st_mtime)),
            "updatedAt": float(raw.get("updatedAt", info.st_mtime)),
        }
        return session, temp

    def _load_upload_sessions(self) -> None:
        directory = self._upload_session_dir
        if not directory.exists():
            return
        if directory.is_symlink() or not directory.is_dir() or directory.resolve() != directory:
            raise PathEscape("invalid upload session directory")
        for metadata_path in directory.glob("*.json"):
            try:
                raw = json.loads(metadata_path.read_text(encoding="utf-8"))
                session, temp = self._validate_upload_session(raw)
            except (OSError, TypeError, ValueError):
                continue
            session_id = str(session["id"])
            if session["uploadedBytes"] != raw.get("uploadedBytes"):
                self._write_upload_session(session)
            self._upload_sessions[session_id] = session
            self._upload_session_locks[session_id] = threading.Lock()
            self._active_uploads.add(temp)

    def _public_upload_session(self, session: dict[str, Any]) -> dict[str, Any]:
        return {
            "sessionId": session["id"],
            "target": session["target"],
            "expectedBytes": session["expectedBytes"],
            "uploadedBytes": session["uploadedBytes"],
            "chunkBytes": DEFAULT_UPLOAD_CHUNK_BYTES,
            "sha256Expected": bool(session["sha256"]),
            "fingerprint": session["fingerprint"],
            "updatedAt": session["updatedAt"],
            "quotaBlocked": session["id"] in self._quota_blocked_sessions,
        }

    def _refresh_upload_session_quota_flags(self) -> None:
        self._quota_blocked_sessions.clear()
        if not self.share_quotas or not self._upload_sessions:
            return
        with self._upload_lock:
            for quota in self.share_quotas:
                try:
                    used_bytes = self._logical_size(self.root / quota.path)
                    reserved_bytes = self._quota_reserved_bytes(quota)
                except OSError:
                    for session_id, session in self._upload_sessions.items():
                        if self._quota_contains(quota.path, str(session["target"])):
                            self._quota_blocked_sessions.add(session_id)
                    continue
                if used_bytes + reserved_bytes <= quota.limit_bytes:
                    continue
                for session_id, session in self._upload_sessions.items():
                    if self._quota_contains(quota.path, str(session["target"])):
                        self._quota_blocked_sessions.add(session_id)

    def _session_and_lock(self, session_id: str) -> tuple[dict[str, Any], threading.Lock]:
        self._upload_session_meta_path(session_id)
        with self._upload_lock:
            try:
                return self._upload_sessions[session_id], self._upload_session_locks[session_id]
            except KeyError as exc:
                raise FileNotFoundError(session_id) from exc

    def _reserved_upload_bytes(self, directory: Path) -> int:
        device = directory.stat().st_dev
        reserved = 0
        for session in self._upload_sessions.values():
            try:
                target = self._resolve(str(session["target"]))
                if target.parent.stat().st_dev != device:
                    continue
            except OSError:
                continue
            reserved += max(
                0,
                int(session["expectedBytes"]) - int(session["uploadedBytes"]),
            )
        for temp, reservation in self._multipart_uploads.items():
            expected_bytes = reservation["expectedBytes"]
            if expected_bytes is None:
                continue
            try:
                target = self._resolve(str(reservation["target"]))
                if target.parent.stat().st_dev != device:
                    continue
                stored_bytes = temp.stat().st_size
            except FileNotFoundError:
                stored_bytes = 0
            reserved += max(0, int(expected_bytes) - stored_bytes)
        return reserved

    def create_upload_session(
        self,
        directory_rel: str,
        filename: str,
        expected_bytes: int,
        expected_sha256: str | None = None,
        overwrite: bool = False,
        fingerprint: str | None = None,
    ) -> dict[str, Any]:
        directory, target = self._upload_destination(
            directory_rel,
            filename,
            overwrite=overwrite,
        )
        self._check_expected_size(expected_bytes)
        expected_digest = expected_sha256.lower() if expected_sha256 else None
        if expected_digest is not None and (
            len(expected_digest) != 64 or any(c not in "0123456789abcdef" for c in expected_digest)
        ):
            raise ValueError("invalid expected SHA-256")
        normalized_fingerprint = fingerprint.lower() if fingerprint else None
        if normalized_fingerprint is not None and (
            len(normalized_fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in normalized_fingerprint)
        ):
            raise ValueError("invalid upload fingerprint")
        with self._upload_lock:
            self.cleanup_expired_upload_sessions()
            if len(self._upload_sessions) >= self.max_upload_sessions:
                raise UploadSessionLimit("too many active upload sessions")
            self._assert_capacity(
                directory,
                self._reserved_upload_bytes(directory) + expected_bytes,
            )
            self._share_quota_reports(target, expected_bytes)
            session_id = uuid.uuid4().hex
            temp = self._upload_session_temp(session_id, target)
            now = time.time()
            session: dict[str, Any] = {
                "version": _UPLOAD_SESSION_VERSION,
                "id": session_id,
                "path": directory_rel,
                "filename": filename,
                "target": self._rel(target),
                "expectedBytes": expected_bytes,
                "uploadedBytes": 0,
                "sha256": expected_digest,
                "fingerprint": normalized_fingerprint,
                "overwrite": overwrite,
                "createdAt": now,
                "updatedAt": now,
            }
            try:
                with temp.open("xb"):
                    os.chmod(temp, 0o600)
                self._write_upload_session(session)
            except Exception:
                temp.unlink(missing_ok=True)
                raise
            self._upload_sessions[session_id] = session
            self._upload_session_locks[session_id] = threading.Lock()
            self._active_uploads.add(temp)
            return self._public_upload_session(session)

    def get_upload_session(self, session_id: str) -> dict[str, Any]:
        session, lock = self._session_and_lock(session_id)
        with lock, self._upload_lock:
            if session_id not in self._upload_sessions:
                raise FileNotFoundError(session_id)
            return self._public_upload_session(session)

    def append_upload_session_chunk(
        self,
        session_id: str,
        offset: int,
        data: bytes,
    ) -> dict[str, Any]:
        if not data:
            raise ValueError("upload chunk must not be empty")
        if len(data) > DEFAULT_UPLOAD_CHUNK_BYTES:
            raise UploadTooLarge("upload chunk exceeds the configured maximum size")
        session, lock = self._session_and_lock(session_id)
        with lock:
            with self._upload_lock:
                if session_id not in self._upload_sessions:
                    raise FileNotFoundError(session_id)
                current_offset = int(session["uploadedBytes"])
                if offset != current_offset:
                    raise UploadOffsetMismatch(current_offset)
                if offset + len(data) > int(session["expectedBytes"]):
                    raise UploadTooLarge("upload chunk exceeds the declared file size")
                if session_id in self._quota_blocked_sessions:
                    target = self._resolve(str(session["target"]))
                    self._share_quota_reports(
                        target,
                        int(session["expectedBytes"]),
                        exclude_session_id=session_id,
                    )
                    self._quota_blocked_sessions.discard(session_id)
                self._assert_capacity(
                    self._resolve(str(session["path"])),
                    self._reserved_upload_bytes(self._resolve(str(session["path"]))),
                )
            target = self._resolve(str(session["target"]))
            temp = self._upload_session_temp(session_id, target)
            try:
                with temp.open("r+b") as output:
                    if output.seek(0, os.SEEK_END) != current_offset:
                        raise UploadOffsetMismatch(temp.stat().st_size)
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
            finally:
                stored_bytes = temp.stat().st_size
                with self._upload_lock:
                    session["uploadedBytes"] = stored_bytes
                    session["updatedAt"] = time.time()
                    self._write_upload_session(session)
            return self._public_upload_session(session)

    def complete_upload_session(self, session_id: str) -> tuple[FileEntry, str, bool]:
        session, lock = self._session_and_lock(session_id)
        with lock:
            with self._upload_lock:
                if session_id not in self._upload_sessions:
                    raise FileNotFoundError(session_id)
                if session["uploadedBytes"] != session["expectedBytes"]:
                    raise ValueError("upload is incomplete")
            target = self._resolve(str(session["target"]))
            temp = self._upload_session_temp(session_id, target)
            digest = hashlib.sha256()
            with temp.open("rb") as uploaded:
                while chunk := uploaded.read(1024 * 1024):
                    digest.update(chunk)
            actual_digest = digest.hexdigest()
            expected_digest = session["sha256"]
            if expected_digest and actual_digest != expected_digest:
                raise UploadHashMismatch("upload SHA-256 does not match")
            directory, current_target = self._upload_destination(
                str(session["path"]),
                str(session["filename"]),
                overwrite=bool(session["overwrite"]),
            )
            if directory != target.parent or current_target != target:
                raise PathEscape("upload target changed")
            entry = self.finalize_upload(
                temp,
                target,
                bool(session["overwrite"]),
                reservation_session_id=session_id,
            )
            with self._upload_lock:
                self._upload_sessions.pop(session_id, None)
                self._upload_session_locks.pop(session_id, None)
                self._quota_blocked_sessions.discard(session_id)
                with contextlib.suppress(OSError):
                    self._upload_session_meta_path(session_id).unlink(missing_ok=True)
            return entry, actual_digest, bool(expected_digest)

    def cancel_upload_session(self, session_id: str) -> dict[str, Any]:
        session, lock = self._session_and_lock(session_id)
        with lock:
            target = self._resolve(str(session["target"]))
            temp = self._upload_session_temp(session_id, target)
            with self._upload_lock:
                if session_id not in self._upload_sessions:
                    raise FileNotFoundError(session_id)
                self._upload_sessions.pop(session_id, None)
                self._upload_session_locks.pop(session_id, None)
                self._quota_blocked_sessions.discard(session_id)
                with contextlib.suppress(OSError):
                    self._upload_session_meta_path(session_id).unlink(missing_ok=True)
            self.discard_upload(temp)
            return {"sessionId": session_id, "cancelled": True}

    def cleanup_expired_upload_sessions(self, *, now: float | None = None) -> dict[str, int]:
        cutoff = (time.time() if now is None else now) - self.stale_upload_seconds
        removed = 0
        removed_bytes = 0
        with self._upload_lock:
            expired = [
                session_id
                for session_id, session in self._upload_sessions.items()
                if float(session["updatedAt"]) <= cutoff
            ]
            for session_id in expired:
                session_lock = self._upload_session_locks[session_id]
                if not session_lock.acquire(blocking=False):
                    continue
                session = self._upload_sessions.pop(session_id)
                try:
                    self._upload_session_locks.pop(session_id, None)
                    self._quota_blocked_sessions.discard(session_id)
                    with contextlib.suppress(OSError, PathEscape, ValueError):
                        target = self._resolve(str(session["target"]))
                        temp = self._upload_session_temp(session_id, target)
                        with contextlib.suppress(OSError):
                            removed_bytes += temp.stat().st_size
                        temp.unlink(missing_ok=True)
                        self._active_uploads.discard(temp)
                    with contextlib.suppress(OSError):
                        self._upload_session_meta_path(session_id).unlink(missing_ok=True)
                    removed += 1
                finally:
                    session_lock.release()
        return {"removed": removed, "removedBytes": removed_bytes}

    def assert_upload_chunk(
        self,
        temp: Path,
        written_bytes: int,
        incoming_bytes: int,
    ) -> None:
        self._assert_upload_temp(temp)
        if written_bytes + incoming_bytes > self.max_upload_bytes:
            raise UploadTooLarge("upload exceeds the configured maximum size")
        with self._upload_lock:
            reservation = self._multipart_uploads.get(temp)
            # 未声明大小的流式上传需按累计大小预检，避免单 chunk 8MiB 绕过 reserve
            if reservation is None or reservation["expectedBytes"] is None:
                unreserved_incoming = written_bytes + incoming_bytes
                # 已写入部分已占磁盘，但需保证“累计”仍在可用空间内
                # 扣除已写入后，增量为 incoming_bytes，需同时校验累计上限
                self._assert_capacity(
                    temp.parent,
                    self._reserved_upload_bytes(temp.parent) + unreserved_incoming,
                )
                # 二次校验增量，避免 free 已接近 reserve 时被打满
                self._assert_capacity(
                    temp.parent,
                    self._reserved_upload_bytes(temp.parent) + incoming_bytes,
                )
            else:
                self._assert_capacity(
                    temp.parent,
                    self._reserved_upload_bytes(temp.parent),
                )

    def _assert_upload_temp(self, temp: Path) -> None:
        if (
            temp.parent.resolve() != temp.parent
            or self.root != temp.parent.resolve()
            and self.root not in temp.parent.resolve().parents
            or not temp.name.startswith(_UPLOAD_TEMP_PREFIX)
            or not temp.name.endswith(_UPLOAD_TEMP_SUFFIX)
        ):
            raise PathEscape("invalid upload temporary path")

    def finalize_upload(
        self,
        temp: Path,
        target: Path,
        overwrite: bool = False,
        reservation_session_id: str | None = None,
    ) -> FileEntry:
        """原子提交上传;默认用硬链接保证并发重名时绝不覆盖。"""
        self._assert_upload_temp(temp)
        committed = False
        try:
            with self._upload_lock:
                file_size = temp.stat().st_size
                self._share_quota_reports(
                    target,
                    file_size,
                    exclude_session_id=reservation_session_id,
                    exclude_temp=temp,
                )
                # 最终提交前再做一次容量检查，防止并发预留耗尽
                self._assert_capacity(
                    target.parent,
                    self._reserved_upload_bytes(target.parent) + file_size,
                )
                if overwrite:
                    if target.is_dir():
                        raise FileExistsError(self._rel(target))
                    try:
                        os.replace(temp, target)
                    except OSError as exc:
                        # 跨设备场景下 fallback 为 copy+unlink
                        if getattr(exc, "errno", None) == 18:  # EXDEV
                            if target.exists() and not overwrite:
                                raise FileExistsError(self._rel(target)) from exc
                            shutil.copy2(temp, target)
                            temp.unlink(missing_ok=True)
                        else:
                            raise
                else:
                    try:
                        os.link(temp, target)
                    except OSError as exc:
                        if getattr(exc, "errno", None) == 18:  # EXDEV 跨文件系统
                            if target.exists():
                                raise FileExistsError(self._rel(target)) from exc
                            shutil.copy2(temp, target)
                        else:
                            raise
                    with contextlib.suppress(FileNotFoundError):
                        temp.unlink(missing_ok=True)
                committed = True
                return self._entry(target)
        finally:
            if committed:
                with self._upload_lock:
                    self._active_uploads.discard(temp)
                    self._multipart_uploads.pop(temp, None)

    def discard_upload(self, temp: Path) -> None:
        self._assert_upload_temp(temp)
        try:
            temp.unlink(missing_ok=True)
        finally:
            with self._upload_lock:
                self._active_uploads.discard(temp)
                self._multipart_uploads.pop(temp, None)

    def cleanup_stale_uploads(
        self,
        directory_rel: str = "",
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        directory = self._resolve(directory_rel)
        if not directory.is_dir():
            raise NotADirectoryError(directory_rel)
        return self._cleanup_stale_in(directory, now=now)

    def _cleanup_stale_in(
        self,
        directory: Path,
        *,
        now: float | None = None,
    ) -> dict[str, int]:
        cutoff = (time.time() if now is None else now) - self.stale_upload_seconds
        removed = 0
        removed_bytes = 0
        with self._upload_lock:
            active = set(self._active_uploads)
        for candidate in directory.iterdir():
            if (
                candidate in active
                or not candidate.name.startswith(_UPLOAD_TEMP_PREFIX)
                or not candidate.name.endswith(_UPLOAD_TEMP_SUFFIX)
            ):
                continue
            try:
                info = candidate.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_mtime > cutoff:
                    continue
                candidate.unlink()
            except OSError:
                continue
            removed += 1
            removed_bytes += info.st_size
        return {"removed": removed, "removedBytes": removed_bytes}

    def file_for_download(self, rel: str) -> Path:
        target = self._resolve(rel)
        # _resolve 已拦截内部目录，此处二次防御
        if target.name == _UPLOAD_SESSION_DIRNAME or target.name.startswith(_UPLOAD_TEMP_PREFIX):
            raise PathEscape("reserved internal path")
        if not target.exists():
            raise FileNotFoundError(rel)
        if not target.is_file():
            raise ValueError("only files can be downloaded")
        # 拒绝符号链接，避免通过链接逃逸
        try:
            if target.is_symlink() or not target.resolve().is_file():
                raise ValueError("symbolic links cannot be downloaded")
        except OSError as exc:
            raise ValueError("only files can be downloaded") from exc
        return target

    # ── 回收站语义 ──────────────────────────────────────────────
    def trash(self, rel: str) -> dict[str, Any]:
        """移入回收站(非物理删除)。"""
        with self._upload_lock:
            src = self._resolve(rel)
            if src == self.root:
                raise ValueError("cannot trash root")
            if not src.exists():
                raise FileNotFoundError(rel)
            self._assert_no_active_uploads(src)
            entry_id = uuid.uuid4().hex
            dest = self._trash_dir / entry_id
            original = self._rel(src)
            if src.stat().st_dev != dest.parent.stat().st_dev:
                logical_bytes = self._logical_size(src)
                self._assert_capacity(
                    dest.parent,
                    self._reserved_upload_bytes(dest.parent) + logical_bytes,
                )
            shutil.move(str(src), str(dest))
            record = {
                "id": entry_id,
                "name": src.name,
                "original": original,
                "kind": "dir" if dest.is_dir() else "file",
                "trashed_at": time.time(),
            }
            manifest = self._read_manifest()
            manifest.append(record)
            self._write_manifest(manifest)
            return record

    def list_trash(self) -> list[dict[str, Any]]:
        return sorted(
            self._read_manifest(),
            key=lambda r: r.get("trashed_at", 0),
            reverse=True,
        )

    def restore(self, entry_id: str) -> FileEntry:
        with self._upload_lock:
            manifest = self._read_manifest()
            record = next((r for r in manifest if r["id"] == entry_id), None)
            if record is None:
                raise FileNotFoundError(entry_id)
            stored = self._trash_dir / entry_id
            if not stored.exists():
                # 清单与磁盘不一致:剔除该条。
                self._write_manifest([r for r in manifest if r["id"] != entry_id])
                raise FileNotFoundError(entry_id)
            dest = self._resolve(record["original"])
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}-restored-{entry_id[:6]}{dest.suffix}")
            logical_bytes = self._logical_size(stored)
            destination_parent = self._existing_parent(dest.parent)
            self._share_quota_reports(dest, logical_bytes)
            if stored.stat().st_dev != destination_parent.stat().st_dev:
                self._assert_capacity(
                    destination_parent,
                    self._reserved_upload_bytes(destination_parent) + logical_bytes,
                )
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(dest))
            self._write_manifest([r for r in manifest if r["id"] != entry_id])
            return self._entry(dest)

    def empty_trash(self) -> int:
        """物理删除回收站全部内容(唯一不可逆路径)。返回清空条数。"""
        with self._upload_lock:
            manifest = self._read_manifest()
            count = len(manifest)
            failed: list[str] = []
            for record in manifest:
                stored = self._trash_dir / record["id"]
                try:
                    if stored.is_dir() and not stored.is_symlink():
                        shutil.rmtree(stored)
                    elif stored.exists() and not stored.is_symlink():
                        stored.unlink()
                    elif stored.is_symlink():
                        # 拒绝删除符号链接，避免被利用删除外部数据
                        stored.unlink()
                except OSError:
                    failed.append(record["id"])
                    continue
            if failed:
                # 部分失败时仅移除成功的条目，保留失败的以便重试审计
                remaining = [r for r in manifest if r["id"] in failed]
                self._write_manifest(remaining)
                return count - len(remaining)
            self._write_manifest([])
            return count

    # ── helpers ─────────────────────────────────────────────────
    def _entry(self, path: Path) -> FileEntry:
        st = path.stat()
        return FileEntry(
            name=path.name,
            path=self._rel(path),
            kind="dir" if path.is_dir() else "file",
            size=st.st_size,
            mtime=st.st_mtime,
        )
