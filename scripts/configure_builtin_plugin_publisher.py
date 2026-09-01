#!/usr/bin/env python3
"""Materialize the public half of the first-party plugin release identity.

The public key is intentionally supplied by release configuration. The private
key remains only in the content-publishing job and is never written here.
"""

from __future__ import annotations

import argparse
import base64
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from runtime.platform.io import atomic_write_json

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO / "runtime" / "platform" / "plugins" / "builtin-publishers.json"


def _decode(value: str, *, name: str, length: int) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be valid base64") from exc
    if len(raw) != length:
        raise ValueError(f"{name} must contain exactly {length} bytes")
    return raw


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure the built-in plugin publisher key")
    parser.add_argument(
        "--public-key",
        default=os.environ.get("ECHO_PLUGIN_SIGNING_PUBLIC_KEY", ""),
    )
    parser.add_argument(
        "--publisher-id",
        default=os.environ.get("ECHO_PLUGIN_SIGNING_PUBLISHER_ID", "echoai"),
    )
    parser.add_argument(
        "--key-id",
        default=os.environ.get("ECHO_PLUGIN_SIGNING_KEY_ID", ""),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    publisher_id = args.publisher_id.strip()
    key_id = args.key_id.strip()
    if not publisher_id or not key_id:
        raise ValueError("publisher id and key id are required")
    public_bytes = _decode(args.public_key.strip(), name="public key", length=32)
    private_value = os.environ.get("ECHO_PLUGIN_SIGNING_PRIVATE_KEY", "").strip()
    if private_value:
        private_key = Ed25519PrivateKey.from_private_bytes(
            _decode(private_value, name="private key", length=32)
        )
        derived = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if derived != public_bytes:
            raise ValueError("configured plugin signing public and private keys do not match")
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        output,
        {
            "schema": "echo.plugin_publisher_trust_store.v1",
            "publishers": [
                {
                    "publisher_id": publisher_id,
                    "display_name": "EchoAI trusted marketplace publisher",
                    "keys": [
                        {
                            "key_id": key_id,
                            "algorithm": "ed25519",
                            "status": "active",
                            "public_key": base64.b64encode(public_bytes).decode("ascii"),
                        }
                    ],
                }
            ],
        },
        sort_keys=True,
    )
    print(f"configured built-in plugin publisher at {output}")


if __name__ == "__main__":
    main()


