from __future__ import annotations

import base64
import hashlib
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json

TRUST_STORE_SCHEMA = "echo.plugin_publisher_trust_store.v1"
_REPORT_SCHEMA = "echo.plugin_publisher_trust_report.v1"
_LOCK = threading.RLock()


def inspect_publisher_trust_store(
    path: str | Path,
    *,
    now: datetime | None = None,
    rotation_days: int = 90,
) -> dict[str, Any]:
    """Return an operator-safe view of publisher keys and rotation health."""

    store_path = Path(path).expanduser()
    payload = _read_store(store_path, allow_missing=True)
    current = now or datetime.now(UTC)
    publishers: list[dict[str, Any]] = []
    for publisher in payload.get("publishers", []):
        if not isinstance(publisher, dict):
            continue
        keys = [
            _key_report(key, now=current, rotation_days=rotation_days)
            for key in publisher.get("keys", [])
            if isinstance(key, dict)
        ]
        publishers.append(
            {
                "publisher_id": _required_text(publisher.get("publisher_id"), "publisher_id"),
                "display_name": str(publisher.get("display_name") or ""),
                "active_key_count": sum(1 for key in keys if key["status"] == "active"),
                "rotation_due_count": sum(1 for key in keys if key["rotation_due"]),
                "keys": keys,
            }
        )
    key_count = sum(len(row["keys"]) for row in publishers)
    active_count = sum(row["active_key_count"] for row in publishers)
    rotation_due_count = sum(row["rotation_due_count"] for row in publishers)
    return {
        "schema": _REPORT_SCHEMA,
        "path": str(store_path),
        "exists": store_path.is_file(),
        "publisher_count": len(publishers),
        "key_count": key_count,
        "active_key_count": active_count,
        "revoked_key_count": sum(
            1 for row in publishers for key in row["keys"] if key["status"] == "revoked"
        ),
        "rotation_due_count": rotation_due_count,
        "ready": bool(publishers) and active_count >= len(publishers) and rotation_due_count == 0,
        "publishers": publishers,
        "next_actions": _next_actions(publishers, exists=store_path.is_file()),
    }


def rotate_publisher_key(
    path: str | Path,
    *,
    publisher_id: str,
    new_key_id: str,
    new_public_key: str,
    previous_key_id: str | None = None,
    reason: str = "scheduled rotation",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Install a new key and retire the previous key in one atomic update."""

    store_path = Path(path).expanduser()
    publisher_name = _required_text(publisher_id, "publisher_id")
    key_name = _required_text(new_key_id, "new_key_id")
    public_key = _valid_public_key(new_public_key)
    previous_name = str(previous_key_id or "").strip()
    timestamp = _iso(now)
    with _LOCK:
        payload = _read_store(store_path, allow_missing=True)
        publisher = _publisher(payload, publisher_name, create=True)
        keys = publisher.setdefault("keys", [])
        if any(str(key.get("key_id") or "") == key_name for key in keys if isinstance(key, dict)):
            raise ValueError("new_key_id already exists for publisher")
        if previous_name:
            previous = _key(keys, previous_name)
            if str(previous.get("status") or "active").lower() != "active":
                raise ValueError("previous publisher key is not active")
            previous.update(
                {
                    "status": "retired",
                    "retired_at": timestamp,
                    "retirement_reason": _clean_reason(reason),
                    "replaced_by": key_name,
                }
            )
        keys.append(
            {
                "key_id": key_name,
                "algorithm": "ed25519",
                "status": "active",
                "public_key": public_key,
                "created_at": timestamp,
                "replaces": previous_name,
            }
        )
        payload["updated_at"] = timestamp
        atomic_write_json(store_path, payload)
    return {
        "schema": "echo.plugin_publisher_key_rotation.v1",
        "publisher_id": publisher_name,
        "new_key_id": key_name,
        "previous_key_id": previous_name,
        "status": "rotated",
        "public_key_fingerprint": _fingerprint(public_key),
        "applied_at": timestamp,
        "trust": inspect_publisher_trust_store(store_path, now=now),
    }


def revoke_publisher_key(
    path: str | Path,
    *,
    publisher_id: str,
    key_id: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Revoke a key atomically while preserving its audit history."""

    store_path = Path(path).expanduser()
    publisher_name = _required_text(publisher_id, "publisher_id")
    key_name = _required_text(key_id, "key_id")
    revocation_reason = _required_text(reason, "reason")
    timestamp = _iso(now)
    with _LOCK:
        payload = _read_store(store_path, allow_missing=False)
        publisher = _publisher(payload, publisher_name, create=False)
        key = _key(publisher.get("keys", []), key_name)
        if str(key.get("status") or "active").lower() == "revoked":
            raise ValueError("publisher key is already revoked")
        key.update(
            {
                "status": "revoked",
                "revoked_at": timestamp,
                "revocation_reason": _clean_reason(revocation_reason),
            }
        )
        payload["updated_at"] = timestamp
        atomic_write_json(store_path, payload)
    return {
        "schema": "echo.plugin_publisher_key_revocation.v1",
        "publisher_id": publisher_name,
        "key_id": key_name,
        "status": "revoked",
        "reason": _clean_reason(revocation_reason),
        "applied_at": timestamp,
        "trust": inspect_publisher_trust_store(store_path, now=now),
    }


def _read_store(path: Path, *, allow_missing: bool) -> dict[str, Any]:
    if not path.is_file():
        if allow_missing:
            return {"schema": TRUST_STORE_SCHEMA, "publishers": []}
        raise ValueError("publisher trust store is unavailable")
    import json

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publisher trust store is malformed") from exc
    if not isinstance(payload, dict) or payload.get("schema") != TRUST_STORE_SCHEMA:
        raise ValueError("publisher trust store schema is unsupported")
    if not isinstance(payload.get("publishers"), list):
        raise ValueError("publisher trust store publishers must be a list")
    return payload


def _publisher(payload: dict[str, Any], publisher_id: str, *, create: bool) -> dict[str, Any]:
    for row in payload["publishers"]:
        if isinstance(row, dict) and str(row.get("publisher_id") or "") == publisher_id:
            if not isinstance(row.get("keys", []), list):
                raise ValueError("publisher keys must be a list")
            return row
    if not create:
        raise ValueError("publisher is not trusted")
    row: dict[str, Any] = {"publisher_id": publisher_id, "keys": []}
    payload["publishers"].append(row)
    return row


def _key(keys: list[Any], key_id: str) -> dict[str, Any]:
    for row in keys:
        if isinstance(row, dict) and str(row.get("key_id") or "") == key_id:
            return row
    raise ValueError("publisher key was not found")


def _key_report(key: dict[str, Any], *, now: datetime, rotation_days: int) -> dict[str, Any]:
    public_key = str(key.get("public_key") or "")
    created = _parse_time(str(key.get("created_at") or ""))
    age_days = max(0, (now - created).days) if created is not None else None
    status = str(key.get("status") or "active").lower()
    return {
        "key_id": str(key.get("key_id") or ""),
        "algorithm": str(key.get("algorithm") or "ed25519").lower(),
        "status": status,
        "public_key_fingerprint": _fingerprint(public_key) if public_key else "",
        "created_at": str(key.get("created_at") or ""),
        "age_days": age_days,
        "rotation_due": status == "active" and age_days is not None and age_days >= rotation_days,
        "replaces": str(key.get("replaces") or ""),
        "replaced_by": str(key.get("replaced_by") or ""),
        "retired_at": str(key.get("retired_at") or ""),
        "revoked_at": str(key.get("revoked_at") or ""),
        "revocation_reason": str(key.get("revocation_reason") or ""),
    }


def _next_actions(publishers: list[dict[str, Any]], *, exists: bool) -> list[str]:
    if not exists:
        return ["Create the publisher trust store and register a release key."]
    actions: list[str] = []
    for publisher in publishers:
        if publisher["active_key_count"] == 0:
            actions.append(f"Register an active key for {publisher['publisher_id']}.")
        if publisher["rotation_due_count"]:
            actions.append(
                f"Rotate {publisher['rotation_due_count']} key(s) for {publisher['publisher_id']}."
            )
    return actions


def _valid_public_key(value: str) -> str:
    text = _required_text(value, "new_public_key")
    try:
        decoded = base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("new_public_key must be valid base64") from exc
    if len(decoded) != 32:
        raise ValueError("new_public_key must contain a 32-byte Ed25519 public key")
    return text


def _fingerprint(public_key: str) -> str:
    try:
        raw = base64.b64decode(public_key, validate=True)
    except (ValueError, TypeError):
        return "invalid"
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:16]


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    if len(text) > 160:
        raise ValueError(f"{name} is too long")
    return text


def _clean_reason(value: str) -> str:
    return " ".join(value.split())[:500]


def _iso(now: datetime | None) -> str:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


__all__ = [
    "TRUST_STORE_SCHEMA",
    "inspect_publisher_trust_store",
    "revoke_publisher_key",
    "rotate_publisher_key",
]
