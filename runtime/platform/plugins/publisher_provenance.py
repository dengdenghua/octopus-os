"""Plugin publisher provenance verification.

Validates the Ed25519-signed ``.codex-plugin/provenance.json`` envelope that
attests which operator-trusted publisher published a given plugin artifact, so
the plugin registry can reject content whose publisher can't be traced to the
operator-controlled trust store. Leaf module: depends only on cryptography and
``project_root``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from runtime.platform.process.paths import project_root

SIGNATURE_RELATIVE_PATH = Path(".codex-plugin/provenance.json")
_ENVELOPE_SCHEMA = "echo.plugin_publisher_signature.v1"
_PAYLOAD_SCHEMA = "echo.plugin_publisher_signature_payload.v1"
_TRUST_STORE_SCHEMA = "echo.plugin_publisher_trust_store.v1"


def canonical_publisher_signature_payload(
    *,
    plugin_id: str,
    version: str,
    content_digest: str,
    publisher_id: str,
    key_id: str,
) -> bytes:
    """Return the exact bytes covered by an Ed25519 plugin signature."""

    payload = {
        "content_digest": content_digest,
        "key_id": key_id,
        "plugin_id": plugin_id,
        "publisher_id": publisher_id,
        "schema": _PAYLOAD_SCHEMA,
        "version": version,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def verify_plugin_publisher_provenance(
    plugin_dir: Path,
    manifest: dict[str, Any],
    content_provenance: dict[str, Any],
    *,
    trust_store_path: str | Path | None = None,
    signature_relative_path: Path = SIGNATURE_RELATIVE_PATH,
) -> dict[str, Any]:
    """Verify a plugin envelope against an operator-controlled publisher store."""

    signature_path = plugin_dir / signature_relative_path
    base = {
        "schema": "echo.plugin_publisher_provenance.v1",
        "envelope_schema": _ENVELOPE_SCHEMA,
        "algorithm": "ed25519",
        "present": signature_path.is_file(),
        "verified": False,
        "trusted": False,
        "status": "unsigned",
        "publisher_id": "",
        "key_id": "",
        "content_digest": str(content_provenance.get("digest") or ""),
        "signature_digest": "",
        "trust_store_path": "",
        "reason": "publisher signature is not present",
    }
    if not signature_path.is_file():
        return base

    envelope = _read_json(signature_path)
    if envelope is None:
        return _failed(base, "invalid", "publisher signature envelope is malformed")

    publisher_id = _string(envelope.get("publisher_id"))
    key_id = _string(envelope.get("key_id"))
    base.update({"publisher_id": publisher_id, "key_id": key_id})
    if envelope.get("schema") != _ENVELOPE_SCHEMA:
        return _failed(base, "invalid", "publisher signature schema is unsupported")
    if _string(envelope.get("algorithm")).lower() != "ed25519":
        return _failed(base, "invalid", "publisher signature algorithm must be ed25519")
    if not publisher_id or not key_id:
        return _failed(base, "invalid", "publisher_id and key_id are required")
    if content_provenance.get("complete") is not True:
        return _failed(base, "invalid", "content provenance is incomplete")

    plugin_id = _string(manifest.get("name"))
    version = _string(manifest.get("version"))
    content_digest = str(content_provenance.get("digest") or "")
    if _string(envelope.get("plugin_id")) != plugin_id:
        return _failed(base, "invalid", "publisher signature plugin_id does not match manifest")
    if _string(envelope.get("version")) != version:
        return _failed(base, "invalid", "publisher signature version does not match manifest")
    if _string(envelope.get("content_digest")) != content_digest:
        return _failed(base, "tampered", "publisher signature content digest does not match")

    resolved_store = resolve_publisher_trust_store_path(trust_store_path)
    base["trust_store_path"] = str(resolved_store) if resolved_store is not None else ""
    trust_store = _read_json(resolved_store) if resolved_store is not None else None
    if trust_store is None:
        return _failed(base, "untrusted", "publisher trust store is unavailable")
    if trust_store.get("schema") != _TRUST_STORE_SCHEMA:
        return _failed(base, "untrusted", "publisher trust store schema is unsupported")

    key = _find_trusted_key(trust_store, publisher_id=publisher_id, key_id=key_id)
    if key is None:
        return _failed(base, "untrusted", "publisher key is not trusted")
    if _string(key.get("status"), "active").lower() != "active":
        return _failed(base, "revoked", "publisher key is not active")
    if _string(key.get("algorithm"), "ed25519").lower() != "ed25519":
        return _failed(base, "untrusted", "trusted publisher key algorithm is unsupported")

    public_key = _decode_base64(_string(key.get("public_key")))
    signature = _decode_base64(_string(envelope.get("signature")))
    if public_key is None or len(public_key) != 32:
        return _failed(base, "untrusted", "trusted publisher public key is invalid")
    if signature is None or len(signature) != 64:
        return _failed(base, "invalid", "publisher signature bytes are invalid")

    payload = canonical_publisher_signature_payload(
        plugin_id=plugin_id,
        version=version,
        content_digest=content_digest,
        publisher_id=publisher_id,
        key_id=key_id,
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        return _failed(base, "tampered", "publisher signature verification failed")

    base.update(
        {
            "verified": True,
            "trusted": True,
            "status": "verified",
            "signature_digest": hashlib.sha256(signature).hexdigest(),
            "reason": "publisher signature verified against operator trust store",
        }
    )
    return base


def resolve_publisher_trust_store_path(
    path: str | Path | None = None,
    *,
    existing_only: bool = False,
) -> Path | None:
    """Resolve the operator trust store, optionally requiring it to exist."""

    if path is not None:
        resolved = Path(path).expanduser()
        return resolved if not existing_only or resolved.is_file() else None
    configured = os.getenv("ECHO_PLUGIN_PUBLISHER_TRUST_STORE", "").strip()
    if configured:
        resolved = Path(configured).expanduser()
        return resolved if not existing_only or resolved.is_file() else None
    root = project_root(Path(__file__))
    candidates = (
        root / ".echo" / "plugin-publishers.json",
        Path.home() / ".echo" / "plugin-publishers.json",
        Path(__file__).resolve().with_name("builtin-publishers.json"),
    )
    existing = next((candidate for candidate in candidates if candidate.is_file()), None)
    if existing is not None or existing_only:
        return existing
    return candidates[0]


def _find_trusted_key(
    trust_store: dict[str, Any],
    *,
    publisher_id: str,
    key_id: str,
) -> dict[str, Any] | None:
    publishers = trust_store.get("publishers")
    if not isinstance(publishers, list):
        return None
    for publisher in publishers:
        if (
            not isinstance(publisher, dict)
            or _string(publisher.get("publisher_id")) != publisher_id
        ):
            continue
        keys = publisher.get("keys")
        if not isinstance(keys, list):
            return None
        return next(
            (key for key in keys if isinstance(key, dict) and _string(key.get("key_id")) == key_id),
            None,
        )
    return None


def _read_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _decode_base64(value: str) -> bytes | None:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _failed(base: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    base.update({"status": status, "reason": reason})
    return base


def _string(value: Any, default: str = "") -> str:
    return value if isinstance(value, str) else default


__all__ = [
    "SIGNATURE_RELATIVE_PATH",
    "canonical_publisher_signature_payload",
    "resolve_publisher_trust_store_path",
    "verify_plugin_publisher_provenance",
]
