"""Guarded read-only Btrfs snapshots for Echo-managed shared folders.

Only shared folders originally created as Btrfs subvolumes are eligible.  The
managed snapshot tree is private, same-volume, and never returned through the
public API.  Every mutation is bound to a fresh deterministic plan.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import uuid
from pathlib import Path
from typing import Any

from appliance import native_storage as storage
from appliance.btrfs_snapshot_lock_policy import locked_snapshot_ids
from appliance.omv_protocol import (
    BTRFS_SNAPSHOT_DELETE_PLAN_SCHEMA,
    BTRFS_SNAPSHOT_PLAN_SCHEMA,
    BTRFS_SNAPSHOT_RESTORE_COPY_PLAN_SCHEMA,
    validate_btrfs_snapshot_delete_desired,
    validate_btrfs_snapshot_desired,
    validate_btrfs_snapshot_restore_copy_desired,
    validate_omv_uuid,
)

MAX_SNAPSHOTS_PER_SHARE = 256
_MAX_COMMAND_OUTPUT_BYTES = 64 * 1024
_SNAPSHOT_NAMESPACE = uuid.UUID("b12e7b15-6c89-49ec-925f-129781c7bf50")
# Btrfs subvolume UUIDs are raw 128-bit identifiers, not RFC 4122 UUIDs.
# Kernel-generated values may therefore have any hexadecimal nibble in the
# RFC version/variant positions (for example ``...-9a46-aec9-...``).
_BTRFS_UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_AUTOMATIC_NAME_PATTERN = re.compile(r"auto-[0-9]{8}t[0-9]{6}z")


def _run_read(*args: str, timeout: float = 15.0) -> str:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("Btrfs inspection command could not be completed") from exc
    stdout = completed.stdout or b""
    stderr = completed.stderr or b""
    if len(stdout) > _MAX_COMMAND_OUTPUT_BYTES or len(stderr) > _MAX_COMMAND_OUTPUT_BYTES:
        raise OSError("Btrfs inspection output exceeded the safety limit")
    if completed.returncode != 0:
        raise OSError("Btrfs inspection command failed")
    try:
        return stdout.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise OSError("Btrfs inspection output is not valid UTF-8") from exc


def _run_mutation(*args: str, timeout: float = 60.0) -> None:
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        completed = subprocess.run(
            list(args),
            capture_output=True,
            text=False,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("Btrfs snapshot command could not be completed") from exc
    if (
        len(completed.stdout or b"") > _MAX_COMMAND_OUTPUT_BYTES
        or len(completed.stderr or b"") > _MAX_COMMAND_OUTPUT_BYTES
    ):
        raise OSError("Btrfs snapshot command output exceeded the safety limit")
    if completed.returncode != 0:
        raise OSError("Btrfs snapshot command failed")


def _parse_subvolume_show(output: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for raw_line in output.splitlines():
        key, separator, value = raw_line.strip().partition(":")
        if separator and key in {"UUID", "Parent UUID", "Subvolume ID"}:
            values[key] = value.strip()
    raw_uuid = values.get("UUID", "")
    if _BTRFS_UUID_PATTERN.fullmatch(raw_uuid) is None:
        raise OSError("Btrfs subvolume has no stable UUID")
    raw_id = values.get("Subvolume ID", "")
    try:
        subvolume_id = int(raw_id)
    except ValueError as exc:
        raise OSError("Btrfs subvolume has no stable ID") from exc
    if subvolume_id <= 0:
        raise OSError("Btrfs subvolume has an invalid ID")
    parent_uuid = values.get("Parent UUID", "-")
    if parent_uuid != "-" and _BTRFS_UUID_PATTERN.fullmatch(parent_uuid) is None:
        raise OSError("Btrfs subvolume has an invalid parent UUID")
    return {
        "subvolumeUuid": raw_uuid.lower(),
        "subvolumeId": subvolume_id,
        "parentUuid": None if parent_uuid == "-" else parent_uuid.lower(),
    }


def _subvolume_identity(path: Path) -> dict[str, Any]:
    identity = _parse_subvolume_show(_run_read("btrfs", "subvolume", "show", str(path)))
    property_output = _run_read("btrfs", "property", "get", "-ts", str(path), "ro").strip()
    if property_output not in {"ro=true", "ro=false"}:
        raise OSError("Btrfs read-only property is invalid")
    return {**identity, "readOnly": property_output == "ro=true"}


def _snapshot_id(shared_folder_ref: str, name: str) -> str:
    return str(uuid.uuid5(_SNAPSHOT_NAMESPACE, f"{shared_folder_ref}:{name}"))


def _resolve_share(shared_folder_ref: str) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    matches = [
        entry
        for entry in storage._registry_load(strict=True)
        if storage._registered_uuid(entry) == shared_folder_ref
    ]
    if not matches:
        raise ValueError("sharedFolderRef does not match any native shared folder")
    if len(matches) != 1:
        raise OSError("native shared-folder registry contains duplicate UUIDs")
    entry = matches[0]
    if storage._registered_storage_kind(entry) != "btrfsSubvolume":
        raise ValueError("only Echo-managed Btrfs share subvolumes support snapshots")
    source = storage._native_folder_path(entry)
    source_identity = _subvolume_identity(source)
    if source_identity["readOnly"]:
        raise OSError("the shared-folder source subvolume is read-only")
    return entry, source, source_identity


def _private_directory(path: Path, *, expected_device: int) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != 0
        or (info.st_mode & 0o777) != 0o700
        or info.st_dev != expected_device
    ):
        raise OSError("managed Btrfs snapshot directory is unsafe")


def _snapshot_directory(entry: dict[str, Any], source: Path) -> Path:
    share_uuid = storage._registered_uuid(entry, strict=True)
    assert share_uuid is not None
    root = Path(str(entry.get("volumePath") or "")) / ".echo-snapshots"
    return root / share_uuid


def _ensure_snapshot_directory(entry: dict[str, Any], source: Path) -> Path:
    directory = _snapshot_directory(entry, source)
    expected_device = source.lstat().st_dev
    root = directory.parent
    for candidate in (root, directory):
        created = False
        try:
            candidate.mkdir(mode=0o700)
        except FileExistsError:
            _private_directory(candidate, expected_device=expected_device)
        else:
            created = True
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(candidate, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode) or info.st_dev != expected_device:
                raise OSError("managed Btrfs snapshot directory is unsafe")
            if created:
                os.fchown(descriptor, 0, 0)
                os.fchmod(descriptor, 0o700)
            elif info.st_uid != 0 or (info.st_mode & 0o777) != 0o700:
                raise OSError("managed Btrfs snapshot directory is unsafe")
            verified = os.fstat(descriptor)
            if verified.st_uid != 0 or (verified.st_mode & 0o777) != 0o700:
                raise OSError("managed Btrfs snapshot directory is unsafe")
        finally:
            os.close(descriptor)
    return directory


def _public_snapshot(
    shared_folder_ref: str,
    name: str,
    identity: dict[str, Any],
    *,
    locked: bool = False,
) -> dict[str, Any]:
    return {
        "snapshotId": _snapshot_id(shared_folder_ref, name),
        "name": name,
        "subvolumeUuid": identity["subvolumeUuid"],
        "readOnly": identity["readOnly"],
        "kind": "automatic" if _AUTOMATIC_NAME_PATTERN.fullmatch(name) else "manual",
        "locked": locked,
    }


def _inventory(
    entry: dict[str, Any], source: Path, source_identity: dict[str, Any]
) -> list[dict[str, Any]]:
    directory = _snapshot_directory(entry, source)
    if not directory.parent.exists():
        if directory.parent.is_symlink():
            raise OSError("managed Btrfs snapshot root is unsafe")
        return []
    _private_directory(directory.parent, expected_device=source.lstat().st_dev)
    if not directory.exists():
        if directory.is_symlink():
            raise OSError("managed Btrfs snapshot directory is unsafe")
        return []
    _private_directory(directory, expected_device=source.lstat().st_dev)
    shared_folder_ref = storage._registered_uuid(entry, strict=True)
    assert shared_folder_ref is not None
    locked_ids = locked_snapshot_ids(shared_folder_ref)
    snapshots: list[dict[str, Any]] = []
    try:
        with os.scandir(directory) as entries:
            children = sorted(entries, key=lambda child: child.name)
    except OSError as exc:
        raise OSError("managed Btrfs snapshots cannot be inspected") from exc
    for child in children:
        if _AUTOMATIC_NAME_PATTERN.fullmatch(child.name):
            normalized_name = child.name
        else:
            try:
                normalized_name = validate_btrfs_snapshot_desired(
                    {
                        "schema": "echo.omv.btrfs-snapshot-desired.v1",
                        "sharedFolderRef": shared_folder_ref,
                        "name": child.name,
                    }
                )["name"]
            except ValueError as exc:
                raise OSError("managed Btrfs snapshot tree contains an unsafe entry") from exc
        if not child.is_dir(follow_symlinks=False) or child.is_symlink():
            raise OSError("managed Btrfs snapshot tree contains a non-directory entry")
        path = Path(child.path)
        if path.lstat().st_dev != source.lstat().st_dev:
            raise OSError("managed Btrfs snapshot is not on the source filesystem")
        identity = _subvolume_identity(path)
        if not identity["readOnly"] or identity["parentUuid"] != source_identity["subvolumeUuid"]:
            raise OSError("managed Btrfs snapshot identity does not match its source")
        snapshot_id = _snapshot_id(shared_folder_ref, normalized_name)
        snapshots.append(
            _public_snapshot(
                shared_folder_ref,
                normalized_name,
                identity,
                locked=snapshot_id in locked_ids,
            )
        )
    if len(snapshots) > MAX_SNAPSHOTS_PER_SHARE:
        raise OSError("managed Btrfs snapshot count exceeds the supported limit")
    return snapshots


def list_snapshots(shared_folder_ref: str) -> dict[str, Any]:
    """List safe public metadata for one eligible shared folder."""
    normalized_ref = validate_omv_uuid(shared_folder_ref).lower()
    with storage._registry_transaction():
        entry, source, source_identity = _resolve_share(normalized_ref)
        snapshots = _inventory(entry, source, source_identity)
    return {
        "sharedFolderRef": normalized_ref,
        "snapshots": snapshots,
        "limit": MAX_SNAPSHOTS_PER_SHARE,
        "source": "native",
    }


def _lock_policy_inventory(shared_folder_ref: str) -> dict[str, Any]:
    """Read inventory while the caller already owns the shared-folder transaction."""
    normalized_ref = validate_omv_uuid(shared_folder_ref).lower()
    entry, source, source_identity = _resolve_share(normalized_ref)
    return {
        "sharedFolderRef": normalized_ref,
        "snapshots": _inventory(entry, source, source_identity),
    }


def _create_context(
    desired: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    entry, source, source_identity = _resolve_share(desired["sharedFolderRef"])
    snapshots = _inventory(entry, source, source_identity)
    existing = next((item for item in snapshots if item["name"] == desired["name"]), None)
    if existing is None and len(snapshots) >= MAX_SNAPSHOTS_PER_SHARE:
        raise ValueError("this shared folder already has the maximum 256 snapshots")
    base_revision = storage._canonical_hash(
        {
            "registryEntry": entry,
            "source": source_identity,
            "snapshots": snapshots,
        }
    )
    plan_id = storage._canonical_hash(
        {
            "schema": BTRFS_SNAPSHOT_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    plan = {
        "schema": BTRFS_SNAPSHOT_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "none" if existing else "create",
        "requiresApproval": existing is None,
        "sharedFolderRef": desired["sharedFolderRef"],
        "desired": desired,
        "changes": []
        if existing
        else [{"field": "snapshot", "before": "absent", "after": "readOnly"}],
        "safety": {
            "readOnly": True,
            "sameFilesystem": True,
            "applicationQuiesce": False,
            "maximumPerShare": MAX_SNAPSHOTS_PER_SHARE,
            "restoreSupported": False,
        },
        "snapshot": existing,
        "source": "native",
    }
    return plan, entry, source, source_identity


def _create_plan(desired: dict[str, Any]) -> dict[str, Any]:
    return _create_context(desired)[0]


def plan_snapshot(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_btrfs_snapshot_desired(dict(desired_state))
    with storage._registry_transaction():
        return _create_plan(desired)


def _apply_snapshot_desired(desired: dict[str, Any], plan_id: str) -> dict[str, Any]:
    with storage._registry_transaction():
        plan, entry, source, source_identity = _create_context(desired)
        if plan["planId"] != plan_id:
            raise ValueError("Btrfs snapshot plan is stale; preview the change again")
        if plan["operation"] == "none":
            return {**plan, "applied": True, "verified": True}
        directory = _ensure_snapshot_directory(entry, source)
        destination = directory / desired["name"]
        if destination.exists() or destination.is_symlink():
            raise ValueError("snapshot destination changed during apply; preview again")
        command_error: OSError | None = None
        try:
            _run_mutation("btrfs", "subvolume", "snapshot", "-r", str(source), str(destination))
        except OSError as exc:
            command_error = exc
        try:
            identity = _subvolume_identity(destination)
            if (
                not identity["readOnly"]
                or identity["parentUuid"] != source_identity["subvolumeUuid"]
                or destination.lstat().st_dev != source.lstat().st_dev
            ):
                raise OSError("created Btrfs snapshot did not match its source")
        except Exception as verify_error:
            # Only delete a failed target after a successful command.  When
            # the command itself failed, another root process may have won a
            # race for the name and that target is not ours to remove.
            if command_error is None and destination.exists() and not destination.is_symlink():
                try:
                    _run_mutation("btrfs", "subvolume", "delete", str(destination))
                except OSError as rollback_error:
                    raise OSError(
                        "Btrfs snapshot verification failed and rollback also failed"
                    ) from rollback_error
            if command_error is not None:
                raise command_error from verify_error
            if isinstance(verify_error, OSError):
                raise
            raise OSError("created Btrfs snapshot could not be verified") from verify_error
        snapshot = _public_snapshot(desired["sharedFolderRef"], desired["name"], identity)
        return {**plan, "applied": True, "verified": True, "snapshot": snapshot}


def apply_snapshot(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_btrfs_snapshot_desired(dict(desired_state))
    return _apply_snapshot_desired(desired, plan_id)


def _automatic_desired(shared_folder_ref: str, name: str) -> dict[str, Any]:
    normalized_ref = validate_omv_uuid(shared_folder_ref).lower()
    if _AUTOMATIC_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("automatic snapshot name is invalid")
    return {
        "schema": "echo.omv.btrfs-snapshot-desired.v1",
        "sharedFolderRef": normalized_ref,
        "name": name,
    }


def plan_automatic_snapshot(shared_folder_ref: str, name: str) -> dict[str, Any]:
    """Plan one scheduler-owned snapshot; this is not exposed as an HTTP route."""
    desired = _automatic_desired(shared_folder_ref, name)
    with storage._registry_transaction():
        return _create_plan(desired)


def apply_automatic_snapshot(shared_folder_ref: str, name: str, plan_id: str) -> dict[str, Any]:
    """Apply a pre-authorized scheduled snapshot using the normal safety checks."""
    return _apply_snapshot_desired(_automatic_desired(shared_folder_ref, name), plan_id)


def _restore_copy_context(
    desired: dict[str, Any],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Path,
    dict[str, Any],
    dict[str, Any],
    Path,
]:
    entry, source, source_identity = _resolve_share(desired["sharedFolderRef"])
    snapshots = _inventory(entry, source, source_identity)
    selected = next(
        (item for item in snapshots if item["snapshotId"] == desired["snapshotId"]), None
    )
    if selected is None:
        raise ValueError("snapshotId does not match a managed snapshot for this shared folder")
    volume_ref = entry.get("mountPointRef")
    volume_path = entry.get("volumePath")
    if not isinstance(volume_ref, str) or not isinstance(volume_path, str):
        raise OSError("native shared-folder registry has no stable volume identity")
    normalized_volume_ref = validate_omv_uuid(volume_ref).lower()
    writable_path = storage._writable_targets().get(normalized_volume_ref)
    if writable_path is None or os.path.normpath(writable_path) != os.path.normpath(volume_path):
        raise ValueError("snapshot source volume is not currently mounted writable")
    target = storage._shared_folder_target_payload(normalized_volume_ref, volume_path)
    if str(target.get("type") or "").casefold() != "btrfs":
        raise OSError("snapshot source registry no longer resolves to a Btrfs volume")
    registry = storage._registry_load(strict=True)
    target_uuid = storage._share_uuid(normalized_volume_ref, desired["name"])
    if any(
        item.get("uuid") == target_uuid
        or (
            item.get("mountPointRef") == normalized_volume_ref
            and item.get("name") == desired["name"]
        )
        for item in registry
    ):
        raise ValueError("restore-copy shared folder already exists")
    destination = Path(volume_path) / desired["name"]
    target_state = storage._target_state(destination)
    if target_state["kind"] != "absent":
        raise ValueError("restore-copy target already exists outside the native registry")
    snapshot_path = _snapshot_directory(entry, source) / selected["name"]
    base_revision = storage._canonical_hash(
        {
            "registry": registry,
            "sourceEntry": entry,
            "sourceIdentity": source_identity,
            "snapshots": snapshots,
            "selected": selected,
            "target": target,
            "targetState": target_state,
        }
    )
    plan_id = storage._canonical_hash(
        {
            "schema": BTRFS_SNAPSHOT_RESTORE_COPY_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    plan = {
        "schema": BTRFS_SNAPSHOT_RESTORE_COPY_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "createRecoveredShare",
        "requiresApproval": True,
        "desired": desired,
        "sourceSnapshot": selected,
        "recoveredShareUuid": target_uuid,
        "target": target,
        "changes": [{"field": "sharedFolder", "before": "absent", "after": desired["name"]}],
        "safety": {
            "sourceShareUntouched": True,
            "sourceSnapshotUntouched": True,
            "sameFilesystem": True,
            "destinationMustBeAbsent": True,
            "writableRecoveredCopy": True,
            "applicationQuiesce": False,
            "fullVolumeRollback": False,
        },
        "source": "native",
    }
    return plan, entry, source, selected, registry, snapshot_path


def plan_snapshot_restore_copy(desired_state: dict[str, Any]) -> dict[str, Any]:
    """Preview recovery as a new writable share without replacing live data."""
    desired = validate_btrfs_snapshot_restore_copy_desired(dict(desired_state))
    with storage._registry_transaction():
        return _restore_copy_context(desired)[0]


def apply_snapshot_restore_copy(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    """Clone one read-only snapshot into a new, registered writable share."""
    desired = validate_btrfs_snapshot_restore_copy_desired(dict(desired_state))
    with storage._registry_transaction():
        plan, entry, _source, selected, original_registry, snapshot_path = _restore_copy_context(
            desired
        )
        if plan["planId"] != plan_id:
            raise ValueError("Btrfs snapshot restore-copy plan is stale; preview again")
        volume_ref = validate_omv_uuid(str(entry["mountPointRef"])).lower()
        volume_path = str(entry["volumePath"])
        destination = Path(volume_path) / desired["name"]
        group_gid = storage._users_group_gid()
        created = False
        registry_changed = False
        try:
            _run_mutation("btrfs", "subvolume", "snapshot", str(snapshot_path), str(destination))
            created = True
            identity = _subvolume_identity(destination)
            if (
                identity["readOnly"]
                or identity["parentUuid"] != selected["subvolumeUuid"]
                or destination.lstat().st_dev != snapshot_path.lstat().st_dev
            ):
                raise OSError("recovered Btrfs share does not match the selected snapshot")
            storage._configure_shared_folder(destination, group_gid)
            entry = storage._registry_folder_entry(
                volume_ref=volume_ref,
                volume_path=volume_path,
                name=desired["name"],
                comment=f"Recovered from snapshot {selected['name']}",
                storage_kind="btrfsSubvolume",
            )
            if (
                entry.get("uuid") != plan["recoveredShareUuid"]
                or entry.get("mountPointRef") != volume_ref
                or entry.get("name") != desired["name"]
                or entry.get("storageKind") != "btrfsSubvolume"
            ):
                raise OSError("recovered shared-folder registry identity is invalid")
            registry = [*original_registry, entry]
            storage._registry_save(registry)
            registry_changed = True
            observed = storage._registry_load(strict=True)
            observed_entry = next(
                (item for item in observed if item.get("uuid") == plan["recoveredShareUuid"]),
                None,
            )
            verified_identity = _subvolume_identity(destination)
            if (
                observed_entry != entry
                or not storage._verify_shared_folder(destination, group_gid)
                or verified_identity["readOnly"]
                or verified_identity["parentUuid"] != selected["subvolumeUuid"]
            ):
                raise OSError("recovered Btrfs shared folder verification failed")
        except Exception as exc:
            rollback_errors: list[Exception] = []
            if registry_changed:
                try:
                    storage._registry_save(original_registry)
                    if storage._registry_load(strict=True) != original_registry:
                        raise OSError("shared-folder registry rollback was not verified")
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if created and destination.exists() and not destination.is_symlink():
                try:
                    _run_mutation("btrfs", "subvolume", "delete", str(destination))
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if rollback_errors:
                raise OSError(
                    "Btrfs snapshot restore-copy failed and rollback was incomplete"
                ) from rollback_errors[0]
            if isinstance(exc, (OSError, ValueError)):
                raise
            raise OSError("Btrfs snapshot restore-copy failed") from exc
        return {
            **plan,
            "applied": True,
            "verified": True,
            "sharedFolder": storage._public_shared_folder_entry(entry),
        }


def _delete_context(
    desired: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], Path, dict[str, Any]]:
    entry, source, source_identity = _resolve_share(desired["sharedFolderRef"])
    snapshots = _inventory(entry, source, source_identity)
    selected = next(
        (item for item in snapshots if item["snapshotId"] == desired["snapshotId"]), None
    )
    if selected is None:
        raise ValueError("snapshotId does not match a managed snapshot for this shared folder")
    if selected.get("locked") is True:
        raise ValueError("locked snapshots must be unlocked before deletion")
    base_revision = storage._canonical_hash(
        {
            "registryEntry": entry,
            "source": source_identity,
            "snapshots": snapshots,
            "selected": selected,
        }
    )
    plan_id = storage._canonical_hash(
        {
            "schema": BTRFS_SNAPSHOT_DELETE_PLAN_SCHEMA,
            "baseRevision": base_revision,
            "desired": desired,
        }
    )
    plan = {
        "schema": BTRFS_SNAPSHOT_DELETE_PLAN_SCHEMA,
        "planId": plan_id,
        "baseRevision": base_revision,
        "operation": "delete",
        "requiresApproval": True,
        "sharedFolderRef": desired["sharedFolderRef"],
        "desired": desired,
        "snapshot": selected,
        "changes": [{"field": "snapshot", "before": "readOnly", "after": "deleted"}],
        "safety": {"sourceUntouched": True, "recursive": False, "restoreSupported": False},
        "source": "native",
    }
    return plan, entry, source, source_identity


def _delete_plan(desired: dict[str, Any]) -> dict[str, Any]:
    return _delete_context(desired)[0]


def plan_snapshot_delete(desired_state: dict[str, Any]) -> dict[str, Any]:
    desired = validate_btrfs_snapshot_delete_desired(dict(desired_state))
    with storage._registry_transaction():
        return _delete_plan(desired)


def apply_snapshot_delete(desired_state: dict[str, Any], plan_id: str) -> dict[str, Any]:
    desired = validate_btrfs_snapshot_delete_desired(dict(desired_state))
    with storage._registry_transaction():
        plan, entry, source, source_identity = _delete_context(desired)
        if plan["planId"] != plan_id:
            raise ValueError("Btrfs snapshot delete plan is stale; preview the change again")
        selected = plan["snapshot"]
        destination = _snapshot_directory(entry, source) / selected["name"]
        try:
            _run_mutation("btrfs", "subvolume", "delete", str(destination))
        except OSError:
            if destination.exists() or destination.is_symlink():
                raise
        if destination.exists() or destination.is_symlink():
            raise OSError("deleted Btrfs snapshot is still present")
        current_source = _subvolume_identity(source)
        if current_source != source_identity:
            raise OSError("shared-folder source identity changed during snapshot deletion")
        return {**plan, "applied": True, "verified": True, "snapshotDeleted": True}
