"""SQLite persistence for device enrollment, entitlements and edge messages."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_SHARE_TTL_SECONDS = 30 * 24 * 60 * 60
MAX_SHARE_TTL_SECONDS = 365 * 24 * 60 * 60
DEFAULT_SHARE_MAX_PER_OWNER = 50
DEFAULT_SHARE_MAX_SNAPSHOT_BYTES = 1_000_000
DEFAULT_SHARE_MAX_TOTAL_BYTES = 512 * 1024 * 1024
_SHARE_ID_RE = re.compile(r"^shr_[a-f0-9]{32}$")
_SHARE_TOKEN_RE = re.compile(r"^oct_share_[A-Za-z0-9_-]{32,64}$")


class ShareLimitError(ValueError):
    """Raised when a public share exceeds a configured storage boundary."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class CloudEdgeStore:
    """Small durable control-plane store with tenant-scoped operations."""

    def __init__(
        self,
        path: str | Path,
        *,
        share_ttl_seconds: int = DEFAULT_SHARE_TTL_SECONDS,
        share_max_per_owner: int = DEFAULT_SHARE_MAX_PER_OWNER,
        share_max_snapshot_bytes: int = DEFAULT_SHARE_MAX_SNAPSHOT_BYTES,
        share_max_total_bytes: int = DEFAULT_SHARE_MAX_TOTAL_BYTES,
    ) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.share_ttl_seconds = self._positive_limit("share_ttl_seconds", share_ttl_seconds)
        if self.share_ttl_seconds > MAX_SHARE_TTL_SECONDS:
            raise ValueError("share_ttl_seconds cannot exceed one year")
        self.share_max_per_owner = self._positive_limit("share_max_per_owner", share_max_per_owner)
        self.share_max_snapshot_bytes = self._positive_limit(
            "share_max_snapshot_bytes", share_max_snapshot_bytes
        )
        self.share_max_total_bytes = self._positive_limit(
            "share_max_total_bytes", share_max_total_bytes
        )
        self._lock = threading.RLock()
        self._init_schema()

    @staticmethod
    def _positive_limit(name: str, value: int) -> int:
        parsed = int(value)
        if parsed <= 0:
            raise ValueError(f"{name} must be positive")
        return parsed

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    code_hash TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS devices (
                    device_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    public_key TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_edge_devices_owner
                    ON devices(tenant_id, owner_id);
                CREATE TABLE IF NOT EXISTS challenges (
                    challenge_hash TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    expires_at INTEGER NOT NULL,
                    used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS entitlements (
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    feature TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    expires_at INTEGER,
                    PRIMARY KEY(tenant_id, owner_id, feature)
                );
                CREATE TABLE IF NOT EXISTS edge_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    device_id TEXT NOT NULL REFERENCES devices(device_id),
                    source TEXT NOT NULL,
                    source_room_id TEXT NOT NULL,
                    source_message_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    published_at TEXT,
                    payload_json TEXT NOT NULL,
                    received_at INTEGER NOT NULL,
                    UNIQUE(tenant_id, owner_id, source, source_room_id, source_message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_edge_messages_owner_received
                    ON edge_messages(tenant_id, owner_id, received_at DESC);
                CREATE TABLE IF NOT EXISTS public_thread_shares (
                    share_id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    tenant_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    source_thread_id TEXT,
                    creator_type TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    snapshot_bytes INTEGER NOT NULL CHECK(snapshot_bytes > 0),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_public_thread_shares_owner
                    ON public_thread_shares(tenant_id, owner_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_public_thread_shares_source
                    ON public_thread_shares(
                        tenant_id, owner_id, source_thread_id, created_at DESC
                    );
                CREATE INDEX IF NOT EXISTS idx_public_thread_shares_expiry
                    ON public_thread_shares(expires_at);
                """
            )

    @staticmethod
    def _hash_secret(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create_pairing_code(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        device_name: str,
        ttl_seconds: int = 600,
    ) -> dict[str, Any]:
        now = int(time.time())
        code = "oct_pair_" + secrets.token_urlsafe(24)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO pairing_codes VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    self._hash_secret(code),
                    tenant_id,
                    owner_id,
                    device_name[:80],
                    now + max(60, min(int(ttl_seconds), 3600)),
                ),
            )
        return {"pairing_code": code, "expires_at": now + max(60, min(int(ttl_seconds), 3600))}

    def enroll(
        self, *, pairing_code: str, public_key: str, device_name: str = ""
    ) -> dict[str, Any] | None:
        now = int(time.time())
        code_hash = self._hash_secret(pairing_code)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM pairing_codes WHERE code_hash=?",
                (code_hash,),
            ).fetchone()
            if row is None or row["used_at"] is not None or int(row["expires_at"]) < now:
                return None
            device_id = "dev_" + uuid.uuid4().hex
            conn.execute(
                """INSERT INTO devices
                (device_id, tenant_id, owner_id, device_name, public_key, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    device_id,
                    row["tenant_id"],
                    row["owner_id"],
                    (device_name.strip() or row["device_name"])[:80],
                    public_key,
                    now,
                ),
            )
            conn.execute("UPDATE pairing_codes SET used_at=? WHERE code_hash=?", (now, code_hash))
        return {
            "device_id": device_id,
            "tenant_id": str(row["tenant_id"]),
            "owner_id": str(row["owner_id"]),
        }

    def device(self, device_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM devices WHERE device_id=?", (device_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_devices(self, *, tenant_id: str, owner_id: str) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT device_id, device_name, created_at, last_seen_at, revoked_at
                FROM devices WHERE tenant_id=? AND owner_id=? ORDER BY created_at DESC""",
                (tenant_id, owner_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def revoke_device(self, *, tenant_id: str, owner_id: str, device_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """UPDATE devices SET revoked_at=?
                WHERE tenant_id=? AND owner_id=? AND device_id=? AND revoked_at IS NULL""",
                (int(time.time()), tenant_id, owner_id, device_id),
            )
        return cur.rowcount > 0

    def create_challenge(self, device_id: str, *, ttl_seconds: int = 120) -> str | None:
        device = self.device(device_id)
        if device is None or device.get("revoked_at") is not None:
            return None
        challenge = secrets.token_urlsafe(32)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO challenges VALUES (?, ?, ?, NULL)",
                (self._hash_secret(challenge), device_id, int(time.time()) + ttl_seconds),
            )
        return challenge

    def consume_challenge(self, *, device_id: str, challenge: str) -> bool:
        now = int(time.time())
        digest = self._hash_secret(challenge)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM challenges WHERE challenge_hash=? AND device_id=?",
                (digest, device_id),
            ).fetchone()
            if row is None or row["used_at"] is not None or int(row["expires_at"]) < now:
                return False
            conn.execute("UPDATE challenges SET used_at=? WHERE challenge_hash=?", (now, digest))
        return True

    def touch_device(self, device_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE devices SET last_seen_at=? WHERE device_id=?", (int(time.time()), device_id)
            )

    def entitlements(self, *, tenant_id: str, owner_id: str) -> list[str]:
        now = int(time.time())
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT feature FROM entitlements
                WHERE tenant_id=? AND owner_id=? AND active=1
                AND (expires_at IS NULL OR expires_at>=?) ORDER BY feature""",
                (tenant_id, owner_id, now),
            ).fetchall()
        return [str(row["feature"]) for row in rows]

    def set_entitlement(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        feature: str,
        active: bool,
        expires_at: int | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO entitlements VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, owner_id, feature)
                DO UPDATE SET active=excluded.active, expires_at=excluded.expires_at""",
                (tenant_id, owner_id, feature, int(active), expires_at),
            )

    def ingest_messages(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        device_id: str,
        messages: list[dict[str, Any]],
    ) -> dict[str, int]:
        received_at = int(time.time())
        accepted = 0
        duplicate = 0
        with self._lock, self._connect() as conn:
            for message in messages:
                try:
                    conn.execute(
                        """INSERT INTO edge_messages
                        (tenant_id, owner_id, device_id, source, source_room_id,
                         source_message_id, title, content, published_at, payload_json, received_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            tenant_id,
                            owner_id,
                            device_id,
                            str(message["source"])[:40],
                            str(message["source_room_id"])[:128],
                            str(message["source_message_id"])[:160],
                            str(message.get("title") or "")[:240],
                            str(message["content"])[:50_000],
                            str(message.get("published_at") or "")[:64] or None,
                            json.dumps(message.get("payload") or {}, ensure_ascii=False)[:100_000],
                            received_at,
                        ),
                    )
                    accepted += 1
                except sqlite3.IntegrityError:
                    duplicate += 1
        self.touch_device(device_id)
        return {"accepted": accepted, "duplicate": duplicate}

    def list_messages(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        limit: int = 100,
        after_id: int = 0,
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT id, device_id, source, source_room_id, source_message_id,
                title, content, published_at, received_at FROM edge_messages
                WHERE tenant_id=? AND owner_id=? AND id>? ORDER BY id ASC LIMIT ?""",
                (tenant_id, owner_id, max(0, after_id), max(1, min(limit, 500))),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _iso_time(value: int) -> str:
        return datetime.fromtimestamp(int(value), UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _share_snapshot_json(snapshot: dict[str, Any]) -> tuple[str, int]:
        encoded = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        return encoded, len(encoded.encode("utf-8"))

    @staticmethod
    def _decode_share_snapshot(value: str) -> dict[str, Any] | None:
        try:
            snapshot = json.loads(value)
        except (TypeError, ValueError):
            return None
        return snapshot if isinstance(snapshot, dict) else None

    @staticmethod
    def _gc_thread_shares_conn(conn: sqlite3.Connection, now: int) -> int:
        cursor = conn.execute(
            "DELETE FROM public_thread_shares WHERE revoked_at IS NOT NULL OR expires_at<=?",
            (now,),
        )
        return max(0, int(cursor.rowcount))

    def gc_thread_shares(self, *, now: int | None = None) -> int:
        """Physically remove revoked and expired snapshots."""

        current = int(time.time()) if now is None else int(now)
        with self._lock, self._connect() as conn:
            return self._gc_thread_shares_conn(conn, current)

    def create_thread_share(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        creator_type: str,
        creator_id: str,
        snapshot: dict[str, Any],
        source_thread_id: str | None = None,
        ttl_seconds: int | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        """Persist one bounded snapshot and return its capability exactly once."""

        snapshot_json, snapshot_bytes = self._share_snapshot_json(snapshot)
        if snapshot_bytes > self.share_max_snapshot_bytes:
            raise ShareLimitError("snapshot", "share snapshot is too large")
        current = int(time.time()) if now is None else int(now)
        requested_ttl = self.share_ttl_seconds if ttl_seconds is None else int(ttl_seconds)
        if requested_ttl <= 0:
            raise ValueError("ttl_seconds must be positive")
        effective_ttl = min(requested_ttl, self.share_ttl_seconds)
        expires_at = current + effective_ttl
        clean_source = str(source_thread_id or "").strip()[:128] or None

        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._gc_thread_shares_conn(conn, current)
            owner_count = conn.execute(
                """SELECT COUNT(*) AS count FROM public_thread_shares
                WHERE tenant_id=? AND owner_id=?""",
                (tenant_id, owner_id),
            ).fetchone()
            if int(owner_count["count"] if owner_count else 0) >= self.share_max_per_owner:
                raise ShareLimitError("owner", "active share quota exceeded")
            total = conn.execute(
                "SELECT COALESCE(SUM(snapshot_bytes), 0) AS size FROM public_thread_shares"
            ).fetchone()
            if int(total["size"] if total else 0) + snapshot_bytes > self.share_max_total_bytes:
                raise ShareLimitError("total", "public share storage capacity exceeded")

            for _attempt in range(8):
                token = "oct_share_" + secrets.token_urlsafe(32)
                token_hash = self._hash_secret(token)
                share_id = "shr_" + uuid.uuid4().hex
                try:
                    conn.execute(
                        """INSERT INTO public_thread_shares
                        (share_id, token_hash, tenant_id, owner_id, source_thread_id,
                         creator_type, creator_id, snapshot_json, snapshot_bytes,
                         created_at, expires_at, revoked_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                        (
                            share_id,
                            token_hash,
                            tenant_id,
                            owner_id,
                            clean_source,
                            creator_type,
                            creator_id,
                            snapshot_json,
                            snapshot_bytes,
                            current,
                            expires_at,
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue
                return {
                    "share_id": share_id,
                    "token": token,
                    "created_at": self._iso_time(current),
                    "expires_at": self._iso_time(expires_at),
                }
        raise RuntimeError("could not mint a unique public share")

    def list_thread_shares(
        self,
        *,
        tenant_id: str,
        owner_id: str,
        source_thread_id: str | None = None,
        limit: int = 100,
        now: int | None = None,
    ) -> list[dict[str, Any]]:
        current = int(time.time()) if now is None else int(now)
        clean_source = str(source_thread_id or "").strip()[:128] or None
        where = "tenant_id=? AND owner_id=?"
        params: list[Any] = [tenant_id, owner_id]
        if clean_source is not None:
            where += " AND source_thread_id=?"
            params.append(clean_source)
        params.append(max(1, min(int(limit), 500)))
        with self._lock, self._connect() as conn:
            self._gc_thread_shares_conn(conn, current)
            rows = conn.execute(
                f"""SELECT share_id, source_thread_id, snapshot_json, created_at, expires_at
                FROM public_thread_shares WHERE {where}
                ORDER BY created_at DESC, share_id DESC LIMIT ?""",
                params,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            snapshot = self._decode_share_snapshot(str(row["snapshot_json"]))
            if snapshot is None:
                continue
            result.append(
                {
                    "share_id": str(row["share_id"]),
                    "source_thread_id": row["source_thread_id"],
                    "created_at": self._iso_time(int(row["created_at"])),
                    "expires_at": self._iso_time(int(row["expires_at"])),
                    "title": str(snapshot.get("title") or ""),
                    "stats": snapshot.get("stats")
                    if isinstance(snapshot.get("stats"), dict)
                    else {},
                }
            )
        return result

    def revoke_thread_share(
        self,
        share_id: str,
        *,
        tenant_id: str,
        owner_id: str,
        now: int | None = None,
    ) -> bool:
        if not _SHARE_ID_RE.fullmatch(share_id):
            return False
        current = int(time.time()) if now is None else int(now)
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """UPDATE public_thread_shares SET revoked_at=?
                WHERE share_id=? AND tenant_id=? AND owner_id=?
                AND revoked_at IS NULL AND expires_at>?""",
                (current, share_id, tenant_id, owner_id, current),
            )
        return cursor.rowcount > 0

    def get_public_thread_share(
        self,
        token: str,
        *,
        now: int | None = None,
    ) -> dict[str, Any] | None:
        if not _SHARE_TOKEN_RE.fullmatch(token):
            return None
        current = int(time.time()) if now is None else int(now)
        token_hash = self._hash_secret(token)
        with self._lock, self._connect() as conn:
            self._gc_thread_shares_conn(conn, current)
            row = conn.execute(
                """SELECT token_hash, snapshot_json, created_at, expires_at
                FROM public_thread_shares WHERE token_hash=?""",
                (token_hash,),
            ).fetchone()
        if row is None or not secrets.compare_digest(str(row["token_hash"]), token_hash):
            return None
        snapshot = self._decode_share_snapshot(str(row["snapshot_json"]))
        if snapshot is None:
            return None
        return {
            "schema": "echo.thread-share.v1",
            "created_at": self._iso_time(int(row["created_at"])),
            "expires_at": self._iso_time(int(row["expires_at"])),
            **snapshot,
        }


__all__ = [
    "CloudEdgeStore",
    "DEFAULT_SHARE_MAX_PER_OWNER",
    "DEFAULT_SHARE_MAX_SNAPSHOT_BYTES",
    "DEFAULT_SHARE_MAX_TOTAL_BYTES",
    "DEFAULT_SHARE_TTL_SECONDS",
    "MAX_SHARE_TTL_SECONDS",
    "ShareLimitError",
]
