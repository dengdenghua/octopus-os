#!/usr/bin/env python3
"""Exercise native Btrfs RAID1 scrub and its scheduler on disposable loop devices."""

from __future__ import annotations

import argparse
import os
import platform
import re
import shutil
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows imports exercise pure helpers only
    fcntl = None  # type: ignore[assignment]

try:
    from deploy.appliance import btrfs_snapshot_functional_lab as base
except ModuleNotFoundError:
    import btrfs_snapshot_functional_lab as base

FSTAB_PATH = Path("/etc/fstab")
POLICY_PATH = Path("/etc/echo-os/btrfs-scrub-schedule.json")
TIMER_PATH = Path("/etc/systemd/system/echo-btrfs-scrub.timer")
LAB_LOCK_PATH = Path("/run/echo-btrfs-scrub-functional-lab.lock")
IMAGE_ROOT = Path("/var/tmp")
MOUNT_ROOT = Path("/data")
IMAGE_BYTES = 512 * 1024 * 1024
PAYLOAD_BYTES = 64 * 1024 * 1024
LOOP_DEVICE = re.compile(r"/dev/loop[0-9]+")
BTRFS_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class BtrfsScrubFunctionalLabError(RuntimeError):
    """The disposable RAID1 scrub lab was unsafe or did not verify."""


def _checked(command: Sequence[str], label: str) -> str:
    try:
        return base._checked(command, base._run, label)
    except base.BtrfsSnapshotFunctionalLabError as exc:
        raise BtrfsScrubFunctionalLabError(str(exc)) from exc


def _preflight() -> dict[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BtrfsScrubFunctionalLabError("functional scrub lab requires Linux root")
    for root in (IMAGE_ROOT, MOUNT_ROOT):
        if not root.is_dir() or root.is_symlink():
            raise BtrfsScrubFunctionalLabError(f"{root} is not a safe lab root")
    for tool in (
        "btrfs",
        "dd",
        "findmnt",
        "losetup",
        "mkfs.btrfs",
        "mount",
        "sync",
        "truncate",
        "umount",
    ):
        if shutil.which(tool) is None:
            raise BtrfsScrubFunctionalLabError(f"required tool {tool} is unavailable")
    version = _checked(["btrfs", "version"], "Btrfs version probe").strip()
    return {"system": platform.system(), "kernel": platform.release(), "btrfs": version}


def _paths(run_id: str) -> tuple[Path, Path, Path]:
    if len(run_id) != 10 or any(character not in "0123456789abcdef" for character in run_id):
        raise BtrfsScrubFunctionalLabError("scrub lab run id is invalid")
    first = IMAGE_ROOT / f"echo-btrfs-scrub-lab-{run_id}-1.img"
    second = IMAGE_ROOT / f"echo-btrfs-scrub-lab-{run_id}-2.img"
    mountpoint = MOUNT_ROOT / f"scrublab{run_id[:8]}"
    if first.parent != IMAGE_ROOT or second.parent != IMAGE_ROOT or mountpoint.parent != MOUNT_ROOT:
        raise BtrfsScrubFunctionalLabError("scrub lab paths escaped their fixed roots")
    if any(path.exists() or path.is_symlink() for path in (first, second, mountpoint)):
        raise BtrfsScrubFunctionalLabError("unique scrub lab paths already exist")
    return first, second, mountpoint


def _loop_device(output: str) -> str:
    candidate = output.strip()
    if LOOP_DEVICE.fullmatch(candidate) is None:
        raise BtrfsScrubFunctionalLabError("losetup returned an unsafe loop device")
    return candidate


def _filesystem_uuid(mountpoint: Path) -> str:
    filesystem_uuid = (
        _checked(
            ["findmnt", "-n", "-o", "UUID", "-T", str(mountpoint)],
            "filesystem UUID probe",
        )
        .strip()
        .lower()
    )
    if BTRFS_UUID.fullmatch(filesystem_uuid) is None:
        raise BtrfsScrubFunctionalLabError("disposable RAID1 has no stable filesystem UUID")
    return filesystem_uuid


def _maintenance_record(value: Mapping[str, Any], filesystem_uuid: str) -> dict[str, Any]:
    filesystems = value.get("filesystems")
    if not isinstance(filesystems, list):
        raise BtrfsScrubFunctionalLabError("Btrfs maintenance inventory is invalid")
    matches = [
        item
        for item in filesystems
        if isinstance(item, dict)
        and isinstance(item.get("filesystem"), dict)
        and item["filesystem"].get("uuid") == filesystem_uuid
    ]
    if len(matches) != 1:
        raise BtrfsScrubFunctionalLabError("disposable RAID1 was not uniquely inventoried")
    record = matches[0]
    filesystem = record["filesystem"]
    if (
        filesystem.get("status") != "healthy"
        or filesystem.get("readOnly") is not False
        or filesystem.get("totalDevices") != 2
        or filesystem.get("activeDevices") != 2
        or filesystem.get("missingDevices") != 0
        or filesystem.get("dataProfile") != "raid1"
        or filesystem.get("metadataProfile") != "raid1"
        or filesystem.get("deviceErrorCount") != 0
        or record.get("exclusiveOperation") != "none"
        or record.get("canStartScrub") is not True
    ):
        raise BtrfsScrubFunctionalLabError("disposable RAID1 is not safe to scrub")
    return dict(record)


def _scrub_finished(output: str, filesystem_uuid: str) -> bool:
    lowered = output.casefold()
    return (
        f"uuid:             {filesystem_uuid}" in lowered
        and re.search(r"^status:\s+finished\s*$", output, re.MULTILINE | re.IGNORECASE) is not None
        and re.search(
            r"^error summary:\s+no errors found\s*$",
            output,
            re.MULTILINE | re.IGNORECASE,
        )
        is not None
    )


def _assert_backing_images_hidden(value: Mapping[str, Any], images: Sequence[Path]) -> None:
    private = tuple(image.as_posix() for image in images)

    def contains(candidate: Any) -> bool:
        if isinstance(candidate, str):
            normalized = candidate.replace("\\", "/")
            return any(path in normalized for path in private)
        if isinstance(candidate, Mapping):
            return any(contains(key) or contains(item) for key, item in candidate.items())
        if isinstance(candidate, list):
            return any(contains(item) for item in candidate)
        return False

    if contains(value):
        raise BtrfsScrubFunctionalLabError("public API leaked a backing image path")


def _wait_scrub(mountpoint: Path, filesystem_uuid: str) -> str:
    latest = ""
    for _attempt in range(60):
        completed = base._run(["btrfs", "scrub", "status", "--raw", str(mountpoint)])
        latest = f"{completed.stdout}\n{completed.stderr}"
        if _scrub_finished(latest, filesystem_uuid):
            return latest
        time.sleep(0.25)
    raise BtrfsScrubFunctionalLabError("Btrfs scrub did not finish cleanly within the bound")


def _restore(saved_files: Sequence[base.SavedFile]) -> None:
    errors: list[Exception] = []
    for saved in reversed(saved_files):
        try:
            base._atomic_restore(saved)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise BtrfsScrubFunctionalLabError(
            "scrub lab state restoration was incomplete"
        ) from errors[0]


def _cleanup(
    *,
    images: Sequence[Path],
    mountpoint: Path,
    loop_devices: Sequence[str],
    mounted: bool,
    saved_files: Sequence[base.SavedFile],
) -> None:
    errors: list[Exception] = []
    if mounted:
        try:
            _checked(["umount", str(mountpoint)], "scrub lab unmount")
        except Exception as exc:
            errors.append(exc)
    for device in reversed(loop_devices):
        if LOOP_DEVICE.fullmatch(device) is None:
            errors.append(BtrfsScrubFunctionalLabError("cleanup loop device is unsafe"))
            continue
        try:
            _checked(["losetup", "-d", device], "loop device detach")
        except Exception as exc:
            errors.append(exc)
    try:
        if mountpoint.exists() and not mountpoint.is_symlink():
            mountpoint.rmdir()
        for image in images:
            if image.exists() and not image.is_symlink():
                image.unlink()
    except OSError as exc:
        errors.append(exc)
    try:
        _restore(saved_files)
    except Exception as exc:
        errors.append(exc)
    try:
        _checked(
            ["systemctl", "restart", "echo-appliance.service"],
            "appliance service restoration",
        )
    except Exception as exc:
        errors.append(exc)
    if errors:
        raise BtrfsScrubFunctionalLabError(
            "functional scrub lab cleanup was incomplete"
        ) from errors[0]


def run_lab(*, base_url: str, password: str) -> dict[str, Any]:
    base_url = base._loopback_origin(base_url)
    if not password:
        raise BtrfsScrubFunctionalLabError("ECHO_ADMIN_PASSWORD must be set")
    host = _preflight()
    run_id = uuid.uuid4().hex[:10]
    first_image, second_image, mountpoint = _paths(run_id)
    saved_files: list[base.SavedFile] = []
    loop_devices: list[str] = []
    mounted = False
    LAB_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(LAB_LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            if fcntl is None:
                raise BtrfsScrubFunctionalLabError("functional scrub lab requires POSIX locking")
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BtrfsScrubFunctionalLabError("another scrub functional lab is active") from exc
        for path in (base.AUTH_PATH, FSTAB_PATH, POLICY_PATH):
            saved_files.append(base._save_file(path))
        base._set_test_password(saved_files[0], password)
        _checked(
            ["systemctl", "restart", "echo-appliance.service"],
            "appliance service restart",
        )
        base._wait_health(base._http_call, base_url)
        token = base._admin_token(saved_files[0])

        for image in (first_image, second_image):
            _checked(["truncate", "-s", str(IMAGE_BYTES), str(image)], "image creation")
            device = _loop_device(
                _checked(["losetup", "-f", "--show", str(image)], "loop device allocation")
            )
            if device in loop_devices:
                raise BtrfsScrubFunctionalLabError("losetup reused a loop device")
            loop_devices.append(device)
        _checked(
            [
                "mkfs.btrfs",
                "-f",
                "-d",
                "raid1",
                "-m",
                "raid1",
                *loop_devices,
            ],
            "Btrfs RAID1 formatting",
        )
        mountpoint.mkdir(mode=0o700)
        _checked(["mount", loop_devices[0], str(mountpoint)], "Btrfs RAID1 mount")
        mounted = True
        filesystem_uuid = _filesystem_uuid(mountpoint)

        from appliance import native_btrfs

        rendered_fstab = native_btrfs._render_fstab(
            saved_files[1].payload,
            filesystem_uuid=filesystem_uuid,
            mountpoint=str(mountpoint),
        )
        base._atomic_write(
            FSTAB_PATH,
            rendered_fstab,
            mode=saved_files[1].mode or 0o644,
        )
        _checked(
            [
                "dd",
                "if=/dev/zero",
                f"of={mountpoint / 'scrub-payload.bin'}",
                "bs=1M",
                f"count={PAYLOAD_BYTES // (1024 * 1024)}",
                "status=none",
            ],
            "RAID1 payload creation",
        )
        _checked(["sync"], "RAID1 payload sync")

        inventory = base._request(
            base._http_call,
            base_url,
            "GET",
            "/api/appliance/omv/volumes/btrfs-raid1/maintenance",
            expected=200,
            token=token,
        )
        _maintenance_record(inventory, filesystem_uuid)
        _assert_backing_images_hidden(inventory, (first_image, second_image))

        desired = {
            "schema": "echo.omv.btrfs-scrub-desired.v1",
            "filesystemUuid": filesystem_uuid,
            "operation": "start",
        }
        scrub_plan, scrub_result = base._planned_apply(
            base._http_call,
            base_url,
            token,
            password,
            desired=desired,
            plan_path="/api/appliance/omv/volumes/btrfs-raid1/scrub/plan",
            apply_path="/api/appliance/omv/volumes/btrfs-raid1/scrub/apply",
            action="omv.btrfs.scrub.start",
        )
        if scrub_plan.get("operation") != "start" or scrub_result.get("maintenanceState") not in {
            "scrubbing",
            "completed",
        }:
            raise BtrfsScrubFunctionalLabError("HTTP scrub start was not verified")
        _wait_scrub(mountpoint, filesystem_uuid)

        schedule_desired = {
            "schema": "echo.btrfs-scrub-schedule-desired.v1",
            "enabled": True,
        }
        _schedule_plan, schedule_result = base._planned_apply(
            base._http_call,
            base_url,
            token,
            password,
            desired=schedule_desired,
            plan_path="/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/plan",
            apply_path="/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule/apply",
            action="storage.btrfs.scrub.schedule",
        )
        schedule_status = base._request(
            base._http_call,
            base_url,
            "GET",
            "/api/appliance/omv/volumes/btrfs-raid1/scrub/schedule",
            expected=200,
            token=token,
        )
        if (
            schedule_result.get("desired") != {"schemaVersion": 1, "enabled": True}
            or schedule_status.get("enabled") is not True
            or schedule_status.get("schedulerInstalled") is not True
        ):
            raise BtrfsScrubFunctionalLabError("scrub schedule policy was not verified")

        from deploy.appliance.btrfs_scrub_schedule_runner import run_schedule

        scheduled = run_schedule()
        if scheduled != {"outcome": "completed", "started": 1, "skipped": 0, "errors": 0}:
            raise BtrfsScrubFunctionalLabError("scheduled scrub runner did not verify")
        final_status = _wait_scrub(mountpoint, filesystem_uuid)
        final_inventory = base._request(
            base._http_call,
            base_url,
            "GET",
            "/api/appliance/omv/volumes/btrfs-raid1/maintenance",
            expected=200,
            token=token,
        )
        final_record = _maintenance_record(final_inventory, filesystem_uuid)
        if final_record.get("scan", {}).get("state") != "completed":
            raise BtrfsScrubFunctionalLabError("completed scrub was not visible through HTTP")
        if not _scrub_finished(final_status, filesystem_uuid):
            raise BtrfsScrubFunctionalLabError("kernel scrub status did not verify")

        return {
            "schemaVersion": 1,
            "kind": "echo-btrfs-scrub-functional-result",
            "outcome": "verified",
            "host": host,
            "filesystem": {
                "uuid": filesystem_uuid,
                "type": "btrfs",
                "totalDevices": 2,
                "activeDevices": 2,
                "dataProfile": "raid1",
                "metadataProfile": "raid1",
                "imageBytesEach": IMAGE_BYTES,
                "payloadBytes": PAYLOAD_BYTES,
            },
            "http": {
                "planApprovalApply": True,
                "backingImagePathsHidden": True,
                "kernelStatusReadBack": True,
            },
            "scrub": {
                "manualStartVerified": True,
                "manualCompletionVerified": True,
                "errorCount": 0,
            },
            "schedule": {
                "policyEnabled": True,
                "timerContractPresent": TIMER_PATH.is_file(),
                "runnerStarted": 1,
                "runnerCompletionVerified": True,
            },
            "observedAt": datetime.now(UTC).isoformat(),
        }
    except base.BtrfsSnapshotFunctionalLabError as exc:
        raise BtrfsScrubFunctionalLabError(str(exc)) from exc
    finally:
        cleanup_error: Exception | None = None
        try:
            _cleanup(
                images=(first_image, second_image),
                mountpoint=mountpoint,
                loop_devices=loop_devices,
                mounted=mounted,
                saved_files=saved_files,
            )
        except Exception as exc:
            cleanup_error = exc
        try:
            os.close(lock_descriptor)
        finally:
            if cleanup_error is not None:
                raise cleanup_error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        result = run_lab(
            base_url=args.base_url,
            password=os.environ.get("ECHO_ADMIN_PASSWORD", ""),
        )
    except BtrfsScrubFunctionalLabError as exc:
        print(f"Btrfs scrub functional lab failed: {exc}", file=sys.stderr)
        return 2
    print(base._canonical(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
