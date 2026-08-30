#!/usr/bin/env python3
"""Copy exactly one authenticated Echo OS raw-image byte stream."""

from __future__ import annotations

import argparse
import sys
from typing import BinaryIO


CHUNK_SIZE = 1024 * 1024
MAX_INSTALL_BYTES = 64 * 1024**4


class InstallStreamError(ValueError):
    """Raised when a decompressed installer stream violates its size contract."""


def validate_expected_size(expected_size: int) -> None:
    if (
        not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size <= 0
        or expected_size % 512 != 0
        or expected_size > MAX_INSTALL_BYTES
    ):
        raise InstallStreamError(
            "expected size must be a positive 512-byte multiple no larger than 64 TiB"
        )


def write_all(target: BinaryIO, block: bytes) -> None:
    remaining = memoryview(block)
    while remaining:
        written = target.write(remaining)
        if not isinstance(written, int) or written <= 0:
            raise InstallStreamError("target stream stopped accepting bytes")
        remaining = remaining[written:]


def copy_exact(source: BinaryIO, target: BinaryIO, expected_size: int) -> int:
    validate_expected_size(expected_size)
    remaining = expected_size
    copied = 0
    while remaining:
        block = source.read(min(CHUNK_SIZE, remaining))
        if not block:
            raise InstallStreamError(
                f"decompressed image is truncated: expected {expected_size}, got {copied} bytes"
            )
        write_all(target, block)
        block_size = len(block)
        copied += block_size
        remaining -= block_size

    if source.read(1) != b"":
        raise InstallStreamError(
            f"decompressed image contains data after the declared {expected_size} bytes"
        )
    target.flush()
    return copied


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("expected_size", type=int)
    args = parser.parse_args()
    try:
        copy_exact(sys.stdin.buffer, sys.stdout.buffer, args.expected_size)
    except (BrokenPipeError, InstallStreamError, OSError) as error:
        print(f"Echo OS install stream rejected: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
