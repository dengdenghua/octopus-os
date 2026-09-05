"""Approval-bound, read-only checks for offline Echo-managed EXT4 volumes.

The workflow deliberately does not unmount or remount anything.  A target is
eligible only when its exact UUID entry is inside the Echo-owned fstab block,
the UUID still resolves to a healthy Echo-managed md RAID1, and neither the
device nor its expected mountpoint is mounted.  The generated systemd mount
unit is masked for the duration of ``e2fsck -f -n`` and restored afterwards.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from appliance.native_ext4 import _FSTAB_PATH, _managed_entries, _read_fstab, _volume_transaction
from appliance.native_mdraid import _MDADM_CONFIG, managed_mdraid1_arrays
from appliance.omv_protocol import (
    EXT4_CHECK_PLAN_SCHEMA,
    validate_devicefile,
    validate_ext4_check_desired,
)

_MAX_OUTPUT_BYTES = 256 * 1024
_MOUNT_UNIT = re.compile(r"[A-Za-z0-9_.@\\x-]{1,240}\.mount")
_UNIT_STATES = frozenset({"disabled", "enabled", "generated", "indirect", "static"})


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_tools() -> None:
    missing = [
        name
        for name in ("blkid", "e2fsck", "findmnt", "mdadm", "systemctl", "systemd-escape")
        if shutil.which(name) is None
    ]
    if missing:
        raise OSError(f"native EXT4 offline check tools are unavailable: {', '.join(missing)}")


def _run(
    *args: str,
    timeout: float = 120.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    try:
        completed = runner(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError(f"native command failed to start: {args[0]}") from exc
    if (
        len((completed.stdout or "").encode("utf-8")) > _MAX_OUTPUT_BYTES
        or len((completed.stderr or "").encode("utf-8")) > _MAX_OUTPUT_BYTES
    ):
        raise OSError(f"{args[0]} returned excessive output")
    return completed


def _run_checked(
    *args: str,
    timeout: float = 120.0,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    completed = _run(*args, timeout=timeout, runner=runner)
    if completed.returncode != 0:
        raise OSError(f"{args[0]} rejected the bounded EXT4 maintenance operation")
    return completed.stdout


def _managed_fstab_rows(path: Path) -> tuple[str, list[dict[str, str]]]:
    payload = _read_fstab(path)
    text = payload.decode("utf-8") if payload is not None else ""
    _begin, _end, entries = _managed_entries(text)
    rows: list[dict[str, str]] = []
    for entry in entries:
        fields = entry.split()
        if len(fields) != 6 or not fields[0].startswith("UUID="):
            raise OSError("Echo EXT4 fstab entry is malformed")
        rows.append(
            {
                "filesystemUuid": fields[0].removeprefix("UUID=").lower(),
                "mountpoint": fields[1],
                "name": Path(fields[1]).name,
            }
        )
    return hashlib.sha256(payload or b"").hexdigest(), rows


def _parse_blkid(output: str, expected_uuid: str) -> None:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in fields or len(key) > 64 or len(value) > 256:
            raise OSError("blkid returned invalid EXT4 identity data")
        fields[key] = value
    if fields.get("TYPE") != "ext4" or fields.get("UUID", "").lower() != expected_uuid:
        raise OSError("managed EXT4 UUID no longer resolves to the expected filesystem")


def _is_mounted(
    selector: str,
    value: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> bool:
    completed = _run("findmnt", "--noheadings", "--raw", selector, value, runner=runner)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1 and not (completed.stdout or "").strip():
        return False
    raise OSError("findmnt could not determine EXT4 mount state")


def _inventory(
    *,
    fstab_path: Path,
    mdadm_config_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> list[dict[str, Any]]:
    _require_tools()
    fstab_sha256, rows = _managed_fstab_rows(fstab_path)
    arrays = managed_mdraid1_arrays(config_path=mdadm_config_path)
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        filesystem_uuid = row["filesystemUuid"]
        if filesystem_uuid in seen:
            raise OSError("Echo EXT4 fstab block contains duplicate filesystem UUIDs")
        seen.add(filesystem_uuid)
        resolved = _run_checked("blkid", "--uuid", filesystem_uuid, runner=runner).splitlines()
        if len(resolved) != 1:
            raise OSError("managed EXT4 UUID did not resolve to exactly one device")
        devicefile = validate_devicefile(resolved[0].strip())
        _parse_blkid(
            _run_checked("blkid", "--probe", "--output", "export", devicefile, runner=runner),
            filesystem_uuid,
        )
        matches = [
            array
            for array in arrays
            if os.path.realpath(array["devicefile"]) == os.path.realpath(devicefile)
        ]
        if len(matches) != 1:
            raise OSError("managed EXT4 filesystem is not on its healthy Echo RAID1")
        source_mounted = _is_mounted(
            "--source", f"UUID={filesystem_uuid}", runner=runner
        ) or _is_mounted("--source", devicefile, runner=runner)
        mountpoint_occupied = _is_mounted("--mountpoint", row["mountpoint"], runner=runner)
        mounted = source_mounted or mountpoint_occupied
        material = {
            **row,
            "devicefile": devicefile,
            "array": matches[0],
            "fstabSha256": fstab_sha256,
            "mounted": mounted,
        }
        results.append(
            {
                **material,
                "stateHash": _canonical_hash(material),
                "canCheck": not mounted,
                "reason": "mounted" if mounted else None,
            }
        )
    return results


def ext4_check_inventory(
    *,
    fstab_path: Path = _FSTAB_PATH,
    mdadm_config_path: Path = _MDADM_CONFIG,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[dict[str, Any]]:
    return _inventory(
        fstab_path=fstab_path,
        mdadm_config_path=mdadm_config_path,
        runner=runner,
    )


def _mount_unit(
    mountpoint: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    unit = _run_checked(
        "systemd-escape", "--path", "--suffix=mount", mountpoint, runner=runner
    ).strip()
    if _MOUNT_UNIT.fullmatch(unit) is None:
        raise OSError("systemd returned an invalid EXT4 mount unit name")
    return unit


def _unit_state(
    unit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    allow_masked: bool = False,
) -> str:
    completed = _run("systemctl", "is-enabled", unit, runner=runner)
    state = (completed.stdout or "").strip()
    allowed = _UNIT_STATES | ({"masked", "masked-runtime"} if allow_masked else set())
    if completed.returncode not in {0, 1} or state not in allowed:
        raise OSError("systemd could not verify the EXT4 mount unit state")
    return state


def _build_plan(
    desired: dict[str, Any],
    *,
    fstab_path: Path,
    mdadm_config_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> dict[str, Any]:
    matches = [
        item
        for item in _inventory(
            fstab_path=fstab_path,
            mdadm_config_path=mdadm_config_path,
            runner=runner,
        )
        if item["filesystemUuid"] == desired["filesystemUuid"]
    ]
    if len(matches) != 1:
        raise ValueError("EXT4 filesystem is not in the Echo-managed fstab block")
    filesystem = matches[0]
    if not filesystem["canCheck"]:
        raise ValueError("EXT4 filesystem must already be unmounted before an offline check")
    unit = _mount_unit(filesystem["mountpoint"], runner=runner)
    unit_state = _unit_state(unit, runner=runner)
    material = {
        "schema": EXT4_CHECK_PLAN_SCHEMA,
        "operation": "offlineReadOnlyCheck",
        "desired": desired,
        "filesystem": filesystem,
        "mountUnit": unit,
        "mountUnitState": unit_state,
    }
    return {
        **material,
        "planId": _canonical_hash(material),
        "requiresApproval": True,
        "safety": {
            "filesystem": "echoManagedExt4OnHealthyMdRaid1Only",
            "mountedFilesystem": "rejected",
            "automaticUnmount": False,
            "automaticMount": False,
            "repair": False,
            "command": "e2fsckForcedReadOnly",
            "mountUnit": "runtimeMaskedDuringCheck",
            "ioLoad": "high",
        },
        "source": "native",
    }


def plan_ext4_check(
    desired_state: dict[str, Any],
    *,
    fstab_path: Path = _FSTAB_PATH,
    mdadm_config_path: Path = _MDADM_CONFIG,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    desired = validate_ext4_check_desired(dict(desired_state))
    with _volume_transaction():
        return _build_plan(
            desired,
            fstab_path=fstab_path,
            mdadm_config_path=mdadm_config_path,
            runner=runner,
        )


def apply_ext4_check(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    fstab_path: Path = _FSTAB_PATH,
    mdadm_config_path: Path = _MDADM_CONFIG,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    desired = validate_ext4_check_desired(dict(desired_state))
    if os.name == "posix" and os.geteuid() != 0:
        raise OSError("EXT4 offline check requires root")
    with _volume_transaction():
        plan = _build_plan(
            desired,
            fstab_path=fstab_path,
            mdadm_config_path=mdadm_config_path,
            runner=runner,
        )
        if plan["planId"] != plan_id:
            raise ValueError("EXT4 offline check plan is stale; preview again")
        mask_attempted = False
        check: subprocess.CompletedProcess[str] | None = None
        operation_error: Exception | None = None
        try:
            mask_attempted = True
            _run_checked(
                "systemctl", "mask", "--runtime", plan["mountUnit"], runner=runner
            )
            if _unit_state(plan["mountUnit"], runner=runner, allow_masked=True) not in {
                "masked",
                "masked-runtime",
            }:
                raise OSError("EXT4 mount unit runtime mask was not verified")
            current = next(
                (
                    item
                    for item in _inventory(
                        fstab_path=fstab_path,
                        mdadm_config_path=mdadm_config_path,
                        runner=runner,
                    )
                    if item["filesystemUuid"] == desired["filesystemUuid"]
                ),
                None,
            )
            if current is None or current["stateHash"] != plan["filesystem"]["stateHash"]:
                raise ValueError("EXT4 filesystem changed after its mount unit was masked")
            check = _run(
                "e2fsck",
                "-f",
                "-n",
                plan["filesystem"]["devicefile"],
                timeout=24 * 60 * 60,
                runner=runner,
            )
            if check.returncode not in {0, 4}:
                raise OSError("e2fsck could not complete the read-only EXT4 check")
            after = next(
                (
                    item
                    for item in _inventory(
                        fstab_path=fstab_path,
                        mdadm_config_path=mdadm_config_path,
                        runner=runner,
                    )
                    if item["filesystemUuid"] == desired["filesystemUuid"]
                ),
                None,
            )
            if after is None or after["stateHash"] != plan["filesystem"]["stateHash"]:
                raise OSError("EXT4 identity or mount state changed during the offline check")
        except Exception as exc:
            operation_error = exc
        finally:
            if mask_attempted:
                try:
                    _run_checked(
                        "systemctl", "unmask", "--runtime", plan["mountUnit"], runner=runner
                    )
                    if _unit_state(plan["mountUnit"], runner=runner) != plan["mountUnitState"]:
                        raise OSError("EXT4 mount unit state was not restored")
                except Exception as restore_exc:
                    raise OSError(
                        "EXT4 offline check ended with an incomplete mount-unit restore"
                    ) from restore_exc
        if operation_error is not None:
            raise operation_error
        assert check is not None
        return {
            **plan,
            "applied": True,
            "verified": True,
            "clean": check.returncode == 0,
            "errorsDetected": check.returncode == 4,
            "result": "clean" if check.returncode == 0 else "errorsFound",
            "exitCode": check.returncode,
        }


__all__ = ["apply_ext4_check", "ext4_check_inventory", "plan_ext4_check"]
