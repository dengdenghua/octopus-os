"""Approval-bound, path-free public download links for NAS files."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from appliance.data_access import DataAccessPolicy, DataAccessScope
from appliance.files.manager import FileManager, _replace_upload_metadata

SCHEMA = "echo.file-shares.v1"
PLAN_SCHEMA = "echo.file-share.plan.v1"
MIN_TTL_SECONDS = 5 * 60
MAX_TTL_SECONDS = 30 * 24 * 3600
MAX_DOWNLOADS = 10_000
MAX_ACTIVE_SHARES = 1_000


class FileShareError(RuntimeError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class OpenedShare:
    stream: BinaryIO
    filename: str
    media_type: str
    size: int
    mtime_ns: int
    share_id: str


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FileShareService:
    """Persist share capabilities without ever persisting their bearer tokens."""

    def __init__(
        self,
        manager: FileManager,
        state_dir: str | Path,
        *,
        token_pepper: str,
        data_access: DataAccessPolicy | None = None,
        clock=time.time,
    ) -> None:
        if not token_pepper:
            raise ValueError("file-share token pepper is required")
        self.manager = manager
        self.directory = Path(state_dir).absolute() / "file-shares"
        self.registry = self.directory / "registry.json"
        self._key = hashlib.sha256(f"echo-file-shares\0{token_pepper}".encode()).digest()
        self.data_access = data_access
        self.clock = clock
        self._lock = threading.RLock()
        self._ensure_directory()

    def _ensure_directory(self) -> None:
        if os.name == "nt":
            from appliance.windows_state import private_state_directory

            with private_state_directory(self.directory, create=True):
                return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise OSError("unsafe file-share state directory")
        self.directory.chmod(0o700)

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            info = self.registry.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("unsafe file-share registry")
            payload = self.registry.read_bytes()
        except FileNotFoundError:
            return {}
        if len(payload) > 4 * 1024 * 1024:
            raise OSError("oversized file-share registry")
        try:
            decoded = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OSError("invalid file-share registry") from exc
        if not isinstance(decoded, dict) or decoded.get("schema") != SCHEMA:
            raise OSError("invalid file-share registry")
        shares = decoded.get("shares")
        if not isinstance(shares, dict):
            raise OSError("invalid file-share registry")
        for share_id, record in shares.items():
            if (
                not isinstance(share_id, str)
                or len(share_id) != 24
                or any(char not in "0123456789abcdef" for char in share_id)
                or not isinstance(record, dict)
                or record.get("id") != share_id
                or not isinstance(record.get("tokenDigest"), str)
                or len(record["tokenDigest"]) != 64
                or any(char not in "0123456789abcdef" for char in record["tokenDigest"])
                or not all(
                    isinstance(record.get(key), str) and record[key]
                    for key in ("owner", "path", "filename")
                )
                or not isinstance(record.get("identity"), dict)
                or not all(
                    isinstance(record["identity"].get(key), int)
                    and not isinstance(record["identity"].get(key), bool)
                    for key in ("device", "inode", "size", "mtimeNs")
                )
                or not all(
                    isinstance(record.get(key), int) and not isinstance(record.get(key), bool)
                    for key in ("createdAt", "expiresAt", "maxDownloads", "downloadCount")
                )
                or record["maxDownloads"] < 1
                or record["downloadCount"] < 0
                or record["downloadCount"] > record["maxDownloads"]
            ):
                raise OSError("invalid file-share registry record")
        return shares

    def _save(self, shares: dict[str, dict[str, Any]]) -> None:
        payload = _canonical({"schema": SCHEMA, "shares": shares}) + b"\n"
        temporary = self.directory / f".registry-{secrets.token_hex(12)}.tmp"
        if os.name == "nt":
            from appliance.windows_state import open_private_file

            fd = open_private_file(temporary, create_new=True)
        else:
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            _replace_upload_metadata(temporary, self.registry)
        finally:
            temporary.unlink(missing_ok=True)

    def _scope(self, actor: str) -> DataAccessScope:
        return (
            self.data_access.scope_for_actor(actor)
            if self.data_access is not None
            else DataAccessScope.unrestricted(actor)
        )

    @staticmethod
    def _identity(path: Path) -> dict[str, int]:
        info = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode):
            raise FileShareError(422, "only regular files can be shared")
        return {
            "device": int(info.st_dev),
            "inode": int(info.st_ino),
            "size": int(info.st_size),
            "mtimeNs": int(info.st_mtime_ns),
        }

    def _revision(self, shares: dict[str, dict[str, Any]]) -> str:
        public = {
            key: {name: value for name, value in record.items() if name != "tokenDigest"}
            for key, record in shares.items()
        }
        return hashlib.sha256(_canonical(public)).hexdigest()

    def _live(self, shares: dict[str, dict[str, Any]], *, now: int) -> dict[str, dict[str, Any]]:
        return {
            share_id: record
            for share_id, record in shares.items()
            if now < record["expiresAt"] and record["downloadCount"] < record["maxDownloads"]
        }

    def plan_create(
        self, actor: str, path: str, *, ttl_seconds: int, max_downloads: int
    ) -> dict[str, Any]:
        if ttl_seconds < MIN_TTL_SECONDS or ttl_seconds > MAX_TTL_SECONDS:
            raise FileShareError(422, "share expiry must be between 5 minutes and 30 days")
        if max_downloads < 1 or max_downloads > MAX_DOWNLOADS:
            raise FileShareError(422, "download limit must be between 1 and 10000")
        self._scope(actor).require_read(path)
        target = self.manager.file_for_download(path)
        identity = self._identity(target)
        with self._lock:
            shares = self._load()
            shares = self._live(shares, now=int(self.clock()))
            desired = {
                "operation": "create",
                "owner": actor,
                "path": path,
                "identity": identity,
                "ttlSeconds": ttl_seconds,
                "maxDownloads": max_downloads,
                "revision": self._revision(shares),
            }
        plan_id = hashlib.sha256(_canonical(desired)).hexdigest()
        return {
            "schema": PLAN_SCHEMA,
            "planId": plan_id,
            "operation": "create",
            "path": path,
            "filename": target.name,
            "size": identity["size"],
            "ttlSeconds": ttl_seconds,
            "maxDownloads": max_downloads,
            "requiresApproval": True,
            "approval": {"action": "files.share.create", "target": plan_id},
        }

    def plan_revoke(self, actor: str, share_id: str) -> dict[str, Any]:
        with self._lock:
            shares = self._load()
            record = shares.get(share_id)
            if record is None or (record.get("owner") != actor and not self._scope(actor).operator):
                raise FileShareError(404, "share not found")
            desired = {
                "operation": "revoke",
                "owner": actor,
                "shareId": share_id,
                "revision": self._revision(shares),
            }
        plan_id = hashlib.sha256(_canonical(desired)).hexdigest()
        return {
            "schema": PLAN_SCHEMA,
            "planId": plan_id,
            "operation": "revoke",
            "shareId": share_id,
            "requiresApproval": True,
            "approval": {"action": "files.share.revoke", "target": plan_id},
        }

    def create(
        self,
        actor: str,
        path: str,
        *,
        ttl_seconds: int,
        max_downloads: int,
        plan_id: str,
    ) -> dict[str, Any]:
        plan = self.plan_create(actor, path, ttl_seconds=ttl_seconds, max_downloads=max_downloads)
        if not hmac.compare_digest(plan["planId"], plan_id):
            raise FileShareError(409, "share plan changed; preview it again")
        now = int(self.clock())
        token = secrets.token_urlsafe(32)
        share_id = secrets.token_hex(12)
        target = self.manager.file_for_download(path)
        with self._lock:
            shares = self._load()
            shares = self._live(shares, now=now)
            if len(shares) >= MAX_ACTIVE_SHARES:
                raise FileShareError(409, "too many active file shares")
            shares[share_id] = {
                "id": share_id,
                "tokenDigest": self._digest(token),
                "owner": actor,
                "path": path,
                "filename": target.name,
                "identity": self._identity(target),
                "createdAt": now,
                "expiresAt": now + ttl_seconds,
                "maxDownloads": max_downloads,
                "downloadCount": 0,
            }
            self._save(shares)
        return {"share": self._public(shares[share_id]), "token": token}

    def revoke(self, actor: str, share_id: str, *, plan_id: str) -> dict[str, Any]:
        plan = self.plan_revoke(actor, share_id)
        if not hmac.compare_digest(plan["planId"], plan_id):
            raise FileShareError(409, "share plan changed; preview it again")
        with self._lock:
            shares = self._load()
            record = shares.get(share_id)
            if record is None or (record.get("owner") != actor and not self._scope(actor).operator):
                raise FileShareError(404, "share not found")
            del shares[share_id]
            self._save(shares)
        return {"ok": True, "shareId": share_id}

    def list(self, actor: str) -> list[dict[str, Any]]:
        scope = self._scope(actor)
        now = int(self.clock())
        with self._lock:
            return [
                self._public(record, now=now)
                for record in self._load().values()
                if scope.operator or record.get("owner") == actor
            ]

    def _digest(self, token: str) -> str:
        return hmac.new(self._key, token.encode("ascii"), hashlib.sha256).hexdigest()

    @staticmethod
    def _public(record: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
        result = {
            key: value
            for key, value in record.items()
            if key not in {"tokenDigest", "path", "identity", "owner"}
        }
        if now is not None:
            result["active"] = (
                now < record["expiresAt"] and record["downloadCount"] < record["maxDownloads"]
            )
        return result

    def redeem(self, token: str) -> OpenedShare:
        if len(token) < 40 or len(token) > 64:
            raise FileShareError(404, "share not found")
        digest = self._digest(token)
        with self._lock:
            shares = self._load()
            record = next(
                (
                    item
                    for item in shares.values()
                    if hmac.compare_digest(item.get("tokenDigest", ""), digest)
                ),
                None,
            )
            now = int(self.clock())
            if (
                record is None
                or now >= record.get("expiresAt", 0)
                or record.get("downloadCount", 0) >= record.get("maxDownloads", 0)
            ):
                raise FileShareError(404, "share not found")
            self._scope(str(record["owner"])).require_read(str(record["path"]))
            target = self.manager.file_for_download(str(record["path"]))
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(target, flags)
            stream = os.fdopen(fd, "rb")
            try:
                info = os.fstat(fd)
                actual = {
                    "device": int(info.st_dev),
                    "inode": int(info.st_ino),
                    "size": int(info.st_size),
                    "mtimeNs": int(info.st_mtime_ns),
                }
                if not stat.S_ISREG(info.st_mode) or actual != record.get("identity"):
                    raise FileShareError(404, "share not found")
                record["downloadCount"] += 1
                shares[str(record["id"])] = record
                self._save(shares)
            except Exception:
                stream.close()
                raise
        media_type = mimetypes.guess_type(str(record["filename"]))[0] or "application/octet-stream"
        return OpenedShare(
            stream=stream,
            filename=str(record["filename"]),
            media_type=media_type,
            size=actual["size"],
            mtime_ns=actual["mtimeNs"],
            share_id=str(record["id"]),
        )


__all__ = ["FileShareError", "FileShareService", "OpenedShare"]
