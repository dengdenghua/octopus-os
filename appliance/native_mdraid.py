"""Plan-bound creation of one persistent two-disk Linux md RAID1 array.

This slice intentionally stops at the block-array boundary.  It never makes
a filesystem, mounts the array, grows it, removes it, or accepts a degraded
start.  Both selected whole disks must be blank and carry a persistent serial
or WWN every time the plan is built.  A successful create is persisted by one
owned block in ``mdadm.conf`` and followed by an initramfs refresh.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from appliance.native_storage_pool import inspect_blank_whole_disks
from appliance.omv_protocol import (
    MDRAID1_PLAN_SCHEMA,
    validate_mdraid1_desired,
)

_MDADM_CONFIG = Path("/etc/mdadm/mdadm.conf")
_LOCK_PATH = Path("/run/lock/echo-os-mdraid.lock")
_THREAD_LOCK = threading.RLock()
_MAX_CONFIG_BYTES = 256 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
_BEGIN = "# BEGIN ECHO OS MANAGED MDRAID"
_END = "# END ECHO OS MANAGED MDRAID"
_ARRAY_PATH_PATTERN = re.compile(r"/dev/md/echo-[a-z][a-z0-9_-]{0,26}")
_UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}(?::[0-9a-fA-F]{8}){3}")
_EXPORT_KEY_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_SCAN_PATH_PATTERN = re.compile(r"/dev/[A-Za-z0-9._/+:-]+")


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _require_tools() -> None:
    missing = [
        name
        for name in ("mdadm", "lsblk", "wipefs", "update-initramfs")
        if shutil.which(name) is None
    ]
    if missing:
        raise OSError(f"native md RAID1 tools are unavailable: {', '.join(missing)}")


def _run(*args: str, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
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
        len(completed.stdout.encode("utf-8")) > _MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8")) > _MAX_OUTPUT_BYTES
    ):
        raise OSError(f"{args[0]} returned excessive output")
    return completed


def _run_checked(*args: str, timeout: float = 120.0) -> str:
    completed = _run(*args, timeout=timeout)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit {completed.returncode}"
        raise OSError(f"{args[0]} failed: {detail[:512]}")
    return completed.stdout


def _run_mutating(*args: str, timeout: float = 300.0) -> None:
    _run_checked(*args, timeout=timeout)


def _array_target(name: str) -> str:
    target = f"/dev/md/echo-{name}"
    if _ARRAY_PATH_PATTERN.fullmatch(target) is None:  # defensive after protocol validation
        raise ValueError("md RAID1 target path is invalid")
    return target


def mdraid1_candidates() -> list[dict[str, Any]]:
    """Return only disks that pass the destructive plan's blank-disk gate."""
    _require_tools()
    output = _run_checked("lsblk", "-J", "-p", "-o", "PATH,TYPE")
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise OSError("lsblk returned invalid JSON") from exc
    rows = payload.get("blockdevices") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise OSError("lsblk did not return a block-device inventory")
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "disk":
            continue
        device = row.get("path")
        if not isinstance(device, str):
            continue
        try:
            candidates.extend(inspect_blank_whole_disks([device]))
        except ValueError:
            continue
    return sorted(candidates, key=lambda item: item["devicefile"])


def _safe_config_directory(path: Path) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OSError(f"mdadm configuration directory is unavailable: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise OSError("mdadm configuration directory is unsafe")
    if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022):
        raise OSError("mdadm configuration directory ownership or mode is unsafe")


def _read_config(path: Path = _MDADM_CONFIG) -> bytes | None:
    _safe_config_directory(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (
        not stat.S_ISREG(info.st_mode)
        or path.is_symlink()
        or info.st_size > _MAX_CONFIG_BYTES
        or (os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022))
    ):
        raise OSError("mdadm configuration file is unsafe")
    payload = path.read_bytes()
    if len(payload) > _MAX_CONFIG_BYTES or b"\x00" in payload:
        raise OSError("mdadm configuration file is invalid")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OSError("mdadm configuration must be UTF-8") from exc
    return payload


def _scan_arrays() -> list[dict[str, str | None]]:
    output = _run_checked("mdadm", "--detail", "--scan")
    arrays: list[dict[str, str | None]] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if (
            len(tokens) < 2
            or tokens[0] != "ARRAY"
            or _SCAN_PATH_PATTERN.fullmatch(tokens[1]) is None
            or any("=" not in token for token in tokens[2:])
        ):
            raise OSError("mdadm returned an invalid array scan")
        pairs = [item.split("=", 1) for item in tokens[2:]]
        if any(
            not key or len(key) > 64 or not value or len(value) > 512 for key, value in pairs
        ) or len({key for key, _value in pairs}) != len(pairs):
            raise OSError("mdadm returned an invalid array scan")
        fields = dict(pairs)
        arrays.append(
            {
                "path": tokens[1],
                "name": fields.get("name"),
                "uuid": fields.get("UUID"),
            }
        )
    return sorted(arrays, key=lambda item: (str(item["path"]), str(item["uuid"])))


def _managed_entries(text: str) -> tuple[int | None, int | None, list[str]]:
    lines = text.splitlines()
    begins = [index for index, line in enumerate(lines) if line == _BEGIN]
    ends = [index for index, line in enumerate(lines) if line == _END]
    if not begins and not ends:
        return None, None, []
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise OSError("mdadm configuration has an invalid Echo managed block")
    entries = lines[begins[0] + 1 : ends[0]]
    if any(
        re.fullmatch(
            r"ARRAY /dev/md/echo-[a-z][a-z0-9_-]{0,26} UUID="
            r"[0-9a-f]{8}(?::[0-9a-f]{8}){3} metadata=1\.2",
            entry,
        )
        is None
        for entry in entries
    ):
        raise OSError("mdadm Echo managed block contains an unsupported entry")
    return begins[0], ends[0], entries


def _config_declares_identity(text: str, *, target: str, name: str) -> bool:
    expected_names = {name, f"echo-{name}"}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        if tokens[0] != "ARRAY" or len(tokens) < 2:
            continue
        if tokens[1] == target:
            return True
        for token in tokens[2:]:
            if (
                token.startswith("name=")
                and token.removeprefix("name=").rsplit(":", 1)[-1] in expected_names
            ):
                return True
    return False


def _render_config(original: bytes | None, *, target: str, array_uuid: str) -> bytes:
    text = original.decode("utf-8") if original is not None else ""
    begin, end, entries = _managed_entries(text)
    entry = f"ARRAY {target} UUID={array_uuid} metadata=1.2"
    if any(line.split()[1] == target for line in entries):
        raise OSError("mdadm target is already registered")
    if any(f"UUID={array_uuid}" in line for line in entries):
        raise OSError("mdadm UUID is already registered")
    updated_entries = sorted([*entries, entry])
    block = [_BEGIN, *updated_entries, _END]
    lines = text.splitlines()
    if begin is None or end is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(block)
    else:
        lines[begin : end + 1] = block
    rendered = ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")
    if len(rendered) > _MAX_CONFIG_BYTES:
        raise OSError("mdadm configuration would exceed its safety limit")
    return rendered


def _atomic_write(path: Path, payload: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        if os.name == "posix":
            os.chown(temporary, 0, 0)
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_config(path: Path, original: bytes | None) -> None:
    if original is None:
        if path.is_symlink():
            raise OSError("refusing to unlink a replaced mdadm configuration symlink")
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, original)


@contextmanager
def _array_transaction() -> Iterator[None]:
    with _THREAD_LOCK:
        _LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(_LOCK_PATH, flags, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise OSError("native md RAID1 lock is not a regular file")
            fchmod = getattr(os, "fchmod", None)
            if callable(fchmod):
                fchmod(descriptor, 0o600)
            try:
                import fcntl
            except ImportError:  # pragma: no cover - Windows unit tests
                fcntl = None  # type: ignore[assignment]
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def _build_plan(desired: dict[str, Any], *, config_path: Path = _MDADM_CONFIG) -> dict[str, Any]:
    _require_tools()
    devices = inspect_blank_whole_disks(desired["devices"])
    scan = _scan_arrays()
    target = _array_target(desired["name"])
    config = _read_config(config_path)
    _begin, _end, managed_entries = _managed_entries(
        config.decode("utf-8") if config is not None else ""
    )
    config_text = config.decode("utf-8") if config is not None else ""
    expected_names = {f"echo-{desired['name']}", desired["name"]}
    if os.path.lexists(target) or any(
        row["path"] == target
        or (isinstance(row["name"], str) and row["name"].rsplit(":", 1)[-1] in expected_names)
        for row in scan
    ):
        raise ValueError("the derived md RAID1 name or device already exists")
    if any(line.split()[1] == target for line in managed_entries) or _config_declares_identity(
        config_text,
        target=target,
        name=desired["name"],
    ):
        raise ValueError("the derived md RAID1 target is already registered")
    config_sha256 = hashlib.sha256(config or b"").hexdigest()
    base_revision = _canonical_hash(
        {
            "devices": devices,
            "arrays": scan,
            "configSha256": config_sha256,
        }
    )
    plan = {
        "schema": MDRAID1_PLAN_SCHEMA,
        "operation": "create",
        "desired": desired,
        "target": target,
        "devices": devices,
        "usableBytes": min(item["sizeBytes"] for item in devices),
        "baseRevision": base_revision,
        "configSha256": config_sha256,
        "configExists": config is not None,
        "steps": [
            {
                "operation": "create",
                "level": "raid1",
                "metadata": "1.2",
                "devices": [item["devicefile"] for item in devices],
                "dataLoss": True,
            },
            {
                "operation": "persistAssembly",
                "config": str(config_path),
                "initramfsRefresh": True,
            },
        ],
        "filesystemCreated": False,
        "requiresApproval": True,
        "changes": [
            {
                "operation": "create",
                "resource": "mdRaid1Array",
                "name": desired["name"],
            }
        ],
        "safety": {
            "destructive": True,
            "dataLossConfirmed": True,
            "layout": "twoDiskRaid1Only",
            "devices": "wholeBlankNonRemovableWithPersistentIdentity",
            "force": False,
            "degradedStart": False,
            "filesystemCreated": False,
            "unsupported": [
                "filesystemCreate",
                "mount",
                "grow",
                "remove",
                "degradedStart",
                "assumeClean",
            ],
        },
        "rollback": {
            "array": "stoppedAndNewSuperblocksClearedOnFailure",
            "config": "restoredOnFailure",
            "filesystem": "notCreated",
        },
        "source": "native",
    }
    return {**plan, "planId": _canonical_hash(plan)}


def plan_mdraid1(
    desired_state: dict[str, Any],
    *,
    config_path: Path = _MDADM_CONFIG,
) -> dict[str, Any]:
    desired = validate_mdraid1_desired(dict(desired_state))
    with _array_transaction():
        return _build_plan(desired, config_path=config_path)


def _parse_detail(output: str, plan: dict[str, Any]) -> dict[str, Any]:
    fields = _parse_export_fields(output)
    return _array_from_export_fields(fields, plan)


def _parse_export_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        if "=" not in raw_line:
            raise OSError("mdadm detail output is invalid")
        key, value = raw_line.split("=", 1)
        if _EXPORT_KEY_PATTERN.fullmatch(key) is None or len(value) > 512:
            raise OSError("mdadm detail output is invalid")
        if key in fields:
            raise OSError("mdadm detail output contains duplicate fields")
        fields[key] = value
    return fields


def _array_from_export_fields(fields: dict[str, str], plan: dict[str, Any]) -> dict[str, Any]:
    array_uuid = fields.get("MD_UUID", "")
    devices = sorted(value for key, value in fields.items() if key.endswith("_DEV"))
    roles = sorted(value for key, value in fields.items() if key.endswith("_ROLE"))
    expected_devices = sorted(item["devicefile"] for item in plan["devices"])
    if (
        fields.get("MD_LEVEL") != "raid1"
        or fields.get("MD_DEVICES") != "2"
        or fields.get("MD_METADATA") != "1.2"
        or fields.get("MD_DEVNAME") != f"echo-{plan['desired']['name']}"
        or _UUID_PATTERN.fullmatch(array_uuid) is None
        or devices != expected_devices
        or roles != ["0", "1"]
    ):
        raise OSError("created md RAID1 did not retain the planned topology")
    return {
        "name": plan["desired"]["name"],
        "devicefile": plan["target"],
        "uuid": array_uuid.lower(),
        "level": "raid1",
        "devices": expected_devices,
        "filesystem": None,
    }


def managed_mdraid1_arrays(*, config_path: Path = _MDADM_CONFIG) -> list[dict[str, Any]]:
    """Return healthy assembled RAID1 arrays owned by the Echo config block."""
    if shutil.which("mdadm") is None:
        raise OSError("native md RAID1 inventory tool is unavailable: mdadm")
    config = _read_config(config_path)
    _begin, _end, entries = _managed_entries(
        config.decode("utf-8") if config is not None else ""
    )
    arrays: list[dict[str, Any]] = []
    for entry in entries:
        tokens = entry.split()
        target = tokens[1]
        configured_uuid = tokens[2].removeprefix("UUID=")
        name = target.removeprefix("/dev/md/echo-")
        if _run("mdadm", "--detail", "--test", target).returncode != 0:
            continue
        fields = _parse_export_fields(_run_checked("mdadm", "--detail", "--export", target))
        devices = sorted(value for key, value in fields.items() if key.endswith("_DEV"))
        roles = sorted(value for key, value in fields.items() if key.endswith("_ROLE"))
        if (
            fields.get("MD_LEVEL") != "raid1"
            or fields.get("MD_DEVICES") != "2"
            or fields.get("MD_METADATA") != "1.2"
            or fields.get("MD_DEVNAME") != f"echo-{name}"
            or fields.get("MD_UUID", "").lower() != configured_uuid
            or len(devices) != 2
            or roles != ["0", "1"]
        ):
            raise OSError("managed md RAID1 no longer matches its persisted identity")
        arrays.append(
            {
                "name": name,
                "devicefile": target,
                "uuid": configured_uuid,
                "level": "raid1",
                "devices": devices,
                "filesystem": None,
            }
        )
    uuids = [array["uuid"] for array in arrays]
    if len(uuids) != len(set(uuids)):
        raise OSError("managed md RAID1 inventory contains duplicate UUIDs")
    return sorted(arrays, key=lambda array: (array["name"], array["uuid"]))


def _verify_created_array(plan: dict[str, Any]) -> dict[str, Any]:
    if _run("mdadm", "--detail", "--test", plan["target"]).returncode != 0:
        raise OSError("created md RAID1 is degraded or unavailable")
    return _parse_detail(_run_checked("mdadm", "--detail", "--export", plan["target"]), plan)


def _signatures_absent(devices: list[str]) -> bool:
    try:
        return all(
            not _run_checked("wipefs", "--noheadings", "--output", "TYPE", device).strip()
            for device in devices
        )
    except OSError:
        return False


def _cleanup_partial_array(target: str, devices: list[str]) -> bool:
    try:
        stop = _run("mdadm", "--stop", target)
    except OSError:
        return False
    if stop.returncode != 0 and os.path.lexists(target):
        return False
    for device in devices:
        try:
            _run("mdadm", "--zero-superblock", "--force", device)
        except OSError:
            return False
    return _signatures_absent(devices)


def apply_mdraid1(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    config_path: Path = _MDADM_CONFIG,
) -> dict[str, Any]:
    desired = validate_mdraid1_desired(dict(desired_state))
    with _array_transaction():
        plan = _build_plan(desired, config_path=config_path)
        if plan["planId"] != plan_id:
            raise ValueError("md RAID1 plan is stale; preview the change again")
        devices = [item["devicefile"] for item in plan["devices"]]
        original_config = _read_config(config_path)
        if (original_config is not None) != plan["configExists"] or hashlib.sha256(
            original_config or b""
        ).hexdigest() != plan["configSha256"]:
            raise ValueError("md RAID1 plan is stale; mdadm configuration changed")
        mutation_started = False
        config_written = False
        try:
            mutation_started = True
            _run_mutating(
                "mdadm",
                "--create",
                plan["target"],
                "--metadata=1.2",
                "--level=1",
                "--raid-devices=2",
                f"--name=echo-{desired['name']}",
                "--bitmap=internal",
                *devices,
                timeout=300.0,
            )
            array = _verify_created_array(plan)
            rendered = _render_config(
                original_config,
                target=plan["target"],
                array_uuid=array["uuid"],
            )
            config_written = True
            _atomic_write(config_path, rendered)
            _run_mutating("update-initramfs", "-u", timeout=600.0)
            if _read_config(config_path) != rendered:
                raise OSError("mdadm configuration write-back verification failed")
            array = _verify_created_array(plan)
        except (OSError, ValueError) as exc:
            rollback_errors: list[str] = []
            if config_written:
                try:
                    _restore_config(config_path, original_config)
                    _run_mutating("update-initramfs", "-u", timeout=600.0)
                except OSError:
                    rollback_errors.append("config")
            if mutation_started and not _cleanup_partial_array(plan["target"], devices):
                rollback_errors.append("array")
            if rollback_errors:
                raise OSError(
                    "md RAID1 creation failed and rollback was incomplete: "
                    + ", ".join(rollback_errors)
                ) from exc
            raise OSError("md RAID1 creation failed; the new array was removed") from exc
        return {**plan, "applied": True, "verified": True, "array": array}


__all__ = [
    "apply_mdraid1",
    "managed_mdraid1_arrays",
    "mdraid1_candidates",
    "plan_mdraid1",
]
