"""Validation and row-codec helpers for the control-session store.

Pure functions only: id/text/JSON validation, enum guards for surfaces and
statuses, and sqlite row -> dict mappers. Split from ``control_sessions``
so the store module stays under the god-file threshold.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

_SURFACES = frozenset({"browser", "chrome", "electron_webview", "backend_preview", "computer"})
_SESSION_STATUSES = frozenset(
    {"idle", "running", "paused", "awaiting_confirmation", "stopped", "expired"}
)
_ACTION_STATUSES = frozenset(
    {"queued", "running", "waiting_user", "done", "failed", "cancelled", "expired"}
)
_EVIDENCE_KINDS = frozenset({"action", "result", "screenshot", "dom", "lease", "log"})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,239}$")
_MAX_JSON_BYTES = 1024 * 1024
_MAX_TEXT = 65_536
_BLOB_PREVIEW_BYTES = 16 * 1024


def _require_id(value: object, *, label: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(
            f"invalid {label}: use 1-240 letters, numbers, dot, underscore, colon, @, or hyphen"
        )
    return text


def _optional_text(value: object, *, max_len: int = _MAX_TEXT) -> str:
    text = str(value or "").strip()
    if len(text) > max_len or any(ord(ch) < 32 and ch not in "\n\r\t" for ch in text):
        raise ValueError("invalid text payload")
    return text


def _json_dict(value: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    try:
        blob = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: not JSON serializable") from exc
    if len(blob.encode("utf-8")) > _MAX_JSON_BYTES:
        raise ValueError(f"invalid {label}: JSON payload exceeds {_MAX_JSON_BYTES} bytes")
    data = json.loads(blob)
    return data if isinstance(data, dict) else {}


def _dump(value: dict[str, Any], *, label: str) -> str:
    return json.dumps(_json_dict(value, label=label), ensure_ascii=False, sort_keys=True)


def _dump_loose(value: dict[str, Any]) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return json.dumps({"value": str(value)}, ensure_ascii=False, sort_keys=True)


def _load(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _surface(value: object) -> str:
    text = str(value or "browser").strip()
    if text not in _SURFACES:
        raise ValueError(f"invalid control surface: {text}")
    return text


def _session_status(value: object) -> str:
    text = str(value or "idle").strip()
    if text not in _SESSION_STATUSES:
        raise ValueError(f"invalid control session status: {text}")
    return text


def _action_status(value: object) -> str:
    text = str(value or "queued").strip()
    if text not in _ACTION_STATUSES:
        raise ValueError(f"invalid control action status: {text}")
    return text


def _evidence_kind(value: object) -> str:
    text = str(value or "log").strip()
    if text not in _EVIDENCE_KINDS:
        raise ValueError(f"invalid control evidence kind: {text}")
    return text


def _row_to_session(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "session_id": row[0],
        "owner_id": row[1],
        "owner_label": row[2],
        "surface": row[3],
        "target_id": row[4],
        "status": row[5],
        "paused": bool(row[6]),
        "takeover_count": int(row[7] or 0),
        "metadata": _load(row[8]),
        "created_at": float(row[9]),
        "updated_at": float(row[10]),
        "expires_at": float(row[11]) if row[11] is not None else None,
        "creator_actor": row[12] if len(row) > 12 else None,
    }


def _row_to_action(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    return {
        "action_id": row[0],
        "session_id": row[1],
        "surface": row[2],
        "target_id": row[3],
        "action_type": row[4],
        "status": row[5],
        "descriptor": _load(row[6]),
        "result": _load(row[7]),
        "error": row[8] or "",
        "queued_at": float(row[9]),
        "started_at": float(row[10]) if row[10] is not None else None,
        "completed_at": float(row[11]) if row[11] is not None else None,
        "expires_at": float(row[12]) if row[12] is not None else None,
    }


def _row_to_evidence(row: sqlite3.Row | tuple[Any, ...]) -> dict[str, Any]:
    ok = row[6]
    return {
        "evidence_id": row[0],
        "session_id": row[1],
        "action_id": row[2] or "",
        "seq": int(row[3]),
        "kind": row[4],
        "action": row[5] or "",
        "ok": None if ok is None else bool(ok),
        "summary": row[7] or "",
        "detail": _load(row[8]),
        "created_at": float(row[9]),
    }
