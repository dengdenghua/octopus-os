"""Detached publisher signatures for mutable marketplace catalog indexes.

Package signatures protect downloaded code.  This module protects the index
that chooses *which* package id, version, URL, permissions, and dependencies
the client will act on.  Both layers use the same operator-controlled Ed25519
publisher trust store, but deliberately use different payload schemas so a
package signature can never be replayed as a catalog signature.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from runtime.platform.plugins.publisher_provenance import (
    resolve_publisher_trust_store_path,
)

CATALOG_SIGNATURE_SCHEMA = "echo.marketplace_catalog_signature.v1"
CATALOG_SIGNATURE_PAYLOAD_SCHEMA = "echo.marketplace_catalog_signature_payload.v1"
CATALOG_TRUST_SCHEMA = "echo.plugin_publisher_trust_store.v1"

_MAX_CATALOG_NAME = 128
_MAX_SIGNATURE_BYTES = 64 * 1024


def catalog_signature_path(catalog_path: str | Path) -> Path:
    path = Path(catalog_path)
    return path.with_name(f"{path.stem}.provenance.json")


def canonical_catalog_bytes(catalog: dict[str, Any]) -> bytes:
    """Return the stable catalog representation covered by the digest."""

    if not isinstance(catalog, dict):
        raise ValueError("marketplace catalog must be a JSON object")
    return json.dumps(
        catalog,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def catalog_content_digest(catalog: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_catalog_bytes(catalog)).hexdigest()


def canonical_catalog_signature_payload(
    *,
    catalog_name: str,
    content_digest: str,
    publisher_id: str,
    key_id: str,
) -> bytes:
    name = str(catalog_name or "").strip()
    if not name or len(name) > _MAX_CATALOG_NAME or "/" in name or "\\" in name:
        raise ValueError("marketplace catalog name is invalid")
    payload = {
        "catalog": name,
        "content_digest": str(content_digest),
        "key_id": str(key_id),
        "publisher_id": str(publisher_id),
        "schema": CATALOG_SIGNATURE_PAYLOAD_SCHEMA,
    }
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _read_json(path: Path | None, *, max_bytes: int) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _decode_base64(value: Any) -> bytes | None:
    if not isinstance(value, str):
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None


def _trusted_key(
    trust_store: dict[str, Any],
    *,
    publisher_id: str,
    key_id: str,
) -> dict[str, Any] | None:
    if trust_store.get("schema") != CATALOG_TRUST_SCHEMA:
        return None
    publishers = trust_store.get("publishers")
    if not isinstance(publishers, list):
        return None
    for publisher in publishers:
        if not isinstance(publisher, dict) or publisher.get("publisher_id") != publisher_id:
            continue
        keys = publisher.get("keys")
        if not isinstance(keys, list):
            return None
        return next(
            (key for key in keys if isinstance(key, dict) and key.get("key_id") == key_id),
            None,
        )
    return None


def verify_marketplace_catalog(
    catalog: dict[str, Any],
    envelope: dict[str, Any] | None,
    *,
    catalog_name: str,
    trust_store_path: str | Path | None = None,
    require_trusted: bool = True,
) -> dict[str, Any]:
    """Verify one parsed catalog and return bounded trust evidence.

    ``require_trusted=False`` exists only for a source checkout's checked-in
    development mirror.  Remote and cached catalogs must always pass a trusted
    publisher signature before they can influence installation.
    """

    digest = catalog_content_digest(catalog)
    result: dict[str, Any] = {
        "schema": "echo.marketplace_catalog_trust.v1",
        "catalog": catalog_name,
        "content_digest": digest,
        "publisher_id": "",
        "key_id": "",
        "publisher_verified": False,
        "integrity_verified": False,
        "status": "unsigned",
    }
    if not isinstance(envelope, dict):
        if require_trusted:
            raise ValueError("trusted marketplace catalog signature is required")
        result.update(status="local_dev", integrity_verified=True)
        return result
    if envelope.get("schema") != CATALOG_SIGNATURE_SCHEMA:
        raise ValueError("marketplace catalog signature schema is invalid")
    if str(envelope.get("algorithm") or "").lower() != "ed25519":
        raise ValueError("marketplace catalog signature algorithm is invalid")
    publisher_id = str(envelope.get("publisher_id") or "").strip()
    key_id = str(envelope.get("key_id") or "").strip()
    result.update(publisher_id=publisher_id, key_id=key_id)
    if envelope.get("catalog") != catalog_name or envelope.get("content_digest") != digest:
        raise ValueError("marketplace catalog content digest does not match signature")

    resolved_store = resolve_publisher_trust_store_path(
        trust_store_path,
        existing_only=True,
    )
    trust_store = _read_json(resolved_store, max_bytes=_MAX_SIGNATURE_BYTES)
    key = (
        _trusted_key(trust_store, publisher_id=publisher_id, key_id=key_id)
        if trust_store is not None
        else None
    )
    if key is None or str(key.get("status") or "active").lower() != "active":
        raise ValueError("marketplace catalog publisher key is not trusted")
    if str(key.get("algorithm") or "ed25519").lower() != "ed25519":
        raise ValueError("marketplace catalog publisher key algorithm is invalid")
    public_key = _decode_base64(key.get("public_key"))
    signature = _decode_base64(envelope.get("signature"))
    if public_key is None or len(public_key) != 32 or signature is None or len(signature) != 64:
        raise ValueError("marketplace catalog signature bytes are invalid")
    payload = canonical_catalog_signature_payload(
        catalog_name=catalog_name,
        content_digest=digest,
        publisher_id=publisher_id,
        key_id=key_id,
    )
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("marketplace catalog signature verification failed") from exc
    result.update(
        publisher_verified=True,
        integrity_verified=True,
        status="verified",
        signature_digest=hashlib.sha256(signature).hexdigest(),
    )
    return result


def load_catalog_signature(path: str | Path) -> dict[str, Any] | None:
    return _read_json(Path(path), max_bytes=_MAX_SIGNATURE_BYTES)


__all__ = [
    "CATALOG_SIGNATURE_SCHEMA",
    "canonical_catalog_bytes",
    "canonical_catalog_signature_payload",
    "catalog_content_digest",
    "catalog_signature_path",
    "load_catalog_signature",
    "verify_marketplace_catalog",
]
