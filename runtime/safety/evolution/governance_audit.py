from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.platform.io import atomic_write_json, read_json_with_backup
from runtime.platform.process.paths import app_paths
from runtime.safety.audit.audit_chain import AuditChain, AuditEntry, canonical_bytes

_AUDIT_SCHEMA = "echo.promotion_audit.v1"
_AUDIT_CHAIN_KEY_ID = "governance-local-v1"
_AUDIT_CHAIN_SECRET_ENV = "ECHO_GOVERNANCE_AUDIT_SECRET"


def promotion_audit_signals(row: dict[str, Any]) -> dict[str, bool]:
    context = row.get("decision_context") if isinstance(row.get("decision_context"), dict) else {}
    gate = context.get("replay_gate") if isinstance(context.get("replay_gate"), dict) else {}
    gate_failed = gate.get("passed") is False
    override = context.get("override_replay_gate") is True
    return {
        "failed_apply": str(row.get("status") or "") == "failed",
        "gate_failed": gate_failed,
        "override": override,
        "gate_blocked_override": override and gate_failed,
    }


def append_governance_audit_event(
    *,
    event_type: str,
    target: str,
    status: str,
    artifact: dict[str, Any] | None = None,
    decision_context: dict[str, Any] | None = None,
    agent_id: str = "",
    audit_path: str | Path | None = None,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append a non-promotion governance event to the audit stream.

    The compact JSON audit remains the query-friendly source used by the
    operator panel. A parallel JSONL HMAC chain makes each governance event
    tamper-evident without turning the summary file into an append-only log.
    """
    event = _clean(event_type, limit=80)
    target_name = _clean(target, limit=80)
    status_name = _clean(status, limit=40)
    if not event:
        raise ValueError("event_type is required")
    if not target_name:
        raise ValueError("target is required")
    if not status_name:
        raise ValueError("status is required")
    now_text = _iso(now)
    context = decision_context if isinstance(decision_context, dict) else {}
    payload_artifact = artifact if isinstance(artifact, dict) else {}
    record = {
        "id": _event_id(event_type=event, target=target_name, now_text=now_text),
        "event_type": event,
        "review_queue_item_id": f"event:{event}:{now_text}",
        "agent_id": _clean(agent_id, limit=120),
        "target": target_name,
        "status": status_name,
        "applied_at": now_text,
        "artifact": payload_artifact,
        "decision_context": context,
        "item_snapshot": {},
    }
    path = Path(audit_path) if audit_path is not None else app_paths().promotion_audit_path
    raw = read_json_with_backup(path, default=None)
    payload = _empty_payload()
    if isinstance(raw, dict):
        payload.update(
            {
                "schema": _clean(raw.get("schema"), limit=80) or _AUDIT_SCHEMA,
                "version": int(raw.get("version") or 1),
                "lastUpdated": _clean(raw.get("lastUpdated"), limit=80),
                "records": [row for row in (raw.get("records") or []) if isinstance(row, dict)],
            }
        )
    records = list(payload.get("records") or [])
    records.append(record)
    payload["records"] = records
    payload["lastUpdated"] = now_text
    atomic_write_json(path, payload)
    chain = _governance_audit_chain(
        audit_path=path,
        audit_chain_path=audit_chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    chain_entry = chain.append(kind=event, payload=record)
    record["audit_chain"] = {
        "path": str(chain.path),
        "seq": chain_entry.seq,
        "mac": chain_entry.mac,
    }
    return record


def verify_governance_audit_chain(
    *,
    audit_path: str | Path | None = None,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Verify the tamper-evident governance audit chain."""
    path = Path(audit_path) if audit_path is not None else app_paths().promotion_audit_path
    chain_path = _governance_audit_chain_path(
        audit_path=path,
        audit_chain_path=audit_chain_path,
    )
    governance_records = _governance_audit_records(path)
    if not chain_path.exists():
        if governance_records:
            return {
                "schema": "echo.governance_audit_chain.v1",
                "path": str(chain_path),
                "ok": False,
                "entries_checked": 0,
                "broken_at": None,
                "error": (
                    "missing governance audit chain for "
                    f"{len(governance_records)} governance event(s)"
                ),
                "details": [],
            }
        return {
            "schema": "echo.governance_audit_chain.v1",
            "path": str(chain_path),
            "ok": True,
            "entries_checked": 0,
            "broken_at": None,
            "error": "",
            "details": [],
        }
    chain = _governance_audit_chain(
        audit_path=path,
        audit_chain_path=chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    report = chain.verify(limit=limit)
    consistency_error = ""
    if report.ok and limit is None:
        consistency_error = _governance_audit_consistency_error(
            chain_path=chain.path,
            records=governance_records,
        )
    return {
        "schema": "echo.governance_audit_chain.v1",
        "path": str(chain.path),
        "ok": report.ok and not consistency_error,
        "entries_checked": report.entries_checked,
        "broken_at": report.broken_at,
        "error": report.error or consistency_error,
        "details": list(report.details),
    }


def export_governance_audit_bundle(
    *,
    audit_path: str | Path | None = None,
    audit_chain_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return a self-contained governance audit export bundle."""
    path = Path(audit_path) if audit_path is not None else app_paths().promotion_audit_path
    chain_path = _governance_audit_chain_path(
        audit_path=path,
        audit_chain_path=audit_chain_path,
    )
    raw = read_json_with_backup(path, default=None)
    audit_payload = raw if isinstance(raw, dict) else _empty_payload()
    chain_text = chain_path.read_text(encoding="utf-8") if chain_path.exists() else ""
    chain_lines = [line for line in chain_text.splitlines() if line.strip()]
    integrity = verify_governance_audit_chain(
        audit_path=path,
        audit_chain_path=chain_path,
    )
    return {
        "schema": "echo.governance_audit_export.v1",
        "audit_path": str(path),
        "chain_path": str(chain_path),
        "audit_sha256": hashlib.sha256(
            canonical_bytes(audit_payload),
        ).hexdigest(),
        "chain_sha256": hashlib.sha256(chain_text.encode("utf-8")).hexdigest(),
        "integrity": integrity,
        "audit": audit_payload,
        "chain": {
            "format": "jsonl",
            "line_count": len(chain_lines),
            "lines": chain_lines,
        },
    }


def _empty_payload() -> dict[str, Any]:
    return {
        "schema": _AUDIT_SCHEMA,
        "version": 1,
        "lastUpdated": "",
        "records": [],
    }


def _governance_audit_records(path: Path) -> list[dict[str, Any]]:
    raw = read_json_with_backup(path, default=None)
    if not isinstance(raw, dict):
        return []
    records = raw.get("records")
    if not isinstance(records, list):
        return []
    return [
        row for row in records if isinstance(row, dict) and _clean(row.get("event_type"), limit=80)
    ]


def _governance_audit_consistency_error(
    *,
    chain_path: Path,
    records: list[dict[str, Any]],
) -> str:
    if not records:
        return ""
    chain_payloads: dict[str, dict[str, Any]] = {}
    with chain_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                entry = AuditEntry.from_dict(json.loads(line))
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            payload = entry.payload if isinstance(entry.payload, dict) else {}
            event_id = _clean(payload.get("id"), limit=100)
            if event_id:
                chain_payloads[event_id] = payload
    missing = [
        _clean(row.get("id"), limit=100) or "<missing-id>"
        for row in records
        if (_clean(row.get("id"), limit=100) or "<missing-id>") not in chain_payloads
    ]
    if missing:
        return f"missing chain payload for governance event(s): {', '.join(missing[:5])}"
    mismatched: list[str] = []
    for row in records:
        event_id = _clean(row.get("id"), limit=100)
        if not event_id:
            continue
        chain_payload = chain_payloads.get(event_id)
        if not chain_payload:
            continue
        if canonical_bytes(_chain_comparable_record(row)) != canonical_bytes(
            _chain_comparable_record(chain_payload),
        ):
            mismatched.append(event_id)
    if mismatched:
        return f"governance audit payload mismatch: {', '.join(mismatched[:5])}"
    return ""


def _chain_comparable_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _clean(row.get("id"), limit=100),
        "event_type": _clean(row.get("event_type"), limit=80),
        "review_queue_item_id": _clean(row.get("review_queue_item_id"), limit=120),
        "agent_id": _clean(row.get("agent_id"), limit=120),
        "target": _clean(row.get("target"), limit=80),
        "status": _clean(row.get("status"), limit=40),
        "applied_at": _clean(row.get("applied_at"), limit=80),
        "artifact": row.get("artifact") if isinstance(row.get("artifact"), dict) else {},
        "decision_context": (
            row.get("decision_context") if isinstance(row.get("decision_context"), dict) else {}
        ),
        "item_snapshot": (
            row.get("item_snapshot") if isinstance(row.get("item_snapshot"), dict) else {}
        ),
    }


def _governance_audit_chain(
    *,
    audit_path: Path,
    audit_chain_path: str | Path | None,
    audit_chain_secret: str | bytes | None,
) -> AuditChain:
    chain_path = _governance_audit_chain_path(
        audit_path=audit_path,
        audit_chain_path=audit_chain_path,
    )
    secret = _governance_audit_secret(
        chain_path=chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    return AuditChain(
        path=chain_path,
        keys={_AUDIT_CHAIN_KEY_ID: secret},
        active_key_id=_AUDIT_CHAIN_KEY_ID,
    )


def _governance_audit_chain_path(
    *,
    audit_path: Path,
    audit_chain_path: str | Path | None,
) -> Path:
    if audit_chain_path is not None:
        return Path(audit_chain_path)
    default_paths = app_paths()
    if audit_path == default_paths.promotion_audit_path:
        return default_paths.governance_audit_chain_path
    return audit_path.with_name(f"{audit_path.stem}_chain.jsonl")


def _governance_audit_secret(
    *,
    chain_path: Path,
    audit_chain_secret: str | bytes | None,
) -> bytes:
    if isinstance(audit_chain_secret, bytes) and audit_chain_secret:
        return audit_chain_secret
    if isinstance(audit_chain_secret, str) and audit_chain_secret.strip():
        return _secret_text_to_bytes(audit_chain_secret)
    env_secret = os.environ.get(_AUDIT_CHAIN_SECRET_ENV)
    if env_secret:
        return _secret_text_to_bytes(env_secret)
    default_paths = app_paths()
    if chain_path == default_paths.governance_audit_chain_path:
        secret_path = default_paths.governance_audit_secret_path
    else:
        secret_path = chain_path.with_suffix(chain_path.suffix + ".secret")
    return _read_or_create_secret(secret_path)


def _read_or_create_secret(path: Path) -> bytes:
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return _secret_text_to_bytes(text)
    except FileNotFoundError:  # expected · no secret on disk yet, fall through to mint one
        pass
    generated = secrets.token_hex(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        text = path.read_text(encoding="utf-8").strip()
        return _secret_text_to_bytes(text)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(generated)
        handle.write("\n")
    return bytes.fromhex(generated)


def _secret_text_to_bytes(value: str) -> bytes:
    text = value.strip()
    if not text:
        raise ValueError("governance audit secret cannot be empty")
    with_hex_prefix = text[2:] if text.startswith("0x") else text
    try:
        return bytes.fromhex(with_hex_prefix)
    except ValueError:
        return text.encode("utf-8")


def _event_id(*, event_type: str, target: str, now_text: str) -> str:
    digest = hashlib.blake2b(
        f"{event_type}|{target}|{now_text}".encode(),
        digest_size=10,
    ).hexdigest()
    return f"ga_{digest}"


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()
