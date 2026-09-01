#!/usr/bin/env python3
"""Publish and read the bounded, public Echo OS update UI state.

The update bundle and its verification metadata remain in the private root
cache.  This file exposes only the authenticated version, manifest digest and
coarse operation state that the unprivileged desktop needs to render.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import stat
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

DEFAULT_STATE_ROOT = Path("/var/lib/echo-os-update")
STATUS_NAME = "status.json"
MAX_STATUS_BYTES = 4096
SOURCE_TEST_SENTINEL = "USE-SOURCE-RUNTIME"
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+:~_-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
STATES = {"checking", "ready", "installing", "reboot-required", "failed"}
PHASES = {"fetch", "apply"}


class StatusError(RuntimeError):
    pass


def required_owner() -> int:
    if os.environ.get("ECHO_UPDATE_CHANNEL_SOURCE_TEST") == SOURCE_TEST_SENTINEL:
        return os.geteuid()
    return 0


def validate_root(root: Path, *, create: bool, owner: int) -> None:
    if not root.is_absolute() or root == DEFAULT_STATE_ROOT.parent:
        raise StatusError("update status root must be one dedicated absolute directory")
    if create:
        if owner == 0 and os.geteuid() != 0:
            raise StatusError("publishing update status requires root privileges")
        root.mkdir(mode=0o755, parents=True, exist_ok=True)
    try:
        metadata = root.lstat()
    except FileNotFoundError as error:
        raise StatusError("update status root is unavailable") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or root.is_symlink()
        or metadata.st_uid != owner
        or metadata.st_mode & 0o022
    ):
        raise StatusError("update status root has unsafe ownership or permissions")


def validate_record(record: object) -> dict[str, object]:
    if not isinstance(record, dict) or set(record) - {
        "schema",
        "state",
        "phase",
        "version",
        "manifestSha256",
        "updatedAt",
        "errorCode",
    }:
        raise StatusError("update status contains unknown fields")
    schema = record.get("schema")
    state = record.get("state")
    phase = record.get("phase")
    version = record.get("version")
    manifest = record.get("manifestSha256")
    updated_at = record.get("updatedAt")
    error_code = record.get("errorCode")
    if schema != 1 or state not in STATES or phase not in PHASES:
        raise StatusError("update status schema or state is invalid")
    if not isinstance(updated_at, int) or not 1 <= updated_at <= 4_102_444_800:
        raise StatusError("update status timestamp is invalid")
    if version is not None and (not isinstance(version, str) or VERSION.fullmatch(version) is None):
        raise StatusError("update status version is invalid")
    if manifest is not None and (
        not isinstance(manifest, str) or SHA256.fullmatch(manifest) is None
    ):
        raise StatusError("update status manifest digest is invalid")
    if error_code is not None and (not isinstance(error_code, int) or not 1 <= error_code <= 255):
        raise StatusError("update status error code is invalid")
    if state in {"ready", "installing", "reboot-required"} and (
        version is None or manifest is None
    ):
        raise StatusError("authenticated update status is incomplete")
    if state == "failed" and error_code is None:
        raise StatusError("failed update status has no bounded error code")
    if state != "failed" and error_code is not None:
        raise StatusError("non-failed update status contains an error code")
    return record


def read_status(root: Path, *, owner: int) -> dict[str, object] | None:
    validate_root(root, create=False, owner=owner)
    target = root / STATUS_NAME
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StatusError("update status file is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or before.st_mode & 0o022
            or not 1 <= before.st_size <= MAX_STATUS_BYTES
        ):
            raise StatusError("update status file is empty, oversized or unsafe")
        raw = bytearray()
        while len(raw) <= MAX_STATUS_BYTES:
            block = os.read(descriptor, MAX_STATUS_BYTES + 1 - len(raw))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(descriptor)
        if (
            len(raw) > MAX_STATUS_BYTES
            or len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise StatusError("update status changed while reading")
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(bytes(raw))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StatusError("update status is not valid JSON") from error
    return validate_record(decoded)


def write_status(root: Path, record: dict[str, object], *, owner: int) -> None:
    validate_record(record)
    validate_root(root, create=True, owner=owner)
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_STATUS_BYTES:
        raise StatusError("update status exceeds its public bound")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".status-", dir=root)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise StatusError("cannot write update status")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, root / STATUS_NAME)
        directory = os.open(root, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state-root", type=Path, default=DEFAULT_STATE_ROOT)
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show")
    write = subparsers.add_parser("write")
    write.add_argument("--state", choices=sorted(STATES), required=True)
    write.add_argument("--phase", choices=sorted(PHASES), required=True)
    write.add_argument("--version")
    write.add_argument("--manifest-sha256")
    write.add_argument("--error-code", type=int)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    owner = required_owner()
    try:
        if args.command == "show":
            try:
                record = read_status(args.state_root, owner=owner)
            except StatusError as error:
                if "root is unavailable" not in str(error):
                    raise
                record = None
            if record is None:
                print(json.dumps({"schema": 1, "state": "idle"}, separators=(",", ":")))
            else:
                print(json.dumps(record, sort_keys=True, separators=(",", ":")))
            return 0
        record: dict[str, object] = {
            "schema": 1,
            "state": args.state,
            "phase": args.phase,
            "updatedAt": int(time.time()),
        }
        if args.version is not None:
            record["version"] = args.version
        if args.manifest_sha256 is not None:
            record["manifestSha256"] = args.manifest_sha256
        if args.error_code is not None:
            record["errorCode"] = args.error_code
        write_status(args.state_root, record, owner=owner)
        return 0
    except StatusError as error:
        print(f"Echo OS update status failed: {error}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
