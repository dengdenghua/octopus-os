"""Echo appliance tamper-evident administrative audit trail.

The record chain is the official Agent ``AuditChain``. Echo OS adds a signed
tail checkpoint so deleting records from the end is also detectable, and a
small authenticated HTTP surface for the appliance administrator.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from appliance.agent_api.audit import AuditChain, canonical_bytes
from appliance.security import ApplianceAuthenticator, resolve_authenticator

AUDIT_KEY_ID = "echo-appliance-v1"
AUDIT_FILENAME = "appliance-audit.jsonl"
AUDIT_KEYRING_FILENAME = "appliance-audit-keyring.json"
AUDIT_KEYRING_SCHEMA = "echo.appliance-audit-keyring.v1"
AUDIT_ANCHOR_SCHEMA = "echo.appliance-audit-anchor.v1"
AUDIT_ROTATE_ACTION = "audit.key.rotate"
AUDIT_ROTATE_TARGET = "audit-chain"
MAX_AUDIT_KEYS = 64
MAX_KEYRING_BYTES = 64 * 1024
_SENSITIVE_KEYS = ("password", "token", "secret", "authorization", "cookie")
_KEY_ID = re.compile(r"^echo-appliance-k[0-9]+-[0-9a-f]{16}$")


def _derived_key(jwt_secret: str) -> bytes:
    return hmac.new(
        jwt_secret.encode("utf-8"),
        b"echo-os/appliance-audit/v1",
        hashlib.sha256,
    ).digest()


def _chain_key(jwt_secret: str, key_id: str) -> bytes:
    if key_id == AUDIT_KEY_ID:
        return _derived_key(jwt_secret)
    return hmac.new(
        jwt_secret.encode("utf-8"),
        b"echo-os/appliance-audit/key/v2\0" + key_id.encode("ascii"),
        hashlib.sha256,
    ).digest()


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value.encode("ascii") + b"=" * (-len(value) % 4))


def _safe_value(value: Any, *, depth: int = 0) -> Any:
    """Keep audit metadata bounded and remove common credential-shaped keys."""

    if depth >= 4:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:512]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            label = str(key)[:64]
            if any(marker in label.casefold() for marker in _SENSITIVE_KEYS):
                result[label] = "[redacted]"
            else:
                result[label] = _safe_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item, depth=depth + 1) for item in list(value)[:32]]
    return str(value)[:512]


@dataclass(frozen=True)
class ApplianceAuditReport:
    ok: bool
    entries_checked: int
    broken_at: int | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "entriesChecked": self.entries_checked,
            "brokenAt": self.broken_at,
            "error": self.error,
        }


class AuditIntegrityError(RuntimeError):
    """Raised when a mutation is attempted while the audit trail is unhealthy."""


class AuditKeyRotationError(RuntimeError):
    """Raised when a requested audit signing-key transition is unsafe."""


class ApplianceAudit:
    """Append-only action audit backed by Agent's HMAC chain."""

    def __init__(self, path: Path | str, *, jwt_secret: str) -> None:
        if not jwt_secret:
            raise ValueError("jwt_secret is required for appliance audit")
        self.path = Path(path)
        self.checkpoint_path = self.path.with_suffix(f"{self.path.suffix}.checkpoint")
        self.keyring_path = self.path.with_name(AUDIT_KEYRING_FILENAME)
        self._jwt_secret = jwt_secret
        self._key = _derived_key(jwt_secret)
        self._key_ids, self._active_key_id = self._read_keyring()
        self._chain = self._new_chain()
        self._lock = threading.RLock()
        self._known_fingerprints: tuple[tuple[int, int, int] | None, ...] | None = None
        report = self.verify()
        if not report.ok:
            raise AuditIntegrityError(report.error or "appliance audit integrity check failed")

    def _new_chain(self) -> AuditChain:
        return AuditChain(
            self.path,
            keys={key_id: _chain_key(self._jwt_secret, key_id) for key_id in self._key_ids},
            active_key_id=self._active_key_id,
        )

    @staticmethod
    def _fingerprint(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return stat.st_ino, stat.st_size, stat.st_mtime_ns

    def _fingerprints(self) -> tuple[tuple[int, int, int] | None, ...]:
        return (
            self._fingerprint(self.path),
            self._fingerprint(self.checkpoint_path),
            self._fingerprint(self.keyring_path),
        )

    @classmethod
    def from_data_dir(cls, data_dir: Path | str, *, jwt_secret: str) -> ApplianceAudit:
        return cls(Path(data_dir) / AUDIT_FILENAME, jwt_secret=jwt_secret)

    def _checkpoint_mac(self, body: dict[str, Any]) -> str:
        return hmac.new(
            self._key,
            b"echo-os/audit-checkpoint/v1\0" + canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()

    def _keyring_mac(self, body: dict[str, Any]) -> str:
        return hmac.new(
            self._key,
            b"echo-os/audit-keyring/v1\0" + canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()

    def _read_keyring(self) -> tuple[tuple[str, ...], str]:
        if not self.keyring_path.exists():
            return (AUDIT_KEY_ID,), AUDIT_KEY_ID
        try:
            info = self.keyring_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_ISLNK(info.st_mode)
                or info.st_size > MAX_KEYRING_BYTES
            ):
                raise ValueError("unsafe keyring file")
            raw = json.loads(self.keyring_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or set(raw) != {
                "schema",
                "activeKeyId",
                "keyIds",
                "mac",
            }:
                raise ValueError("invalid keyring fields")
            body = {
                "schema": str(raw["schema"]),
                "activeKeyId": str(raw["activeKeyId"]),
                "keyIds": [str(item) for item in raw["keyIds"]],
            }
            supplied_mac = str(raw["mac"])
        except (
            OSError,
            UnicodeError,
            ValueError,
            TypeError,
            KeyError,
            json.JSONDecodeError,
        ) as exc:
            raise AuditIntegrityError("audit keyring is unreadable or invalid") from exc
        key_ids = tuple(body["keyIds"])
        if (
            body["schema"] != AUDIT_KEYRING_SCHEMA
            or not key_ids
            or len(key_ids) > MAX_AUDIT_KEYS
            or key_ids[0] != AUDIT_KEY_ID
            or len(set(key_ids)) != len(key_ids)
            or any(
                key_id != AUDIT_KEY_ID and _KEY_ID.fullmatch(key_id) is None for key_id in key_ids
            )
            or body["activeKeyId"] not in key_ids
            or not hmac.compare_digest(supplied_mac, self._keyring_mac(body))
        ):
            raise AuditIntegrityError("audit keyring integrity check failed")
        return key_ids, body["activeKeyId"]

    def _write_keyring(self, key_ids: tuple[str, ...], active_key_id: str) -> None:
        body = {
            "schema": AUDIT_KEYRING_SCHEMA,
            "activeKeyId": active_key_id,
            "keyIds": list(key_ids),
        }
        payload = {**body, "mac": self._keyring_mac(body)}
        self.keyring_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.keyring_path.name}.",
            dir=self.keyring_path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, self.keyring_path)
            try:
                directory = os.open(self.keyring_path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()

    def _write_checkpoint(self, *, seq: int, mac: str) -> None:
        body = {"seq": seq, "mac": mac, "key_id": AUDIT_KEY_ID}
        payload = {**body, "checkpoint_mac": self._checkpoint_mac(body)}
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{self.checkpoint_path.name}.",
            dir=self.checkpoint_path.parent,
            text=True,
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            temp_path.chmod(0o600)
            os.replace(temp_path, self.checkpoint_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def record(
        self,
        *,
        actor: str,
        action: str,
        target: str,
        outcome: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "actor": str(actor)[:256],
            "action": str(action)[:128],
            "target": str(target)[:512],
            "outcome": str(outcome)[:64],
            "metadata": _safe_value(metadata or {}),
        }
        with self._lock:
            current = self._fingerprints()
            if current != self._known_fingerprints:
                report = self.verify()
                if not report.ok:
                    raise AuditIntegrityError(
                        report.error or "appliance audit integrity check failed"
                    )
            entry = self._chain.append(kind="appliance_action", payload=payload)
            with self.path.open("rb") as audit_file:
                os.fsync(audit_file.fileno())
            self.path.chmod(0o600)
            self._write_checkpoint(seq=entry.seq, mac=entry.mac)
            self._known_fingerprints = self._fingerprints()
            return entry.to_dict()

    def verify(self) -> ApplianceAuditReport:
        with self._lock:
            fingerprints_before = self._fingerprints()
            external_change = fingerprints_before != self._known_fingerprints
            keyring_changed = (
                self._known_fingerprints is not None
                and fingerprints_before[2] != self._known_fingerprints[2]
            )
            if keyring_changed:
                try:
                    self._key_ids, self._active_key_id = self._read_keyring()
                    self._chain = self._new_chain()
                except AuditIntegrityError as exc:
                    return ApplianceAuditReport(
                        ok=False,
                        entries_checked=0,
                        broken_at=0,
                        error=str(exc),
                    )
            report = self._chain.verify()
            if not report.ok:
                return ApplianceAuditReport(
                    ok=False,
                    entries_checked=report.entries_checked,
                    broken_at=report.broken_at,
                    error=report.error,
                )

            tail = self._chain.tail(1)
            if not tail:
                if self.checkpoint_path.exists():
                    return ApplianceAuditReport(
                        ok=False,
                        entries_checked=0,
                        broken_at=0,
                        error="checkpoint exists but audit log is empty",
                    )
                result = ApplianceAuditReport(ok=True, entries_checked=0)
                self._known_fingerprints = fingerprints_before
                if external_change:
                    self._chain = self._new_chain()
                return result

            if not self.checkpoint_path.exists():
                return ApplianceAuditReport(
                    ok=False,
                    entries_checked=report.entries_checked,
                    broken_at=tail[0].seq,
                    error="audit checkpoint missing",
                )
            try:
                raw = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
                body = {
                    "seq": int(raw["seq"]),
                    "mac": str(raw["mac"]),
                    "key_id": str(raw["key_id"]),
                }
                checkpoint_mac = str(raw["checkpoint_mac"])
            except (OSError, ValueError, KeyError, TypeError) as exc:
                return ApplianceAuditReport(
                    ok=False,
                    entries_checked=report.entries_checked,
                    broken_at=tail[0].seq,
                    error=f"invalid audit checkpoint: {type(exc).__name__}",
                )
            if body["key_id"] != AUDIT_KEY_ID or not hmac.compare_digest(
                checkpoint_mac,
                self._checkpoint_mac(body),
            ):
                return ApplianceAuditReport(
                    ok=False,
                    entries_checked=report.entries_checked,
                    broken_at=tail[0].seq,
                    error="audit checkpoint signature mismatch",
                )
            if body["seq"] != tail[0].seq or body["mac"] != tail[0].mac:
                return ApplianceAuditReport(
                    ok=False,
                    entries_checked=report.entries_checked,
                    broken_at=tail[0].seq,
                    error="audit tail does not match signed checkpoint",
                )
            result = ApplianceAuditReport(ok=True, entries_checked=report.entries_checked)
            self._known_fingerprints = fingerprints_before
            if external_change:
                # Another process may have appended a valid entry. Refresh the
                # Agent AuditChain tail cache before our next append.
                self._chain = self._new_chain()
            return result

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            return [entry.to_dict() for entry in self._chain.tail(bounded)]

    def key_status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema": AUDIT_KEYRING_SCHEMA,
                "activeKeyId": self._active_key_id,
                "keyIds": list(self._key_ids),
                "keyCount": len(self._key_ids),
                "maximumKeys": MAX_AUDIT_KEYS,
                "secretsPersisted": False,
            }

    def rotate_key(self, *, actor: str) -> dict[str, Any]:
        with self._lock:
            report = self.verify()
            if not report.ok:
                raise AuditIntegrityError(report.error or "audit integrity check failed")
            if len(self._key_ids) >= MAX_AUDIT_KEYS:
                raise AuditKeyRotationError("audit signing-key rotation limit reached")
            previous = self._active_key_id
            sequence = len(self._key_ids) + 1
            new_key_id = f"echo-appliance-k{sequence}-{secrets.token_hex(8)}"
            key_ids = (*self._key_ids, new_key_id)
            self._write_keyring(key_ids, new_key_id)
            self._key_ids = key_ids
            self._active_key_id = new_key_id
            self._chain = self._new_chain()
            self._known_fingerprints = self._fingerprints()
            entry = self.record(
                actor=actor,
                action=AUDIT_ROTATE_ACTION,
                target=AUDIT_ROTATE_TARGET,
                outcome="succeeded",
                metadata={"previousKeyId": previous, "activeKeyId": new_key_id},
            )
            return {
                **self.key_status(),
                "previousKeyId": previous,
                "rotationEventSeq": entry["seq"],
            }

    @staticmethod
    def _sha256_file(path: Path) -> str | None:
        if not path.exists():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while block := source.read(1024 * 1024):
                digest.update(block)
        return digest.hexdigest()

    def _anchor_signing_key(self) -> Any:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        seed = hmac.new(
            self._jwt_secret.encode("utf-8"),
            b"echo-os/appliance-audit-anchor/ed25519/v1",
            hashlib.sha256,
        ).digest()
        return Ed25519PrivateKey.from_private_bytes(seed)

    def anchor(self) -> dict[str, Any]:
        from cryptography.hazmat.primitives import serialization

        with self._lock:
            report = self.verify()
            if not report.ok:
                raise AuditIntegrityError(report.error or "audit integrity check failed")
            tail = self._chain.tail(1)
            tail_entry = tail[0] if tail else None
            signing_key = self._anchor_signing_key()
            public_key = signing_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            public_key_encoded = _b64url(public_key)
            signing_key_id = f"sha256:{hashlib.sha256(public_key).hexdigest()}"
            body = {
                "schema": AUDIT_ANCHOR_SCHEMA,
                "createdAt": datetime.now(UTC).isoformat(),
                "audit": {
                    "entries": report.entries_checked,
                    "tailSeq": tail_entry.seq if tail_entry is not None else -1,
                    "tailMac": tail_entry.mac if tail_entry is not None else "",
                    "tailKeyId": (
                        tail_entry.key_id if tail_entry is not None else self._active_key_id
                    ),
                    "logSha256": self._sha256_file(self.path),
                    "checkpointSha256": self._sha256_file(self.checkpoint_path),
                    "keyringSha256": self._sha256_file(self.keyring_path),
                },
                "signing": {
                    "algorithm": "Ed25519",
                    "keyId": signing_key_id,
                    "publicKey": public_key_encoded,
                },
            }
            return {**body, "signature": _b64url(signing_key.sign(canonical_bytes(body)))}


def verify_audit_anchor(
    anchor: dict[str, Any],
    *,
    expected_signing_key_id: str | None = None,
) -> dict[str, Any]:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        if set(anchor) != {"schema", "createdAt", "audit", "signing", "signature"}:
            raise ValueError("anchor fields are invalid")
        if anchor["schema"] != AUDIT_ANCHOR_SCHEMA:
            raise ValueError("anchor schema is invalid")
        created_at = datetime.fromisoformat(str(anchor["createdAt"]))
        if created_at.tzinfo is None:
            raise ValueError("anchor timestamp must include a timezone")
        audit = anchor["audit"]
        signing = anchor["signing"]
        if not isinstance(audit, dict) or set(audit) != {
            "entries",
            "tailSeq",
            "tailMac",
            "tailKeyId",
            "logSha256",
            "checkpointSha256",
            "keyringSha256",
        }:
            raise ValueError("anchor audit fields are invalid")
        if not isinstance(signing, dict) or set(signing) != {
            "algorithm",
            "keyId",
            "publicKey",
        }:
            raise ValueError("anchor signing fields are invalid")
        entries = audit["entries"]
        tail_seq = audit["tailSeq"]
        if (
            isinstance(entries, bool)
            or not isinstance(entries, int)
            or entries < 0
            or isinstance(tail_seq, bool)
            or not isinstance(tail_seq, int)
            or tail_seq != entries - 1
        ):
            raise ValueError("anchor sequence is invalid")
        tail_mac = str(audit["tailMac"])
        tail_key_id = str(audit["tailKeyId"])
        if tail_key_id != AUDIT_KEY_ID and _KEY_ID.fullmatch(tail_key_id) is None:
            raise ValueError("anchor tail key ID is invalid")
        if entries == 0:
            if tail_mac:
                raise ValueError("empty anchor has a tail MAC")
        elif re.fullmatch(r"[0-9a-f]{64}", tail_mac) is None:
            raise ValueError("anchor tail MAC is invalid")
        for field in ("logSha256", "checkpointSha256", "keyringSha256"):
            value = audit[field]
            if value is not None and re.fullmatch(r"[0-9a-f]{64}", str(value)) is None:
                raise ValueError(f"anchor {field} is invalid")
        if entries > 0 and (audit["logSha256"] is None or audit["checkpointSha256"] is None):
            raise ValueError("non-empty anchor is missing audit artifact hashes")
        public_key = _b64url_decode(str(signing["publicKey"]))
        signature = _b64url_decode(str(anchor["signature"]))
        signing_key_id = f"sha256:{hashlib.sha256(public_key).hexdigest()}"
        if (
            signing["algorithm"] != "Ed25519"
            or len(public_key) != 32
            or len(signature) != 64
            or signing["keyId"] != signing_key_id
            or (expected_signing_key_id is not None and signing_key_id != expected_signing_key_id)
        ):
            raise ValueError("anchor signing identity is invalid")
        body = {key: anchor[key] for key in ("schema", "createdAt", "audit", "signing")}
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            canonical_bytes(body),
        )
    except (InvalidSignature, OSError, TypeError, ValueError, KeyError) as exc:
        raise AuditIntegrityError("audit anchor verification failed") from exc
    return {
        "ok": True,
        "schema": AUDIT_ANCHOR_SCHEMA,
        "signingKeyId": signing_key_id,
        "entries": entries,
        "tailSeq": tail_seq,
        "tailMac": tail_mac,
        "createdAt": anchor["createdAt"],
    }


def create_audit_router(
    audit: ApplianceAudit,
    *,
    jwt_secret: str | None = None,
    approval: Any | None = None,
    authenticator: ApplianceAuthenticator | None = None,
) -> APIRouter:
    require_auth = resolve_authenticator(
        jwt_secret=jwt_secret, authenticator=authenticator
    ).dependency()
    router = APIRouter(
        prefix="/api/appliance/audit",
        tags=["appliance", "audit"],
        dependencies=[Depends(require_auth)],
    )

    @router.get("/verify")
    def verify_audit() -> dict[str, Any]:
        return audit.verify().to_dict()

    @router.get("/events")
    def recent_events(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        report = audit.verify()
        return {"verification": report.to_dict(), "events": audit.recent(limit)}

    @router.get("/keys")
    def audit_keys() -> dict[str, Any]:
        return audit.key_status()

    @router.get("/anchor")
    def audit_anchor() -> dict[str, Any]:
        try:
            return audit.anchor()
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(
                status_code=503, detail="appliance audit integrity check failed"
            ) from exc

    @router.post("/keys/rotate")
    def rotate_audit_key(
        request: Request,
        actor: str = Depends(require_auth),
    ) -> dict[str, Any]:
        if approval is None:
            raise HTTPException(status_code=503, detail="high-risk approval unavailable")
        from appliance.approval import consume_request_approval

        consume_request_approval(
            request,
            approval,
            actor=actor,
            action=AUDIT_ROTATE_ACTION,
            target=AUDIT_ROTATE_TARGET,
        )
        try:
            return audit.rotate_key(actor=actor)
        except AuditKeyRotationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (OSError, AuditIntegrityError) as exc:
            raise HTTPException(
                status_code=503, detail="appliance audit integrity check failed"
            ) from exc

    return router


__all__ = [
    "AUDIT_FILENAME",
    "AUDIT_ANCHOR_SCHEMA",
    "AUDIT_KEY_ID",
    "AUDIT_KEYRING_FILENAME",
    "AUDIT_KEYRING_SCHEMA",
    "AUDIT_ROTATE_ACTION",
    "AUDIT_ROTATE_TARGET",
    "ApplianceAudit",
    "ApplianceAuditReport",
    "AuditIntegrityError",
    "AuditKeyRotationError",
    "create_audit_router",
    "verify_audit_anchor",
]
