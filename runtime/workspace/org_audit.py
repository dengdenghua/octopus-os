"""Org / permission-change audit log (阶段二 · 协作深化 · 审计日志).

Every mutation of the enterprise org tree — organization / department /
channel creation & deletion, and every **permission change** (org member
add/remove/role-change, channel ACL add/remove/role-change) — is appended to a
tamper-evident HMAC chain so the answer to "谁改了什么权限" is always
recoverable and verifiable.

Design (mirrors ``runtime/safety/evolution/governance_audit.py``):

* The chain is a JSONL file of ``AuditEntry`` records signed with
  HMAC-SHA256 over ``(prev_mac || payload_bytes)``. Deleting, reordering or
  mutating any record breaks every downstream MAC, so the log is append-only
  by construction.
* ``actor`` records *who* performed the change (the authenticated user id from
  the org router). ``detail.before`` / ``detail.after`` capture the state
  transition — for a role change that is the previous vs. new role.
* The chain file is the single query-friendly source of truth; ``list_*``
  reads the tail and ``verify_org_audit_chain`` re-checks every MAC.

The low-level ``OrgStore`` stays I/O-free and actor-free; the router (which
knows the actor) is the audit integration point. See ``org_router.py``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from runtime.safety.audit.audit_chain import AuditChain

_LOG = logging.getLogger("echo.workspace.org_audit")

_ORG_AUDIT_CHAIN_KEY_ID = "org-audit-local-v1"
_ORG_AUDIT_CHAIN_SECRET_ENV = "ECHO_ORG_AUDIT_SECRET"

# Recognized permission/org change event kinds. Kept as a set so callers can
# validate early and the export/verify surface stays explicit.
EVENT_TYPES = frozenset(
    {
        "org_create",
        "org_delete",
        "org_department_create",
        "org_department_delete",
        "org_channel_create",
        "org_channel_delete",
        "org_member_add",
        "org_member_remove",
        "org_member_role_change",
        "channel_member_add",
        "channel_member_remove",
        "channel_member_role_change",
    }
)


def append_org_audit_event(
    *,
    event_type: str,
    actor: str,
    org_id: str,
    target: str,
    channel_id: str = "",
    detail: dict[str, Any] | None = None,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Append one org / permission-change event to the HMAC chain.

    Returns the signed record (with ``audit_chain`` provenance) so callers can
    return it from API handlers or assert on it in tests.
    """
    event = _clean(event_type, limit=80)
    if event not in EVENT_TYPES:
        raise ValueError(f"unknown org audit event_type {event_type!r}")
    actor_name = _clean(actor, limit=120)
    org_name = _clean(org_id, limit=80)
    target_name = _clean(target, limit=80)
    if not org_name:
        raise ValueError("org_id is required")
    if not target_name:
        raise ValueError("target is required")
    now_text = _iso(now)
    record = {
        "id": _event_id(event=event, org=org_name, target=target_name, now_text=now_text),
        "event_type": event,
        "actor": actor_name,
        "org_id": org_name,
        "channel_id": _clean(channel_id, limit=80),
        "target": target_name,
        "detail": detail if isinstance(detail, dict) else {},
        "applied_at": now_text,
    }
    chain = _org_audit_chain(
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


def verify_org_audit_chain(
    *,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Re-verify every MAC in the org audit chain. ``ok`` is True only when the
    whole chain is intact (no mutation, deletion or reordering)."""
    chain = _org_audit_chain(
        audit_chain_path=audit_chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    report = chain.verify(limit=limit)
    return {
        "schema": "echo.org_audit_chain.v1",
        "path": str(chain.path),
        "ok": report.ok,
        "entries_checked": report.entries_checked,
        "broken_at": report.broken_at,
        "error": report.error,
        "details": list(report.details),
    }


def list_org_audit_events(
    *,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
    limit: int = 50,
    event_type: str | None = None,
    org_id: str | None = None,
    actor: str | None = None,
) -> list[dict[str, Any]]:
    """Return matching audit events, newest first.

    ``limit`` caps the number of records inspected from the tail (the chain is
    append-only so the newest records live at the end). Filters are applied
    after a bounded tail scan.
    """
    chain = _org_audit_chain(
        audit_chain_path=audit_chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    entries = chain.tail(max(1, limit))
    result: list[dict[str, Any]] = []
    for entry in reversed(entries):
        payload = entry.payload if isinstance(entry.payload, dict) else {}
        if event_type and payload.get("event_type") != event_type:
            continue
        if org_id and payload.get("org_id") != org_id:
            continue
        if actor and payload.get("actor") != actor:
            continue
        result.append(payload)
    return result


def export_org_audit_bundle(
    *,
    audit_chain_path: str | Path | None = None,
    audit_chain_secret: str | bytes | None = None,
) -> dict[str, Any]:
    """Return a self-contained export bundle: every chain line + integrity
    report, so the entire org audit trail can be handed to a reviewer."""
    chain = _org_audit_chain(
        audit_chain_path=audit_chain_path,
        audit_chain_secret=audit_chain_secret,
    )
    chain_text = chain.path.read_text(encoding="utf-8") if chain.path.exists() else ""
    chain_lines = [line for line in chain_text.splitlines() if line.strip()]
    integrity = verify_org_audit_chain(
        audit_chain_path=chain.path,
        audit_chain_secret=audit_chain_secret,
    )
    return {
        "schema": "echo.org_audit_export.v1",
        "chain_path": str(chain.path),
        "chain_sha256": hashlib.sha256(chain_text.encode("utf-8")).hexdigest(),
        "integrity": integrity,
        "chain": {
            "format": "jsonl",
            "line_count": len(chain_lines),
            "lines": chain_lines,
        },
    }


# ── internals ────────────────────────────────────────────────────────────


def _org_audit_chain(
    *,
    audit_chain_path: str | Path | None,
    audit_chain_secret: str | bytes | None,
) -> AuditChain:
    chain_path = _chain_path(audit_chain_path)
    secret = _chain_secret(chain_path=chain_path, audit_chain_secret=audit_chain_secret)
    return AuditChain(
        path=chain_path,
        keys={_ORG_AUDIT_CHAIN_KEY_ID: secret},
        active_key_id=_ORG_AUDIT_CHAIN_KEY_ID,
    )


def _chain_path(audit_chain_path: str | Path | None) -> Path:
    if audit_chain_path is not None:
        return Path(audit_chain_path)
    from runtime.platform.process.paths import app_paths

    return app_paths().org_audit_chain_path


def _chain_secret(
    *,
    chain_path: Path,
    audit_chain_secret: str | bytes | None,
) -> bytes:
    if isinstance(audit_chain_secret, bytes) and audit_chain_secret:
        return audit_chain_secret
    if isinstance(audit_chain_secret, str) and audit_chain_secret.strip():
        return _secret_text_to_bytes(audit_chain_secret)
    env_secret = os.environ.get(_ORG_AUDIT_CHAIN_SECRET_ENV)
    if env_secret:
        return _secret_text_to_bytes(env_secret)
    from runtime.platform.process.paths import app_paths

    default_paths = app_paths()
    secret_path = (
        default_paths.org_audit_secret_path
        if chain_path == default_paths.org_audit_chain_path
        else chain_path.with_suffix(chain_path.suffix + ".secret")
    )
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
        raise ValueError("org audit secret cannot be empty")
    with_hex_prefix = text[2:] if text.startswith("0x") else text
    try:
        return bytes.fromhex(with_hex_prefix)
    except ValueError:
        return text.encode("utf-8")


def _event_id(*, event: str, org: str, target: str, now_text: str) -> str:
    digest = hashlib.blake2b(
        f"{event}|{org}|{target}|{now_text}".encode(),
        digest_size=10,
    ).hexdigest()
    return f"oa_{digest}"


def _iso(value: datetime | None = None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return current.astimezone(UTC).isoformat()


def _clean(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:limit].rstrip()


__all__ = [
    "EVENT_TYPES",
    "append_org_audit_event",
    "export_org_audit_bundle",
    "list_org_audit_events",
    "verify_org_audit_chain",
]
