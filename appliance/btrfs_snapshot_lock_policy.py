"""Approval-bound deletion locks for Echo-managed Btrfs snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from appliance.omv_protocol import (
    BTRFS_SNAPSHOT_LOCK_PLAN_SCHEMA,
    validate_btrfs_snapshot_lock_desired,
    validate_omv_uuid,
)

SCHEMA_VERSION = 1
DESIRED_SCHEMA = "echo.btrfs-snapshot-lock-desired.v1"
LOCKS_PATH = Path("/var/lib/echo-os/btrfs-snapshot-locks.json")
MAX_FILE_BYTES = 1024 * 1024
MAX_LOCKS = 4096
_LOCK = threading.RLock()


class BtrfsSnapshotLockPolicyError(ValueError):
    """A snapshot lock registry or desired state is invalid."""


def _uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise BtrfsSnapshotLockPolicyError(f"{field} is invalid")
    try:
        return validate_omv_uuid(value).lower()
    except ValueError as exc:
        raise BtrfsSnapshotLockPolicyError(f"{field} is invalid") from exc


def _lock_entry(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != {"sharedFolderRef", "snapshotId"}:
        raise BtrfsSnapshotLockPolicyError("snapshot lock entry has an invalid schema")
    return {
        "sharedFolderRef": _uuid(value.get("sharedFolderRef"), "sharedFolderRef"),
        "snapshotId": _uuid(value.get("snapshotId"), "snapshotId"),
    }


def validate_registry(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"schemaVersion": SCHEMA_VERSION, "locks": []}
    if set(value) != {"schemaVersion", "locks"} or value.get("schemaVersion") != SCHEMA_VERSION:
        raise BtrfsSnapshotLockPolicyError("snapshot lock registry has an unexpected schema")
    raw_locks = value.get("locks")
    if not isinstance(raw_locks, list) or len(raw_locks) > MAX_LOCKS:
        raise BtrfsSnapshotLockPolicyError("snapshot lock registry is too large")
    locks: list[dict[str, str]] = []
    for raw in raw_locks:
        if not isinstance(raw, dict):
            raise BtrfsSnapshotLockPolicyError("snapshot lock entry is invalid")
        locks.append(_lock_entry(raw))
    identities = [(item["sharedFolderRef"], item["snapshotId"]) for item in locks]
    if len(identities) != len(set(identities)):
        raise BtrfsSnapshotLockPolicyError("snapshot lock registry has duplicate entries")
    locks.sort(key=lambda item: (item["sharedFolderRef"], item["snapshotId"]))
    return {"schemaVersion": SCHEMA_VERSION, "locks": locks}


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("snapshot lock registry is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o077):
        raise OSError("snapshot lock registry has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("snapshot lock registry exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("snapshot lock registry exceeds the safety limit")
    return True, payload


def read_registry(path: Path = LOCKS_PATH, *, trusted_uid: int = 0) -> tuple[bool, dict[str, Any]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, validate_registry(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("snapshot lock registry is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("snapshot lock registry is not a JSON object")
    try:
        return exists, validate_registry(value)
    except BtrfsSnapshotLockPolicyError as exc:
        raise OSError(str(exc)) from exc


def locked_snapshot_ids(
    shared_folder_ref: str,
    path: Path = LOCKS_PATH,
    *,
    trusted_uid: int = 0,
) -> frozenset[str]:
    reference = _uuid(shared_folder_ref, "sharedFolderRef")
    _exists, registry = read_registry(path, trusted_uid=trusted_uid)
    return frozenset(
        item["snapshotId"] for item in registry["locks"] if item["sharedFolderRef"] == reference
    )


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_btrfs_snapshot_lock_desired(dict(value))
    except ValueError as exc:
        raise BtrfsSnapshotLockPolicyError(str(exc)) from exc


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _plan_context(
    desired_state: Mapping[str, Any],
    *,
    path: Path = LOCKS_PATH,
    trusted_uid: int = 0,
    inventory_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    if inventory_reader is None:
        from appliance.native_btrfs_snapshot import list_snapshots

        inventory_reader = list_snapshots
    inventory = inventory_reader(desired["sharedFolderRef"])
    snapshots = inventory.get("snapshots")
    if inventory.get("sharedFolderRef") != desired["sharedFolderRef"] or not isinstance(
        snapshots, list
    ):
        raise OSError("Btrfs snapshot inventory is invalid")
    selected = next(
        (
            item
            for item in snapshots
            if isinstance(item, dict) and item.get("snapshotId") == desired["snapshotId"]
        ),
        None,
    )
    if selected is None:
        raise BtrfsSnapshotLockPolicyError(
            "snapshotId does not match a managed snapshot for this shared folder"
        )
    configured, current = read_registry(path, trusted_uid=trusted_uid)
    identity = {
        "sharedFolderRef": desired["sharedFolderRef"],
        "snapshotId": desired["snapshotId"],
    }
    current_identities = {
        (item["sharedFolderRef"], item["snapshotId"]): item for item in current["locks"]
    }
    key = (desired["sharedFolderRef"], desired["snapshotId"])
    before = key in current_identities
    wanted_identities = dict(current_identities)
    if desired["locked"]:
        wanted_identities[key] = identity
    else:
        wanted_identities.pop(key, None)
    wanted = validate_registry(
        {"schemaVersion": SCHEMA_VERSION, "locks": list(wanted_identities.values())}
    )
    operation = "none" if before == desired["locked"] else "lock" if desired["locked"] else "unlock"
    binding = {
        "schema": BTRFS_SNAPSHOT_LOCK_PLAN_SCHEMA,
        "current": current,
        "wanted": wanted,
        "configured": configured,
        "desired": desired,
        "snapshot": {
            "snapshotId": selected.get("snapshotId"),
            "name": selected.get("name"),
            "kind": selected.get("kind"),
            "readOnly": selected.get("readOnly"),
        },
        "operation": operation,
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
        "safety": {
            "preventsManualDelete": desired["locked"],
            "excludedFromAutomaticRetention": desired["locked"],
            "snapshotDataChanged": False,
        },
    }


def _public_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in context.items()
        if key not in {"current", "wanted", "configured"}
    }


def plan_lock(
    desired_state: Mapping[str, Any],
    *,
    path: Path = LOCKS_PATH,
    trusted_uid: int = 0,
    inventory_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _public_plan(
        _plan_context(
            desired_state,
            path=path,
            trusted_uid=trusted_uid,
            inventory_reader=inventory_reader,
        )
    )


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("snapshot lock registry directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("snapshot lock registry directory has unsafe ownership or mode")


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def apply_lock(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    path: Path = LOCKS_PATH,
    trusted_uid: int = 0,
    inventory_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("Btrfs snapshot lock update requires root")
    transaction: Any = nullcontext()
    if inventory_reader is None:
        from appliance import native_storage as storage
        from appliance.native_btrfs_snapshot import _lock_policy_inventory

        inventory_reader = _lock_policy_inventory
        # Snapshot deletion and automatic pruning already use this same
        # cross-process transaction. Recompute and persist the lock while it
        # is held so a successful lock cannot race behind a deletion check.
        transaction = storage._registry_transaction()
    with _LOCK, transaction:
        context = _plan_context(
            desired_state,
            path=path,
            trusted_uid=trusted_uid,
            inventory_reader=inventory_reader,
        )
        if context["planId"] != plan_id:
            raise BtrfsSnapshotLockPolicyError("Btrfs snapshot lock plan is stale; preview again")
        plan = _public_plan(context)
        if context["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        payload = _canonical(context["wanted"]) + b"\n"
        try:
            _atomic_write(path, payload)
            _exists, verified = read_registry(path, trusted_uid=trusted_uid)
            if verified != context["wanted"]:
                raise OSError("Btrfs snapshot lock verification failed")
        except OSError as exc:
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as rollback_exc:
                raise OSError(
                    "Btrfs snapshot lock update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("Btrfs snapshot lock update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "BtrfsSnapshotLockPolicyError",
    "DESIRED_SCHEMA",
    "LOCKS_PATH",
    "apply_lock",
    "locked_snapshot_ids",
    "plan_lock",
    "read_registry",
    "validate_registry",
]
