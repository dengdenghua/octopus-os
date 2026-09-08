"""Approval-bound, path-redacted restore policy for native NAS backup sets.

The Restic/Btrfs engine already owns the durable multi-volume transaction.  This
module adds the live-appliance boundary: it decrypts the configured systemd
credential, rebuilds the original private manifest from authenticated repository
tags, binds each source member to an explicitly selected empty managed Btrfs
target, and refuses a restore while any network publication or scheduled writer
can still reach those targets.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from appliance import nas_backup_credential_policy as credential_policy
from appliance import nas_backup_schedule_policy as schedule_policy
from deploy.appliance import nas_data_backup
from deploy.appliance import nas_data_backup_schedule_runner as schedule_runner

DESIRED_SCHEMA = "echo.nas-data-backup-restore-desired.v2"
REPOSITORY_SCHEMA = "echo.nas-data-backup-restore-repository.v1"
PLAN_SCHEMA = "echo.nas-data-backup-restore-plan.v1"
TARGET_LIST_SCHEMA = "echo.nas-data-backup-restore-target-list.v1"
MAX_VISIBLE_SETS = 50


class NasBackupRestorePolicyError(ValueError):
    """A safe public restore-policy failure."""

    def __init__(self, message: str, *, code: str = "restore_unavailable") -> None:
        super().__init__(message)
        self.code = code


def _repository(value: Mapping[str, Any]) -> dict[str, str]:
    if (
        set(value) != {"schema", "repository", "repositoryMount"}
        or value.get("schema") != REPOSITORY_SCHEMA
    ):
        raise NasBackupRestorePolicyError(
            "NAS restore repository request has an unexpected schema",
            code="invalid_request",
        )
    repository = value.get("repository")
    repository_mount = value.get("repositoryMount")
    if not isinstance(repository, str) or not isinstance(repository_mount, str):
        raise NasBackupRestorePolicyError(
            "NAS restore repository paths are invalid", code="invalid_request"
        )
    try:
        repository_path = nas_data_backup._canonical_posix_path(repository, "restore repository")
        mount_path = nas_data_backup._canonical_posix_path(
            repository_mount, "restore repository mount"
        )
    except nas_data_backup.NasDataBackupError as exc:
        raise NasBackupRestorePolicyError(
            "NAS restore repository paths are invalid", code="invalid_request"
        ) from exc
    if mount_path == PurePosixPath("/") or mount_path not in repository_path.parents:
        raise NasBackupRestorePolicyError(
            "NAS restore repository must be inside its external mount",
            code="invalid_request",
        )
    return {
        "schema": REPOSITORY_SCHEMA,
        "repository": repository_path.as_posix(),
        "repositoryMount": mount_path.as_posix(),
    }


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value)
        != {
            "schema",
            "selector",
            "repository",
            "repositoryMount",
            "targets",
        }
        or value.get("schema") != DESIRED_SCHEMA
    ):
        raise NasBackupRestorePolicyError(
            "NAS restore request has an unexpected schema", code="invalid_request"
        )
    selector = value.get("selector")
    if not isinstance(selector, str) or not 1 <= len(selector) <= 64:
        raise NasBackupRestorePolicyError(
            "NAS restore snapshot selector is invalid", code="invalid_request"
        )
    valid = selector == "latest" or re.fullmatch(r"[0-9a-f]{12,64}", selector) is not None
    if not valid:
        try:
            valid = nas_data_backup._canonical_uuid(selector, "backup-set selector") == selector
        except nas_data_backup.NasDataBackupError:
            valid = False
    if not valid:
        raise NasBackupRestorePolicyError(
            "NAS restore snapshot selector is invalid", code="invalid_request"
        )
    repository = _repository(
        {
            "schema": REPOSITORY_SCHEMA,
            "repository": value.get("repository"),
            "repositoryMount": value.get("repositoryMount"),
        }
    )
    raw_targets = value.get("targets")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise NasBackupRestorePolicyError(
            "NAS restore target mapping is required", code="invalid_request"
        )
    targets: list[dict[str, str]] = []
    source_refs: set[str] = set()
    target_refs: set[str] = set()
    for raw in raw_targets:
        if not isinstance(raw, Mapping) or set(raw) != {
            "sourceSharedFolderRef",
            "targetSharedFolderRef",
        }:
            raise NasBackupRestorePolicyError(
                "NAS restore target mapping is invalid", code="invalid_request"
            )
        try:
            source_ref = nas_data_backup._canonical_uuid(
                raw.get("sourceSharedFolderRef"), "restore source share"
            )
            target_ref = nas_data_backup._canonical_uuid(
                raw.get("targetSharedFolderRef"), "restore target share"
            )
        except nas_data_backup.NasDataBackupError as exc:
            raise NasBackupRestorePolicyError(
                "NAS restore target mapping is invalid", code="invalid_request"
            ) from exc
        if source_ref in source_refs or target_ref in target_refs:
            raise NasBackupRestorePolicyError(
                "NAS restore target mapping must be one-to-one", code="invalid_request"
            )
        source_refs.add(source_ref)
        target_refs.add(target_ref)
        targets.append(
            {
                "sourceSharedFolderRef": source_ref,
                "targetSharedFolderRef": target_ref,
            }
        )
    return {
        "schema": DESIRED_SCHEMA,
        "selector": selector,
        "repository": repository["repository"],
        "repositoryMount": repository["repositoryMount"],
        "targets": targets,
    }


def _runtime_config(repository_state: Mapping[str, Any]) -> dict[str, Any]:
    repository = _repository(repository_state)
    try:
        _configured, config = schedule_runner.read_config()
    except (OSError, ValueError) as exc:
        raise NasBackupRestorePolicyError("NAS backup configuration is unavailable") from exc
    if config.get("enabled") or schedule_policy.timer_enabled():
        raise NasBackupRestorePolicyError(
            "Disable the NAS backup schedule before restoring", code="writer_active"
        )
    try:
        if credential_policy.rotation_recovery_pending():
            raise NasBackupRestorePolicyError(
                "Finish backup credential recovery before restoring",
                code="credential_recovery_pending",
            )
    except OSError as exc:
        raise NasBackupRestorePolicyError("NAS backup credential state is unavailable") from exc
    return {
        "schema": schedule_runner.CONFIG_SCHEMA,
        "enabled": False,
        "repository": repository["repository"],
        "repositoryMount": repository["repositoryMount"],
    }


def _credential_password() -> bytes:
    try:
        encrypted, _identity = credential_policy._read_private_credential(
            credential_policy.CREDENTIAL_PATH, trusted_uid=0
        )
        password = credential_policy._run_systemd_creds(
            [
                "decrypt",
                f"--name={credential_policy.CREDENTIAL_NAME}",
                "-",
                "-",
            ],
            encrypted,
            runner=subprocess.run,
            systemd_creds=credential_policy.SYSTEMD_CREDS,
        )
    except (OSError, ValueError) as exc:
        raise NasBackupRestorePolicyError("NAS backup credential is unavailable") from exc
    if not password:
        raise NasBackupRestorePolicyError("NAS backup credential is unavailable")
    return password


def _engine_common(config: Mapping[str, Any], password: bytes) -> dict[str, Any]:
    return {
        "repository": Path(str(config["repository"])),
        "repository_mount": Path(str(config["repositoryMount"])),
        "deployment_root": schedule_runner.DEPLOYMENT_ROOT,
        "appliance_env": schedule_runner.APPLIANCE_ENV,
        "state_root_override": schedule_runner.STATE_ROOT,
        "nas_root_override": schedule_runner.NAS_ROOT,
        "password": password,
    }


def _authenticated_sets(
    config: Mapping[str, Any], password: bytes
) -> tuple[str, list[dict[str, Any]]]:
    common = _engine_common(config, password)
    try:
        repository, _nas = nas_data_backup._context(
            repository=common["repository"],
            repository_mount=common["repository_mount"],
            deployment_root=common["deployment_root"],
            appliance_env=common["appliance_env"],
            state_root_override=common["state_root_override"],
            nas_root_override=common["nas_root_override"],
        )
        with (
            nas_data_backup._operation_lock(),
            nas_data_backup._password_memfd(password) as descriptor,
        ):
            repository_id = nas_data_backup._repository_id(
                repository, descriptor, nas_data_backup._run
            )
            snapshots = nas_data_backup._backup_set_snapshots(
                repository, descriptor, nas_data_backup._run
            )
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError("NAS backup repository is unavailable") from exc
    set_ids = [item.get("setId") for item in snapshots]
    if len(set_ids) != len(set(set_ids)):
        raise NasBackupRestorePolicyError("NAS backup repository index is ambiguous")
    return repository_id, snapshots


def _recover_stale_restore_lock(selector: str, config: Mapping[str, Any], password: bytes) -> None:
    """Clear default-stale Restic locks only for a durable unfinished restore."""

    if selector == "latest":
        return
    try:
        receipt = nas_data_backup.BackupSetRestoreReceipts(
            nas_data_backup.RESTORE_SET_RECEIPTS, selector
        ).load()
    except nas_data_backup.NasDataBackupError as exc:
        raise NasBackupRestorePolicyError(
            "NAS restore recovery receipt is unavailable", code="receipt_unavailable"
        ) from exc
    if receipt is None or receipt.get("phase") == "verified":
        return
    if receipt.get("phase") not in {"preparing", "prepared", "promoting"}:
        raise NasBackupRestorePolicyError(
            "NAS restore recovery receipt is invalid", code="receipt_mismatch"
        )
    try:
        repository_id = receipt.get("repositoryId")
        recovered = nas_data_backup._unlock_stale_repository(
            **_engine_common(config, password),
            expected_repository_ids=[repository_id],
        )
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError(
            "NAS backup repository stale-lock recovery did not complete",
            code="repository_unlock_failed",
        ) from exc
    if not recovered:
        raise NasBackupRestorePolicyError(
            "NAS restore recovery receipt belongs to another repository",
            code="receipt_mismatch",
        )


def _recover_stale_restore_lock_for_listing(config: Mapping[str, Any], password: bytes) -> bool:
    """Recover a matching unfinished restore before the UI enumerates its sets."""

    try:
        expected = nas_data_backup.BackupSetRestoreReceipts.unfinished_repository_ids(
            nas_data_backup.RESTORE_SET_RECEIPTS
        )
        if not expected:
            return False
        return nas_data_backup._unlock_stale_repository(
            **_engine_common(config, password),
            expected_repository_ids=sorted(expected),
        )
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError(
            "NAS backup repository stale-lock recovery did not complete",
            code="repository_unlock_failed",
        ) from exc


def _ensure_target_quiesced(shared_folder_ref: str) -> tuple[Path, str]:
    """Resolve one managed target and reject every managed external writer."""
    from appliance import (
        btrfs_snapshot_schedule_policy,
        native_btrfs_snapshot,
        native_storage,
        native_time_machine,
    )

    try:
        entry, target, _identity = native_btrfs_snapshot._resolve_share(shared_folder_ref)
        if native_time_machine.dependency_for(shared_folder_ref) is not None:
            raise NasBackupRestorePolicyError(
                "Remove Time Machine publication before restoring", code="share_published"
            )
        share_name = entry.get("name")
        if not isinstance(share_name, str) or not share_name:
            raise OSError("managed share has no stable name")
        smb_listing = native_storage._run("net", "usershare", "list", timeout=15.0)
        if getattr(smb_listing, "state", "ok") != "ok":
            raise OSError("Samba usershare inventory is unavailable")
        smb_names = {line.strip().casefold() for line in smb_listing.splitlines() if line.strip()}
        if share_name.casefold() in smb_names:
            raise NasBackupRestorePolicyError(
                "Remove SMB publication before restoring", code="share_published"
            )
        if any(
            item.get("sharedFolderRef") == shared_folder_ref
            for item in native_storage._nfs_exports_load(allow_unmounted=True)
        ):
            raise NasBackupRestorePolicyError(
                "Remove NFS publication before restoring", code="share_published"
            )
        _configured, snapshot_policy = btrfs_snapshot_schedule_policy.read_policy()
        if any(
            item.get("sharedFolderRef") == shared_folder_ref
            for item in snapshot_policy.get("shares", [])
        ):
            raise NasBackupRestorePolicyError(
                "Disable automatic snapshots for restore targets first",
                code="writer_active",
            )
        filesystem_uuid = native_btrfs_snapshot._btrfs_filesystem_uuid(target)
    except NasBackupRestorePolicyError:
        raise
    except (OSError, ValueError) as exc:
        raise NasBackupRestorePolicyError(
            "A managed Btrfs restore target is unavailable",
            code="target_unavailable",
        ) from exc
    return target, filesystem_uuid


def _original_target_path(shared_folder_ref: str, source: str) -> str:
    """Reconstruct a source member's original path without requiring its disk online."""
    from appliance import native_storage

    try:
        entries = [
            entry
            for entry in native_storage._registry_load(strict=True)
            if native_storage._registered_uuid(entry, strict=True) == shared_folder_ref
        ]
        if len(entries) != 1:
            raise OSError("source share registry identity is unavailable")
        entry = entries[0]
        if native_storage._registered_storage_kind(entry) != "btrfsSubvolume":
            raise OSError("source share is not a managed Btrfs subvolume")
        relative_name = native_storage._registered_relative_name(entry, strict=True)
        assert relative_name is not None
        source_path = nas_data_backup._canonical_posix_path(source, "backup-set source snapshot")
        if (
            len(source_path.parts) < 4
            or source_path.parts[-3] != ".echo-snapshots"
            or source_path.parts[-2] != shared_folder_ref
        ):
            raise OSError("source snapshot layout is invalid")
        volume_root = PurePosixPath(*source_path.parts[:-3])
        return (volume_root / relative_name).as_posix()
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError(
            "Original NAS backup metadata is unavailable",
            code="source_metadata_unavailable",
        ) from exc


def _manifest_for_snapshot(
    selected: Mapping[str, Any],
    *,
    original_target_resolver: Callable[[str, str], str] = _original_target_path,
) -> dict[str, Any]:
    members = selected.get("members")
    paths = selected.get("paths")
    if not isinstance(members, list) or not isinstance(paths, list) or not members:
        raise NasBackupRestorePolicyError("NAS backup-set index is invalid")
    by_digest = {
        hashlib.sha256(path.encode("utf-8")).hexdigest(): path
        for path in paths
        if isinstance(path, str)
    }
    if len(by_digest) != len(paths):
        raise NasBackupRestorePolicyError("NAS backup-set source mapping is invalid")
    snapshot_names: set[str] = set()
    private_members: list[dict[str, str]] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise NasBackupRestorePolicyError("NAS backup-set member index is invalid")
        reference = member.get("sharedFolderRef")
        source_digest = member.get("sourcePathSha256")
        if not isinstance(reference, str) or not isinstance(source_digest, str):
            raise NasBackupRestorePolicyError("NAS backup-set member index is invalid")
        source = by_digest.get(source_digest)
        if source is None:
            raise NasBackupRestorePolicyError("NAS backup-set source mapping is invalid")
        source_path = PurePosixPath(source)
        snapshot_names.add(source_path.name)
        target_path = nas_data_backup._canonical_posix_path(
            original_target_resolver(reference, source), "original restore target"
        )
        private_members.append(
            {
                "sharedFolderRef": reference,
                "filesystemUuid": str(member.get("filesystemUuid")),
                "snapshotId": str(member.get("snapshotId")),
                "sourceSnapshot": source,
                "restoreTarget": target_path.as_posix(),
                "storageKind": "btrfsSubvolume",
            }
        )
    if len(snapshot_names) != 1:
        raise NasBackupRestorePolicyError("NAS backup-set snapshot names are inconsistent")
    try:
        created_at = schedule_runner._snapshot_time(next(iter(snapshot_names))).isoformat()
        manifest = nas_data_backup._validate_backup_set_manifest(
            {
                "schema": nas_data_backup.BACKUP_SET_SCHEMA,
                "setId": selected.get("setId"),
                "createdAt": created_at,
                "members": private_members,
            }
        )
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError(
            "Original NAS backup metadata cannot be authenticated",
            code="source_metadata_mismatch",
        ) from exc
    if manifest.get("manifestSha256") != selected.get("manifestSha256"):
        raise NasBackupRestorePolicyError(
            "Original NAS backup metadata does not match the authenticated index",
            code="source_metadata_mismatch",
        )
    return manifest


def _restore_targets(
    desired: Mapping[str, Any],
    manifest: Mapping[str, Any],
    *,
    target_resolver: Callable[[str], tuple[Path, str]] = _ensure_target_quiesced,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    expected = {str(member["sharedFolderRef"]) for member in manifest["members"]}
    requested = {
        str(item["sourceSharedFolderRef"]): str(item["targetSharedFolderRef"])
        for item in desired["targets"]
    }
    if set(requested) != expected:
        raise NasBackupRestorePolicyError(
            "Map every backup member to exactly one restore target",
            code="target_mapping_incomplete",
        )
    private: dict[str, dict[str, str]] = {}
    for source_ref, target_ref in requested.items():
        target, filesystem_uuid = target_resolver(target_ref)
        private[source_ref] = {
            "restoreTarget": PurePosixPath(target.as_posix()).as_posix(),
            "filesystemUuid": filesystem_uuid,
        }
    try:
        normalized = nas_data_backup._normalize_restore_targets(manifest, private)
    except nas_data_backup.NasDataBackupError as exc:
        raise NasBackupRestorePolicyError(
            "NAS restore target mapping is unsafe", code="target_mapping_invalid"
        ) from exc
    return normalized, requested


def _public_plan(
    plan: Mapping[str, Any],
    *,
    target_refs: Mapping[str, str] | None = None,
    recovery_pending: bool = False,
    already_verified: bool = False,
) -> dict[str, Any]:
    public_members = []
    for raw_member in plan["members"]:
        member = dict(raw_member)
        source_ref = str(member["sharedFolderRef"])
        member["sourceSharedFolderRef"] = source_ref
        member["targetSharedFolderRef"] = (
            target_refs[source_ref] if target_refs is not None else source_ref
        )
        public_members.append(member)
    return {
        "schema": PLAN_SCHEMA,
        "planId": plan["planId"],
        "operation": (
            "verifyRestore"
            if already_verified
            else "resumeRestore"
            if recovery_pending
            else "restoreBackupSet"
        ),
        "requiresApproval": True,
        "repositoryId": plan["repositoryId"],
        "snapshotId": plan["snapshotId"],
        "setId": plan["setId"],
        "manifestSha256": plan["manifestSha256"],
        "memberCount": plan["memberCount"],
        "members": public_members,
        "confirmation": plan["confirmation"],
        "recoveryPending": recovery_pending,
        "pathsRedacted": True,
        "safety": {
            "originalManagedBtrfsTargetsOnly": False,
            "emptyTargetsRequired": not (recovery_pending or already_verified),
            "networkSharesMustBeUnpublished": True,
            "scheduledWritersMustBeDisabled": True,
            "fullRepositoryReadBeforePromotion": True,
            "perShareAtomicPromotion": True,
            "durableResumeReceipt": True,
            "replacementDiskMappingSupported": True,
        },
    }


def list_restore_sets(
    repository_state: Mapping[str, Any], *, limit: int = MAX_VISIBLE_SETS
) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_VISIBLE_SETS:
        raise NasBackupRestorePolicyError(
            "NAS restore list limit is invalid", code="invalid_request"
        )
    config = _runtime_config(repository_state)
    password = _credential_password()
    _recover_stale_restore_lock_for_listing(config, password)
    try:
        listing = nas_data_backup.list_backup_sets(**_engine_common(config, password), limit=limit)
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError("NAS backup repository is unavailable") from exc
    return {
        **listing,
        "schema": "echo.nas-data-backup-restore-set-list.v1",
        "restoreMode": "explicit-empty-managed-btrfs-target-mapping",
        "pathsRedacted": True,
    }


def list_restore_targets() -> dict[str, Any]:
    """List unpublished managed Btrfs shares without exposing private paths."""
    from appliance import native_storage

    try:
        entries = native_storage._registry_load(strict=True)
        references = [native_storage._registered_uuid(entry, strict=True) for entry in entries]
        if None in references or len(references) != len(set(references)):
            raise OSError("managed restore target identity is ambiguous")
    except OSError as exc:
        raise NasBackupRestorePolicyError("NAS restore target inventory is unavailable") from exc
    targets: list[dict[str, Any]] = []
    for entry, reference in zip(entries, references, strict=True):
        try:
            name = native_storage._registered_relative_name(entry, strict=True)
            if reference is None or name is None:
                raise OSError("managed restore target identity is ambiguous")
            if native_storage._registered_storage_kind(entry) != "btrfsSubvolume":
                continue
            target, filesystem_uuid = _ensure_target_quiesced(reference)
            safe_target = nas_data_backup._safe_directory(target, "backup restore target")
            empty = next(safe_target.iterdir(), None) is None
        except (
            OSError,
            ValueError,
            NasBackupRestorePolicyError,
            nas_data_backup.NasDataBackupError,
        ):
            continue
        targets.append(
            {
                "sharedFolderRef": reference,
                "name": name,
                "filesystemUuid": filesystem_uuid,
                "empty": empty,
            }
        )
    targets.sort(key=lambda item: (str(item["name"]).casefold(), item["sharedFolderRef"]))
    return {
        "schema": TARGET_LIST_SCHEMA,
        "targetCount": len(targets),
        "targets": targets,
        "pathsRedacted": True,
    }


def _restore_context(
    desired_state: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, Any], dict[str, Any]]:
    desired = _desired(desired_state)
    config = _runtime_config(
        {
            "schema": REPOSITORY_SCHEMA,
            "repository": desired["repository"],
            "repositoryMount": desired["repositoryMount"],
        }
    )
    password = _credential_password()
    _recover_stale_restore_lock(desired["selector"], config, password)
    repository_id, snapshots = _authenticated_sets(config, password)
    try:
        selected = nas_data_backup._select_backup_set(desired["selector"], snapshots)
    except nas_data_backup.NasDataBackupError as exc:
        raise NasBackupRestorePolicyError(
            "Selected NAS backup set is missing or ambiguous", code="snapshot_unavailable"
        ) from exc
    manifest = _manifest_for_snapshot(selected)
    restore_targets, target_refs = _restore_targets(desired, manifest)
    return (
        desired,
        config,
        password,
        selected,
        {
            "repositoryId": repository_id,
            "manifest": manifest,
            "restoreTargets": restore_targets,
            "targetRefs": target_refs,
        },
    )


def plan_restore(desired_state: Mapping[str, Any]) -> dict[str, Any]:
    _desired_value, config, password, selected, context = _restore_context(desired_state)
    receipts = nas_data_backup.BackupSetRestoreReceipts(
        nas_data_backup.RESTORE_SET_RECEIPTS, str(selected["setId"])
    )
    try:
        receipt = receipts.load()
    except nas_data_backup.NasDataBackupError as exc:
        raise NasBackupRestorePolicyError("NAS restore recovery receipt is unavailable") from exc
    if receipt is not None:
        try:
            validated = nas_data_backup._validate_set_restore_receipt(
                receipt,
                manifest=context["manifest"],
                plan_id=str(receipt.get("planId") or ""),
                repository_id=context["repositoryId"],
                selected=selected,
                restore_targets=context["restoreTargets"],
            )
        except nas_data_backup.NasDataBackupError as exc:
            raise NasBackupRestorePolicyError("NAS restore recovery receipt is invalid") from exc
        synthetic = {
            "planId": validated["planId"],
            "repositoryId": validated["repositoryId"],
            "snapshotId": validated["snapshotId"],
            "setId": validated["setId"],
            "manifestSha256": validated["manifestSha256"],
            "memberCount": len(validated["members"]),
            "members": [],
            "confirmation": (
                f"RESTORE ECHO NAS SET {validated['setId']} SNAPSHOT {validated['snapshotId']}"
            ),
        }
        manifest_members = {
            member["sharedFolderRef"]: member for member in context["manifest"]["members"]
        }
        for member in validated["members"]:
            source = manifest_members[member["sharedFolderRef"]]
            synthetic["members"].append(
                {
                    "sharedFolderRef": member["sharedFolderRef"],
                    "filesystemUuid": source["filesystemUuid"],
                    "targetFilesystemUuid": member["filesystemUuid"],
                    "snapshotId": member["snapshotId"],
                    "targetSubvolumeUuid": member["targetSubvolumeUuid"],
                    "remapped": (
                        member["filesystemUuid"] != source["filesystemUuid"]
                        or member["restoreTarget"] != source["restoreTarget"]
                    ),
                }
            )
        return _public_plan(
            synthetic,
            target_refs=context["targetRefs"],
            recovery_pending=validated["phase"] != "verified",
            already_verified=validated["phase"] == "verified",
        )
    try:
        plan = nas_data_backup.plan_restore_set(
            manifest=context["manifest"],
            selector=str(selected["id"]),
            restore_targets=context["restoreTargets"],
            **_engine_common(config, password),
        )
    except (OSError, ValueError, nas_data_backup.NasDataBackupError) as exc:
        raise NasBackupRestorePolicyError(
            "Restore targets must be empty, unpublished managed Btrfs subvolumes",
            code="target_not_ready",
        ) from exc
    return _public_plan(plan, target_refs=context["targetRefs"])


def apply_restore(
    desired_state: Mapping[str, Any], plan_id: str, confirmation: str
) -> dict[str, Any]:
    plan = plan_restore(desired_state)
    if plan.get("planId") != plan_id:
        raise NasBackupRestorePolicyError(
            "NAS restore plan changed; preview again", code="stale_plan"
        )
    if plan.get("confirmation") != confirmation:
        raise NasBackupRestorePolicyError(
            "NAS restore confirmation does not match the exact backup set",
            code="confirmation_mismatch",
        )
    _desired_value, config, password, selected, context = _restore_context(desired_state)
    try:
        result = nas_data_backup.restore_set(
            manifest=context["manifest"],
            selector=str(selected["id"]),
            plan_id=plan_id,
            confirmation=confirmation,
            restore_targets=context["restoreTargets"],
            **_engine_common(config, password),
        )
    except nas_data_backup.NasDataBackupError as exc:
        public = exc.public()
        raise NasBackupRestorePolicyError(
            "NAS restore did not complete; retry the same approved plan",
            code=str(public.get("code") or "restore_failed"),
        ) from exc
    except (OSError, ValueError) as exc:
        raise NasBackupRestorePolicyError(
            "NAS restore did not complete; retry the same approved plan"
        ) from exc
    if (
        result.get("pathsRedacted") is not True
        or result.get("contentVerified") is not True
        or result.get("fullReadVerified") is not True
        or result.get("setId") != plan["setId"]
        or result.get("snapshotId") != plan["snapshotId"]
    ):
        raise OSError("NAS restore verification result is invalid")
    return {
        **result,
        "schema": "echo.nas-data-backup-restore-result.v1",
        "planId": plan_id,
        "verified": True,
        "pathsRedacted": True,
    }


__all__ = [
    "DESIRED_SCHEMA",
    "MAX_VISIBLE_SETS",
    "NasBackupRestorePolicyError",
    "PLAN_SCHEMA",
    "REPOSITORY_SCHEMA",
    "TARGET_LIST_SCHEMA",
    "apply_restore",
    "list_restore_sets",
    "list_restore_targets",
    "plan_restore",
]
