"""Device-scoped, resumable file and photo backup for Echo OS.

The transfer bytes deliberately reuse :class:`FileManager`: capacity floors,
share quotas, same-directory temporary files, SHA-256 verification and atomic
commit therefore have one implementation.  This module owns only the device
grant, idempotency, cursor and conflict ledger.  It never opens Agent's private
SQLite databases.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
import stat
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from appliance.approval import HighRiskApprovalService, consume_request_approval
from appliance.audit import ApplianceAudit, AuditIntegrityError
from appliance.device_link import DeviceLinkService
from appliance.files.manager import (
    DEFAULT_UPLOAD_CHUNK_BYTES,
    FileManager,
    InsufficientStorage,
    PathEscape,
    ShareQuotaExceeded,
    UploadHashMismatch,
    UploadOffsetMismatch,
    UploadSessionLimit,
    UploadTooLarge,
)
from appliance.security import ApplianceAuthenticator, resolve_authenticator

SYNC_SCHEMA = "echo.device-sync.v1"
SYNC_PROTOCOL_VERSION = 1
SYNC_VERSION_HEADER = "X-Echo-Sync-Version"
SYNC_DB_FILENAME = "device-sync.db"
SYNC_ROOT = "Mobile Uploads"
SYNC_SCOPES = frozenset({"photos", "files"})
MAX_CHANGE_PAGE = 500
MAX_PATH_DEPTH = 12
MAX_PATH_LENGTH = 1_024
MAX_SEGMENT_LENGTH = 180
_ASSET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEVICE_FOLDER = re.compile(r"[^A-Za-z0-9._-]+")
_PHOTO_EXTENSIONS = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
)
_RESERVED_NAMES = frozenset({".echo-trash", ".echo-upload-sessions"})


def _protocol_fields() -> dict[str, Any]:
    """Return the stable capability contract exposed to device clients."""

    return {
        "schema": SYNC_SCHEMA,
        "protocolVersion": SYNC_PROTOCOL_VERSION,
        "minimumClientProtocolVersion": SYNC_PROTOCOL_VERSION,
        "capabilities": {
            "resumableUpload": True,
            "sha256Verification": True,
            "conflictPolicy": "keep-both",
            "ownDeviceCursor": True,
            "maxChunkBytes": DEFAULT_UPLOAD_CHUNK_BYTES,
            "maxChangePage": MAX_CHANGE_PAGE,
        },
    }


class SyncError(RuntimeError):
    def __init__(self, status_code: int, detail: str | dict[str, Any]) -> None:
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


class _AssetBody(BaseModel):
    asset_id: str = Field(alias="assetId")
    scope: Literal["photos", "files"]
    path: str
    size: int = Field(ge=0)
    sha256: str
    modified_at: int | None = Field(default=None, alias="modifiedAt")


def _scope_action(scope: str, enabled: bool) -> str:
    return f"device-sync.{scope}.{'enable' if enabled else 'disable'}"


class DeviceSyncService:
    """System-owned device sync ledger around the existing file manager."""

    def __init__(
        self,
        *,
        data_dir: str | Path,
        files: FileManager,
        device_link: DeviceLinkService,
        photos: Any | None = None,
        clock: Any = time.time,
    ) -> None:
        self.files = files
        self.device_link = device_link
        self.photos = photos
        self._clock = clock
        self._lock = threading.RLock()
        directory = Path(data_dir).expanduser().resolve() / "sync"
        if directory.is_symlink():
            raise OSError("device sync directory must not be a symbolic link")
        directory.mkdir(parents=True, exist_ok=True)
        info = directory.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise OSError("device sync directory is unsafe")
        with contextlib.suppress(OSError):
            directory.chmod(0o700)
        self.db_path = directory / SYNC_DB_FILENAME
        if self.db_path.is_symlink():
            raise OSError("device sync database must not be a symbolic link")
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version not in {0, 1}:
                raise OSError("device sync database is newer than this Echo version")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS grants (
                    device_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK(scope IN ('photos', 'files')),
                    enabled_at INTEGER NOT NULL,
                    PRIMARY KEY(device_id, scope)
                );
                CREATE TABLE IF NOT EXISTS assets (
                    device_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK(scope IN ('photos', 'files')),
                    asset_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    source_mtime INTEGER,
                    stored_mtime_ns INTEGER,
                    state TEXT NOT NULL CHECK(state IN ('uploading', 'committed', 'cancelled')),
                    session_id TEXT,
                    revision INTEGER NOT NULL DEFAULT 1,
                    conflict INTEGER NOT NULL DEFAULT 0 CHECK(conflict IN (0, 1)),
                    previous_target TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(device_id, scope, asset_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS assets_session
                    ON assets(session_id) WHERE session_id IS NOT NULL;
                CREATE TABLE IF NOT EXISTS events (
                    cursor INTEGER PRIMARY KEY AUTOINCREMENT,
                    device_id TEXT NOT NULL,
                    scope TEXT NOT NULL CHECK(scope IN ('photos', 'files')),
                    asset_id TEXT NOT NULL,
                    target_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('created', 'conflict')),
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_scope_cursor ON events(scope, cursor);
                """
            )
            connection.execute("PRAGMA user_version=1")
        with contextlib.suppress(OSError):
            self.db_path.chmod(0o600)

    @staticmethod
    def _device_folder(device_id: str) -> str:
        readable = _DEVICE_FOLDER.sub("-", device_id).strip("-._")[:48] or "device"
        suffix = hashlib.sha256(device_id.encode("utf-8")).hexdigest()[:8]
        return f"{readable}-{suffix}"

    @staticmethod
    def _normalized_source_path(raw: str, scope: str) -> str:
        if (
            not isinstance(raw, str)
            or not raw
            or len(raw) > MAX_PATH_LENGTH
            or "\x00" in raw
            or "\\" in raw
            or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        ):
            raise SyncError(422, "invalid sync path")
        pure = PurePosixPath(raw)
        parts = pure.parts
        if (
            pure.is_absolute()
            or not parts
            or len(parts) > MAX_PATH_DEPTH
            or any(part in {"", ".", ".."} for part in parts)
            or any(len(part) > MAX_SEGMENT_LENGTH for part in parts)
            or any(part in _RESERVED_NAMES or part.startswith(".echo-") for part in parts)
        ):
            raise SyncError(422, "invalid sync path")
        normalized = "/".join(parts)
        if (
            scope == "photos"
            and PurePosixPath(normalized).suffix.casefold() not in _PHOTO_EXTENSIONS
        ):
            raise SyncError(422, "unsupported photo format")
        return normalized

    @staticmethod
    def _validate_manifest(
        *, asset_id: str, scope: str, path: str, size: int, sha256: str
    ) -> tuple[str, str]:
        if _ASSET_ID.fullmatch(asset_id) is None:
            raise SyncError(422, "invalid asset id")
        if scope not in SYNC_SCOPES:
            raise SyncError(422, "invalid sync scope")
        digest = sha256.strip().lower()
        if _SHA256.fullmatch(digest) is None:
            raise SyncError(422, "invalid SHA-256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SyncError(422, "invalid asset size")
        return DeviceSyncService._normalized_source_path(path, scope), digest

    def _granted_scopes(self, device_id: str) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scope FROM grants WHERE device_id=? ORDER BY scope", (device_id,)
            ).fetchall()
        return tuple(str(row["scope"]) for row in rows)

    def _require_scope(self, device_id: str, scope: str) -> None:
        if scope not in self._granted_scopes(device_id):
            raise SyncError(403, f"{scope} backup is not enabled for this device")

    def set_scope(self, device_id: str, scope: str, *, enabled: bool) -> dict[str, Any]:
        if scope not in SYNC_SCOPES:
            raise SyncError(422, "invalid sync scope")
        if self.device_link.managed_device(device_id) is None:
            raise SyncError(404, "managed paired device not found")
        now = int(self._clock())
        sessions: list[str] = []
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if enabled:
                connection.execute(
                    "INSERT INTO grants(device_id, scope, enabled_at) VALUES(?, ?, ?) "
                    "ON CONFLICT(device_id, scope) DO UPDATE SET enabled_at=excluded.enabled_at",
                    (device_id, scope, now),
                )
            else:
                connection.execute(
                    "DELETE FROM grants WHERE device_id=? AND scope=?", (device_id, scope)
                )
                rows = connection.execute(
                    "SELECT session_id FROM assets WHERE device_id=? AND scope=? "
                    "AND state='uploading' AND session_id IS NOT NULL",
                    (device_id, scope),
                ).fetchall()
                sessions = [str(row["session_id"]) for row in rows]
                connection.execute(
                    "UPDATE assets SET state='cancelled', session_id=NULL, updated_at=? "
                    "WHERE device_id=? AND scope=? AND state='uploading'",
                    (now, device_id, scope),
                )
            connection.commit()
        for session_id in sessions:
            with contextlib.suppress(FileNotFoundError, OSError, ValueError):
                self.files.cancel_upload_session(session_id)
        return self.admin_status()

    def _target_for(self, device_id: str, scope: str, source_path: str) -> str:
        collection = "Photos" if scope == "photos" else "Files"
        return "/".join((SYNC_ROOT, self._device_folder(device_id), collection, source_path))

    def _ensure_parent(self, target_path: str) -> None:
        parent = str(PurePosixPath(target_path).parent)
        if parent in {"", "."}:
            return
        with contextlib.suppress(FileExistsError):
            self.files.mkdir(parent)

    def _digest_file(self, target_path: str) -> tuple[str, int, int]:
        try:
            path = self.files.file_for_download(target_path)
        except FileNotFoundError as exc:
            raise SyncError(404, "synced file is missing") from exc
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        info = path.stat()
        return digest.hexdigest(), info.st_size, info.st_mtime_ns

    def _matching_file(self, target_path: str, digest: str, size: int) -> tuple[bool, int | None]:
        try:
            actual_digest, actual_size, mtime_ns = self._digest_file(target_path)
        except SyncError as exc:
            if exc.status_code == 404:
                return False, None
            raise
        except ValueError:
            return False, None
        return actual_size == size and actual_digest == digest, mtime_ns

    def _target_exists(self, target_path: str) -> bool:
        try:
            self.files.file_for_download(target_path)
            return True
        except FileNotFoundError:
            return False
        except ValueError:
            return True

    def _conflict_target(self, target_path: str, digest: str) -> str:
        path = PurePosixPath(target_path)
        suffix = path.suffix
        stem = path.stem[: max(1, MAX_SEGMENT_LENGTH - len(suffix) - 21)]
        base = f"{stem} (conflict {digest[:8]})"
        for attempt in range(1, 1_000):
            marker = "" if attempt == 1 else f"-{attempt}"
            name = f"{base}{marker}{suffix}"
            candidate = str(path.with_name(name))
            if not self._target_exists(candidate):
                return candidate
        raise SyncError(409, "too many file conflicts")

    @staticmethod
    def _session_payload(
        *, decision: str, session: dict[str, Any] | None, target: str, conflict: bool
    ) -> dict[str, Any]:
        return {
            **_protocol_fields(),
            "decision": decision,
            "target": target,
            "conflict": conflict,
            "session": session,
        }

    def preflight(
        self,
        device_id: str,
        *,
        asset_id: str,
        scope: str,
        path: str,
        size: int,
        sha256: str,
        modified_at: int | None = None,
    ) -> dict[str, Any]:
        source_path, digest = self._validate_manifest(
            asset_id=asset_id, scope=scope, path=path, size=size, sha256=sha256
        )
        self._require_scope(device_id, scope)
        now = int(self._clock())
        stale_session: str | None = None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE device_id=? AND scope=? AND asset_id=?",
                (device_id, scope, asset_id),
            ).fetchone()
            if row is not None and row["state"] == "uploading" and row["session_id"]:
                if row["sha256"] == digest and int(row["size"]) == size:
                    try:
                        session = self.files.get_upload_session(str(row["session_id"]))
                    except FileNotFoundError:
                        stale_session = str(row["session_id"])
                    else:
                        return self._session_payload(
                            decision="resume",
                            session=session,
                            target=str(row["target_path"]),
                            conflict=bool(row["conflict"]),
                        )
                else:
                    stale_session = str(row["session_id"])

            if stale_session:
                with contextlib.suppress(FileNotFoundError, OSError, ValueError):
                    self.files.cancel_upload_session(stale_session)

            desired_target = self._target_for(device_id, scope, source_path)
            conflict = False
            previous_target: str | None = None
            if row is not None and row["state"] == "committed":
                previous_target = str(row["target_path"])
                if row["sha256"] == digest and int(row["size"]) == size:
                    matches, mtime_ns = self._matching_file(previous_target, digest, size)
                    if matches:
                        connection.execute(
                            "UPDATE assets SET stored_mtime_ns=?, updated_at=? "
                            "WHERE device_id=? AND scope=? AND asset_id=?",
                            (mtime_ns, now, device_id, scope, asset_id),
                        )
                        return self._session_payload(
                            decision="skip",
                            session=None,
                            target=previous_target,
                            conflict=bool(row["conflict"]),
                        )
                else:
                    conflict = True

            target = desired_target
            if conflict or self._target_exists(target):
                matches, mtime_ns = self._matching_file(target, digest, size)
                if matches:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute(
                        """INSERT INTO assets(
                            device_id, scope, asset_id, source_path, target_path, sha256, size,
                            source_mtime, stored_mtime_ns, state, session_id, revision, conflict,
                            previous_target, created_at, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'committed', NULL, 1, ?, ?, ?, ?)
                        ON CONFLICT(device_id, scope, asset_id) DO UPDATE SET
                            source_path=excluded.source_path, target_path=excluded.target_path,
                            sha256=excluded.sha256, size=excluded.size,
                            source_mtime=excluded.source_mtime,
                            stored_mtime_ns=excluded.stored_mtime_ns, state='committed',
                            session_id=NULL, revision=assets.revision+1,
                            conflict=excluded.conflict, previous_target=excluded.previous_target,
                            updated_at=excluded.updated_at""",
                        (
                            device_id,
                            scope,
                            asset_id,
                            source_path,
                            target,
                            digest,
                            size,
                            modified_at,
                            mtime_ns,
                            int(conflict),
                            previous_target,
                            now,
                            now,
                        ),
                    )
                    connection.execute(
                        "INSERT INTO events(device_id, scope, asset_id, target_path, sha256, "
                        "size, kind, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            device_id,
                            scope,
                            asset_id,
                            target,
                            digest,
                            size,
                            "conflict" if conflict else "created",
                            now,
                        ),
                    )
                    connection.commit()
                    return self._session_payload(
                        decision="skip", session=None, target=target, conflict=conflict
                    )
                conflict = True
                target = self._conflict_target(desired_target, digest)

            self._ensure_parent(target)
            target_path = PurePosixPath(target)
            fingerprint = hashlib.sha256(
                f"{device_id}\0{scope}\0{asset_id}\0{digest}".encode()
            ).hexdigest()
            session = self.files.create_upload_session(
                str(target_path.parent),
                target_path.name,
                size,
                expected_sha256=digest,
                overwrite=False,
                fingerprint=fingerprint,
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO assets(
                        device_id, scope, asset_id, source_path, target_path, sha256, size,
                        source_mtime, stored_mtime_ns, state, session_id, revision, conflict,
                        previous_target, created_at, updated_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, 'uploading', ?, 1, ?, ?, ?, ?)
                    ON CONFLICT(device_id, scope, asset_id) DO UPDATE SET
                        source_path=excluded.source_path, target_path=excluded.target_path,
                        sha256=excluded.sha256, size=excluded.size,
                        source_mtime=excluded.source_mtime, stored_mtime_ns=NULL,
                        state='uploading', session_id=excluded.session_id,
                        revision=assets.revision+1, conflict=excluded.conflict,
                        previous_target=excluded.previous_target, updated_at=excluded.updated_at""",
                    (
                        device_id,
                        scope,
                        asset_id,
                        source_path,
                        target,
                        digest,
                        size,
                        modified_at,
                        session["sessionId"],
                        int(conflict),
                        previous_target,
                        now,
                        now,
                    ),
                )
                connection.commit()
            except Exception:
                with contextlib.suppress(Exception):
                    self.files.cancel_upload_session(str(session["sessionId"]))
                raise
        return self._session_payload(
            decision="upload", session=session, target=target, conflict=conflict
        )

    def _owned_upload(self, device_id: str, session_id: str) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM assets WHERE device_id=? AND session_id=? AND state='uploading'",
                (device_id, session_id),
            ).fetchone()
        if row is None:
            raise SyncError(404, "upload session not found")
        self._require_scope(device_id, str(row["scope"]))
        return row

    def upload_status(self, device_id: str, session_id: str) -> dict[str, Any]:
        row = self._owned_upload(device_id, session_id)
        try:
            session = self.files.get_upload_session(session_id)
        except FileNotFoundError as exc:
            raise SyncError(410, "upload session expired") from exc
        return self._session_payload(
            decision="resume",
            session=session,
            target=str(row["target_path"]),
            conflict=bool(row["conflict"]),
        )

    def append_chunk(
        self, device_id: str, session_id: str, offset: int, data: bytes
    ) -> dict[str, Any]:
        row = self._owned_upload(device_id, session_id)
        try:
            session = self.files.append_upload_session_chunk(session_id, offset, data)
        except UploadOffsetMismatch as exc:
            raise SyncError(
                409, {"message": str(exc), "uploadedBytes": exc.expected_offset}
            ) from exc
        return self._session_payload(
            decision="resume",
            session=session,
            target=str(row["target_path"]),
            conflict=bool(row["conflict"]),
        )

    def complete(self, device_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._complete_locked(device_id, session_id)

    def _complete_locked(self, device_id: str, session_id: str) -> dict[str, Any]:
        row = self._owned_upload(device_id, session_id)
        try:
            entry, digest, _verified = self.files.complete_upload_session(session_id)
            if entry.path != row["target_path"] or digest != row["sha256"]:
                raise SyncError(409, "upload target changed")
            stored_mtime_ns = self.files.file_for_download(entry.path).stat().st_mtime_ns
        except FileNotFoundError as exc:
            matches, stored_mtime_ns = self._matching_file(
                str(row["target_path"]), str(row["sha256"]), int(row["size"])
            )
            if not matches or stored_mtime_ns is None:
                raise SyncError(410, "upload session expired") from exc
        now = int(self._clock())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state, session_id FROM assets WHERE device_id=? AND scope=? AND asset_id=?",
                (device_id, row["scope"], row["asset_id"]),
            ).fetchone()
            if (
                current is None
                or current["state"] != "uploading"
                or current["session_id"] != session_id
            ):
                connection.rollback()
                raise SyncError(409, "upload ownership changed")
            connection.execute(
                "UPDATE assets SET state='committed', session_id=NULL, stored_mtime_ns=?, "
                "updated_at=? WHERE device_id=? AND scope=? AND asset_id=?",
                (stored_mtime_ns, now, device_id, row["scope"], row["asset_id"]),
            )
            connection.execute(
                "INSERT INTO events(device_id, scope, asset_id, target_path, sha256, size, kind, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    row["scope"],
                    row["asset_id"],
                    row["target_path"],
                    row["sha256"],
                    row["size"],
                    "conflict" if row["conflict"] else "created",
                    now,
                ),
            )
            cursor = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.commit()
        if row["scope"] == "photos" and self.photos is not None:
            invalidator = getattr(self.photos, "invalidate_scan_cache", None)
            if callable(invalidator):
                invalidator()
        return {
            **_protocol_fields(),
            "state": "committed",
            "target": str(row["target_path"]),
            "sha256": str(row["sha256"]),
            "size": int(row["size"]),
            "conflict": bool(row["conflict"]),
            "cursor": cursor,
        }

    def cancel(self, device_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            return self._cancel_locked(device_id, session_id)

    def _cancel_locked(self, device_id: str, session_id: str) -> dict[str, Any]:
        row = self._owned_upload(device_id, session_id)
        with contextlib.suppress(FileNotFoundError):
            self.files.cancel_upload_session(session_id)
        with self._connect() as connection:
            connection.execute(
                "UPDATE assets SET state='cancelled', session_id=NULL, updated_at=? "
                "WHERE device_id=? AND scope=? AND asset_id=? AND session_id=?",
                (int(self._clock()), device_id, row["scope"], row["asset_id"], session_id),
            )
        return {
            **_protocol_fields(),
            "sessionId": session_id,
            "cancelled": True,
        }

    def device_status(self, device_id: str) -> dict[str, Any]:
        scopes = self._granted_scopes(device_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT scope, state, conflict, COUNT(*) AS amount, COALESCE(SUM(size), 0) AS bytes "
                "FROM assets WHERE device_id=? AND state IN ('uploading', 'committed') "
                "GROUP BY scope, state, conflict",
                (device_id,),
            ).fetchall()
            cursor = int(
                connection.execute(
                    "SELECT COALESCE(MAX(cursor), 0) FROM events WHERE device_id=?",
                    (device_id,),
                ).fetchone()[0]
            )
        summary = {
            scope: {"committed": 0, "uploading": 0, "conflicts": 0, "bytes": 0}
            for scope in SYNC_SCOPES
        }
        for row in rows:
            item = summary[str(row["scope"])]
            item[str(row["state"])] = int(row["amount"])
            if row["state"] == "committed":
                item["bytes"] += int(row["bytes"])
            if row["conflict"]:
                item["conflicts"] += int(row["amount"])
        return {
            **_protocol_fields(),
            "deviceId": device_id,
            "grantedScopes": list(scopes),
            "summary": summary,
            "latestCursor": cursor,
            "chunkBytes": DEFAULT_UPLOAD_CHUNK_BYTES,
        }

    def changes(self, device_id: str, *, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        scopes = self._granted_scopes(device_id)
        if not scopes:
            raise SyncError(403, "device backup is not enabled")
        cursor = max(0, int(cursor))
        limit = max(1, min(int(limit), MAX_CHANGE_PAGE))
        placeholders = ",".join("?" for _ in scopes)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT cursor, device_id, scope, asset_id, target_path, sha256, size, kind, created_at "
                f"FROM events WHERE device_id=? AND cursor>? AND scope IN ({placeholders}) "
                "ORDER BY cursor LIMIT ?",
                (device_id, cursor, *scopes, limit),
            ).fetchall()
        items = [
            {
                "cursor": int(row["cursor"]),
                "sourceDeviceId": str(row["device_id"]),
                "scope": str(row["scope"]),
                "assetId": str(row["asset_id"]),
                "target": str(row["target_path"]),
                "sha256": str(row["sha256"]),
                "size": int(row["size"]),
                "kind": str(row["kind"]),
                "createdAt": int(row["created_at"]),
            }
            for row in rows
        ]
        return {
            **_protocol_fields(),
            "cursor": items[-1]["cursor"] if items else cursor,
            "hasMore": len(items) == limit,
            "changes": items,
        }

    def admin_status(self) -> dict[str, Any]:
        link_status = self.device_link.status()
        managed_devices = {
            str(device["id"]): device
            for device in link_status["devices"]
            if bool(device.get("individuallyRevocable"))
        }
        with self._connect() as connection:
            grants = connection.execute(
                "SELECT device_id, scope, enabled_at FROM grants ORDER BY device_id, scope"
            ).fetchall()
            counts = connection.execute(
                "SELECT device_id, scope, state, conflict, COUNT(*) AS amount, "
                "COALESCE(SUM(size), 0) AS bytes FROM assets "
                "WHERE state IN ('uploading', 'committed') "
                "GROUP BY device_id, scope, state, conflict"
            ).fetchall()
        devices: dict[str, dict[str, Any]] = {
            device_id: {
                "id": device_id,
                "name": " ".join(
                    part for part in (device.get("brand"), device.get("model")) if part
                )
                or device_id,
                "online": bool(device.get("online")),
                "grants": {scope: False for scope in SYNC_SCOPES},
                "summary": {
                    scope: {"committed": 0, "uploading": 0, "conflicts": 0, "bytes": 0}
                    for scope in SYNC_SCOPES
                },
            }
            for device_id, device in managed_devices.items()
        }
        for row in grants:
            device = devices.get(str(row["device_id"]))
            if device is not None:
                device["grants"][str(row["scope"])] = True
        for row in counts:
            device = devices.get(str(row["device_id"]))
            if device is None:
                continue
            item = device["summary"][str(row["scope"])]
            item[str(row["state"])] = int(row["amount"])
            if row["state"] == "committed":
                item["bytes"] += int(row["bytes"])
            if row["conflict"]:
                item["conflicts"] += int(row["amount"])
        return {
            **_protocol_fields(),
            "available": link_status["mode"] == "echo-managed",
            "mode": link_status["mode"],
            "conflictPolicy": "keep-both",
            "roots": {
                "photos": f"{SYNC_ROOT}/<device>/Photos",
                "files": f"{SYNC_ROOT}/<device>/Files",
            },
            "devices": list(devices.values()),
        }


def create_device_sync_router(
    service: DeviceSyncService,
    *,
    jwt_secret: str | None = None,
    approval: HighRiskApprovalService,
    audit: ApplianceAudit,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    router = APIRouter(tags=["appliance", "device-sync"])
    require_browser = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).dependency()

    def _audit(actor: str, action: str, target: str, outcome: str) -> None:
        try:
            audit.record(actor=actor, action=action, target=target, outcome=outcome)
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(status_code=503, detail="appliance audit unavailable") from exc

    def _device_actor(request: Request, response: Response) -> str:
        device_id = request.headers.get("X-Echo-Device-ID", "")
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split(" ", 1)
        if (
            len(parts) != 2
            or parts[0] != "EchoDevice"
            or not service.device_link.authenticate_device(device_id, parts[1])
        ):
            raise HTTPException(
                status_code=401,
                detail="valid managed device credential required",
                headers={"WWW-Authenticate": "EchoDevice"},
            )
        requested_version = request.headers.get(SYNC_VERSION_HEADER, "")
        if requested_version != str(SYNC_PROTOCOL_VERSION):
            raise HTTPException(
                status_code=426,
                detail={
                    "message": "unsupported Echo device sync protocol",
                    "supportedProtocolVersion": SYNC_PROTOCOL_VERSION,
                },
                headers={
                    SYNC_VERSION_HEADER: str(SYNC_PROTOCOL_VERSION),
                    "Upgrade": f"EchoDeviceSync/{SYNC_PROTOCOL_VERSION}",
                },
            )
        response.headers[SYNC_VERSION_HEADER] = str(SYNC_PROTOCOL_VERSION)
        request.state.appliance_actor = f"device:{device_id}"
        return device_id

    def _guard(callable_, *args, **kwargs):
        try:
            return callable_(*args, **kwargs)
        except SyncError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except PathEscape as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="upload session not found") from exc
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=f"file already exists: {exc}") from exc
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except UploadHashMismatch as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except UploadSessionLimit as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ShareQuotaExceeded as exc:
            raise HTTPException(
                status_code=507, detail={"message": str(exc), **exc.report}
            ) from exc
        except InsufficientStorage as exc:
            raise HTTPException(status_code=507, detail=str(exc)) from exc
        except (NotADirectoryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/appliance/sync")
    async def admin_status(_actor: str = Depends(require_browser)) -> dict[str, Any]:
        return await run_in_threadpool(service.admin_status)

    @router.post("/api/appliance/sync/devices/{device_id}/{scope}/{operation}")
    async def set_scope(
        device_id: str,
        scope: str,
        operation: str,
        request: Request,
        actor: str = Depends(require_browser),
    ) -> dict[str, Any]:
        if scope not in SYNC_SCOPES or operation not in {"enable", "disable"}:
            raise HTTPException(status_code=404, detail="sync setting not found")
        enabled = operation == "enable"
        action = _scope_action(scope, enabled)
        consume_request_approval(request, approval, actor=actor, action=action, target=device_id)
        _audit(actor, action, device_id, "attempted")
        try:
            result = await run_in_threadpool(
                _guard, service.set_scope, device_id, scope, enabled=enabled
            )
        except HTTPException:
            _audit(actor, action, device_id, "failed")
            raise
        _audit(actor, action, device_id, "succeeded")
        return result

    @router.get("/api/appliance/device-sync")
    async def device_status(device_id: str = Depends(_device_actor)) -> dict[str, Any]:
        return await run_in_threadpool(_guard, service.device_status, device_id)

    @router.get("/api/appliance/device-sync/changes")
    async def changes(
        cursor: int = 0,
        limit: int = 100,
        device_id: str = Depends(_device_actor),
    ) -> dict[str, Any]:
        return await run_in_threadpool(
            _guard, service.changes, device_id, cursor=cursor, limit=limit
        )

    @router.post("/api/appliance/device-sync/assets/preflight")
    async def preflight(
        body: _AssetBody,
        device_id: str = Depends(_device_actor),
    ) -> dict[str, Any]:
        actor = f"device:{device_id}"
        _audit(actor, "device-sync.asset.preflight", body.asset_id, "attempted")
        try:
            result = await run_in_threadpool(
                _guard,
                service.preflight,
                device_id,
                asset_id=body.asset_id,
                scope=body.scope,
                path=body.path,
                size=body.size,
                sha256=body.sha256,
                modified_at=body.modified_at,
            )
        except HTTPException:
            _audit(actor, "device-sync.asset.preflight", body.asset_id, "failed")
            raise
        _audit(actor, "device-sync.asset.preflight", body.asset_id, "succeeded")
        return result

    @router.get("/api/appliance/device-sync/upload-sessions/{session_id}")
    async def upload_status(
        session_id: str, device_id: str = Depends(_device_actor)
    ) -> dict[str, Any]:
        return await run_in_threadpool(_guard, service.upload_status, device_id, session_id)

    @router.put("/api/appliance/device-sync/upload-sessions/{session_id}/chunk")
    async def append_chunk(
        session_id: str,
        request: Request,
        offset: int = Header(alias="X-Echo-Upload-Offset", ge=0),
        device_id: str = Depends(_device_actor),
    ) -> dict[str, Any]:
        data = bytearray()
        try:
            async for chunk in request.stream():
                data.extend(chunk)
                if len(data) > DEFAULT_UPLOAD_CHUNK_BYTES:
                    raise HTTPException(status_code=413, detail="upload chunk is too large")
        except ClientDisconnect:
            raise HTTPException(status_code=499, detail="upload client disconnected") from None
        return await run_in_threadpool(
            _guard, service.append_chunk, device_id, session_id, offset, bytes(data)
        )

    @router.post("/api/appliance/device-sync/upload-sessions/{session_id}/complete")
    async def complete(session_id: str, device_id: str = Depends(_device_actor)) -> dict[str, Any]:
        actor = f"device:{device_id}"
        _audit(actor, "device-sync.asset.complete", session_id, "attempted")
        try:
            result = await run_in_threadpool(_guard, service.complete, device_id, session_id)
        except HTTPException:
            _audit(actor, "device-sync.asset.complete", session_id, "failed")
            raise
        _audit(actor, "device-sync.asset.complete", session_id, "succeeded")
        return result

    @router.delete("/api/appliance/device-sync/upload-sessions/{session_id}")
    async def cancel(session_id: str, device_id: str = Depends(_device_actor)) -> dict[str, Any]:
        actor = f"device:{device_id}"
        _audit(actor, "device-sync.asset.cancel", session_id, "attempted")
        try:
            result = await run_in_threadpool(_guard, service.cancel, device_id, session_id)
        except HTTPException:
            _audit(actor, "device-sync.asset.cancel", session_id, "failed")
            raise
        _audit(actor, "device-sync.asset.cancel", session_id, "succeeded")
        return result

    return router


__all__ = [
    "DeviceSyncService",
    "SYNC_SCHEMA",
    "SYNC_PROTOCOL_VERSION",
    "SYNC_VERSION_HEADER",
    "SYNC_SCOPES",
    "SyncError",
    "create_device_sync_router",
]
