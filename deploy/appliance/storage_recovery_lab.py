#!/usr/bin/env python3
"""Run the destructive candidate-bound SMART/RAID1 storage recovery lab."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab

SCHEMA_VERSION = 1
GATE = "real_disk_smart_and_raid_degradation_recovery"
MARKER_NAME = ".echo-storage-recovery-lab.json"
SEED_DIRECTORY_NAME = ".echo-storage-recovery-seed"
FILL_NAME = ".echo-storage-recovery-fill"
WRITE_PROBE_NAME = ".echo-storage-recovery-write-probe"
SEED_BYTES = 64 * 1024 * 1024
NAS_TRANSFER_BYTES = 1024 * 1024 * 1024
MIN_VOLUME_BYTES = 4 * 1024 * 1024 * 1024
MAX_VOLUME_BYTES = 64 * 1024 * 1024 * 1024
FILL_CHUNK_BYTES = 256 * 1024 * 1024
MIN_FILL_CHUNK_BYTES = 1024 * 1024
MAX_EVIDENCE_BYTES = 8 * 1024 * 1024
SAFE_DEVICE = re.compile(
    r"^/dev/(?:md[0-9]+|md/[A-Za-z0-9_.-]{1,64}|sd[a-z]+[0-9]*|"
    r"vd[a-z]+[0-9]*|nvme[0-9]+n[0-9]+p?[0-9]*)$"
)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PHASES = (
    "baseline",
    "degraded",
    "readonly",
    "volume-full",
    "reconnect",
    "rebuild",
    "reboot",
    "recycle-restore",
)
PHASE_OUTPUTS = {
    "baseline": "storage-baseline.log",
    "degraded": "storage-degraded.log",
    "readonly": "storage-readonly.log",
    "volume-full": "storage-volume-full.log",
    "reconnect": "storage-reconnect.log",
    "rebuild": "storage-rebuild.log",
    "reboot": "storage-reboot.log",
    "recycle-restore": "storage-recycle-restore.log",
}


class StorageRecoveryLabError(RuntimeError):
    """The physical storage recovery lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    smartctl: Path = Path("/usr/sbin/smartctl")
    mdadm: Path = Path("/usr/sbin/mdadm")
    findmnt: Path = Path("/usr/bin/findmnt")
    lsblk: Path = Path("/usr/bin/lsblk")
    mount: Path = Path("/usr/bin/mount")
    touch: Path = Path("/usr/bin/touch")
    fallocate: Path = Path("/usr/bin/fallocate")
    dd: Path = Path("/usr/bin/dd")
    sync: Path = Path("/usr/bin/sync")
    python: Path = Path("/usr/bin/python3")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")


DEFAULT_TOOLS = LabTools()
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
ArrayProbe = Callable[[Path], Mapping[str, Any]]
MountProbe = Callable[[Path], Mapping[str, Any]]
SmartProbe = Callable[[Sequence[Mapping[str, Any]]], Mapping[str, Any]]
MemberProbe = Callable[[Mapping[str, Any]], bool]
DeviceVerifier = Callable[[Path, Sequence[Path], Path, LabTools, CommandRunner], Mapping[str, Any]]
BootIdReader = Callable[[], str]
Sleeper = Callable[[float], None]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=3600,
        env={
            **os.environ,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path, label: str, *, trusted_uid: int, exact_mode: int) -> dict[str, Any]:
    raw = systemd._safe_regular(
        path,
        label,
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=exact_mode & 0o077 == 0,
        exact_mode=exact_mode,
    )
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageRecoveryLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise StorageRecoveryLabError(f"{label} is not an object")
    return value


def _write_new(path: Path, value: Mapping[str, Any], *, trusted_uid: int, mode: int) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise StorageRecoveryLabError("storage lab output must be a new regular file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(parent, "storage lab output directory", trusted_uid=trusted_uid)
    descriptor = os.open(
        parent / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
        mode,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _boot_id() -> str:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        parsed = uuid.UUID(value)
    except (OSError, UnicodeError, ValueError) as exc:
        raise StorageRecoveryLabError("kernel boot identity is unavailable") from exc
    if str(parsed) != value:
        raise StorageRecoveryLabError("kernel boot identity is not canonical")
    return value


def _validated_tools(tools: LabTools, *, trusted_uid: int) -> None:
    for tool in (
        tools.smartctl,
        tools.mdadm,
        tools.findmnt,
        tools.lsblk,
        tools.mount,
        tools.touch,
        tools.fallocate,
        tools.dd,
        tools.sync,
        tools.python,
        tools.dpkg_query,
    ):
        systemd._safe_regular(
            tool,
            f"physical storage lab tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _strict_command_json(
    command: list[str], runner: CommandRunner, label: str, *, maximum: int = 1024 * 1024
) -> Any:
    completed = runner(command)
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8", "replace")) > maximum:
        raise StorageRecoveryLabError(f"{label} command failed")
    try:
        return json.loads(completed.stdout, object_pairs_hook=systemd._reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise StorageRecoveryLabError(f"{label} did not return strict JSON") from exc


def _device_inventory(
    array: Path,
    members: Sequence[Path],
    mountpoint: Path,
    tools: LabTools,
    runner: CommandRunner,
) -> dict[str, Any]:
    for path in (array, *members):
        if not path.is_absolute() or SAFE_DEVICE.fullmatch(str(path)) is None or path.is_symlink():
            raise StorageRecoveryLabError("array and member devices must use safe canonical paths")
        try:
            info = path.stat()
        except OSError as exc:
            raise StorageRecoveryLabError("array or member device is unavailable") from exc
        if not stat.S_ISBLK(info.st_mode):
            raise StorageRecoveryLabError("array and members must be block devices")
    value = _strict_command_json(
        [
            str(tools.lsblk),
            "--json",
            "--bytes",
            "--paths",
            "--output",
            "NAME,TYPE,SIZE,MAJ:MIN,PKNAME,SERIAL,WWN,MOUNTPOINTS",
        ],
        runner,
        "block inventory",
    )
    roots = value.get("blockdevices") if isinstance(value, dict) else None
    if not isinstance(roots, list):
        raise StorageRecoveryLabError("block inventory is incomplete")
    records: dict[str, dict[str, Any]] = {}

    def visit(item: object, parent: str | None = None) -> None:
        if not isinstance(item, dict) or len(records) >= 1024:
            return
        name = item.get("name")
        if isinstance(name, str) and SAFE_DEVICE.fullmatch(name):
            records[name] = {**item, "parent": parent or item.get("pkname")}
            for child in item.get("children", []) if isinstance(item.get("children"), list) else []:
                visit(child, name)

    for root in roots:
        visit(root)
    selected = [str(array), *(str(member) for member in members)]
    if any(name not in records for name in selected):
        raise StorageRecoveryLabError("declared RAID devices are absent from block inventory")
    root_paths = {
        name for name, record in records.items() if "/" in (record.get("mountpoints") or [])
    }
    frontier = list(root_paths)
    while frontier:
        name = frontier.pop()
        parent = records.get(name, {}).get("parent")
        if isinstance(parent, str) and parent in records and parent not in root_paths:
            root_paths.add(parent)
            frontier.append(parent)
    if any(name in root_paths for name in selected):
        raise StorageRecoveryLabError("storage lab cannot target the system root device graph")
    member_records = []
    for member in members:
        record = records[str(member)]
        parent_path = record.get("parent")
        parent = records.get(parent_path, record) if isinstance(parent_path, str) else record
        identity_payload = {
            "path": str(member),
            "majorMinor": record.get("maj:min"),
            "size": record.get("size"),
            "parentMajorMinor": parent.get("maj:min"),
            "parentSize": parent.get("size"),
            "serial": parent.get("serial") or "",
            "wwn": parent.get("wwn") or "",
        }
        if (
            not isinstance(identity_payload["majorMinor"], str)
            or not isinstance(identity_payload["size"], int)
            or identity_payload["size"] <= 0
            or not isinstance(identity_payload["parentMajorMinor"], str)
            or not isinstance(identity_payload["parentSize"], int)
            or identity_payload["parentSize"] <= 0
        ):
            raise StorageRecoveryLabError("member block identity is incomplete")
        member_records.append(
            {
                "path": str(member),
                "parentPath": str(parent.get("name") or member),
                "majorMinor": identity_payload["majorMinor"],
                "sizeBytes": identity_payload["size"],
                "identitySha256": _sha256(_canonical(identity_payload)),
            }
        )
    array_record = records[str(array)]
    return {
        "array": {
            "path": str(array),
            "majorMinor": array_record.get("maj:min"),
            "sizeBytes": array_record.get("size"),
        },
        "members": member_records,
        "mountpoint": str(mountpoint),
    }


def _md_export(array: Path, tools: LabTools, runner: CommandRunner) -> dict[str, str]:
    completed = runner([str(tools.mdadm), "--detail", "--export", str(array)])
    if completed.returncode != 0 or len(completed.stdout) > 128 * 1024:
        raise StorageRecoveryLabError("md RAID detail is unavailable")
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in values:
            raise StorageRecoveryLabError("md RAID detail contains duplicate fields")
        values[key] = value
    return values


def _array_status(array: Path, tools: LabTools, runner: CommandRunner) -> dict[str, Any]:
    values = _md_export(array, tools, runner)
    try:
        devices = int(values["MD_DEVICES"])
        active = int(values["MD_DEVICES_ACTIVE"])
        failed = int(values.get("MD_DEVICES_FAILED", "0"))
    except (KeyError, ValueError) as exc:
        raise StorageRecoveryLabError("md RAID detail is incomplete") from exc
    level = values.get("MD_LEVEL", "").lower()
    state = values.get("MD_STATE", "").lower()
    if level != "raid1" or devices != 2 or active not in {1, 2} or failed not in {0, 1}:
        raise StorageRecoveryLabError("storage lab requires one two-member RAID1 array")
    recovering = any(token in state for token in ("recover", "resync", "reshape"))
    healthy = active == 2 and failed == 0 and not recovering and "clean" in state
    degraded = active == 1 or failed == 1 or "degraded" in state
    return {
        "level": "raid1",
        "devices": devices,
        "active": active,
        "failed": failed,
        "state": state,
        "healthy": healthy,
        "degraded": degraded,
        "recovering": recovering,
    }


def _mount_status(mountpoint: Path, tools: LabTools, runner: CommandRunner) -> dict[str, Any]:
    value = _strict_command_json(
        [
            str(tools.findmnt),
            "--json",
            "--bytes",
            "--target",
            str(mountpoint),
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS,SIZE,AVAIL",
        ],
        runner,
        "filesystem mount",
    )
    filesystems = value.get("filesystems") if isinstance(value, dict) else None
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise StorageRecoveryLabError("storage lab mount is ambiguous")
    record = filesystems[0]
    options = str(record.get("options") or "").split(",")
    try:
        size = int(record["size"])
        available = int(record["avail"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StorageRecoveryLabError("storage lab mount capacity is unavailable") from exc
    return {
        "target": str(record.get("target") or ""),
        "source": str(record.get("source") or ""),
        "filesystem": str(record.get("fstype") or "").lower(),
        "readOnly": "ro" in options and "rw" not in options,
        "sizeBytes": size,
        "availableBytes": available,
    }


def _smart_status(
    members: Sequence[Mapping[str, Any]], tools: LabTools, runner: CommandRunner
) -> dict[str, Any]:
    disks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for member in members:
        disk = str(member["parentPath"])
        if disk in seen:
            continue
        seen.add(disk)
        value = _strict_command_json(
            [str(tools.smartctl), "--health", "--json=c", disk],
            runner,
            "SMART health",
        )
        passed = (
            value.get("smart_status", {}).get("passed")
            if isinstance(value, dict) and isinstance(value.get("smart_status"), dict)
            else None
        )
        if passed is not True:
            raise StorageRecoveryLabError("a RAID member disk did not pass SMART health")
        disks.append({"identitySha256": member["identitySha256"], "passed": True})
    if len(disks) != 2:
        raise StorageRecoveryLabError("storage lab requires two independent SMART disks")
    return {"allPassed": True, "diskCount": 2, "disks": disks}


def _member_present(member: Mapping[str, Any]) -> bool:
    path = Path(str(member["path"]))
    if path.is_symlink():
        return False
    try:
        info = path.stat()
    except OSError:
        return False
    return (
        stat.S_ISBLK(info.st_mode)
        and f"{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}" == member["majorMinor"]
    )


def _bundle_identity(
    bundle_root: Path, candidate: Mapping[str, str], *, trusted_uid: int
) -> dict[str, Any]:
    manifest_raw = systemd._safe_regular(
        bundle_root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(
            manifest_raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageRecoveryLabError("operations bundle manifest is not strict JSON") from exc
    if not isinstance(manifest, dict):
        raise StorageRecoveryLabError("operations bundle manifest is not an object")
    artifact = manifest.get("artifact")
    files = manifest.get("files")
    expected = {
        "storage_recovery_lab.py": (
            0o755,
            "storageRecoveryLab",
            "./storage_recovery_lab.py plan|run",
        ),
        "verify-running-appliance.py": (0o755, None, None),
    }
    if (
        set(manifest) != {"schemaVersion", "artifact", "files"}
        or manifest.get("schemaVersion") != 1
        or not isinstance(artifact, dict)
        or artifact.get("id") != candidate["operationsArtifactId"]
        or artifact.get("name") != bundle_root.name
        or artifact.get("imageReference") != candidate["immutableReference"]
        or not isinstance(artifact.get("entrypoints"), dict)
        or not isinstance(files, dict)
    ):
        raise StorageRecoveryLabError("storage lab bundle is not from the release candidate")
    result: dict[str, Any] = {
        "artifactId": candidate["operationsArtifactId"],
        "archiveSha256": candidate["operationsArchiveSha256"],
        "imageReference": candidate["immutableReference"],
        "manifestSha256": _sha256(manifest_raw),
    }
    for name, (mode, entrypoint, command) in expected.items():
        raw = systemd._safe_regular(
            bundle_root / name,
            f"candidate bundle tool {name}",
            maximum=16 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=mode,
        )
        record = files.get(name)
        if (
            not isinstance(record, dict)
            or record != {"sha256": _sha256(raw), "size": len(raw), "mode": f"{mode:04o}"}
            or (entrypoint is not None and artifact["entrypoints"].get(entrypoint) != command)
        ):
            raise StorageRecoveryLabError("storage lab bundle tool bytes are unbound")
        result[
            "storageRecoveryLabSha256"
            if name == "storage_recovery_lab.py"
            else "runningVerifierSha256"
        ] = _sha256(raw)
    return result


def _validate_marker(
    mountpoint: Path,
    *,
    candidate_index_id: str,
    array: Path,
    trusted_uid: int,
    require_empty: bool = True,
) -> dict[str, Any]:
    if mountpoint in {Path("/"), Path("/home"), Path("/var"), Path("/srv")}:
        raise StorageRecoveryLabError("storage lab mountpoint is a protected system path")
    systemd._assert_owned_directory(
        mountpoint, "storage recovery mountpoint", trusted_uid=trusted_uid
    )
    entries = {entry.name for entry in mountpoint.iterdir()}
    allowed = {MARKER_NAME, "lost+found"}
    if not require_empty:
        allowed.add(SEED_DIRECTORY_NAME)
    if not entries <= allowed or MARKER_NAME not in entries:
        raise StorageRecoveryLabError("storage lab volume must be dedicated and otherwise empty")
    marker = _read_json(
        mountpoint / MARKER_NAME,
        "storage recovery authorization marker",
        trusted_uid=trusted_uid,
        exact_mode=0o444,
    )
    try:
        lab_volume_id = str(uuid.UUID(str(marker.get("labVolumeId")), version=4))
    except ValueError as exc:
        raise StorageRecoveryLabError("storage lab volume ID is invalid") from exc
    if (
        set(marker)
        != {
            "schemaVersion",
            "kind",
            "disposable",
            "candidateIndexId",
            "arrayDevice",
            "mountpoint",
            "labVolumeId",
        }
        or marker.get("schemaVersion") != 1
        or marker.get("kind") != "echo.storage-recovery-lab-authorization"
        or marker.get("disposable") is not True
        or marker.get("candidateIndexId") != candidate_index_id
        or marker.get("arrayDevice") != str(array)
        or marker.get("mountpoint") != str(mountpoint)
        or marker.get("labVolumeId") != lab_volume_id
    ):
        raise StorageRecoveryLabError("storage lab authorization marker is invalid")
    return marker


def _tree_digest(root: Path) -> dict[str, Any]:
    if root.is_symlink() or not root.is_dir():
        raise StorageRecoveryLabError("storage recovery seed directory is unavailable")
    digest = hashlib.sha256()
    total = 0
    count = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            raise StorageRecoveryLabError("storage recovery seed contains an unsafe entry")
        relative = path.relative_to(root).as_posix()
        record = operations_lab._sha256_file(path, maximum=SEED_BYTES * 2)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(record["sha256"].encode())
        digest.update(b"\0")
        digest.update(str(record["size"]).encode())
        digest.update(b"\0")
        total += record["size"]
        count += 1
    if count != 2 or total != SEED_BYTES:
        raise StorageRecoveryLabError("storage recovery seed inventory is incomplete")
    return {"sha256": digest.hexdigest(), "size": total, "fileCount": count}


def _create_seed(root: Path) -> dict[str, Any]:
    if root.exists() or root.is_symlink():
        raise StorageRecoveryLabError("storage recovery seed path must be new")
    root.mkdir(mode=0o700)
    chunk = bytes(range(256)) * 4096
    remaining = SEED_BYTES
    for index in range(2):
        path = root / f"seed-{index}.bin"
        target = SEED_BYTES // 2
        with path.open("xb") as handle:
            written = 0
            while written < target:
                payload = chunk[: min(len(chunk), target - written)]
                handle.write(payload)
                written += len(payload)
            handle.flush()
            os.fsync(handle.fileno())
        remaining -= target
    if remaining != 0:
        raise StorageRecoveryLabError("storage recovery seed size is invalid")
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return _tree_digest(root)


def _safe_relative(value: str) -> str:
    raw = value.strip().strip("/")
    parts = PurePosixPath(raw).parts
    if (
        not raw
        or len(raw) > 256
        or "\\" in raw
        or "\x00" in raw
        or any(part in {"", ".", "..", ".echo-trash"} for part in parts)
    ):
        raise StorageRecoveryLabError("NAS transfer test path is invalid")
    return PurePosixPath(*parts).as_posix()


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    array: Path,
    members: Sequence[Path],
    sacrificial_member: Path,
    mountpoint: Path,
    evidence_directory: Path,
    nas_transfer_path: str,
    base_url: str,
    output: Path,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    array_probe: ArrayProbe | None = None,
    mount_probe: MountProbe | None = None,
    smart_probe: SmartProbe | None = None,
    device_verifier: DeviceVerifier = _device_inventory,
    boot_id_reader: BootIdReader = _boot_id,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise StorageRecoveryLabError("physical storage lab plan requires Linux root")
    if (
        len(members) != 2
        or len({str(path) for path in members}) != 2
        or sacrificial_member not in members
    ):
        raise StorageRecoveryLabError(
            "storage lab requires two members and one exact sacrificial member"
        )
    _validated_tools(tools, trusted_uid=trusted_uid)
    candidate = operations_lab._candidate_identity(candidate_index, trusted_uid=trusted_uid)
    bundle = _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
    platform = {
        **operations_lab._read_os_release(os_release),
        "omvVersion": operations_lab._omv_version(
            operations_lab.LabTools(dpkg_query=tools.dpkg_query), runner
        ),
    }
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "storage lab evidence directory", trusted_uid=trusted_uid
    )
    mount_root = mountpoint.resolve(strict=True)
    devices = dict(device_verifier(array, members, mount_root, tools, runner))
    marker = _validate_marker(
        mount_root,
        candidate_index_id=candidate["indexId"],
        array=array,
        trusted_uid=trusted_uid,
    )
    probe_array = array_probe or (lambda path: _array_status(path, tools, runner))
    probe_mount = mount_probe or (lambda path: _mount_status(path, tools, runner))
    probe_smart = smart_probe or (lambda records: _smart_status(records, tools, runner))
    array_state = dict(probe_array(array))
    mount_state = dict(probe_mount(mount_root))
    smart_state = dict(probe_smart(devices["members"]))
    if (
        array_state.get("healthy") is not True
        or array_state.get("active") != 2
        or mount_state.get("target") != str(mount_root)
        or mount_state.get("source") != str(array)
        or mount_state.get("filesystem") not in {"ext4", "xfs"}
        or mount_state.get("readOnly") is not False
        or not MIN_VOLUME_BYTES <= int(mount_state.get("sizeBytes", 0)) <= MAX_VOLUME_BYTES
        or int(mount_state.get("availableBytes", 0)) < 2 * 1024 * 1024 * 1024
        or smart_state.get("allPassed") is not True
        or smart_state.get("diskCount") != 2
    ):
        raise StorageRecoveryLabError("storage lab requires a healthy dedicated RAID1 volume")
    parsed_url = urlsplit(base_url)
    if (
        parsed_url.scheme not in {"http", "https"}
        or parsed_url.hostname not in {"127.0.0.1", "localhost"}
        or parsed_url.username is not None
        or parsed_url.password is not None
        or parsed_url.path not in {"", "/"}
        or parsed_url.query
        or parsed_url.fragment
    ):
        raise StorageRecoveryLabError("storage lab appliance URL must be one loopback origin")
    payload: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.storage-recovery-physical-lab-plan",
        "gate": GATE,
        "releaseCandidate": candidate,
        "bundleRoot": str(bundle_root.resolve(strict=True)),
        "operationsBundle": bundle,
        "platform": platform,
        "devices": devices,
        "sacrificialMember": str(sacrificial_member),
        "mount": mount_state,
        "authorization": marker,
        "evidenceDirectory": str(evidence_root),
        "nasTransfer": {
            "baseUrl": urlunsplit((parsed_url.scheme, parsed_url.netloc, "", "", "")),
            "path": _safe_relative(nas_transfer_path),
            "bytes": NAS_TRANSFER_BYTES,
        },
        "baselineBootId": boot_id_reader(),
        "phases": list(PHASES),
    }
    payload["planId"] = _sha256(_canonical(payload))
    payload["confirmations"] = {
        phase: f"RUN ECHO STORAGE RECOVERY LAB {phase} {payload['planId']}" for phase in PHASES
    }
    _write_new(output, payload, trusted_uid=trusted_uid, mode=0o400)
    return payload


def _phase_dependencies(root: Path, phase: str, *, plan_id: str, trusted_uid: int) -> None:
    index = PHASES.index(phase)
    required = {PHASE_OUTPUTS[item] for item in PHASES[:index]}
    forbidden = {PHASE_OUTPUTS[item] for item in PHASES[index:]}
    actual = {name for name in required | forbidden if (root / name).exists()}
    if not required <= actual or actual & forbidden:
        raise StorageRecoveryLabError("storage lab evidence sequence is incomplete or stale")
    for name in required:
        value = _read_json(
            root / name,
            f"prior storage lab evidence {name}",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        if (
            set(value) != {"schemaVersion", "kind", "planId", "evidence", "passed", "details"}
            or value.get("schemaVersion") != SCHEMA_VERSION
            or value.get("kind") != "echo.storage-recovery-physical-lab-evidence"
            or value.get("planId") != plan_id
            or value.get("evidence") != name
            or value.get("passed") is not True
            or not isinstance(value.get("details"), dict)
        ):
            raise StorageRecoveryLabError("prior storage lab evidence is invalid")


def _write_phase(
    root: Path, phase: str, plan_id: str, details: Mapping[str, Any], *, trusted_uid: int
) -> None:
    _write_new(
        root / PHASE_OUTPUTS[phase],
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.storage-recovery-physical-lab-evidence",
            "planId": plan_id,
            "evidence": PHASE_OUTPUTS[phase],
            "passed": True,
            "details": dict(details),
        },
        trusted_uid=trusted_uid,
        mode=0o444,
    )


def _verify_plan_identity(plan: Mapping[str, Any], confirmation: str, phase: str) -> None:
    expected_keys = {
        "schemaVersion",
        "kind",
        "gate",
        "releaseCandidate",
        "bundleRoot",
        "operationsBundle",
        "platform",
        "devices",
        "sacrificialMember",
        "mount",
        "authorization",
        "evidenceDirectory",
        "nasTransfer",
        "baselineBootId",
        "phases",
        "planId",
        "confirmations",
    }
    unsigned = dict(plan)
    confirmations = unsigned.pop("confirmations", None)
    plan_id = unsigned.pop("planId", None)
    expected_confirmations = {
        item: f"RUN ECHO STORAGE RECOVERY LAB {item} {plan_id}" for item in PHASES
    }
    if (
        set(plan) != expected_keys
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.storage-recovery-physical-lab-plan"
        or plan.get("gate") != GATE
        or plan.get("phases") != list(PHASES)
        or confirmations != expected_confirmations
        or confirmations.get(phase) != confirmation
        or plan_id != _sha256(_canonical(unsigned))
    ):
        raise StorageRecoveryLabError("storage lab plan or confirmation is invalid")


def _fill_volume(
    mountpoint: Path,
    *,
    tools: LabTools,
    runner: CommandRunner,
    mount_probe: MountProbe,
) -> dict[str, Any]:
    fill = mountpoint / FILL_NAME
    write_probe = mountpoint / WRITE_PROBE_NAME
    if fill.exists() or fill.is_symlink() or write_probe.exists() or write_probe.is_symlink():
        raise StorageRecoveryLabError("storage lab fill paths must be new")
    offset = 0
    before = int(mount_probe(mountpoint)["availableBytes"])
    try:
        chunk_size = FILL_CHUNK_BYTES
        while chunk_size >= MIN_FILL_CHUNK_BYTES:
            while True:
                completed = runner(
                    [
                        str(tools.fallocate),
                        "--offset",
                        str(offset),
                        "--length",
                        str(chunk_size),
                        str(fill),
                    ]
                )
                if completed.returncode != 0:
                    if not _reports_enospc(completed):
                        raise StorageRecoveryLabError(
                            "lab volume allocation failed without an ENOSPC result"
                        )
                    break
                offset += chunk_size
                if offset > before + FILL_CHUNK_BYTES:
                    raise StorageRecoveryLabError("lab volume allocation exceeded its safe bound")
            chunk_size //= 2
        if offset <= 0:
            raise StorageRecoveryLabError("dedicated lab volume did not consume any free capacity")
        write_result = runner(
            [
                str(tools.dd),
                "if=/dev/zero",
                f"of={write_probe}",
                f"bs={MIN_FILL_CHUNK_BYTES}",
                "count=1",
                "conv=fsync",
                "status=none",
            ]
        )
        if write_result.returncode == 0 or not _reports_enospc(write_result):
            raise StorageRecoveryLabError("full lab volume still accepted a new write")
    finally:
        if fill.exists() and not fill.is_symlink():
            fill.unlink()
        if write_probe.exists() and not write_probe.is_symlink():
            write_probe.unlink()
        runner([str(tools.sync)])
    after = int(mount_probe(mountpoint)["availableBytes"])
    if after < before - FILL_CHUNK_BYTES:
        raise StorageRecoveryLabError("lab volume capacity did not recover after fill cleanup")
    return {
        "enospcObserved": True,
        "rejectedWrite": True,
        "cleanupRecovered": True,
        "allocatedBytes": offset,
    }


def _reports_enospc(completed: subprocess.CompletedProcess[str]) -> bool:
    message = f"{completed.stdout}\n{completed.stderr}".casefold()
    return "no space left on device" in message or "disk quota exceeded" in message


def _verify_recycle_result(value: Any) -> dict[str, Any]:
    transfer = value.get("nas_transfer") if isinstance(value, dict) else None
    if (
        not isinstance(transfer, dict)
        or transfer.get("writeExecuted") is not True
        or transfer.get("size") != NAS_TRANSFER_BYTES
        or transfer.get("recycleRestoreVerified") is not True
        or transfer.get("physicallyDeleted") is not False
        or not isinstance(transfer.get("sha256"), str)
        or SHA256.fullmatch(transfer["sha256"]) is None
        or transfer.get("restoredSha256") != transfer["sha256"]
    ):
        raise StorageRecoveryLabError("appliance recycle-bin restoration verification failed")
    return {
        "bytes": transfer["size"],
        "sha256": transfer["sha256"],
        "restoreVerified": True,
        "finalState": "recoverable-trash",
    }


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    wait_seconds: int = 0,
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    array_probe: ArrayProbe | None = None,
    mount_probe: MountProbe | None = None,
    smart_probe: SmartProbe | None = None,
    member_probe: MemberProbe = _member_present,
    device_verifier: DeviceVerifier = _device_inventory,
    boot_id_reader: BootIdReader = _boot_id,
    sleeper: Sleeper = time.sleep,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    if phase not in PHASES:
        raise StorageRecoveryLabError("storage lab phase is invalid")
    if (
        not isinstance(wait_seconds, int)
        or isinstance(wait_seconds, bool)
        or not 0 <= wait_seconds <= 86400
    ):
        raise StorageRecoveryLabError("storage rebuild wait must be between 0 and 86400 seconds")
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise StorageRecoveryLabError("physical storage lab phase requires Linux root")
    _validated_tools(tools, trusted_uid=trusted_uid)
    plan = _read_json(
        plan_path, "physical storage recovery lab plan", trusted_uid=trusted_uid, exact_mode=0o400
    )
    _verify_plan_identity(plan, confirmation, phase)
    candidate = operations_lab._candidate_identity(
        Path(plan["releaseCandidate"]["indexPath"]), trusted_uid=trusted_uid
    )
    bundle_root = Path(str(plan["bundleRoot"]))
    if not bundle_root.is_absolute():
        raise StorageRecoveryLabError("storage lab bundle root is absent from the plan")
    if (
        candidate != plan["releaseCandidate"]
        or _bundle_identity(bundle_root, candidate, trusted_uid=trusted_uid)
        != plan["operationsBundle"]
    ):
        raise StorageRecoveryLabError("storage lab candidate or operations bundle drifted")
    current_platform = {
        **operations_lab._read_os_release(os_release),
        "omvVersion": operations_lab._omv_version(
            operations_lab.LabTools(dpkg_query=tools.dpkg_query), runner
        ),
    }
    if current_platform != plan["platform"]:
        raise StorageRecoveryLabError("storage lab platform drifted")
    root = Path(plan["evidenceDirectory"]).resolve(strict=True)
    mountpoint = Path(plan["authorization"]["mountpoint"]).resolve(strict=True)
    array = Path(plan["authorization"]["arrayDevice"])
    members = plan["devices"]["members"]
    member_paths = [Path(str(record["path"])) for record in members]
    if phase in {"baseline", "reconnect", "rebuild", "reboot", "recycle-restore"}:
        current_devices = dict(device_verifier(array, member_paths, mountpoint, tools, runner))
        if current_devices != plan["devices"]:
            raise StorageRecoveryLabError("storage lab block device identities drifted")
    marker = _validate_marker(
        mountpoint,
        candidate_index_id=candidate["indexId"],
        array=array,
        trusted_uid=trusted_uid,
        require_empty=phase == "baseline",
    )
    if marker != plan["authorization"]:
        raise StorageRecoveryLabError("storage lab authorization marker drifted")
    probe_array = array_probe or (lambda path: _array_status(path, tools, runner))
    probe_mount = mount_probe or (lambda path: _mount_status(path, tools, runner))
    probe_smart = smart_probe or (lambda records: _smart_status(records, tools, runner))
    _phase_dependencies(root, phase, plan_id=plan["planId"], trusted_uid=trusted_uid)
    sacrificial = next(
        (record for record in members if record["path"] == plan["sacrificialMember"]), None
    )
    if sacrificial is None:
        raise StorageRecoveryLabError("storage lab sacrificial member identity is invalid")
    seed_root = mountpoint / SEED_DIRECTORY_NAME
    array_state = dict(probe_array(array))
    mount_state = dict(probe_mount(mountpoint))

    if phase == "baseline":
        if array_state.get("healthy") is not True or any(
            not member_probe(item) for item in members
        ):
            raise StorageRecoveryLabError("baseline requires both healthy RAID members")
        seed = _create_seed(seed_root)
        smart = dict(probe_smart(members))
        details = {
            "smartHealthy": smart.get("allPassed") is True,
            "smartDiskCount": smart.get("diskCount"),
            "arrayHealthy": True,
            "activeMembers": 2,
            "seed": seed,
        }
    elif phase == "degraded":
        baseline = _read_json(
            root / PHASE_OUTPUTS["baseline"],
            "storage baseline evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        if (
            member_probe(sacrificial)
            or array_state.get("degraded") is not True
            or array_state.get("active") != 1
        ):
            raise StorageRecoveryLabError(
                "physical member disconnect or RAID degradation was not observed"
            )
        if _tree_digest(seed_root) != baseline["details"]["seed"]:
            raise StorageRecoveryLabError("seed data changed during member disconnect")
        details = {
            "memberDisconnected": True,
            "raidDegraded": True,
            "activeMembers": 1,
            "dataReadable": True,
        }
    elif phase == "readonly":
        if array_state.get("degraded") is not True or mount_state.get("readOnly") is not False:
            raise StorageRecoveryLabError("read-only test requires degraded read-write RAID mount")
        restored = False
        try:
            if (
                runner(
                    [str(tools.mount), "--options", "remount,ro", "--", str(mountpoint)]
                ).returncode
                != 0
            ):
                raise StorageRecoveryLabError("lab filesystem could not be remounted read-only")
            if probe_mount(mountpoint).get("readOnly") is not True:
                raise StorageRecoveryLabError("filesystem did not enter read-only state")
            write = runner([str(tools.touch), str(mountpoint / WRITE_PROBE_NAME)])
            if write.returncode == 0:
                raise StorageRecoveryLabError("read-only filesystem accepted a write")
        finally:
            restored = (
                runner(
                    [str(tools.mount), "--options", "remount,rw", "--", str(mountpoint)]
                ).returncode
                == 0
                and probe_mount(mountpoint).get("readOnly") is False
            )
        if not restored or (mountpoint / WRITE_PROBE_NAME).exists():
            raise StorageRecoveryLabError("filesystem read-write state was not restored")
        details = {"readOnlyObserved": True, "writeRejected": True, "readWriteRestored": True}
    elif phase == "volume-full":
        if array_state.get("degraded") is not True or mount_state.get("readOnly") is not False:
            raise StorageRecoveryLabError(
                "volume-full test requires degraded read-write RAID mount"
            )
        details = _fill_volume(mountpoint, tools=tools, runner=runner, mount_probe=probe_mount)
    elif phase == "reconnect":
        if not member_probe(sacrificial) or array_state.get("degraded") is not True:
            raise StorageRecoveryLabError("original RAID member was not safely reconnected")
        completed = runner(
            [str(tools.mdadm), "--manage", str(array), "--add", str(sacrificial["path"])]
        )
        if completed.returncode != 0:
            raise StorageRecoveryLabError("mdadm rejected the exact reconnected member")
        after = dict(probe_array(array))
        if after.get("active") != 2 or after.get("recovering") is not True:
            raise StorageRecoveryLabError("RAID rebuild did not start")
        details = {"sameMemberReconnected": True, "rebuildStarted": True}
    elif phase == "rebuild":
        deadline = time.monotonic() + wait_seconds
        while array_state.get("healthy") is not True and time.monotonic() < deadline:
            sleeper(min(30, max(1, deadline - time.monotonic())))
            array_state = dict(probe_array(array))
        baseline = _read_json(
            root / PHASE_OUTPUTS["baseline"],
            "storage baseline evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        seed = _tree_digest(seed_root)
        if (
            array_state.get("healthy") is not True
            or array_state.get("active") != 2
            or seed != baseline["details"]["seed"]
        ):
            raise StorageRecoveryLabError("RAID rebuild or data preservation is incomplete")
        details = {
            "raidRebuildCompleted": True,
            "activeMembers": 2,
            "dataPreserved": True,
            "seed": seed,
        }
    elif phase == "reboot":
        baseline = _read_json(
            root / PHASE_OUTPUTS["baseline"],
            "storage baseline evidence",
            trusted_uid=trusted_uid,
            exact_mode=0o444,
        )
        current_boot = boot_id_reader()
        if (
            current_boot == plan["baselineBootId"]
            or array_state.get("healthy") is not True
            or mount_state.get("readOnly") is not False
            or _tree_digest(seed_root) != baseline["details"]["seed"]
        ):
            raise StorageRecoveryLabError("post-reboot RAID recovery is incomplete")
        details = {
            "bootIdChanged": True,
            "arrayHealthy": True,
            "mountedReadWrite": True,
            "dataPreserved": True,
        }
    else:
        transfer = plan["nasTransfer"]
        origin = transfer["baseUrl"]
        path_label = transfer["path"] or "ROOT"
        confirmation_value = (
            f"VERIFY ECHO NAS TRANSFER {transfer['bytes']} {path_label} ON {origin}"
        )
        command = [
            str(tools.python),
            str(bundle_root / "verify-running-appliance.py"),
            "--base-url",
            origin,
            "--require-clean-bundle",
            "--require-omv",
            "--nas-transfer-test-bytes",
            str(transfer["bytes"]),
            "--nas-transfer-test-path",
            transfer["path"],
            "--nas-transfer-write-confirm",
            confirmation_value,
            "--require-nas-transfer",
        ]
        completed = runner(command)
        if completed.returncode != 0 or len(completed.stdout) > MAX_EVIDENCE_BYTES:
            raise StorageRecoveryLabError("running appliance recycle verification failed")
        try:
            verifier_result = json.loads(
                completed.stdout, object_pairs_hook=systemd._reject_duplicate_keys
            )
        except json.JSONDecodeError as exc:
            raise StorageRecoveryLabError(
                "running appliance verifier returned invalid JSON"
            ) from exc
        details = _verify_recycle_result(verifier_result)
    _write_phase(root, phase, plan["planId"], details, trusted_uid=trusted_uid)
    return {"phase": phase, "planId": plan["planId"], "output": PHASE_OUTPUTS[phase]}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--array", type=Path, required=True)
    plan.add_argument("--member", type=Path, action="append", required=True)
    plan.add_argument("--sacrificial-member", type=Path, required=True)
    plan.add_argument("--mountpoint", type=Path, required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--nas-transfer-path", required=True)
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--wait-seconds", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                array=args.array,
                members=args.member,
                sacrificial_member=args.sacrificial_member,
                mountpoint=args.mountpoint,
                evidence_directory=args.evidence_directory,
                nas_transfer_path=args.nas_transfer_path,
                base_url=args.base_url,
                output=args.output,
            )
            print(
                "ECHO_STORAGE_RECOVERY_LAB_PLAN_READY "
                f"candidate={plan['releaseCandidate']['indexId']} plan={plan['planId']} "
                f"phases={len(plan['phases'])}"
            )
            for phase in PHASES:
                print(f"{phase}: {plan['confirmations'][phase]}")
            return 0
        report = run_phase(
            plan_path=args.plan,
            phase=args.phase,
            confirmation=args.confirm,
            wait_seconds=args.wait_seconds,
        )
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
        StorageRecoveryLabError,
        operations_lab.OperationsSystemdLabError,
        systemd.OperationsSystemdError,
    ) as exc:
        print(f"Echo storage recovery physical lab failed: {exc}", file=sys.stderr)
        return 1
    print(
        "ECHO_STORAGE_RECOVERY_LAB_PHASE_OK "
        f"phase={report['phase']} plan={report['planId']} output={report['output']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
