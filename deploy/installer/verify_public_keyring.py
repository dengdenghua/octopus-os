#!/usr/bin/env python3
"""Reject installer trust roots that are not bounded public OpenPGP keyrings."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


MAX_KEYRING_SIZE = 16 * 1024 * 1024
SECRET_PACKET_TAGS = {5, 7}
ALLOWED_PUBLIC_KEYRING_TAGS = {2, 6, 12, 13, 14, 17}


class PublicKeyringError(ValueError):
    """Raised when a release keyring can contain private or opaque material."""


def require_bytes(data: bytes, offset: int, size: int, context: str) -> int:
    end = offset + size
    if size < 0 or end > len(data):
        raise PublicKeyringError(f"truncated OpenPGP {context}")
    return end


def consume_new_body(data: bytes, offset: int) -> int:
    while True:
        length_end = require_bytes(data, offset, 1, "new-format length")
        first = data[offset]
        offset = length_end
        if first < 192:
            return require_bytes(data, offset, first, "packet body")
        if first <= 223:
            second_end = require_bytes(data, offset, 1, "two-octet length")
            length = ((first - 192) << 8) + data[offset] + 192
            return require_bytes(data, second_end, length, "packet body")
        if first == 255:
            length_end = require_bytes(data, offset, 4, "five-octet length")
            length = int.from_bytes(data[offset:length_end], "big")
            return require_bytes(data, length_end, length, "packet body")

        # Partial-body lengths are a sequence of chunks terminated by one
        # ordinary new-format length. The packet tag was already checked, so
        # skipping each bounded chunk is safe and still detects truncation.
        partial_length = 1 << (first & 0x1F)
        offset = require_bytes(data, offset, partial_length, "partial packet body")


def consume_old_body(data: bytes, offset: int, length_type: int) -> int:
    if length_type == 3:
        raise PublicKeyringError("indeterminate-length OpenPGP packets are not allowed")
    length_octets = (1, 2, 4)[length_type]
    length_end = require_bytes(data, offset, length_octets, "old-format length")
    length = int.from_bytes(data[offset:length_end], "big")
    return require_bytes(data, length_end, length, "packet body")


def verify_public_keyring_bytes(data: bytes) -> int:
    if not data:
        raise PublicKeyringError("public keyring is empty")
    if len(data) > MAX_KEYRING_SIZE:
        raise PublicKeyringError("public keyring exceeds 16 MiB")

    offset = 0
    packet_count = 0
    public_key_count = 0
    while offset < len(data):
        header_end = require_bytes(data, offset, 1, "packet header")
        header = data[offset]
        offset = header_end
        if not header & 0x80:
            raise PublicKeyringError("invalid OpenPGP packet header")

        new_format = bool(header & 0x40)
        tag = header & 0x3F if new_format else (header >> 2) & 0x0F
        if tag in SECRET_PACKET_TAGS:
            raise PublicKeyringError("secret-key material is forbidden in Recovery")
        if tag not in ALLOWED_PUBLIC_KEYRING_TAGS:
            raise PublicKeyringError(
                f"OpenPGP packet tag {tag} is not valid in a strict public keyring"
            )
        if new_format:
            next_offset = consume_new_body(data, offset)
        else:
            next_offset = consume_old_body(data, offset, header & 0x03)
        if tag == 6:
            public_key_count += 1
        packet_count += 1
        offset = next_offset

    if public_key_count == 0:
        raise PublicKeyringError("public keyring contains no primary public-key packet")
    return packet_count


def verify_public_keyring(path: Path) -> int:
    if not path.is_file() or path.is_symlink():
        raise PublicKeyringError("public keyring must be a regular, non-symlink file")
    size = path.stat().st_size
    if size <= 0 or size > MAX_KEYRING_SIZE:
        raise PublicKeyringError("public keyring must be 1 byte to 16 MiB")
    return verify_public_keyring_bytes(path.read_bytes())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("keyring", type=Path)
    args = parser.parse_args()
    try:
        packet_count = verify_public_keyring(args.keyring)
    except (OSError, PublicKeyringError) as error:
        print(f"Echo OS installer keyring rejected: {error}", file=sys.stderr)
        return 1
    print(f"Echo OS installer public keyring accepted: packets={packet_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
