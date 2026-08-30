#!/usr/bin/env python3
"""Crash-consistent selection journal for Echo appliance image upgrades."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
KIND = "echo.appliance-upgrade-transaction"
MAX_JOURNAL_BYTES = 16 * 1024
IMAGE_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}$")
TRANSACTION_ID = re.compile(r"^[0-9a-f]{64}$")
PHASES = {"prepared", "switching", "selected", "recovering"}


class UpgradeTransactionError(RuntimeError):
    """The durable appliance upgrade transaction is unsafe or inconsistent."""


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _transaction_id(value: Mapping[str, Any]) -> str:
    identity = {
        "schemaVersion": value["schemaVersion"],
        "kind": value["kind"],
        "releaseEnv": value["releaseEnv"],
        "previousImage": value["previousImage"],
        "targetImage": value["targetImage"],
        "previousReleasePresent": value["previousReleasePresent"],
    }
    return hashlib.sha256(_canonical(identity)).hexdigest()


def _safe_parent(path: Path, label: str) -> Path:
    if not path.is_absolute() or path.name in {"", ".", ".."}:
        raise UpgradeTransactionError(f"{label} must be one absolute file path")
    parent = path.parent
    cursor = Path(path.anchor)
    for part in parent.parts[1:]:
        cursor /= part
        if cursor.is_symlink():
            raise UpgradeTransactionError(f"{label} parent is unsafe")
    try:
        resolved = parent.resolve(strict=True)
    except OSError as exc:
        raise UpgradeTransactionError(f"{label} parent is unavailable") from exc
    if parent.is_symlink() or not resolved.is_dir():
        raise UpgradeTransactionError(f"{label} parent is unsafe")
    info = resolved.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise UpgradeTransactionError(f"{label} parent has unsafe ownership or permissions")
    return resolved / path.name


def _read_regular(path: Path, label: str, *, exact_mode: int) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise UpgradeTransactionError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != exact_mode
            or not 1 <= before.st_size <= MAX_JOURNAL_BYTES
        ):
            raise UpgradeTransactionError(f"{label} has unsafe ownership, mode, or size")
        payload = bytearray()
        while len(payload) <= MAX_JOURNAL_BYTES:
            chunk = os.read(descriptor, min(4096, MAX_JOURNAL_BYTES - len(payload) + 1))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) > MAX_JOURNAL_BYTES or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise UpgradeTransactionError(f"{label} changed while it was read")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    target = _safe_parent(path, "transaction output")
    if target.is_symlink():
        raise UpgradeTransactionError("transaction output must not be a symbolic link")
    if target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise UpgradeTransactionError("transaction output is unsafe")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        directory = os.open(target.parent, os.O_RDONLY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary.exists():
            temporary.unlink()


def _remove(path: Path) -> None:
    target = _safe_parent(path, "upgrade transaction")
    if target.is_symlink():
        raise UpgradeTransactionError("upgrade transaction must not be a symbolic link")
    target.unlink()
    directory = os.open(target.parent, os.O_RDONLY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _release_payload(image: str) -> bytes:
    if IMAGE_REFERENCE.fullmatch(image) is None:
        raise UpgradeTransactionError("upgrade images must be immutable digest references")
    return f"ECHO_OS_IMAGE={image}\n".encode("ascii")


def _release_image(path: Path, *, required: bool) -> str | None:
    if not path.exists() and not path.is_symlink():
        if required:
            raise UpgradeTransactionError("release environment is missing")
        return None
    raw = _read_regular(path, "release environment", exact_mode=0o600)
    try:
        text = raw.decode("ascii")
    except UnicodeError as exc:
        raise UpgradeTransactionError("release environment is invalid") from exc
    prefix = "ECHO_OS_IMAGE="
    if not text.startswith(prefix) or text.count("\n") != 1 or not text.endswith("\n"):
        raise UpgradeTransactionError("release environment is invalid")
    image = text[len(prefix) : -1]
    if IMAGE_REFERENCE.fullmatch(image) is None:
        raise UpgradeTransactionError("release environment is not immutable")
    return image


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schemaVersion",
        "kind",
        "transactionId",
        "phase",
        "releaseEnv",
        "previousImage",
        "targetImage",
        "previousReleasePresent",
    }
    if (
        set(value) != expected
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != KIND
        or value.get("phase") not in PHASES
        or not isinstance(value.get("releaseEnv"), str)
        or not Path(value["releaseEnv"]).is_absolute()
        or IMAGE_REFERENCE.fullmatch(str(value.get("previousImage"))) is None
        or IMAGE_REFERENCE.fullmatch(str(value.get("targetImage"))) is None
        or value.get("previousImage") == value.get("targetImage")
        or type(value.get("previousReleasePresent")) is not bool
        or TRANSACTION_ID.fullmatch(str(value.get("transactionId"))) is None
        or value.get("transactionId") != _transaction_id(value)
    ):
        raise UpgradeTransactionError("upgrade transaction schema is invalid")
    return dict(value)


def _load(path: Path) -> dict[str, Any]:
    raw = _read_regular(path, "upgrade transaction", exact_mode=0o600)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise UpgradeTransactionError("upgrade transaction is not strict JSON") from exc
    if not isinstance(value, dict):
        raise UpgradeTransactionError("upgrade transaction is not an object")
    return _validate(value)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise UpgradeTransactionError("upgrade transaction has duplicate fields")
        value[key] = item
    return value


def _set_phase(path: Path, value: Mapping[str, Any], phase: str) -> dict[str, Any]:
    updated = {**value, "phase": phase}
    validated = _validate(updated)
    _atomic_write(path, _canonical(validated), mode=0o600)
    return validated


def begin(
    journal: Path,
    release_env: Path,
    previous_image: str,
    target_image: str,
    *,
    previous_release_present: bool,
) -> dict[str, Any]:
    journal = _safe_parent(journal, "upgrade transaction")
    release_env = _safe_parent(release_env, "release environment")
    if journal.exists() or journal.is_symlink():
        raise UpgradeTransactionError("a pending upgrade transaction already exists")
    _release_payload(previous_image)
    _release_payload(target_image)
    if previous_image == target_image:
        raise UpgradeTransactionError("upgrade target is already selected")
    selected = _release_image(release_env, required=previous_release_present)
    if selected is not None and selected != previous_image:
        raise UpgradeTransactionError("release environment and running image disagree")
    if not previous_release_present and selected is not None:
        raise UpgradeTransactionError("release environment presence changed before upgrade")
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": KIND,
        "transactionId": "0" * 64,
        "phase": "prepared",
        "releaseEnv": str(release_env),
        "previousImage": previous_image,
        "targetImage": target_image,
        "previousReleasePresent": previous_release_present,
    }
    value["transactionId"] = _transaction_id(value)
    validated = _validate(value)
    _atomic_write(journal, _canonical(validated), mode=0o600)
    return validated


def select(journal: Path, release_env: Path) -> dict[str, Any]:
    value = _load(journal)
    if value["phase"] != "prepared" or value["releaseEnv"] != str(release_env):
        raise UpgradeTransactionError("upgrade transaction cannot select its target")
    selected = _release_image(release_env, required=value["previousReleasePresent"])
    if selected is not None and selected != value["previousImage"]:
        raise UpgradeTransactionError("release selection changed before target switch")
    switching = _set_phase(journal, value, "switching")
    _atomic_write(release_env, _release_payload(value["targetImage"]), mode=0o600)
    return _set_phase(journal, switching, "selected")


def commit(journal: Path, release_env: Path) -> dict[str, Any]:
    value = _load(journal)
    if value["phase"] != "selected" or value["releaseEnv"] != str(release_env):
        raise UpgradeTransactionError("upgrade transaction is not ready to commit")
    if _release_image(release_env, required=True) != value["targetImage"]:
        raise UpgradeTransactionError("target release selection changed before commit")
    _remove(journal)
    return {"committed": True, "transactionId": value["transactionId"]}


def recover(journal: Path, release_env: Path) -> dict[str, Any]:
    value = _load(journal)
    if value["releaseEnv"] != str(release_env):
        raise UpgradeTransactionError("recovery release path differs from the transaction")
    if value["phase"] not in PHASES:
        raise UpgradeTransactionError("upgrade transaction cannot be recovered")
    recovering = _set_phase(journal, value, "recovering")
    _atomic_write(release_env, _release_payload(value["previousImage"]), mode=0o600)
    return {
        "recoveryPending": True,
        "transactionId": recovering["transactionId"],
        "previousImage": recovering["previousImage"],
        "targetImage": recovering["targetImage"],
    }


def finish_recovery(journal: Path, release_env: Path) -> dict[str, Any]:
    value = _load(journal)
    if value["phase"] != "recovering" or value["releaseEnv"] != str(release_env):
        raise UpgradeTransactionError("upgrade recovery is not ready to finish")
    if _release_image(release_env, required=True) != value["previousImage"]:
        raise UpgradeTransactionError("previous release selection changed during recovery")
    _remove(journal)
    return {"recovered": True, "transactionId": value["transactionId"]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    begin_parser = subparsers.add_parser("begin")
    begin_parser.add_argument("--journal", type=Path, required=True)
    begin_parser.add_argument("--release-env", type=Path, required=True)
    begin_parser.add_argument("--previous-image", required=True)
    begin_parser.add_argument("--target-image", required=True)
    begin_parser.add_argument("--previous-release-present", choices=("yes", "no"), required=True)
    for name in ("select", "commit", "recover", "finish-recovery"):
        command = subparsers.add_parser(name)
        command.add_argument("--journal", type=Path, required=True)
        command.add_argument("--release-env", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "begin":
            result = begin(
                args.journal,
                args.release_env,
                args.previous_image,
                args.target_image,
                previous_release_present=args.previous_release_present == "yes",
            )
        elif args.command == "select":
            result = select(args.journal, args.release_env)
        elif args.command == "commit":
            result = commit(args.journal, args.release_env)
        elif args.command == "recover":
            result = recover(args.journal, args.release_env)
        else:
            result = finish_recovery(args.journal, args.release_env)
    except (OSError, UpgradeTransactionError) as exc:
        print(f"upgrade transaction failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
