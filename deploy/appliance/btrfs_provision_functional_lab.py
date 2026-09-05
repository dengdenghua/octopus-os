#!/usr/bin/env python3
"""Exercise native Btrfs RAID1 creation on two disposable VM disks."""

from __future__ import annotations

import argparse
import hashlib
import os
import platform
import re
import shutil
import sys
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
MOUNT_ROOT = Path("/data")
MOUNTPOINT = MOUNT_ROOT / "btrfslab"
DEVICES = ("/dev/vdb", "/dev/vdc")
SERIALS = ("ECHO-BTRFS-LAB-01", "ECHO-BTRFS-LAB-02")
LOCK_PATH = Path("/run/echo-btrfs-provision-functional-lab.lock")
PAYLOAD_BYTES = 16 * 1024 * 1024
FS_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class BtrfsProvisionFunctionalLabError(RuntimeError):
    """The disposable Btrfs creation lab was unsafe or did not verify."""


def _checked(command: Sequence[str], label: str) -> str:
    try:
        return base._checked(command, base._run, label)
    except base.BtrfsSnapshotFunctionalLabError as exc:
        raise BtrfsProvisionFunctionalLabError(str(exc)) from exc


def _preflight() -> dict[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BtrfsProvisionFunctionalLabError("functional provisioning lab requires Linux root")
    if not MOUNT_ROOT.is_dir() or MOUNT_ROOT.is_symlink():
        raise BtrfsProvisionFunctionalLabError("/data is not a safe mount root")
    if MOUNTPOINT.exists() or MOUNTPOINT.is_symlink():
        raise BtrfsProvisionFunctionalLabError("fixed lab mountpoint already exists")
    for tool in (
        "blkid",
        "btrfs",
        "findmnt",
        "mkfs.btrfs",
        "mount",
        "sha256sum",
        "sync",
        "systemctl",
        "umount",
        "wipefs",
    ):
        if shutil.which(tool) is None:
            raise BtrfsProvisionFunctionalLabError(f"required tool {tool} is unavailable")
    version = _checked(["btrfs", "version"], "Btrfs version probe").strip()
    return {"system": platform.system(), "kernel": platform.release(), "btrfs": version}


def _bound_candidates(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = value.get("devices")
    if not isinstance(raw, list):
        raise BtrfsProvisionFunctionalLabError("Btrfs candidate inventory is invalid")
    by_path = {
        item.get("devicefile"): item
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("devicefile"), str)
    }
    selected: list[dict[str, Any]] = []
    for device, serial in zip(DEVICES, SERIALS, strict=True):
        item = by_path.get(device)
        if (
            not isinstance(item, dict)
            or item.get("serial") != serial
            or item.get("wwn") is not None
            or isinstance(item.get("sizeBytes"), bool)
            or not isinstance(item.get("sizeBytes"), int)
            or item["sizeBytes"] < 1024**3
        ):
            raise BtrfsProvisionFunctionalLabError(
                "dedicated VM disks were not uniquely offered as blank candidates"
            )
        selected.append(dict(item))
    if len({item["serial"] for item in selected}) != 2:
        raise BtrfsProvisionFunctionalLabError("dedicated VM disk identities are not distinct")
    return selected


def _profiles(output: str) -> dict[str, set[str]]:
    profiles: dict[str, set[str]] = {"Data": set(), "Metadata": set()}
    for line in output.splitlines():
        match = re.match(r"^(Data|Metadata),\s*([^:]+):", line.strip())
        if match:
            profiles[match.group(1)].add(match.group(2).casefold())
    return profiles


def _verify_host(filesystem_uuid: str, payload_sha256: str) -> dict[str, Any]:
    if FS_UUID.fullmatch(filesystem_uuid) is None:
        raise BtrfsProvisionFunctionalLabError("created filesystem UUID is invalid")
    mount = _checked(
        ["findmnt", "-n", "-r", "-o", "UUID,FSTYPE,OPTIONS", "-M", str(MOUNTPOINT)],
        "Btrfs mount verification",
    ).strip()
    parts = mount.split(None, 2)
    if len(parts) != 3:
        raise BtrfsProvisionFunctionalLabError("Btrfs mount identity is incomplete")
    observed_uuid, filesystem_type, options = parts
    option_set = set(options.split(","))
    if (
        observed_uuid.casefold() != filesystem_uuid
        or filesystem_type != "btrfs"
        or "rw" not in option_set
        or "ro" in option_set
    ):
        raise BtrfsProvisionFunctionalLabError("created Btrfs mount identity drifted")
    member_uuids = {
        _checked(
            ["blkid", "--probe", "--output", "value", "--match-tag", "UUID", device],
            "Btrfs member UUID verification",
        )
        .strip()
        .casefold()
        for device in DEVICES
    }
    if member_uuids != {filesystem_uuid}:
        raise BtrfsProvisionFunctionalLabError("Btrfs members do not share one filesystem UUID")
    profiles = _profiles(
        _checked(
            ["btrfs", "filesystem", "df", "--raw", str(MOUNTPOINT)],
            "Btrfs profile verification",
        )
    )
    if profiles != {"Data": {"raid1"}, "Metadata": {"raid1"}}:
        raise BtrfsProvisionFunctionalLabError("Btrfs RAID1 profiles did not persist")
    show = _checked(
        ["btrfs", "filesystem", "show", "--raw", str(MOUNTPOINT)],
        "Btrfs topology verification",
    )
    if (
        re.search(r"^\s*Total devices\s+2\b", show, re.MULTILINE) is None
        or "Some devices missing" in show
    ):
        raise BtrfsProvisionFunctionalLabError("Btrfs topology is incomplete")
    observed_payload = _checked(
        ["sha256sum", str(MOUNTPOINT / "payload.bin")], "payload read-back"
    ).split()[0]
    if observed_payload != payload_sha256:
        raise BtrfsProvisionFunctionalLabError("payload did not survive filesystem creation")
    return {
        "filesystemUuid": filesystem_uuid,
        "dataProfile": "raid1",
        "metadataProfile": "raid1",
        "totalDevices": 2,
        "activeDevices": 2,
        "readOnly": False,
        "payloadSha256": observed_payload,
    }


def _write_payload() -> str:
    path = MOUNTPOINT / "payload.bin"
    block = hashlib.sha256(b"echo-btrfs-provision-functional-lab").digest() * 4096
    digest = hashlib.sha256()
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            remaining = PAYLOAD_BYTES
            while remaining:
                chunk = block[: min(remaining, len(block))]
                handle.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    _checked(["sync"], "payload sync")
    return digest.hexdigest()


def _restore(saved_files: Sequence[base.SavedFile]) -> None:
    errors: list[Exception] = []
    for saved in reversed(saved_files):
        try:
            base._atomic_restore(saved)
        except Exception as exc:
            errors.append(exc)
    if errors:
        raise BtrfsProvisionFunctionalLabError("lab state restoration was incomplete") from errors[
            0
        ]


def _cleanup(*, saved_files: Sequence[base.SavedFile], devices_bound: bool) -> None:
    errors: list[Exception] = []
    if MOUNTPOINT.is_mount():
        try:
            _checked(["umount", str(MOUNTPOINT)], "Btrfs lab unmount")
        except Exception as exc:
            errors.append(exc)
    try:
        _restore(saved_files)
        _checked(["systemctl", "daemon-reload"], "systemd reload after fstab restoration")
    except Exception as exc:
        errors.append(exc)
    if devices_bound and not errors:
        for device in DEVICES:
            try:
                _checked(["wipefs", "--all", device], "Btrfs lab signature cleanup")
                if _checked(
                    ["wipefs", "--noheadings", "--output", "TYPE", device],
                    "Btrfs lab cleanup verification",
                ).strip():
                    raise BtrfsProvisionFunctionalLabError("Btrfs signature remained after cleanup")
            except Exception as exc:
                errors.append(exc)
    try:
        if MOUNTPOINT.exists() and not MOUNTPOINT.is_symlink():
            MOUNTPOINT.rmdir()
    except OSError as exc:
        errors.append(exc)
    try:
        _checked(["systemctl", "restart", "echo-appliance.service"], "service restoration")
    except Exception as exc:
        errors.append(exc)
    if errors:
        raise BtrfsProvisionFunctionalLabError(
            "functional provisioning cleanup was incomplete"
        ) from errors[0]


def run_lab(*, base_url: str, password: str) -> dict[str, Any]:
    base_url = base._loopback_origin(base_url)
    if not password:
        raise BtrfsProvisionFunctionalLabError("ECHO_ADMIN_PASSWORD must be set")
    host = _preflight()
    saved_files: list[base.SavedFile] = []
    devices_bound = False
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            if fcntl is None:
                raise BtrfsProvisionFunctionalLabError("functional lab requires POSIX locking")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BtrfsProvisionFunctionalLabError("another provisioning lab is active") from exc
        for path in (base.AUTH_PATH, FSTAB_PATH):
            saved_files.append(base._save_file(path))
        base._set_test_password(saved_files[0], password)
        _checked(["systemctl", "restart", "echo-appliance.service"], "service restart")
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
        selected = _bound_candidates(candidates)
        devices_bound = True
        desired = {
            "schema": "echo.omv.btrfs-raid1-desired.v1",
            "name": MOUNTPOINT.name,
            "devices": list(DEVICES),
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
            plan.get("operation") != "createAndMount"
            or plan.get("mountpoint") != str(MOUNTPOINT)
            or result.get("applied") is not True
            or not isinstance(filesystem, dict)
            or filesystem.get("devices") != list(DEVICES)
            or filesystem.get("mountpoint") != str(MOUNTPOINT)
            or filesystem.get("dataProfile") != "raid1"
            or filesystem.get("metadataProfile") != "raid1"
        ):
            raise BtrfsProvisionFunctionalLabError("HTTP Btrfs creation result is invalid")
        filesystem_uuid = str(filesystem.get("uuid", "")).casefold()
        payload_sha256 = _write_payload()
        verified = _verify_host(filesystem_uuid, payload_sha256)
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
            and item.get("filesystem", {}).get("uuid") == filesystem_uuid
            and item.get("canStartScrub") is True
            for item in records
        ):
            raise BtrfsProvisionFunctionalLabError("created RAID1 was not exposed for maintenance")
        return {
            "schemaVersion": 1,
            "kind": "echo-btrfs-provision-functional-result",
            "outcome": "verified",
            "host": host,
            "selection": {
                "selectedDeviceCount": len(selected),
                "stableIdentities": True,
                "sizeBytesEach": selected[0]["sizeBytes"],
            },
            "http": {"planApprovalApply": True, "maintenanceReadBack": True},
            "filesystem": {
                **verified,
                "payloadBytes": PAYLOAD_BYTES,
            },
            "observedAt": datetime.now(UTC).isoformat(),
            "runId": str(uuid.uuid4()),
        }
    except base.BtrfsSnapshotFunctionalLabError as exc:
        raise BtrfsProvisionFunctionalLabError(str(exc)) from exc
    finally:
        cleanup_error: Exception | None = None
        try:
            _cleanup(saved_files=saved_files, devices_bound=devices_bound)
        except Exception as exc:
            cleanup_error = exc
        try:
            os.close(descriptor)
        finally:
            if cleanup_error is not None:
                raise cleanup_error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        result = run_lab(base_url=args.base_url, password=os.environ.get("ECHO_ADMIN_PASSWORD", ""))
    except BtrfsProvisionFunctionalLabError as exc:
        print(f"Btrfs provisioning functional lab failed: {exc}", file=sys.stderr)
        return 2
    print(base._canonical(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
