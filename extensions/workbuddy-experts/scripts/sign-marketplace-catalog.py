#!/usr/bin/env python3
"""Sign or verify marketplace catalog indexes with the protected release key."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from runtime.platform.plugins.catalog_provenance import (  # noqa: E402
    CATALOG_SIGNATURE_SCHEMA,
    canonical_catalog_signature_payload,
    catalog_content_digest,
    catalog_signature_path,
    load_catalog_signature,
    verify_marketplace_catalog,
)


def _load_catalog(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"catalog is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"catalog must be a JSON object: {path}")
    return value


def _private_key() -> Ed25519PrivateKey:
    encoded = os.environ.get("ECHO_PLUGIN_SIGNING_PRIVATE_KEY", "").strip()
    if not encoded:
        raise ValueError("ECHO_PLUGIN_SIGNING_PRIVATE_KEY is required")
    try:
        raw = base64.b64decode(encoded, validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ECHO_PLUGIN_SIGNING_PRIVATE_KEY must be a base64 Ed25519 private key"
        ) from exc


def sign(path: Path) -> Path:
    catalog = _load_catalog(path)
    publisher_id = os.environ.get(
        "ECHO_PLUGIN_SIGNING_PUBLISHER_ID",
        "echoai",
    ).strip()
    key_id = os.environ.get("ECHO_PLUGIN_SIGNING_KEY_ID", "").strip()
    if not publisher_id or not key_id:
        raise ValueError("publisher id and signing key id are required")
    digest = catalog_content_digest(catalog)
    signature = _private_key().sign(
        canonical_catalog_signature_payload(
            catalog_name=path.name,
            content_digest=digest,
            publisher_id=publisher_id,
            key_id=key_id,
        )
    )
    envelope = {
        "schema": CATALOG_SIGNATURE_SCHEMA,
        "algorithm": "ed25519",
        "catalog": path.name,
        "content_digest": digest,
        "publisher_id": publisher_id,
        "key_id": key_id,
        "signature": base64.b64encode(signature).decode("ascii"),
    }
    output = catalog_signature_path(path)
    output.write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output


def verify(path: Path, *, trust_store: Path | None) -> None:
    catalog = _load_catalog(path)
    envelope = load_catalog_signature(catalog_signature_path(path))
    verify_marketplace_catalog(
        catalog,
        envelope,
        catalog_name=path.name,
        trust_store_path=trust_store,
        require_trusted=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogs", nargs="+", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--trust-store", type=Path)
    args = parser.parse_args()
    for path in args.catalogs:
        if args.verify:
            verify(path, trust_store=args.trust_store)
            print(f"verified signed marketplace catalog: {path}")
        else:
            output = sign(path)
            print(f"signed marketplace catalog: {path} -> {output}")


if __name__ == "__main__":
    main()

