"""Approval-bound daily snapshots with bounded count or age retention."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from appliance.omv_protocol import validate_omv_uuid

SCHEMA_VERSION = 2
LEGACY_SCHEMA_VERSION = 1
DESIRED_SCHEMA = "echo.btrfs-snapshot-schedule-desired.v2"
LEGACY_DESIRED_SCHEMA = "echo.btrfs-snapshot-schedule-desired.v1"
POLICY_PATH = Path("/etc/echo-os/btrfs-snapshot-schedule.json")
TIMER_PATH = Path("/etc/systemd/system/echo-btrfs-snapshot.timer")
SCHEDULE = "daily after 02:15 local time, randomized within 45 minutes"
MAX_FILE_BYTES = 32 * 1024
MAX_TIMER_BYTES = 16 * 1024
MAX_CONFIGURED_SHARES = 64
MIN_KEEP_LATEST = 1
MAX_KEEP_LATEST = 64
MIN_KEEP_DAYS = 1
MAX_KEEP_DAYS = 3650
MIN_KEEP_MONTHS = 1
MAX_KEEP_MONTHS = 120
RETENTION_MODES = frozenset({"latest", "days", "months"})
_POLICY_LOCK = threading.RLock()


class BtrfsSnapshotSchedulePolicyError(ValueError):
    """A scheduled-snapshot policy or desired state is invalid."""


def _retention(value: Mapping[str, Any]) -> dict[str, Any]:
    if set(value) != {"mode", "value"}:
        raise BtrfsSnapshotSchedulePolicyError("snapshot retention policy has an invalid schema")
    mode = value.get("mode")
    amount = value.get("value")
    if not isinstance(mode, str) or mode not in RETENTION_MODES:
        raise BtrfsSnapshotSchedulePolicyError("snapshot retention mode is invalid")
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise BtrfsSnapshotSchedulePolicyError("snapshot retention value is invalid")
    maximum = {
        "latest": MAX_KEEP_LATEST,
        "days": MAX_KEEP_DAYS,
        "months": MAX_KEEP_MONTHS,
    }[mode]
    if amount < 1 or amount > maximum:
        raise BtrfsSnapshotSchedulePolicyError("snapshot retention value is invalid")
    return {"mode": mode, "value": amount}


def _share(value: Mapping[str, Any], *, legacy: bool = False) -> dict[str, Any]:
    expected = (
        {"sharedFolderRef", "keepLatest"}
        if legacy
        else {
            "sharedFolderRef",
            "retention",
        }
    )
    if set(value) != expected:
        raise BtrfsSnapshotSchedulePolicyError("snapshot policy share has an invalid schema")
    reference = value.get("sharedFolderRef")
    if not isinstance(reference, str):
        raise BtrfsSnapshotSchedulePolicyError("snapshot policy share UUID is invalid")
    try:
        reference = validate_omv_uuid(reference).lower()
    except ValueError as exc:
        raise BtrfsSnapshotSchedulePolicyError("snapshot policy share UUID is invalid") from exc
    if legacy:
        keep_latest = value.get("keepLatest")
        retention = _retention({"mode": "latest", "value": keep_latest})
    else:
        raw_retention = value.get("retention")
        if not isinstance(raw_retention, Mapping):
            raise BtrfsSnapshotSchedulePolicyError("snapshot retention policy is invalid")
        retention = _retention(raw_retention)
    return {"sharedFolderRef": reference, "retention": retention}


def validate_policy(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"schemaVersion": SCHEMA_VERSION, "shares": []}
    if set(value) != {"schemaVersion", "shares"}:
        raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot policy has an unexpected schema")
    schema_version = value.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version not in {
        LEGACY_SCHEMA_VERSION,
        SCHEMA_VERSION,
    }:
        raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot policy has an unexpected schema")
    raw_shares = value.get("shares")
    if not isinstance(raw_shares, list) or len(raw_shares) > MAX_CONFIGURED_SHARES:
        raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot policy share list is invalid")
    shares: list[dict[str, Any]] = []
    for value in raw_shares:
        if not isinstance(value, dict):
            raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot policy share is invalid")
        shares.append(_share(value, legacy=schema_version == LEGACY_SCHEMA_VERSION))
    references = [item["sharedFolderRef"] for item in shares]
    if len(references) != len(set(references)):
        raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot policy has duplicate shares")
    shares.sort(key=lambda item: item["sharedFolderRef"])
    return {"schemaVersion": SCHEMA_VERSION, "shares": shares}


def _safe_read(path: Path, *, trusted_uid: int) -> tuple[bool, bytes | None]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False, None
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("Btrfs snapshot policy is not a regular file")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("Btrfs snapshot policy has unsafe ownership or mode")
    if metadata.st_size > MAX_FILE_BYTES:
        raise OSError("Btrfs snapshot policy exceeds the safety limit")
    payload = path.read_bytes()
    if len(payload) > MAX_FILE_BYTES:
        raise OSError("Btrfs snapshot policy exceeds the safety limit")
    return True, payload


def read_policy(path: Path = POLICY_PATH, *, trusted_uid: int = 0) -> tuple[bool, dict[str, Any]]:
    exists, payload = _safe_read(path, trusted_uid=trusted_uid)
    if payload is None:
        return False, validate_policy(None)
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise OSError("Btrfs snapshot policy is invalid JSON") from exc
    if not isinstance(value, dict):
        raise OSError("Btrfs snapshot policy is not a JSON object")
    try:
        return exists, validate_policy(value)
    except BtrfsSnapshotSchedulePolicyError as exc:
        raise OSError(str(exc)) from exc


def scheduler_installed(timer_path: Path = TIMER_PATH, *, trusted_uid: int = 0) -> bool:
    try:
        metadata = timer_path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and 0 < metadata.st_size <= MAX_TIMER_BYTES
        and metadata.st_uid == trusted_uid
        and (os.name != "posix" or metadata.st_mode & 0o022 == 0)
    )


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    schema = value.get("schema")
    expected = (
        {"schema", "sharedFolderRef", "enabled", "keepLatest"}
        if schema == LEGACY_DESIRED_SCHEMA
        else {"schema", "sharedFolderRef", "enabled", "retention"}
    )
    if set(value) != expected:
        raise BtrfsSnapshotSchedulePolicyError(
            "Btrfs snapshot schedule desired state has an unexpected schema"
        )
    if schema not in {LEGACY_DESIRED_SCHEMA, DESIRED_SCHEMA} or not isinstance(
        value.get("enabled"), bool
    ):
        raise BtrfsSnapshotSchedulePolicyError("Btrfs snapshot schedule desired state is invalid")
    share = _share(
        (
            {
                "sharedFolderRef": value.get("sharedFolderRef"),
                "keepLatest": value.get("keepLatest"),
            }
            if schema == LEGACY_DESIRED_SCHEMA
            else {
                "sharedFolderRef": value.get("sharedFolderRef"),
                "retention": value.get("retention"),
            }
        ),
        legacy=schema == LEGACY_DESIRED_SCHEMA,
    )
    return {"schema": DESIRED_SCHEMA, **share, "enabled": value["enabled"]}


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def policy_status(
    shared_folder_ref: str,
    path: Path = POLICY_PATH,
    *,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
) -> dict[str, Any]:
    try:
        reference = validate_omv_uuid(shared_folder_ref).lower()
    except ValueError as exc:
        raise BtrfsSnapshotSchedulePolicyError("sharedFolderRef is invalid") from exc
    configured, policy = read_policy(path, trusted_uid=trusted_uid)
    selected = next(
        (item for item in policy["shares"] if item["sharedFolderRef"] == reference), None
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sharedFolderRef": reference,
        "enabled": selected is not None,
        "retention": selected["retention"] if selected else {"mode": "latest", "value": 8},
        "keepLatest": (
            selected["retention"]["value"]
            if selected and selected["retention"]["mode"] == "latest"
            else 8
            if selected is None
            else None
        ),
        "configured": configured,
        "schedulerInstalled": scheduler_installed(timer_path, trusted_uid=trusted_uid),
        "schedule": SCHEDULE,
        "scope": "automaticSnapshotsOnly",
        "source": "localPolicy",
    }


def _plan_context(
    desired_state: Mapping[str, Any],
    *,
    path: Path = POLICY_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
    eligibility_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    desired = _desired(desired_state)
    installed = scheduler_installed(timer_path, trusted_uid=trusted_uid)
    if desired["enabled"] and not installed:
        raise OSError("Btrfs snapshot scheduler is not installed")
    if desired["enabled"]:
        if eligibility_reader is None:
            from appliance.native_btrfs_snapshot import list_snapshots

            eligibility_reader = list_snapshots
        inventory = eligibility_reader(desired["sharedFolderRef"])
        if inventory.get("sharedFolderRef") != desired["sharedFolderRef"]:
            raise OSError("Btrfs snapshot eligibility response is invalid")
    configured, current = read_policy(path, trusted_uid=trusted_uid)
    current_by_ref = {item["sharedFolderRef"]: item for item in current["shares"]}
    before = current_by_ref.get(desired["sharedFolderRef"])
    wanted_by_ref = dict(current_by_ref)
    if desired["enabled"]:
        wanted_by_ref[desired["sharedFolderRef"]] = {
            "sharedFolderRef": desired["sharedFolderRef"],
            "retention": desired["retention"],
        }
    else:
        wanted_by_ref.pop(desired["sharedFolderRef"], None)
    wanted = validate_policy(
        {"schemaVersion": SCHEMA_VERSION, "shares": list(wanted_by_ref.values())}
    )
    after = wanted_by_ref.get(desired["sharedFolderRef"])
    operation = (
        "none"
        if wanted == current
        else "disable"
        if after is None
        else "enable"
        if before is None
        else "update"
    )
    binding = {
        "schema": DESIRED_SCHEMA,
        "current": current,
        "desired": desired,
        "wanted": wanted,
        "configured": configured,
        "schedulerInstalled": installed,
        "operation": operation,
        "schedule": SCHEDULE,
        "scope": "automaticSnapshotsOnly",
    }
    return {
        **binding,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
        "requiresApproval": operation != "none",
        "safety": {
            "readOnlySnapshots": True,
            "manualSnapshotsPreserved": True,
            "retention": "boundedAutomaticOnly",
            "applicationQuiesce": False,
            "maximumConfiguredShares": MAX_CONFIGURED_SHARES,
        },
    }


def plan_policy(
    desired_state: Mapping[str, Any],
    *,
    path: Path = POLICY_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
    eligibility_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = _plan_context(
        desired_state,
        path=path,
        timer_path=timer_path,
        trusted_uid=trusted_uid,
        eligibility_reader=eligibility_reader,
    )
    return {
        key: value
        for key, value in context.items()
        if key not in {"current", "wanted", "configured", "schedulerInstalled"}
    }


def _assert_parent(path: Path, *, trusted_uid: int) -> None:
    metadata = path.parent.lstat()
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise OSError("Btrfs snapshot policy directory is unsafe")
    if metadata.st_uid != trusted_uid or (os.name == "posix" and metadata.st_mode & 0o022):
        raise OSError("Btrfs snapshot policy directory has unsafe ownership or mode")


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def apply_policy(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    path: Path = POLICY_PATH,
    timer_path: Path = TIMER_PATH,
    trusted_uid: int = 0,
    eligibility_reader: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("Btrfs snapshot schedule update requires root")
    with _POLICY_LOCK:
        context = _plan_context(
            desired_state,
            path=path,
            timer_path=timer_path,
            trusted_uid=trusted_uid,
            eligibility_reader=eligibility_reader,
        )
        if context["planId"] != plan_id:
            raise BtrfsSnapshotSchedulePolicyError(
                "Btrfs snapshot schedule plan is stale; preview again"
            )
        plan = {
            key: value
            for key, value in context.items()
            if key not in {"current", "wanted", "configured", "schedulerInstalled"}
        }
        if context["operation"] == "none":
            return {**plan, "applied": False, "verified": True}
        _assert_parent(path, trusted_uid=trusted_uid)
        existed, previous = _safe_read(path, trusted_uid=trusted_uid)
        payload = _canonical(context["wanted"]) + b"\n"
        try:
            _atomic_write(path, payload)
            _, verified = read_policy(path, trusted_uid=trusted_uid)
            if verified != context["wanted"]:
                raise OSError("Btrfs snapshot schedule verification failed")
        except OSError as exc:
            try:
                if existed and previous is not None:
                    _atomic_write(path, previous)
                elif path.exists() and not path.is_symlink():
                    path.unlink()
            except OSError as rollback_exc:
                raise OSError(
                    "Btrfs snapshot schedule update failed and rollback was incomplete"
                ) from rollback_exc
            raise OSError("Btrfs snapshot schedule update failed and was rolled back") from exc
        return {**plan, "applied": True, "verified": True}


__all__ = [
    "BtrfsSnapshotSchedulePolicyError",
    "DESIRED_SCHEMA",
    "LEGACY_DESIRED_SCHEMA",
    "POLICY_PATH",
    "SCHEDULE",
    "TIMER_PATH",
    "apply_policy",
    "plan_policy",
    "policy_status",
    "read_policy",
    "scheduler_installed",
]
