"""Local MX message outbox and proof-of-possession cloud connector."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from runtime.platform.io import atomic_write_json


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _valid_cloud_url(value: str) -> str:
    clean = value.strip().rstrip("/")
    parsed = urlsplit(clean)
    loopback = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("cloud_url must use HTTPS (HTTP is allowed only for loopback development)")
    if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("invalid cloud_url")
    return clean


class MXCloudSyncConnector:
    """Durable local outbox; only short-lived access tokens live in memory."""

    def __init__(self, data_dir: str | Path, *, http_client: Any = None) -> None:
        self.data_dir = Path(data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "cloud-sync.json"
        self.db_path = self.data_dir / "cloud-sync.sqlite3"
        self._lock = threading.RLock()
        self._http_client = http_client
        self._access_token = ""
        self._access_token_expires_at = 0
        self._last_error = ""
        self._last_sync_at = 0
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    message_json TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    sent_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_mx_sync_pending ON outbox(sent_at, id);
                """
            )

    def _load_config(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    def _save_config(self, config: dict[str, Any]) -> None:
        # This config holds the device Ed25519 private key. Writing the temp
        # first and chmod-ing after left it umask-wide (0o644 typically) for the
        # duration of the write, so any local reader could lift the key. The
        # shared helper fchmods the fd before the first byte lands, so neither
        # the temp nor the renamed target is ever wider than 0o600.
        atomic_write_json(self.config_path, config, mode=0o600, keep_backup=False)

    @staticmethod
    def _new_keypair() -> tuple[str, str]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        private = Ed25519PrivateKey.generate()
        private_raw = private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        public_raw = private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64(private_raw), _b64(public_raw)

    def configure(self, *, cloud_url: str, pairing_code: str, device_name: str) -> dict[str, Any]:
        url = _valid_cloud_url(cloud_url)
        private_key, public_key = self._new_keypair()
        payload = {
            "pairing_code": pairing_code,
            "public_key": public_key,
            "device_name": (device_name.strip() or "Echo Desktop")[:80],
        }
        with self._client() as client:
            response = client.post(url + "/edge/v1/enroll", json=payload)
            response.raise_for_status()
            enrolled = response.json()
        config = {
            "cloud_url": url,
            "device_id": str(enrolled["device_id"]),
            "device_name": payload["device_name"],
            "private_key": private_key,
            "public_key": public_key,
            "configured_at": int(time.time()),
        }
        self._save_config(config)
        self._access_token = ""
        self._last_error = ""
        return self.status()

    def configure_official_ingest(
        self, *, cloud_url: str, ingest_key: str, device_name: str = "MX Official Collector"
    ) -> dict[str, Any]:
        """Bind the operator-owned collector to the shared official message database.

        This is deliberately not exposed by the plugin's browser API. The key is
        provisioned by an operator and stored only in the chmod-0600 connector config.
        """
        url = _valid_cloud_url(cloud_url)
        clean_key = ingest_key.strip()
        if len(clean_key) < 32:
            raise ValueError("official ingest key must contain at least 32 characters")
        self._save_config(
            {
                "mode": "official_ingest",
                "cloud_url": url,
                "device_id": "official-mx-collector",
                "device_name": (device_name.strip() or "MX Official Collector")[:80],
                "ingest_key": clean_key,
                "configured_at": int(time.time()),
            }
        )
        self._access_token = ""
        self._last_error = ""
        return self.status()

    def disconnect(self) -> None:
        with self._lock:
            with contextlib.suppress(FileNotFoundError):
                self.config_path.unlink()
            self._access_token = ""
            self._access_token_expires_at = 0

    def enqueue(self, messages: list[dict[str, Any]]) -> dict[str, int]:
        queued = 0
        duplicate = 0
        with self._lock, self._connect() as conn:
            for message in messages[:100]:
                canonical = json.dumps(
                    message, ensure_ascii=False, separators=(",", ":"), sort_keys=True
                )
                fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                try:
                    conn.execute(
                        "INSERT INTO outbox(fingerprint, message_json, created_at) VALUES (?, ?, ?)",
                        (fingerprint, canonical, int(time.time())),
                    )
                    queued += 1
                except sqlite3.IntegrityError:
                    duplicate += 1
        return {"queued": queued, "duplicate": duplicate}

    def _pending(self, limit: int = 100) -> list[sqlite3.Row]:
        with self._lock, self._connect() as conn:
            return conn.execute(
                "SELECT id, message_json FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()

    def _client(self) -> Any:
        if self._http_client is not None:
            return self._http_client
        return httpx.Client(timeout=15, follow_redirects=False)

    def _token(self, config: dict[str, Any]) -> str:
        now = int(time.time())
        if self._access_token and now < self._access_token_expires_at - 60:
            return self._access_token
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        url = str(config["cloud_url"])
        device_id = str(config["device_id"])
        with self._client() as client:
            challenge_response = client.post(url + f"/edge/v1/challenge/{device_id}")
            challenge_response.raise_for_status()
            challenge = str(challenge_response.json()["challenge"])
            private_raw = base64.urlsafe_b64decode(str(config["private_key"]) + "==")
            message = f"echo-edge-token-v1:{device_id}:{challenge}".encode()
            signature = _b64(Ed25519PrivateKey.from_private_bytes(private_raw).sign(message))
            token_response = client.post(
                url + "/edge/v1/token",
                json={"device_id": device_id, "challenge": challenge, "signature": signature},
            )
            token_response.raise_for_status()
            body = token_response.json()
        self._access_token = str(body["access_token"])
        self._access_token_expires_at = now + int(body.get("expires_in") or 900)
        return self._access_token

    def flush(self) -> dict[str, Any]:
        config = self._load_config()
        if not config.get("cloud_url") or not config.get("device_id"):
            return {"ok": False, "configured": False, "sent": 0}
        pending = self._pending()
        if not pending:
            return {"ok": True, "configured": True, "sent": 0}
        try:
            messages = [json.loads(str(row["message_json"])) for row in pending]
            with self._client() as client:
                if config.get("mode") == "official_ingest":
                    response = client.post(
                        str(config["cloud_url"]) + "/v1/data-sources/mx/messages/batch",
                        headers={"X-Official-Data-Key": str(config.get("ingest_key") or "")},
                        json={"messages": messages},
                    )
                else:
                    token = self._token(config)
                    response = client.post(
                        str(config["cloud_url"]) + "/edge/v1/messages/batch",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"messages": messages},
                    )
                response.raise_for_status()
            now = int(time.time())
            ids = [int(row["id"]) for row in pending]
            placeholders = ",".join("?" for _ in ids)
            with self._lock, self._connect() as conn:
                conn.execute(
                    f"UPDATE outbox SET sent_at=? WHERE id IN ({placeholders})",  # noqa: S608
                    (now, *ids),
                )
            self._last_sync_at = now
            self._last_error = ""
            return {"ok": True, "configured": True, "sent": len(ids)}
        except Exception as exc:  # noqa: BLE001
            self._last_error = str(exc)[:300]
            return {"ok": False, "configured": True, "sent": 0, "error": self._last_error}

    def status(self) -> dict[str, Any]:
        config = self._load_config()
        with self._lock, self._connect() as conn:
            pending = int(
                conn.execute("SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL").fetchone()[0]
            )
        return {
            "configured": bool(config.get("cloud_url") and config.get("device_id")),
            "cloud_url": str(config.get("cloud_url") or ""),
            "device_id": str(config.get("device_id") or ""),
            "device_name": str(config.get("device_name") or ""),
            "mode": str(config.get("mode") or "device"),
            "pending": pending,
            "last_sync_at": self._last_sync_at or None,
            "last_error": self._last_error,
        }

    def recent_messages(
        self,
        *,
        limit: int = 100,
        query: str = "",
        room_id: str = "",
    ) -> list[dict[str, Any]]:
        clean_query = query.strip().casefold()
        clean_room = room_id.strip()
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """SELECT message_json, sent_at FROM outbox
                ORDER BY created_at DESC, id DESC LIMIT 1000"""
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            try:
                message = json.loads(str(row["message_json"]))
            except json.JSONDecodeError:
                continue
            if clean_room and str(message.get("source_room_id") or "") != clean_room:
                continue
            searchable = f"{message.get('title', '')}\n{message.get('content', '')}".casefold()
            if clean_query and clean_query not in searchable:
                continue
            message["cloud_synced"] = row["sent_at"] is not None
            results.append(message)
            if len(results) >= max(1, min(int(limit), 500)):
                break
        return results


__all__ = ["MXCloudSyncConnector"]
