from __future__ import annotations

import hashlib
import json
from typing import Any


def browser_session_replay_identity(
    *,
    session_id: str,
    actions: list[dict[str, Any]],
    health: dict[str, Any] | None = None,
) -> dict[str, str]:
    fingerprint = _fingerprint(
        {
            "kind": "browser_session",
            "session_id": session_id,
            "health": _normalize_health(health or {}),
            "actions": [_normalize_browser_action(action) for action in actions],
        }
    )
    return {"fingerprint": fingerprint, "case_id": f"browser-session:{fingerprint}"}


def computer_activity_replay_identity(
    *,
    items: list[dict[str, Any]],
    pending_count: int,
) -> dict[str, str]:
    fingerprint = _fingerprint(
        {
            "kind": "computer_activity",
            "pending_count": int(pending_count),
            "items": [_normalize_computer_activity(item) for item in items],
        }
    )
    return {"fingerprint": fingerprint, "case_id": f"computer-activity:{fingerprint}"}


def _fingerprint(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _normalize_health(health: dict[str, Any]) -> dict[str, Any]:
    issues = health.get("issues") if isinstance(health.get("issues"), list) else []
    return {
        "healthy": bool(health.get("healthy")),
        "issues": [str(issue) for issue in issues],
        "score": float(health.get("score") or 0.0),
    }


def _normalize_browser_action(action: dict[str, Any]) -> dict[str, Any]:
    metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}
    return {
        "action": str(action.get("action") or ""),
        "detail": str(action.get("detail") or ""),
        "status": str(action.get("status") or ""),
        "error": str(action.get("error") or ""),
        "metadata": _normalize_mapping(metadata),
    }


def _normalize_computer_activity(item: dict[str, Any]) -> dict[str, Any]:
    action = item.get("action") if isinstance(item.get("action"), dict) else {}
    risk = item.get("risk") if isinstance(item.get("risk"), dict) else {}
    detail = item.get("detail") if isinstance(item.get("detail"), dict) else {}
    proof = item.get("proof") if isinstance(item.get("proof"), dict) else {}
    return {
        "event": str(item.get("event") or ""),
        "ok": bool(item.get("ok")),
        "action": _normalize_mapping(action),
        "risk": _normalize_mapping(risk),
        "error": str(item.get("error") or ""),
        "detail": _normalize_mapping(detail),
        "proof": _normalize_mapping(proof),
    }


def _normalize_mapping(value: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in sorted(value):
        if key in {"created_at", "expires_at", "id", "token", "timestamp"}:
            continue
        normalized[str(key)] = _normalize_value(value[key])
    return normalized


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _normalize_mapping(value)
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if isinstance(value, bool | int | float):
        return value
    if value is None:
        return None
    return str(value)
