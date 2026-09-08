#!/usr/bin/env python3
"""Back up one coherent set of scheduled native Btrfs share snapshots."""

from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from deploy.appliance import nas_data_backup
except ModuleNotFoundError:  # pragma: no cover - standalone operations copy
    import nas_data_backup  # type: ignore[no-redef]

CONFIG_PATH = Path("/etc/echo-os/nas-data-backup-schedule.json")
CONFIG_SCHEMA = "echo.nas-data-backup-schedule.v1"
RESULT_SCHEMA = "echo.nas-data-backup-schedule-result.v1"
STATUS_SCHEMA = "echo.nas-data-backup-schedule-history.v1"
STATUS_PATH = Path("/var/lib/echo-os/nas-data-backup-history.json")
MAX_CONFIG_BYTES = 32 * 1024
MAX_STATUS_BYTES = 64 * 1024
MAX_STATUS_ATTEMPTS = 32
MAX_SNAPSHOT_AGE = timedelta(hours=26)
FUTURE_TOLERANCE = timedelta(minutes=5)
DEPLOYMENT_ROOT = Path("/opt/echo-os")
APPLIANCE_ENV = Path("/opt/echo-os/deploy/appliance/appliance.env")
STATE_ROOT = Path("/data")
NAS_ROOT = Path("/data/nas")
_SET_NAMESPACE = uuid.UUID("bc12b9dc-7764-4c95-88e2-58d96cbf8ac8")


class NasDataBackupScheduleError(RuntimeError):
    """The scheduled NAS backup could not be performed safely."""


def _strict_json(payload: bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise NasDataBackupScheduleError("NAS backup schedule has duplicate fields")
            value[key] = item
        return value

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=object_pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NasDataBackupScheduleError("NAS backup schedule is invalid JSON") from exc


def _absolute_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not 2 <= len(value) <= 4096:
        raise NasDataBackupScheduleError(f"{label} is invalid")
    if any(token in value for token in ("\0", "\r", "\n", "\\", "*", "?", "[")):
        raise NasDataBackupScheduleError(f"{label} is invalid")
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or any(
        part in {"", ".", ".."} for part in path.parts[1:]
    ):
        raise NasDataBackupScheduleError(f"{label} is invalid")
    return str(path)


def validate_config(value: Mapping[str, Any]) -> dict[str, Any]:
    expected = {"schema", "enabled", "repository", "repositoryMount"}
    if set(value) != expected or value.get("schema") != CONFIG_SCHEMA:
        raise NasDataBackupScheduleError("NAS backup schedule has an invalid schema")
    enabled = value.get("enabled")
    if not isinstance(enabled, bool):
        raise NasDataBackupScheduleError("NAS backup schedule enabled state is invalid")
    repository = value.get("repository")
    repository_mount = value.get("repositoryMount")
    if not enabled and repository is None and repository_mount is None:
        return {
            "schema": CONFIG_SCHEMA,
            "enabled": False,
            "repository": None,
            "repositoryMount": None,
        }
    repository = _absolute_path(repository, "NAS backup repository")
    repository_mount = _absolute_path(repository_mount, "NAS backup repository mount")
    repository_path = PurePosixPath(repository)
    mount_path = PurePosixPath(repository_mount)
    if repository_path == mount_path or mount_path not in repository_path.parents:
        raise NasDataBackupScheduleError("NAS backup repository must be below its mount")
    return {
        "schema": CONFIG_SCHEMA,
        "enabled": enabled,
        "repository": repository,
        "repositoryMount": repository_mount,
    }


def read_config(path: Path = CONFIG_PATH, *, owner: int = 0) -> tuple[bool, dict[str, Any]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return False, validate_config(
            {
                "schema": CONFIG_SCHEMA,
                "enabled": False,
                "repository": None,
                "repositoryMount": None,
            }
        )
    except OSError as exc:
        raise NasDataBackupScheduleError("NAS backup schedule is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or (os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600)
            or not 1 <= before.st_size <= MAX_CONFIG_BYTES
        ):
            raise NasDataBackupScheduleError("NAS backup schedule is unsafe")
        raw = bytearray()
        while len(raw) <= MAX_CONFIG_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_CONFIG_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        payload = bytes(raw)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_ino,
            before.st_dev,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_ino,
            after.st_dev,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise NasDataBackupScheduleError("NAS backup schedule changed while reading")
    finally:
        os.close(descriptor)
    value = _strict_json(payload)
    if not isinstance(value, dict):
        raise NasDataBackupScheduleError("NAS backup schedule is not an object")
    return True, validate_config(value)


def _validate_attempt(value: Any) -> dict[str, Any]:
    allowed = {
        "schema",
        "outcome",
        "completedAt",
        "setId",
        "snapshotId",
        "repositoryId",
        "memberCount",
        "encrypted",
        "fullReadVerified",
        "idempotent",
        "errorCode",
        "pathsRedacted",
    }
    if not isinstance(value, dict) or not set(value) <= allowed:
        raise NasDataBackupScheduleError("NAS backup history is invalid")
    if (
        value.get("schema") != RESULT_SCHEMA
        or value.get("outcome") not in {"completed", "disabled", "failed"}
        or value.get("pathsRedacted") is not True
        or not isinstance(value.get("completedAt"), str)
    ):
        raise NasDataBackupScheduleError("NAS backup history is invalid")
    try:
        completed = datetime.fromisoformat(value["completedAt"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise NasDataBackupScheduleError("NAS backup history is invalid") from exc
    if completed.tzinfo is None or completed.utcoffset() is None:
        raise NasDataBackupScheduleError("NAS backup history is invalid")
    return dict(value)


def read_history(path: Path = STATUS_PATH, *, owner: int = 0) -> dict[str, Any]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif path.is_symlink():
        raise NasDataBackupScheduleError("NAS backup history is unsafe")
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return {"schema": STATUS_SCHEMA, "attempts": []}
    except OSError as exc:
        raise NasDataBackupScheduleError("NAS backup history is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner
            or (os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600)
            or not 1 <= before.st_size <= MAX_STATUS_BYTES
        ):
            raise NasDataBackupScheduleError("NAS backup history is unsafe")
        raw = bytearray()
        while len(raw) <= MAX_STATUS_BYTES:
            chunk = os.read(descriptor, min(8192, MAX_STATUS_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        payload = bytes(raw)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
            before.st_ino,
            before.st_dev,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_ino,
            after.st_dev,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise NasDataBackupScheduleError("NAS backup history changed while reading")
    finally:
        os.close(descriptor)
    value = _strict_json(payload)
    if not isinstance(value, dict) or set(value) != {"schema", "attempts"}:
        raise NasDataBackupScheduleError("NAS backup history is invalid")
    attempts = value.get("attempts")
    if (
        value.get("schema") != STATUS_SCHEMA
        or not isinstance(attempts, list)
        or len(attempts) > MAX_STATUS_ATTEMPTS
    ):
        raise NasDataBackupScheduleError("NAS backup history is invalid")
    return {"schema": STATUS_SCHEMA, "attempts": [_validate_attempt(item) for item in attempts]}


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise OSError("private status write made no progress")
        offset += written


def record_attempt(
    result: Mapping[str, Any],
    *,
    completed_at: datetime | None = None,
    path: Path = STATUS_PATH,
    owner: int = 0,
    group: int = 0,
) -> dict[str, Any]:
    timestamp = (completed_at or datetime.now(UTC)).astimezone(UTC)
    attempt = _validate_attempt(
        {
            **dict(result),
            "completedAt": timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
        }
    )
    history = read_history(path, owner=owner)
    updated = {
        "schema": STATUS_SCHEMA,
        "attempts": [*history["attempts"], attempt][-MAX_STATUS_ATTEMPTS:],
    }
    payload = json.dumps(updated, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(payload) > MAX_STATUS_BYTES:
        raise NasDataBackupScheduleError("NAS backup history exceeds the safety limit")
    parent = path.parent
    try:
        parent_metadata = parent.lstat()
    except OSError as exc:
        raise NasDataBackupScheduleError("NAS backup history directory is unavailable") from exc
    if (
        not stat.S_ISDIR(parent_metadata.st_mode)
        or stat.S_ISLNK(parent_metadata.st_mode)
        or parent_metadata.st_uid != owner
        or (os.name == "posix" and parent_metadata.st_mode & 0o022)
    ):
        raise NasDataBackupScheduleError("NAS backup history directory is unsafe")
    temporary = parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(temporary, flags, 0o600)
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, owner, group)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, path)
        if os.name == "posix":
            directory_descriptor = os.open(
                parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
    except (OSError, NasDataBackupScheduleError) as exc:
        raise NasDataBackupScheduleError("NAS backup history could not be committed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()
    return updated


def _snapshot_time(name: str) -> datetime:
    try:
        return datetime.strptime(name, "auto-%Y%m%dt%H%M%Sz").replace(tzinfo=UTC)
    except ValueError as exc:
        raise NasDataBackupScheduleError("scheduled Btrfs snapshot name is invalid") from exc


def _common_snapshot_name(
    references: list[str],
    *,
    now: datetime,
    inventory_reader: Callable[[str], dict[str, Any]],
) -> str | None:
    common: set[str] | None = None
    for reference in references:
        inventory = inventory_reader(reference)
        if inventory.get("sharedFolderRef") != reference:
            raise NasDataBackupScheduleError("scheduled Btrfs snapshot inventory is invalid")
        snapshots = inventory.get("snapshots")
        if not isinstance(snapshots, list):
            raise NasDataBackupScheduleError("scheduled Btrfs snapshot inventory is invalid")
        names = {
            item["name"]
            for item in snapshots
            if isinstance(item, dict)
            and item.get("kind") == "automatic"
            and isinstance(item.get("name"), str)
        }
        common = names if common is None else common & names
    if not common:
        return None
    latest = max(common)
    age = now - _snapshot_time(latest)
    if age < -FUTURE_TOLERANCE or age > MAX_SNAPSHOT_AGE:
        return None
    return latest


def _private_member(shared_folder_ref: str, snapshot_name: str) -> dict[str, str]:
    from appliance import native_btrfs_snapshot, native_storage

    with native_storage._registry_transaction():
        entry, source, source_identity = native_btrfs_snapshot._resolve_share(shared_folder_ref)
        selected = next(
            (
                item
                for item in native_btrfs_snapshot._inventory(entry, source, source_identity)
                if item.get("name") == snapshot_name
            ),
            None,
        )
        if selected is None:
            raise NasDataBackupScheduleError("scheduled Btrfs snapshot set is incomplete")
        snapshot_path = native_btrfs_snapshot._snapshot_directory(entry, source) / snapshot_name
        filesystem_uuid = native_btrfs_snapshot._btrfs_filesystem_uuid(source)
    return {
        "sharedFolderRef": shared_folder_ref,
        "filesystemUuid": filesystem_uuid,
        "snapshotId": selected["snapshotId"],
        "sourceSnapshot": str(snapshot_path),
        "restoreTarget": str(source),
        "storageKind": "btrfsSubvolume",
    }


def _manifest(
    references: list[str],
    snapshot_name: str,
    member_builder: Callable[[str, str], dict[str, str]],
) -> dict[str, Any]:
    created_at = _snapshot_time(snapshot_name)
    identity = snapshot_name + "\0" + "\0".join(references)
    raw = {
        "schema": nas_data_backup.BACKUP_SET_SCHEMA,
        "setId": str(uuid.uuid5(_SET_NAMESPACE, identity)),
        "createdAt": created_at.isoformat(),
        "members": [member_builder(reference, snapshot_name) for reference in references],
    }
    return nas_data_backup._validate_backup_set_manifest(raw)


def _read_snapshot_policy() -> tuple[bool, dict[str, Any]]:
    from appliance.btrfs_snapshot_schedule_policy import read_policy

    return read_policy()


def _read_snapshot_inventory(shared_folder_ref: str) -> dict[str, Any]:
    from appliance.native_btrfs_snapshot import list_snapshots

    return list_snapshots(shared_folder_ref)


def _run_snapshot_schedule(**kwargs: Any) -> dict[str, Any]:
    from deploy.appliance.btrfs_snapshot_schedule_runner import run_schedule

    return run_schedule(**kwargs)


def run_scheduled_backup(
    *,
    now: datetime | None = None,
    config_reader: Callable[[], tuple[bool, dict[str, Any]]] = read_config,
    snapshot_policy_reader: Callable[[], tuple[bool, dict[str, Any]]] = _read_snapshot_policy,
    inventory_reader: Callable[[str], dict[str, Any]] = _read_snapshot_inventory,
    snapshot_runner: Callable[..., dict[str, Any]] = _run_snapshot_schedule,
    member_builder: Callable[[str, str], dict[str, str]] = _private_member,
    plan_fn: Callable[..., dict[str, Any]] = nas_data_backup.plan_backup_set,
    backup_fn: Callable[..., dict[str, Any]] = nas_data_backup.backup_set,
    password_reader: Callable[[], bytes] = nas_data_backup._password_from_credential,
) -> dict[str, Any]:
    configured, config = config_reader()
    if not configured or not config["enabled"]:
        return {
            "schema": RESULT_SCHEMA,
            "outcome": "disabled",
            "memberCount": 0,
            "pathsRedacted": True,
        }
    policy_configured, snapshot_policy = snapshot_policy_reader()
    shares = snapshot_policy.get("shares")
    if not policy_configured or not isinstance(shares, list) or not shares:
        raise NasDataBackupScheduleError("no scheduled Btrfs shares are configured")
    references = sorted(
        item["sharedFolderRef"]
        for item in shares
        if isinstance(item, dict) and isinstance(item.get("sharedFolderRef"), str)
    )
    if len(references) != len(shares) or len(references) != len(set(references)):
        raise NasDataBackupScheduleError("scheduled Btrfs share policy is invalid")
    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    snapshot_name = _common_snapshot_name(
        references, now=timestamp, inventory_reader=inventory_reader
    )
    if snapshot_name is None:
        snapshot_result = snapshot_runner(now=timestamp)
        if snapshot_result.get("errors") != 0:
            raise NasDataBackupScheduleError("scheduled Btrfs snapshot set is incomplete")
        snapshot_name = timestamp.strftime("auto-%Y%m%dt%H%M%Sz")
        if (
            _common_snapshot_name(
                references, now=timestamp, inventory_reader=inventory_reader
            )
            != snapshot_name
        ):
            raise NasDataBackupScheduleError("scheduled Btrfs snapshot set is incomplete")
    manifest = _manifest(references, snapshot_name, member_builder)
    common = {
        "manifest": manifest,
        "repository": Path(config["repository"]),
        "repository_mount": Path(config["repositoryMount"]),
        "deployment_root": DEPLOYMENT_ROOT,
        "appliance_env": APPLIANCE_ENV,
        "state_root_override": STATE_ROOT,
        "nas_root_override": NAS_ROOT,
    }
    plan = plan_fn(**common)
    plan_id = plan.get("planId")
    if not isinstance(plan_id, str) or len(plan_id) != 64:
        raise NasDataBackupScheduleError("NAS backup-set preflight result is invalid")
    result = backup_fn(**common, plan_id=plan_id, password=password_reader())
    if (
        result.get("setId") != manifest["setId"]
        or result.get("memberCount") != len(references)
        or result.get("encrypted") is not True
        or result.get("fullReadVerified") is not True
        or result.get("pathsRedacted") is not True
    ):
        raise NasDataBackupScheduleError("scheduled NAS backup was not verified")
    return {
        "schema": RESULT_SCHEMA,
        "outcome": "completed",
        "setId": result["setId"],
        "snapshotId": result["snapshotId"],
        "repositoryId": result["repositoryId"],
        "memberCount": result["memberCount"],
        "encrypted": True,
        "fullReadVerified": True,
        "idempotent": result.get("idempotent") is True,
        "pathsRedacted": True,
    }


def main() -> int:
    if not hasattr(os, "geteuid") or os.geteuid() != 0 or os.uname().sysname != "Linux":
        print(json.dumps({"schema": RESULT_SCHEMA, "outcome": "unsupported"}))
        return 1
    try:
        result = run_scheduled_backup()
    except (OSError, ValueError, NasDataBackupScheduleError, nas_data_backup.NasDataBackupError):
        result = {
            "schema": RESULT_SCHEMA,
            "outcome": "failed",
            "errorCode": "preflight_or_backup_failed",
            "pathsRedacted": True,
        }
        try:
            record_attempt(result)
        except NasDataBackupScheduleError:
            result["errorCode"] = "backup_and_history_failed"
        print(json.dumps(result, sort_keys=True, separators=(",", ":")), file=sys.stderr)
        return 2
    try:
        record_attempt(result)
    except NasDataBackupScheduleError:
        print(
            json.dumps(
                {
                    "schema": RESULT_SCHEMA,
                    "outcome": "failed",
                    "errorCode": "history_commit_failed",
                    "pathsRedacted": True,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by systemd
    raise SystemExit(main())
