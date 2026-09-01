#!/usr/bin/env python3
"""Kill a real systemd-sysupdate process after its inactive root starts changing.

This helper is intentionally only a PATH shim for the destructive raw-image
acceptance test.  The production update entrypoint still performs all bundle
authentication and launches what it resolves as systemd-sysupdate; this shim
starts the real binary in its own process group and cuts power at the first
observed write to a bounded sample at the inactive root's start.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import NoReturn

SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SAMPLE_BYTES = 64 * 1024 * 1024
POLL_INTERVAL_SECONDS = 0.01
WRITE_DEADLINE_SECONDS = 120.0


class InterruptionError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise InterruptionError(message)


def positive_integer(name: str) -> int:
    value = os.environ.get(name, "")
    if not value.isdecimal() or int(value) <= 0:
        fail(f"{name} must be a positive decimal integer")
    return int(value)


def runtime_paths() -> tuple[Path, Path]:
    real_input = Path(os.environ.get("ECHO_REAL_SYSUPDATE_BIN", ""))
    image_input = Path(os.environ.get("ECHO_UPDATE_INTERRUPT_IMAGE", ""))
    if not real_input.is_absolute() or not image_input.is_absolute():
        fail("interruption helper paths must be absolute")
    if image_input.is_symlink():
        fail("interruption image must not be a symlink")
    try:
        real = real_input.resolve(strict=True)
        image = image_input.resolve(strict=True)
    except OSError as error:
        raise InterruptionError("interruption helper input is unavailable") from error
    real_stat = real.stat()
    image_stat = image.stat()
    if not stat.S_ISREG(real_stat.st_mode) or not os.access(real, os.X_OK):
        fail("real systemd-sysupdate is not an executable regular file")
    if not stat.S_ISREG(image_stat.st_mode) or image_stat.st_size <= 0:
        fail("interruption image is not a non-empty regular file")
    if real == Path(__file__).resolve():
        fail("interruption helper cannot recursively launch itself")
    return real, image


def sample_parameters(image_size: int) -> tuple[int, int, str]:
    sector_size = positive_integer("ECHO_UPDATE_INTERRUPT_SECTOR_SIZE")
    start_sector = positive_integer("ECHO_UPDATE_INTERRUPT_START_SECTOR")
    sector_count = positive_integer("ECHO_UPDATE_INTERRUPT_SECTOR_COUNT")
    expected = os.environ.get("ECHO_UPDATE_INTERRUPT_BEFORE_SHA256", "")
    if SHA256.fullmatch(expected) is None:
        fail("ECHO_UPDATE_INTERRUPT_BEFORE_SHA256 is invalid")
    offset = sector_size * start_sector
    length = sector_size * sector_count
    if length > MAX_SAMPLE_BYTES or offset >= image_size or length > image_size - offset:
        fail("inactive-root interruption sample is outside its bounded image range")
    return offset, length, expected


def sample_sha256(descriptor: int, offset: int, length: int) -> str:
    digest = hashlib.sha256()
    consumed = 0
    while consumed < length:
        block = os.pread(descriptor, min(1024 * 1024, length - consumed), offset + consumed)
        if not block:
            fail("interruption image ended inside its inactive-root sample")
        digest.update(block)
        consumed += len(block)
    return digest.hexdigest()


def kill_process_group(process: subprocess.Popen[bytes]) -> int:
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    try:
        return process.wait(timeout=10)
    except subprocess.TimeoutExpired as error:
        raise InterruptionError(
            "sysupdate process group did not terminate after SIGKILL"
        ) from error


def run(argv: Sequence[str]) -> int:
    real, image = runtime_paths()
    if "update" not in argv:
        return subprocess.run([str(real), *argv], check=False).returncode
    image_stat = image.stat()
    offset, length, expected = sample_parameters(image_stat.st_size)
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(image, flags)
    process: subprocess.Popen[bytes] | None = None
    try:
        before = sample_sha256(descriptor, offset, length)
        if before != expected:
            fail("inactive-root sample changed before systemd-sysupdate started")
        process = subprocess.Popen([str(real), *argv], start_new_session=True)
        deadline = time.monotonic() + WRITE_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            after = sample_sha256(descriptor, offset, length)
            if after != before:
                print(
                    "ECHO_UPDATE_INTERRUPTION_TRIGGERED "
                    f"sample=inactive-root-first-{length} signal=SIGKILL "
                    f"before={before} after={after}",
                    flush=True,
                )
                status = kill_process_group(process)
                if status != -signal.SIGKILL:
                    fail(f"systemd-sysupdate escaped the interruption with status {status}")
                print("ECHO_UPDATE_INTERRUPTION_OBSERVED result=signal-9", flush=True)
                return 128 + signal.SIGKILL
            status = process.poll()
            if status is not None:
                fail(
                    f"systemd-sysupdate exited with status {status} before a root write was observed"
                )
            time.sleep(POLL_INTERVAL_SECONDS)
        kill_process_group(process)
        fail("systemd-sysupdate made no observable inactive-root write before the deadline")
    finally:
        os.close(descriptor)
        if process is not None and process.poll() is None:
            kill_process_group(process)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except (InterruptionError, OSError, subprocess.SubprocessError) as error:
        print(f"sysupdate interruption failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
