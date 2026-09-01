"""Durable, privacy-bounded snapshots for public thread sharing.

The public token is a capability: anonymous readers can only retrieve the
sanitised snapshot captured when the owner shared it. Live thread state,
tool arguments, reasoning, workspace paths and ownership metadata never cross
this boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")
_TOKEN_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)(\b(?:api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)[^\s,;]+"),
)
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![\w:])/(?:Users|home)/[^\s<>'\"]+"),
    re.compile(r"(?i)(?<!\w)(?:[a-z]:[\\/]|\\\\)[^\s<>'\"]+"),
)
_MAX_MESSAGE_CHARS = 60_000
_MAX_TOTAL_CHARS = 1_000_000
_MAX_MESSAGES = 200
_MAX_ARTIFACTS = 50
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_DEFAULT_MAX_ACTIVE_PER_OWNER = 100
_DEFAULT_MAX_SNAPSHOT_BYTES = 1_200_000


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _utc_after(seconds: int) -> str:
    return (
        (datetime.now(UTC) + timedelta(seconds=max(60, int(seconds))))
        .isoformat()
        .replace("+00:00", "Z")
    )


def _parse_utc(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _sanitise_text(value: Any, *, limit: int = _MAX_MESSAGE_CHARS) -> str:
    text = _CONTROL_RE.sub("", str(value or "")).strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(r"\1[已隐藏]", text) if pattern.groups else pattern.sub("[已隐藏]", text)
    for pattern in _LOCAL_PATH_PATTERNS:
        text = pattern.sub("[本地路径已隐藏]", text)
    return text if len(text) <= limit else f"{text[:limit]}\n…（分享内容已截断）"


def _public_basename(value: Any) -> str:
    """Return only the final component of either a POSIX or Windows path."""
    raw = str(value or "").strip().rstrip("/\\")
    return re.split(r"[/\\]", raw)[-1] if raw else ""


def _token_hash(token: str) -> str:
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid share token")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _message_role(raw_type: Any) -> str | None:
    kind = str(raw_type or "").strip().lower()
    if kind in {"human", "user"}:
        return "user"
    if kind in {"ai", "assistant"}:
        return "assistant"
    return None


def _public_message_text(value: Any) -> str:
    """Allow only explicit text blocks from structured model content."""
    if isinstance(value, str):
        return value
    blocks = value if isinstance(value, list) else [value]
    text_parts: list[str] = []
    allowed_types = {"", "text", "input_text", "output_text", "inputtext", "outputtext"}
    for block in blocks:
        if isinstance(block, str):
            text_parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").strip().lower()
        if kind not in allowed_types:
            continue
        text = block.get("text")
        if not isinstance(text, str) and kind == "":
            text = block.get("content")
        if isinstance(text, str) and text.strip():
            text_parts.append(text)
    return "\n".join(text_parts)


def _public_artifact_name(value: Any) -> str:
    if isinstance(value, str):
        return _public_basename(value)
    if isinstance(value, dict):
        for key in ("name", "file_name", "filename", "path"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return _public_basename(candidate)
    return ""


def build_public_thread_snapshot(
    thread: dict[str, Any],
    state: dict[str, Any],
) -> dict[str, Any]:
    """Project a thread state onto the intentionally small public contract."""
    raw_thread_values = thread.get("values")
    raw_state_values = state.get("values")
    thread_values: dict[str, Any] = raw_thread_values if isinstance(raw_thread_values, dict) else {}
    state_values: dict[str, Any] = raw_state_values if isinstance(raw_state_values, dict) else {}
    raw_messages = state_values.get("messages")
    if not isinstance(raw_messages, list):
        raw_messages = thread_values.get("messages")
    raw_messages = raw_messages if isinstance(raw_messages, list) else []

    messages: list[dict[str, str]] = []
    total_chars = 0
    for raw in raw_messages:
        if not isinstance(raw, dict):
            continue
        role = _message_role(raw.get("type") or raw.get("role"))
        if role is None:
            continue
        content = _sanitise_text(_public_message_text(raw.get("content")))
        if not content:
            continue
        if total_chars + len(content) > _MAX_TOTAL_CHARS:
            break
        messages.append({"role": role, "content": content})
        total_chars += len(content)

    # Match WorkBuddy's bounded-share principle: retain at most the latest
    # 50 user turns (plus their assistant replies), with a hard message cap.
    user_indexes = [index for index, item in enumerate(messages) if item["role"] == "user"]
    if len(user_indexes) > 50:
        messages = messages[user_indexes[-50] :]
    messages = messages[-_MAX_MESSAGES:]
    if not messages:
        raise ValueError("thread has no shareable messages")

    title = _sanitise_text(
        thread_values.get("title") or state_values.get("title"),
        limit=160,
    )
    if not title:
        first_user = next((item["content"] for item in messages if item["role"] == "user"), "")
        title = first_user[:80].strip() or "EchoAI 分享任务"

    raw_artifacts = state_values.get("artifacts")
    if not isinstance(raw_artifacts, list):
        raw_artifacts = thread_values.get("artifacts")
    artifacts: list[str] = []
    seen: set[str] = set()
    for raw in raw_artifacts if isinstance(raw_artifacts, list) else []:
        # Only the final basename is public; absolute workspace paths are not.
        name = _sanitise_text(_public_artifact_name(raw), limit=160)
        if name and name not in seen:
            artifacts.append(name)
            seen.add(name)
        if len(artifacts) >= _MAX_ARTIFACTS:
            break

    return {
        "title": title,
        "messages": messages,
        "artifacts": artifacts,
        "stats": {
            "turns": sum(1 for item in messages if item["role"] == "user"),
            "messages": len(messages),
            "artifacts": len(artifacts),
        },
    }


class ThreadShareStore:
    """Small file-backed share store with hashed capability-token lookup."""

    def __init__(
        self,
        root: Path | str,
        *,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        max_active_per_owner: int = _DEFAULT_MAX_ACTIVE_PER_OWNER,
        max_snapshot_bytes: int = _DEFAULT_MAX_SNAPSHOT_BYTES,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with suppress(OSError):
            self.root.chmod(0o700)
        self.ttl_seconds = min(365 * 24 * 60 * 60, max(60, int(ttl_seconds)))
        self.max_active_per_owner = max(1, int(max_active_per_owner))
        self.max_snapshot_bytes = max(1024, int(max_snapshot_bytes))
        self._lock = threading.RLock()

    def _path_for_hash(self, token_hash: str) -> Path:
        if not _TOKEN_HASH_RE.fullmatch(token_hash):
            raise ValueError("invalid share token hash")
        return self.root / f"{token_hash}.json"

    def _path(self, token: str) -> Path:
        return self._path_for_hash(_token_hash(token))

    def _write(self, record: dict[str, Any]) -> None:
        # Defense in depth: capability secrets must never cross the durable
        # boundary, even if a caller accidentally leaves one on the mapping.
        persisted = {key: value for key, value in record.items() if key != "token"}
        token_hash = str(persisted["token_hash"])
        target = self._path_for_hash(token_hash)
        temporary = self.root / f".{token_hash}.{os.getpid()}.tmp"
        temporary.write_text(
            json.dumps(persisted, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        with suppress(OSError):
            temporary.chmod(0o600)
        os.replace(temporary, target)

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _is_expired(record: dict[str, Any], *, now: datetime | None = None) -> bool:
        expires_at = _parse_utc(record.get("expires_at"))
        return expires_at is not None and expires_at <= (now or datetime.now(UTC))

    def cleanup(self) -> int:
        """Physically remove expired and already-revoked snapshots."""
        removed = 0
        now = datetime.now(UTC)
        with self._lock:
            for path in self.root.glob("*.json"):
                record = self._read(path)
                if record is None or record.get("revoked_at") or self._is_expired(record, now=now):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
                    removed += 1
        return removed

    def _owner_records(self, *, actor_id: str, tenant_id: str) -> list[dict[str, Any]]:
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        for path in self.root.glob("*.json"):
            record = self._read(path)
            if (
                not record
                or record.get("actor_id", "") != actor_id
                or record.get("tenant_id", "") != tenant_id
                or record.get("revoked_at")
                or self._is_expired(record, now=now)
            ):
                continue
            records.append(record)
        return records

    def create(
        self,
        *,
        thread_id: str,
        actor_id: str,
        tenant_id: str,
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        canonical = json.dumps(
            snapshot,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot_bytes = len(canonical.encode("utf-8"))
        if snapshot_bytes > self.max_snapshot_bytes:
            raise ValueError("share snapshot is too large")
        snapshot_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        with self._lock:
            self.cleanup()
            if (
                len(self._owner_records(actor_id=actor_id, tenant_id=tenant_id))
                >= self.max_active_per_owner
            ):
                raise RuntimeError("active share quota exceeded")
            # A capability cannot be recovered from its digest, so creation is
            # deliberately never "reused". Each call mints one fresh secret and
            # returns it exactly once to the authenticated caller.
            for _attempt in range(8):
                token = secrets.token_urlsafe(32)
                token_hash = _token_hash(token)
                if self._path_for_hash(token_hash).exists():
                    continue
                record = {
                    "schema": "echo.thread-share.v1",
                    "share_id": "shr_" + secrets.token_urlsafe(12),
                    "token_hash": token_hash,
                    "thread_id": thread_id,
                    "actor_id": actor_id,
                    "tenant_id": tenant_id,
                    "created_at": _utc_now(),
                    "expires_at": _utc_after(self.ttl_seconds),
                    "revoked_at": None,
                    "snapshot_hash": snapshot_hash,
                    "snapshot": snapshot,
                }
                self._write(record)
                return {**record, "token": token}
        raise RuntimeError("could not mint a unique share token")

    def get(self, token: str, *, include_revoked: bool = False) -> dict[str, Any] | None:
        try:
            path = self._path(token)
        except ValueError:
            return None
        with self._lock:
            record = self._read(path)
        expected_hash = path.stem
        stored_hash = str(record.get("token_hash") or "") if record else ""
        if not record or not secrets.compare_digest(stored_hash, expected_hash):
            return None
        if (record.get("revoked_at") or self._is_expired(record)) and not include_revoked:
            return None
        return record

    def revoke(self, token: str, *, actor_id: str, tenant_id: str) -> bool:
        with self._lock:
            record = self.get(token, include_revoked=True)
            if not record or record.get("revoked_at"):
                return False
            if record.get("actor_id", "") != actor_id or record.get("tenant_id", "") != tenant_id:
                return False
            self._path_for_hash(str(record["token_hash"])).unlink(missing_ok=True)
            return True

    def revoke_by_id(self, share_id: str, *, actor_id: str, tenant_id: str) -> bool:
        clean_id = str(share_id or "").strip()
        if not clean_id.startswith("shr_") or len(clean_id) > 80:
            return False
        with self._lock:
            for record in self._owner_records(actor_id=actor_id, tenant_id=tenant_id):
                if not secrets.compare_digest(str(record.get("share_id") or ""), clean_id):
                    continue
                self._path_for_hash(str(record["token_hash"])).unlink(missing_ok=True)
                return True
        return False

    def list_for_thread(
        self,
        *,
        thread_id: str,
        actor_id: str,
        tenant_id: str,
    ) -> list[dict[str, Any]]:
        with self._lock:
            records = [
                record
                for record in self._owner_records(actor_id=actor_id, tenant_id=tenant_id)
                if record.get("thread_id") == thread_id
            ]
        records.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        output: list[dict[str, Any]] = []
        for record in records:
            raw_snapshot = record.get("snapshot")
            snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}
            output.append(
                {
                    "share_id": record.get("share_id"),
                    "created_at": record.get("created_at"),
                    "expires_at": record.get("expires_at"),
                    "title": snapshot.get("title"),
                    "stats": snapshot.get("stats")
                    if isinstance(snapshot.get("stats"), dict)
                    else {},
                }
            )
        return output

    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        raw_snapshot = record.get("snapshot")
        snapshot: dict[str, Any] = raw_snapshot if isinstance(raw_snapshot, dict) else {}
        return {
            "schema": record.get("schema"),
            "created_at": record.get("created_at"),
            "expires_at": record.get("expires_at"),
            **snapshot,
        }


__all__ = ["ThreadShareStore", "build_public_thread_snapshot"]
