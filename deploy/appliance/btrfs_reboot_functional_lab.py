#!/usr/bin/env python3
"""Reboot-verify native Btrfs RAID1 provisioning on dedicated VM disks."""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import sys
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows imports exercise pure helpers only
    fcntl = None  # type: ignore[assignment]

try:
    from deploy.appliance import btrfs_provision_functional_lab as provision
except ModuleNotFoundError:
    import btrfs_provision_functional_lab as provision

base = provision.base
STATE_PATH = Path("/var/lib/echo-os/btrfs-provision-reboot-lab-state.json")
STATE_SCHEMA = "echo-btrfs-provision-reboot-lab-state-v1"
PHASES = ("provision", "reboot-verify-cleanup")


class BtrfsRebootFunctionalLabError(RuntimeError):
    """The two-phase reboot lab was unsafe or did not verify."""


def _boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> str:
    try:
        value = path.read_text(encoding="ascii").strip().lower()
        parsed = uuid.UUID(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise BtrfsRebootFunctionalLabError("kernel boot ID is unavailable") from exc
    if str(parsed) != value:
        raise BtrfsRebootFunctionalLabError("kernel boot ID is not canonical")
    return value


def _saved_record(saved: base.SavedFile) -> dict[str, Any]:
    return {
        "path": str(saved.path),
        "existed": saved.existed,
        "payload": (
            base64.b64encode(saved.payload).decode("ascii") if saved.payload is not None else None
        ),
        "mode": saved.mode,
    }


def _saved_files(value: Any) -> list[base.SavedFile]:
    if not isinstance(value, list) or len(value) != 2:
        raise BtrfsRebootFunctionalLabError("recovery state has no exact saved-file set")
    expected = [str(base.AUTH_PATH), str(provision.FSTAB_PATH)]
    restored: list[base.SavedFile] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != {"path", "existed", "payload", "mode"}:
            raise BtrfsRebootFunctionalLabError("recovery state saved-file record is invalid")
        if item.get("path") != expected[index] or not isinstance(item.get("existed"), bool):
            raise BtrfsRebootFunctionalLabError("recovery state path identity is invalid")
        existed = item["existed"]
        encoded = item.get("payload")
        mode = item.get("mode")
        if existed:
            if (
                not isinstance(encoded, str)
                or not isinstance(mode, int)
                or isinstance(mode, bool)
                or mode not in ({0o600}, {0o600, 0o644})[index]
            ):
                raise BtrfsRebootFunctionalLabError("recovery state file metadata is invalid")
            try:
                payload = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise BtrfsRebootFunctionalLabError(
                    "recovery state file payload is invalid"
                ) from exc
            if len(payload) > base.MAX_RESPONSE_BYTES:
                raise BtrfsRebootFunctionalLabError("recovery state file payload is too large")
        else:
            if encoded is not None or mode is not None:
                raise BtrfsRebootFunctionalLabError("absent recovery file has unexpected data")
            payload = None
        restored.append(base.SavedFile(Path(expected[index]), existed, payload, mode))
    return restored


def _state_material(
    *,
    saved_files: Sequence[base.SavedFile],
    baseline_boot_id: str,
    status: str,
    filesystem_uuid: str | None = None,
    payload_sha256: str | None = None,
    plan_id: str | None = None,
) -> dict[str, Any]:
    if status not in {"prepared", "provisioned"}:
        raise BtrfsRebootFunctionalLabError("recovery state status is invalid")
    return {
        "schema": STATE_SCHEMA,
        "status": status,
        "baselineBootId": baseline_boot_id,
        "filesystemUuid": filesystem_uuid,
        "payloadSha256": payload_sha256,
        "planId": plan_id,
        "savedFiles": [_saved_record(saved) for saved in saved_files],
    }


def _safe_state_parent() -> None:
    try:
        metadata = STATE_PATH.parent.lstat()
    except OSError as exc:
        raise BtrfsRebootFunctionalLabError("recovery state directory is unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or STATE_PATH.parent.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise BtrfsRebootFunctionalLabError("recovery state directory is unsafe")


def _write_state(value: Mapping[str, Any], *, replace: bool) -> None:
    _safe_state_parent()
    if not replace and (STATE_PATH.exists() or STATE_PATH.is_symlink()):
        raise BtrfsRebootFunctionalLabError("a reboot provisioning recovery state already exists")
    if replace:
        _read_state(expected_status="prepared")
    base._atomic_write(STATE_PATH, base._canonical(value), mode=0o600)


def _read_state(*, expected_status: str = "provisioned") -> dict[str, Any]:
    _safe_state_parent()
    try:
        metadata = STATE_PATH.lstat()
    except OSError as exc:
        raise BtrfsRebootFunctionalLabError(
            "reboot provisioning recovery state is missing"
        ) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > base.MAX_RESPONSE_BYTES
    ):
        raise BtrfsRebootFunctionalLabError("reboot provisioning recovery state is unsafe")
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BtrfsRebootFunctionalLabError(
            "reboot provisioning recovery state is invalid"
        ) from exc
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "status",
            "baselineBootId",
            "filesystemUuid",
            "payloadSha256",
            "planId",
            "savedFiles",
        }
        or value.get("schema") != STATE_SCHEMA
        or value.get("status") != expected_status
    ):
        raise BtrfsRebootFunctionalLabError("reboot provisioning recovery state shape is invalid")
    try:
        if str(uuid.UUID(str(value.get("baselineBootId")))) != value.get("baselineBootId"):
            raise ValueError
    except ValueError as exc:
        raise BtrfsRebootFunctionalLabError("recovery state boot identity is invalid") from exc
    _saved_files(value.get("savedFiles"))
    if expected_status == "provisioned" and (
        provision.FS_UUID.fullmatch(str(value.get("filesystemUuid"))) is None
        or not isinstance(value.get("payloadSha256"), str)
        or len(value["payloadSha256"]) != 64
        or any(character not in "0123456789abcdef" for character in value["payloadSha256"])
        or not isinstance(value.get("planId"), str)
        or len(value["planId"]) != 64
        or any(character not in "0123456789abcdef" for character in value["planId"])
    ):
        raise BtrfsRebootFunctionalLabError("provisioned recovery identity is invalid")
    return dict(value)


@contextmanager
def _lock() -> Any:
    provision.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        provision.LOCK_PATH,
        os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise BtrfsRebootFunctionalLabError("functional lab lock is unsafe")
        if fcntl is None:
            raise BtrfsRebootFunctionalLabError("functional lab requires POSIX locking")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BtrfsRebootFunctionalLabError("another provisioning lab is active") from exc
        yield
    finally:
        os.close(descriptor)


def _api_create(
    base_url: str, password: str, saved_files: Sequence[base.SavedFile]
) -> dict[str, Any]:
    base._set_test_password(saved_files[0], password)
    provision._checked(["systemctl", "restart", "echo-appliance.service"], "service restart")
    base._wait_health(base._http_call, base_url)
    token = base._admin_token(saved_files[0])
    candidates = base._request(
        base._http_call,
        base_url,
        "GET",
        "/api/appliance/omv/volumes/btrfs-raid1/candidates",
        expected=200,
        token=token,
    )
    selected = provision._bound_candidates(candidates)
    desired = {
        "schema": "echo.omv.btrfs-raid1-desired.v1",
        "name": provision.MOUNTPOINT.name,
        "devices": list(provision.DEVICES),
        "dataLossConfirmed": True,
    }
    plan, result = base._planned_apply(
        base._http_call,
        base_url,
        token,
        password,
        desired=desired,
        plan_path="/api/appliance/omv/volumes/btrfs-raid1/plan",
        apply_path="/api/appliance/omv/volumes/btrfs-raid1/apply",
        action="omv.btrfs-raid1.create",
    )
    filesystem = result.get("filesystem")
    if (
        result.get("applied") is not True
        or not isinstance(filesystem, dict)
        or filesystem.get("devices") != list(provision.DEVICES)
        or filesystem.get("mountpoint") != str(provision.MOUNTPOINT)
    ):
        raise BtrfsRebootFunctionalLabError("HTTP Btrfs creation result is invalid")
    filesystem_uuid = str(filesystem.get("uuid", "")).casefold()
    payload_sha256 = provision._write_payload()
    verified = provision._verify_host(filesystem_uuid, payload_sha256)
    return {
        "planId": plan["planId"],
        "filesystemUuid": filesystem_uuid,
        "payloadSha256": payload_sha256,
        "selectedDeviceCount": len(selected),
        "verified": verified,
    }


def _provision_phase(*, base_url: str, password: str) -> dict[str, Any]:
    host = provision._preflight()
    baseline_boot_id = _boot_id()
    from appliance.native_storage_pool import inspect_blank_whole_disks

    provision._bound_candidates({"devices": inspect_blank_whole_disks(list(provision.DEVICES))})
    saved_files = [base._save_file(base.AUTH_PATH), base._save_file(provision.FSTAB_PATH)]
    _write_state(
        _state_material(
            saved_files=saved_files,
            baseline_boot_id=baseline_boot_id,
            status="prepared",
        ),
        replace=False,
    )
    try:
        created = _api_create(base_url, password, saved_files)
        _write_state(
            _state_material(
                saved_files=saved_files,
                baseline_boot_id=baseline_boot_id,
                status="provisioned",
                filesystem_uuid=created["filesystemUuid"],
                payload_sha256=created["payloadSha256"],
                plan_id=created["planId"],
            ),
            replace=True,
        )
    except Exception:
        try:
            provision._cleanup(saved_files=saved_files, devices_bound=True)
            STATE_PATH.unlink(missing_ok=True)
        except Exception as cleanup_exc:
            raise BtrfsRebootFunctionalLabError(
                "provision phase failed and recovery was incomplete"
            ) from cleanup_exc
        raise
    return {
        "schemaVersion": 1,
        "kind": "echo-btrfs-provision-reboot-phase-result",
        "phase": "provision",
        "outcome": "verified",
        "host": host,
        "http": {"planApprovalApply": True},
        "filesystem": {
            **created["verified"],
            "payloadBytes": provision.PAYLOAD_BYTES,
        },
        "rebootRequired": True,
        "observedAt": datetime.now(UTC).isoformat(),
    }


def _reboot_verify_cleanup_phase(*, base_url: str) -> dict[str, Any]:
    state = _read_state()
    saved_files = _saved_files(state["savedFiles"])
    current_boot_id = _boot_id()
    if current_boot_id == state["baselineBootId"]:
        raise BtrfsRebootFunctionalLabError("reboot verification requires a new kernel boot")
    verification_error: Exception | None = None
    result: dict[str, Any] | None = None
    try:
        base._wait_health(base._http_call, base_url)
        token = base._admin_token(saved_files[0])
        verified = provision._verify_host(state["filesystemUuid"], state["payloadSha256"])
        from appliance.native_btrfs import managed_btrfs_filesystems

        if managed_btrfs_filesystems() != [
            {"uuid": state["filesystemUuid"], "mountpoint": str(provision.MOUNTPOINT)}
        ]:
            raise BtrfsRebootFunctionalLabError("managed fstab identity drifted after reboot")
        maintenance = base._request(
            base._http_call,
            base_url,
            "GET",
            "/api/appliance/omv/volumes/btrfs-raid1/maintenance",
            expected=200,
            token=token,
        )
        records = maintenance.get("filesystems")
        if not isinstance(records, list) or not any(
            isinstance(item, dict)
            and item.get("filesystem", {}).get("uuid") == state["filesystemUuid"]
            and item.get("canStartScrub") is True
            for item in records
        ):
            raise BtrfsRebootFunctionalLabError("rebooted RAID1 is absent from maintenance")
        result = {
            "schemaVersion": 1,
            "kind": "echo-btrfs-provision-reboot-phase-result",
            "phase": "reboot-verify-cleanup",
            "outcome": "verified",
            "filesystem": {
                **verified,
                "payloadBytes": provision.PAYLOAD_BYTES,
                "mountedAfterReboot": True,
                "dataPreserved": True,
                "managedFstabReadBack": True,
            },
            "http": {"maintenanceReadBack": True},
            "cleanup": {
                "globalStateRestored": True,
                "filesystemSignaturesRemoved": True,
            },
            "observedAt": datetime.now(UTC).isoformat(),
        }
    except Exception as exc:
        verification_error = exc
    try:
        provision._cleanup(saved_files=saved_files, devices_bound=True)
        STATE_PATH.unlink()
    except Exception as exc:
        raise BtrfsRebootFunctionalLabError("reboot verification cleanup was incomplete") from exc
    if verification_error is not None:
        raise BtrfsRebootFunctionalLabError("reboot verification failed") from verification_error
    assert result is not None
    return result


def run_phase(*, phase: str, base_url: str, password: str) -> dict[str, Any]:
    base_url = base._loopback_origin(base_url)
    if phase not in PHASES:
        raise BtrfsRebootFunctionalLabError("functional lab phase is invalid")
    if phase == "provision" and not password:
        raise BtrfsRebootFunctionalLabError("ECHO_ADMIN_PASSWORD must be set")
    with _lock():
        try:
            if phase == "provision":
                return _provision_phase(base_url=base_url, password=password)
            return _reboot_verify_cleanup_phase(base_url=base_url)
        except (
            base.BtrfsSnapshotFunctionalLabError,
            provision.BtrfsProvisionFunctionalLabError,
        ) as exc:
            raise BtrfsRebootFunctionalLabError(str(exc)) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        result = run_phase(
            phase=args.phase,
            base_url=args.base_url,
            password=os.environ.get("ECHO_ADMIN_PASSWORD", ""),
        )
    except BtrfsRebootFunctionalLabError as exc:
        print(f"Btrfs reboot functional lab failed: {exc}", file=sys.stderr)
        return 2
    print(base._canonical(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
