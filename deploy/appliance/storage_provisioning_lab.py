#!/usr/bin/env python3
"""Provision and reboot-verify an Echo-managed RAID1/EXT4 test volume.

This is a destructive, release-candidate-bound lab for two disposable whole
disks.  It deliberately uses the running appliance HTTP plan/approval/apply
contract.  The resulting volume is retained so ``storage_recovery_lab.py``
can exercise degradation and rebuild on the same candidate.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import stat
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    from deploy.appliance import operations_systemd as systemd
    from deploy.appliance import operations_systemd_lab as operations_lab
    from deploy.appliance import storage_recovery_lab as recovery_lab
except ModuleNotFoundError:
    import operations_systemd as systemd
    import operations_systemd_lab as operations_lab
    import storage_recovery_lab as recovery_lab

SCHEMA_VERSION = 1
PHASES = ("provision", "reboot-verify")
PHASE_OUTPUTS = {
    "provision": "storage-provisioning.log",
    "reboot-verify": "storage-provisioning-reboot.log",
}
MD_DESIRED_SCHEMA = "echo.omv.mdraid1-desired.v1"
MD_PLAN_SCHEMA = "echo.omv.mdraid1-plan.v1"
EXT4_DESIRED_SCHEMA = "echo.omv.ext4-volume-desired.v1"
EXT4_PLAN_SCHEMA = "echo.omv.ext4-volume-plan.v1"
SAFE_WHOLE_DISK = re.compile(
    r"^/dev/(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme[0-9]+n[0-9]+|mmcblk[0-9]+)$"
)
PORTABLE_ARRAY_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,26}$")
PORTABLE_VOLUME_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,15}$")
MD_UUID = re.compile(r"^[0-9a-f]{8}(?::[0-9a-f]{8}){3}$")
FS_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MIN_DISK_BYTES = 4 * 1024**3
MAX_DISK_BYTES = 64 * 1024**3
PROBE_NAME = ".echo-storage-provisioning-probe"
PROBE_BYTES = 1024 * 1024


class StorageProvisioningLabError(RuntimeError):
    """The destructive provisioning lab cannot proceed safely."""


@dataclass(frozen=True)
class LabTools:
    mdadm: Path = Path("/usr/sbin/mdadm")
    blkid: Path = Path("/usr/sbin/blkid")
    findmnt: Path = Path("/usr/bin/findmnt")
    dpkg_query: Path = Path("/usr/bin/dpkg-query")


DEFAULT_TOOLS = LabTools()
CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]
HttpCaller = Callable[
    [str, str, str, Mapping[str, Any] | None, str | None, Mapping[str, str] | None],
    tuple[int, Mapping[str, Any]],
]
CandidateLoader = Callable[[Path, int], Mapping[str, str]]
BundleLoader = Callable[[Path, Mapping[str, str], int], Mapping[str, Any]]
PlatformProbe = Callable[[Path, LabTools, CommandRunner], Mapping[str, str]]
BootIdReader = Callable[[], str]
HostVerifier = Callable[
    [str, str, str, str, Sequence[str], LabTools, CommandRunner], Mapping[str, Any]
]
ProbeWriter = Callable[[Path, int], Mapping[str, Any]]
ProbeReader = Callable[[Path, Mapping[str, Any], int], Mapping[str, Any]]
ToolValidator = Callable[[LabTools, int], None]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,
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


def _boot_id() -> str:
    return recovery_lab._boot_id()


def _candidate_identity(path: Path, trusted_uid: int) -> Mapping[str, str]:
    return operations_lab._candidate_identity(path, trusted_uid=trusted_uid)


def _platform(path: Path, tools: LabTools, runner: CommandRunner) -> Mapping[str, str]:
    return {
        **operations_lab._read_os_release(path),
        "omvVersion": operations_lab._omv_version(
            operations_lab.LabTools(dpkg_query=tools.dpkg_query), runner
        ),
    }


def _validated_tools(tools: LabTools, trusted_uid: int) -> None:
    for tool in (tools.mdadm, tools.blkid, tools.findmnt, tools.dpkg_query):
        systemd._safe_regular(
            tool,
            f"storage provisioning tool {tool.name}",
            maximum=64 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )


def _bundle_identity(
    root: Path, candidate: Mapping[str, str], trusted_uid: int
) -> Mapping[str, Any]:
    raw = systemd._safe_regular(
        root / "bundle-manifest.json",
        "operations bundle manifest",
        maximum=systemd.MAX_PLAN_BYTES,
        trusted_uid=trusted_uid,
        private=False,
        exact_mode=0o644,
    )
    try:
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageProvisioningLabError("operations bundle manifest is not strict JSON") from exc
    artifact = manifest.get("artifact") if isinstance(manifest, dict) else None
    files = manifest.get("files") if isinstance(manifest, dict) else None
    expected = {
        "storage_provisioning_lab.py": (
            "storageProvisioningLab",
            "./storage_provisioning_lab.py plan|run",
        ),
        "storage_recovery_lab.py": (
            "storageRecoveryLab",
            "./storage_recovery_lab.py plan|run",
        ),
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "artifact", "files"}
        or manifest.get("schemaVersion") != 1
        or not isinstance(artifact, dict)
        or artifact.get("id") != candidate["operationsArtifactId"]
        or artifact.get("name") != root.name
        or artifact.get("imageReference") != candidate["immutableReference"]
        or not isinstance(artifact.get("entrypoints"), dict)
        or not isinstance(files, dict)
    ):
        raise StorageProvisioningLabError("provisioning lab bundle is not from the candidate")
    tool_hashes: dict[str, str] = {}
    for name, (entrypoint, command) in expected.items():
        tool = systemd._safe_regular(
            root / name,
            f"candidate bundle tool {name}",
            maximum=16 * 1024 * 1024,
            trusted_uid=trusted_uid,
            private=False,
            exact_mode=0o755,
        )
        record = files.get(name)
        if (
            record != {"sha256": _sha256(tool), "size": len(tool), "mode": "0755"}
            or artifact["entrypoints"].get(entrypoint) != command
        ):
            raise StorageProvisioningLabError("provisioning lab bundle tool bytes are unbound")
        tool_hashes[f"{entrypoint}Sha256"] = _sha256(tool)
    return {
        "artifactId": candidate["operationsArtifactId"],
        "archiveSha256": candidate["operationsArchiveSha256"],
        "imageReference": candidate["immutableReference"],
        "manifestSha256": _sha256(raw),
        **tool_hashes,
    }


def _loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise StorageProvisioningLabError("appliance URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        and ":" in parsed.netloc.rsplit("]", 1)[-1]
    ):
        raise StorageProvisioningLabError("appliance URL must be one loopback origin")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _http_call(
    base_url: str,
    method: str,
    path: str,
    payload: Mapping[str, Any] | None,
    token: str | None,
    headers: Mapping[str, str] | None,
) -> tuple[int, Mapping[str, Any]]:
    parsed = urlsplit(base_url)
    body = json.dumps(payload, separators=(",", ":")).encode() if payload is not None else None
    request_headers = {"Accept": "application/json"}
    if body is not None:
        request_headers["Content-Type"] = "application/json"
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    request_headers.update(headers or {})
    connection_type = (
        http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    )
    connection = connection_type(parsed.hostname, parsed.port, timeout=900)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read(1024 * 1024 + 1)
    finally:
        connection.close()
    if len(raw) > 1024 * 1024:
        raise StorageProvisioningLabError("appliance response exceeds the lab safety bound")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageProvisioningLabError("appliance returned a non-JSON response") from exc
    if not isinstance(value, dict):
        raise StorageProvisioningLabError("appliance returned a non-object response")
    return response.status, value


def _request(
    caller: HttpCaller,
    base_url: str,
    method: str,
    path: str,
    *,
    expected: int,
    payload: Mapping[str, Any] | None = None,
    token: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    status, value = caller(base_url, method, path, payload, token, headers)
    if status != expected:
        raise StorageProvisioningLabError(
            f"appliance {method} {path} returned HTTP {status}, expected {expected}"
        )
    return dict(value)


def _login(caller: HttpCaller, base_url: str, password: str) -> str:
    response = _request(
        caller,
        base_url,
        "POST",
        "/api/auth/local/login",
        expected=200,
        payload={"username": "admin", "password": password},
    )
    token = response.get("access_token")
    if response.get("success") is not True or not isinstance(token, str) or not token:
        raise StorageProvisioningLabError("appliance login returned no access token")
    return token


def _password(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise StorageProvisioningLabError(f"{name} must contain the appliance admin password")
    return value


def _validate_candidates(value: Any, selected: Sequence[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise StorageProvisioningLabError("md RAID1 candidate response is invalid")
    by_path: dict[str, dict[str, Any]] = {}
    for raw in value:
        if not isinstance(raw, dict) or set(raw) != {
            "devicefile",
            "sizeBytes",
            "serial",
            "wwn",
            "model",
        }:
            raise StorageProvisioningLabError("md RAID1 candidate identity is invalid")
        path = raw.get("devicefile")
        size = raw.get("sizeBytes")
        serial = raw.get("serial")
        wwn = raw.get("wwn")
        if (
            not isinstance(path, str)
            or SAFE_WHOLE_DISK.fullmatch(path) is None
            or isinstance(size, bool)
            or not isinstance(size, int)
            or not MIN_DISK_BYTES <= size <= MAX_DISK_BYTES
            or not ((isinstance(serial, str) and serial) or (isinstance(wwn, str) and wwn))
            or any(
                len(identity) > 512 or any(character < " " for character in identity)
                for identity in (serial, wwn)
                if isinstance(identity, str)
            )
            or path in by_path
        ):
            raise StorageProvisioningLabError("md RAID1 candidate identity is unsafe")
        by_path[path] = dict(raw)
    if (
        len(selected) != 2
        or len(set(selected)) != 2
        or any(path not in by_path for path in selected)
    ):
        raise StorageProvisioningLabError("both selected disks must remain blank API candidates")
    records = [by_path[path] for path in sorted(selected)]
    stable = [record.get("wwn") or record.get("serial") for record in records]
    if len(set(stable)) != 2:
        raise StorageProvisioningLabError(
            "selected disks do not have distinct persistent identities"
        )
    return records


def _bound_candidates(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bind stable disk identity without persisting serial or WWN in the plan."""
    bound = []
    for record in records:
        identity_type = "wwn" if record.get("wwn") else "serial"
        identity = str(record.get(identity_type))
        bound.append(
            {
                "devicefile": record["devicefile"],
                "sizeBytes": record["sizeBytes"],
                "identitySha256": _sha256(f"{identity_type}:{identity}".encode()),
            }
        )
    return sorted(bound, key=lambda item: str(item["devicefile"]))


def _md_desired(name: str, devices: Sequence[str]) -> dict[str, Any]:
    return {
        "schema": MD_DESIRED_SCHEMA,
        "name": name,
        "devices": sorted(devices),
        "dataLossConfirmed": True,
    }


def _validate_md_plan(plan: Mapping[str, Any], desired: Mapping[str, Any]) -> dict[str, Any]:
    target = f"/dev/md/echo-{desired['name']}"
    devices = plan.get("devices")
    if (
        plan.get("schema") != MD_PLAN_SCHEMA
        or plan.get("operation") != "create"
        or plan.get("desired") != desired
        or plan.get("target") != target
        or not isinstance(plan.get("planId"), str)
        or SHA256.fullmatch(str(plan["planId"])) is None
        or not isinstance(devices, list)
        or sorted(str(item.get("devicefile")) for item in devices if isinstance(item, dict))
        != desired["devices"]
        or plan.get("requiresApproval") is not True
        or not isinstance(plan.get("safety"), dict)
        or plan["safety"].get("destructive") is not True
        or plan["safety"].get("force") is not False
    ):
        raise StorageProvisioningLabError("appliance returned an invalid md RAID1 plan")
    return dict(plan)


def _validate_ext4_plan(
    plan: Mapping[str, Any], desired: Mapping[str, Any], *, target: str
) -> dict[str, Any]:
    array = plan.get("array")
    if (
        plan.get("schema") != EXT4_PLAN_SCHEMA
        or plan.get("operation") != "createAndMount"
        or plan.get("desired") != desired
        or plan.get("mountpoint") != f"/data/{desired['name']}"
        or not isinstance(plan.get("planId"), str)
        or SHA256.fullmatch(str(plan["planId"])) is None
        or not isinstance(array, dict)
        or array.get("devicefile") != target
        or array.get("uuid") != desired["arrayUuid"]
        or plan.get("requiresApproval") is not True
        or not isinstance(plan.get("safety"), dict)
        or plan["safety"].get("destructive") is not True
        or plan["safety"].get("force") is not False
    ):
        raise StorageProvisioningLabError("appliance returned an invalid EXT4 plan")
    return dict(plan)


def _approve(
    caller: HttpCaller,
    base_url: str,
    token: str,
    password: str,
    action: str,
    plan_id: str,
) -> str:
    response = _request(
        caller,
        base_url,
        "POST",
        "/api/appliance/approvals",
        expected=200,
        payload={"action": action, "target": plan_id, "password": password},
        token=token,
    )
    approval = response.get("approvalToken")
    if (
        not isinstance(approval, str)
        or not approval
        or response.get("action") != action
        or response.get("target") != plan_id
    ):
        raise StorageProvisioningLabError("appliance returned an invalid one-shot approval")
    return approval


def _command_output(command: list[str], runner: CommandRunner, label: str) -> str:
    completed = runner(command)
    if completed.returncode != 0 or len(completed.stdout.encode("utf-8")) > 1024 * 1024:
        raise StorageProvisioningLabError(f"{label} verification failed")
    return completed.stdout


def _export_fields(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not re.fullmatch(r"[A-Z0-9_]+", key) or key in fields or len(value) > 512:
            raise StorageProvisioningLabError("host identity output is invalid")
        fields[key] = value
    return fields


def _verify_host(
    target: str,
    array_uuid: str,
    mountpoint: str,
    filesystem_uuid: str,
    devices: Sequence[str],
    tools: LabTools,
    runner: CommandRunner,
) -> Mapping[str, Any]:
    test = runner([str(tools.mdadm), "--detail", "--test", target])
    if test.returncode != 0:
        raise StorageProvisioningLabError("provisioned RAID1 is not healthy")
    md = _export_fields(
        _command_output([str(tools.mdadm), "--detail", "--export", target], runner, "md RAID1")
    )
    actual_devices = sorted(value for key, value in md.items() if key.endswith("_DEV"))
    if (
        md.get("MD_LEVEL") != "raid1"
        or md.get("MD_DEVICES") != "2"
        or md.get("MD_UUID", "").lower() != array_uuid
        or actual_devices != sorted(devices)
    ):
        raise StorageProvisioningLabError("provisioned RAID1 identity drifted")
    filesystem = _export_fields(
        _command_output(
            [str(tools.blkid), "--probe", "--output", "export", target],
            runner,
            "EXT4 identity",
        )
    )
    if filesystem.get("TYPE") != "ext4" or filesystem.get("UUID", "").lower() != filesystem_uuid:
        raise StorageProvisioningLabError("provisioned EXT4 identity drifted")
    mount = _command_output(
        [
            str(tools.findmnt),
            "--noheadings",
            "--raw",
            "--output",
            "SOURCE,FSTYPE,OPTIONS,TARGET",
            "--mountpoint",
            mountpoint,
        ],
        runner,
        "EXT4 mount",
    ).strip()
    parts = mount.split(None, 3)
    if len(parts) != 4:
        raise StorageProvisioningLabError("provisioned mount identity is incomplete")
    source, fstype, options, actual_mountpoint = parts
    option_set = set(options.split(","))
    if (
        source not in {target, f"UUID={filesystem_uuid}"}
        or fstype != "ext4"
        or "rw" not in option_set
        or "ro" in option_set
        or actual_mountpoint != mountpoint
    ):
        raise StorageProvisioningLabError("provisioned EXT4 mount is not writable or persistent")
    return {
        "arrayUuid": array_uuid,
        "filesystemUuid": filesystem_uuid,
        "target": target,
        "mountpoint": mountpoint,
        "devices": sorted(devices),
        "healthy": True,
        "readOnly": False,
    }


def _write_probe(mountpoint: Path, trusted_uid: int) -> Mapping[str, Any]:
    if mountpoint.is_symlink() or not mountpoint.is_dir():
        raise StorageProvisioningLabError("provisioned mountpoint is unsafe")
    path = mountpoint / PROBE_NAME
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o400)
    digest = hashlib.sha256()
    pattern = hashlib.sha256(b"Echo OS storage provisioning lab v1").digest()
    remaining = PROBE_BYTES
    try:
        os.fchmod(descriptor, 0o400)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, trusted_uid, trusted_uid)
        while remaining:
            chunk = (pattern * ((min(remaining, 64 * 1024) + len(pattern) - 1) // len(pattern)))[
                : min(remaining, 64 * 1024)
            ]
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise StorageProvisioningLabError("storage probe write made no progress")
                digest.update(view[:written])
                remaining -= written
                view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(mountpoint, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"name": PROBE_NAME, "size": PROBE_BYTES, "sha256": digest.hexdigest()}


def _read_probe(
    mountpoint: Path, expected: Mapping[str, Any], trusted_uid: int
) -> Mapping[str, Any]:
    if (
        expected.get("name") != PROBE_NAME
        or expected.get("size") != PROBE_BYTES
        or not isinstance(expected.get("sha256"), str)
        or SHA256.fullmatch(str(expected["sha256"])) is None
    ):
        raise StorageProvisioningLabError("provisioning probe evidence is invalid")
    raw = systemd._safe_regular(
        mountpoint / PROBE_NAME,
        "storage provisioning persistence probe",
        maximum=PROBE_BYTES,
        trusted_uid=trusted_uid,
        private=True,
        exact_mode=0o400,
    )
    if len(raw) != PROBE_BYTES or _sha256(raw) != expected["sha256"]:
        raise StorageProvisioningLabError("storage provisioning probe did not survive reboot")
    return dict(expected)


def _read_plan(path: Path, trusted_uid: int) -> dict[str, Any]:
    return _read_json(path, "storage provisioning lab plan", trusted_uid, 0o400)


def _read_json(path: Path, label: str, trusted_uid: int, exact_mode: int) -> dict[str, Any]:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise StorageProvisioningLabError(f"{label} path is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StorageProvisioningLabError(f"{label} is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or not 1 <= before.st_size <= systemd.MAX_PLAN_BYTES
            or (
                os.name == "posix"
                and (before.st_uid != trusted_uid or stat.S_IMODE(before.st_mode) != exact_mode)
            )
        ):
            raise StorageProvisioningLabError(f"{label} ownership, mode, or size is unsafe")
        raw = os.read(descriptor, systemd.MAX_PLAN_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) != before.st_size or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise StorageProvisioningLabError(f"{label} changed while it was read")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=systemd._reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StorageProvisioningLabError(f"{label} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise StorageProvisioningLabError(f"{label} is not an object")
    return value


def _write_new(path: Path, value: Mapping[str, Any], trusted_uid: int, mode: int) -> None:
    if path.exists() or path.is_symlink() or path.parent.is_symlink():
        raise StorageProvisioningLabError("storage provisioning output must be a new file")
    parent = path.parent.resolve(strict=True)
    systemd._assert_owned_directory(
        parent, "storage provisioning output directory", trusted_uid=trusted_uid
    )
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_BINARY", 0)
    )
    descriptor = os.open(parent / path.name, flags, mode)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = -1
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def build_plan(
    *,
    candidate_index: Path,
    bundle_root: Path,
    devices: Sequence[str],
    array_name: str,
    volume_name: str,
    evidence_directory: Path,
    base_url: str,
    output: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    http_caller: HttpCaller = _http_call,
    candidate_loader: CandidateLoader = _candidate_identity,
    bundle_loader: BundleLoader = _bundle_identity,
    platform_probe: PlatformProbe = _platform,
    tool_validator: ToolValidator = _validated_tools,
    boot_id_reader: BootIdReader = _boot_id,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise StorageProvisioningLabError("storage provisioning plan requires Linux root")
    if (
        len(devices) != 2
        or len(set(devices)) != 2
        or any(SAFE_WHOLE_DISK.fullmatch(device) is None for device in devices)
        or PORTABLE_ARRAY_NAME.fullmatch(array_name) is None
        or PORTABLE_VOLUME_NAME.fullmatch(volume_name) is None
    ):
        raise StorageProvisioningLabError("two safe whole disks and portable names are required")
    origin = _loopback_origin(base_url)
    tool_validator(tools, trusted_uid)
    evidence_root = evidence_directory.resolve(strict=True)
    systemd._assert_owned_directory(
        evidence_root, "storage provisioning evidence directory", trusted_uid=trusted_uid
    )
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    password = _password(password_env)
    token = _login(http_caller, origin, password)
    response = _request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/arrays/mdraid1/candidates",
        expected=200,
        token=token,
    )
    identities = _validate_candidates(response.get("devices"), devices)
    desired = _md_desired(array_name, devices)
    md_plan = _validate_md_plan(
        _request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/arrays/mdraid1/plan",
            expected=200,
            payload=desired,
            token=token,
        ),
        desired,
    )
    material: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "echo.storage-provisioning-physical-lab-plan",
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "platform": platform,
        "appliance": {"baseUrl": origin},
        "selection": {
            "devices": _bound_candidates(identities),
            "mdDesired": desired,
            "mdPlanId": md_plan["planId"],
            "arrayTarget": md_plan["target"],
            "volumeName": volume_name,
            "mountpoint": f"/data/{volume_name}",
        },
        "evidenceDirectory": str(evidence_root),
        "baselineBootId": boot_id_reader(),
        "phases": list(PHASES),
        "retention": "retainForStorageRecoveryLab",
    }
    material["planId"] = _sha256(_canonical(material))
    material["confirmations"] = {
        phase: f"RUN ECHO STORAGE PROVISIONING LAB {phase} {material['planId']}" for phase in PHASES
    }
    _write_new(output, material, trusted_uid, 0o400)
    return material


def _validate_plan_shape(plan: Mapping[str, Any], phase: str, confirmation: str) -> None:
    selection = plan.get("selection")
    confirmations = plan.get("confirmations")
    unsigned = dict(plan)
    plan_id = unsigned.pop("planId", None)
    unsigned.pop("confirmations", None)
    if (
        phase not in PHASES
        or set(plan)
        != {
            "schemaVersion",
            "kind",
            "releaseCandidate",
            "operationsBundle",
            "platform",
            "appliance",
            "selection",
            "evidenceDirectory",
            "baselineBootId",
            "phases",
            "retention",
            "planId",
            "confirmations",
        }
        or plan.get("schemaVersion") != SCHEMA_VERSION
        or plan.get("kind") != "echo.storage-provisioning-physical-lab-plan"
        or plan.get("phases") != list(PHASES)
        or plan.get("retention") != "retainForStorageRecoveryLab"
        or not isinstance(plan_id, str)
        or plan_id != _sha256(_canonical(unsigned))
        or not isinstance(confirmations, dict)
        or confirmations.get(phase) != confirmation
        or not isinstance(selection, dict)
    ):
        raise StorageProvisioningLabError("storage provisioning plan or confirmation is invalid")


def _rebind_plan(
    plan: Mapping[str, Any],
    *,
    candidate_index: Path,
    bundle_root: Path,
    tools: LabTools,
    runner: CommandRunner,
    candidate_loader: CandidateLoader,
    bundle_loader: BundleLoader,
    platform_probe: PlatformProbe,
    trusted_uid: int,
    os_release: Path,
) -> None:
    candidate = dict(candidate_loader(candidate_index, trusted_uid))
    bundle = dict(bundle_loader(bundle_root.resolve(strict=True), candidate, trusted_uid))
    platform = dict(platform_probe(os_release, tools, runner))
    if (
        candidate != plan.get("releaseCandidate")
        or bundle != plan.get("operationsBundle")
        or platform != plan.get("platform")
    ):
        raise StorageProvisioningLabError("candidate, bundle, or platform changed after planning")


def _provision(
    plan: Mapping[str, Any],
    *,
    password: str,
    http_caller: HttpCaller,
    tools: LabTools,
    runner: CommandRunner,
    host_verifier: HostVerifier,
    probe_writer: ProbeWriter,
    trusted_uid: int,
) -> dict[str, Any]:
    origin = str(plan["appliance"]["baseUrl"])
    selection = dict(plan["selection"])
    desired = dict(selection["mdDesired"])
    device_paths = list(desired["devices"])
    token = _login(http_caller, origin, password)
    candidates = _request(
        http_caller,
        origin,
        "GET",
        "/api/appliance/omv/arrays/mdraid1/candidates",
        expected=200,
        token=token,
    )
    if (
        _bound_candidates(_validate_candidates(candidates.get("devices"), device_paths))
        != selection["devices"]
    ):
        raise StorageProvisioningLabError("selected disk identities changed after planning")
    current_md_plan = _validate_md_plan(
        _request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/arrays/mdraid1/plan",
            expected=200,
            payload=desired,
            token=token,
        ),
        desired,
    )
    if current_md_plan["planId"] != selection["mdPlanId"]:
        raise StorageProvisioningLabError("md RAID1 plan became stale before approval")
    mutation_started = False
    try:
        approval = _approve(
            http_caller,
            origin,
            token,
            password,
            "omv.mdraid1.create",
            current_md_plan["planId"],
        )
        mutation_started = True
        md_result = _request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/arrays/mdraid1/apply",
            expected=200,
            payload={"desired": desired, "planId": current_md_plan["planId"]},
            token=token,
            headers={"X-Echo-Approval": approval},
        )
        array = md_result.get("array")
        if (
            md_result.get("applied") is not True
            or md_result.get("verified") is not True
            or not isinstance(array, dict)
            or array.get("devicefile") != selection["arrayTarget"]
            or not isinstance(array.get("uuid"), str)
            or MD_UUID.fullmatch(str(array["uuid"])) is None
            or sorted(array.get("devices", [])) != device_paths
        ):
            raise StorageProvisioningLabError("md RAID1 apply result is invalid")
        ext4_candidates = _request(
            http_caller,
            origin,
            "GET",
            "/api/appliance/omv/volumes/ext4/candidates",
            expected=200,
            token=token,
        )
        matches = [
            item
            for item in ext4_candidates.get("arrays", [])
            if isinstance(item, dict) and item.get("uuid") == array["uuid"]
        ]
        if len(matches) != 1 or matches[0].get("devicefile") != selection["arrayTarget"]:
            raise StorageProvisioningLabError("new RAID1 is not the exact blank EXT4 candidate")
        ext4_desired = {
            "schema": EXT4_DESIRED_SCHEMA,
            "arrayUuid": array["uuid"],
            "name": selection["volumeName"],
            "dataLossConfirmed": True,
        }
        ext4_plan = _validate_ext4_plan(
            _request(
                http_caller,
                origin,
                "POST",
                "/api/appliance/omv/volumes/ext4/plan",
                expected=200,
                payload=ext4_desired,
                token=token,
            ),
            ext4_desired,
            target=selection["arrayTarget"],
        )
        ext4_approval = _approve(
            http_caller,
            origin,
            token,
            password,
            "omv.ext4-volume.create",
            ext4_plan["planId"],
        )
        ext4_result = _request(
            http_caller,
            origin,
            "POST",
            "/api/appliance/omv/volumes/ext4/apply",
            expected=200,
            payload={"desired": ext4_desired, "planId": ext4_plan["planId"]},
            token=token,
            headers={"X-Echo-Approval": ext4_approval},
        )
        filesystem = ext4_result.get("filesystem")
        if (
            ext4_result.get("applied") is not True
            or ext4_result.get("verified") is not True
            or not isinstance(filesystem, dict)
            or filesystem.get("type") != "ext4"
            or filesystem.get("devicefile") != selection["arrayTarget"]
            or filesystem.get("mountpoint") != selection["mountpoint"]
            or not isinstance(filesystem.get("uuid"), str)
            or FS_UUID.fullmatch(str(filesystem["uuid"])) is None
        ):
            raise StorageProvisioningLabError("EXT4 apply result is invalid")
        host = dict(
            host_verifier(
                selection["arrayTarget"],
                array["uuid"],
                selection["mountpoint"],
                filesystem["uuid"],
                device_paths,
                tools,
                runner,
            )
        )
        probe = dict(probe_writer(Path(selection["mountpoint"]), trusted_uid))
    except Exception as exc:
        if mutation_started:
            raise StorageProvisioningLabError(
                "provisioning failed after mutation began; preserve the host for manual inspection"
            ) from exc
        raise
    return {
        "mdPlanId": current_md_plan["planId"],
        "ext4PlanId": ext4_plan["planId"],
        "array": dict(array),
        "filesystem": dict(filesystem),
        "host": host,
        "probe": probe,
        "retainedForRecoveryLab": True,
    }


def _prior_provision(root: Path, plan_id: str, trusted_uid: int) -> dict[str, Any]:
    value = _read_json(
        root / PHASE_OUTPUTS["provision"], "storage provisioning evidence", trusted_uid, 0o444
    )
    if (
        set(value) != {"schemaVersion", "kind", "planId", "phase", "passed", "details"}
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != "echo.storage-provisioning-physical-lab-evidence"
        or value.get("planId") != plan_id
        or value.get("phase") != "provision"
        or value.get("passed") is not True
        or not isinstance(value.get("details"), dict)
    ):
        raise StorageProvisioningLabError("prior provisioning evidence is invalid")
    return dict(value["details"])


def run_phase(
    *,
    plan_path: Path,
    phase: str,
    confirmation: str,
    candidate_index: Path,
    bundle_root: Path,
    password_env: str = "ECHO_ADMIN_PASSWORD",
    tools: LabTools = DEFAULT_TOOLS,
    runner: CommandRunner = _run,
    http_caller: HttpCaller = _http_call,
    candidate_loader: CandidateLoader = _candidate_identity,
    bundle_loader: BundleLoader = _bundle_identity,
    platform_probe: PlatformProbe = _platform,
    tool_validator: ToolValidator = _validated_tools,
    boot_id_reader: BootIdReader = _boot_id,
    host_verifier: HostVerifier = _verify_host,
    probe_writer: ProbeWriter = _write_probe,
    probe_reader: ProbeReader = _read_probe,
    effective_uid: int | None = None,
    trusted_uid: int = 0,
    system_name: str | None = None,
    os_release: Path = Path("/etc/os-release"),
) -> dict[str, Any]:
    uid = os.geteuid() if effective_uid is None else effective_uid
    host_system = os.uname().sysname if system_name is None else system_name
    if uid != 0 or host_system != "Linux":
        raise StorageProvisioningLabError("storage provisioning phase requires Linux root")
    plan = _read_plan(plan_path, trusted_uid)
    _validate_plan_shape(plan, phase, confirmation)
    tool_validator(tools, trusted_uid)
    _rebind_plan(
        plan,
        candidate_index=candidate_index,
        bundle_root=bundle_root,
        tools=tools,
        runner=runner,
        candidate_loader=candidate_loader,
        bundle_loader=bundle_loader,
        platform_probe=platform_probe,
        trusted_uid=trusted_uid,
        os_release=os_release,
    )
    root = Path(str(plan["evidenceDirectory"])).resolve(strict=True)
    systemd._assert_owned_directory(
        root, "storage provisioning evidence directory", trusted_uid=trusted_uid
    )
    output = root / PHASE_OUTPUTS[phase]
    if output.exists() or output.is_symlink():
        raise StorageProvisioningLabError("storage provisioning phase evidence already exists")
    current_boot = boot_id_reader()
    if phase == "provision":
        if current_boot != plan["baselineBootId"]:
            raise StorageProvisioningLabError("host rebooted after provisioning was planned")
        if (root / PHASE_OUTPUTS["reboot-verify"]).exists():
            raise StorageProvisioningLabError("provisioning evidence sequence is stale")
        details = _provision(
            plan,
            password=_password(password_env),
            http_caller=http_caller,
            tools=tools,
            runner=runner,
            host_verifier=host_verifier,
            probe_writer=probe_writer,
            trusted_uid=trusted_uid,
        )
    else:
        prior = _prior_provision(root, str(plan["planId"]), trusted_uid)
        if current_boot == plan["baselineBootId"]:
            raise StorageProvisioningLabError("reboot verification requires a new kernel boot")
        array = prior.get("array")
        filesystem = prior.get("filesystem")
        if not isinstance(array, dict) or not isinstance(filesystem, dict):
            raise StorageProvisioningLabError("provisioning evidence lacks storage identities")
        selection = dict(plan["selection"])
        host = dict(
            host_verifier(
                selection["arrayTarget"],
                str(array.get("uuid")),
                selection["mountpoint"],
                str(filesystem.get("uuid")),
                list(selection["mdDesired"]["devices"]),
                tools,
                runner,
            )
        )
        probe = dict(
            probe_reader(Path(selection["mountpoint"]), dict(prior.get("probe") or {}), trusted_uid)
        )
        details = {
            "previousBootId": plan["baselineBootId"],
            "currentBootId": current_boot,
            "host": host,
            "probe": probe,
            "assembledAfterReboot": True,
            "mountedAfterReboot": True,
            "dataPreserved": True,
            "retainedForRecoveryLab": True,
        }
    _write_new(
        output,
        {
            "schemaVersion": SCHEMA_VERSION,
            "kind": "echo.storage-provisioning-physical-lab-evidence",
            "planId": plan["planId"],
            "phase": phase,
            "passed": True,
            "details": details,
        },
        trusted_uid,
        0o444,
    )
    return {"phase": phase, "planId": plan["planId"], "output": output.name}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--candidate-index", type=Path, required=True)
    plan.add_argument("--bundle-root", type=Path, required=True)
    plan.add_argument("--device", type=str, action="append", required=True)
    plan.add_argument("--array-name", required=True)
    plan.add_argument("--volume-name", required=True)
    plan.add_argument("--evidence-directory", type=Path, required=True)
    plan.add_argument("--base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--output", type=Path, required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--phase", choices=PHASES, required=True)
    run.add_argument("--confirm", required=True)
    run.add_argument("--candidate-index", type=Path, required=True)
    run.add_argument("--bundle-root", type=Path, required=True)
    run.add_argument("--password-env", default="ECHO_ADMIN_PASSWORD")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            report = build_plan(
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                devices=args.device,
                array_name=args.array_name,
                volume_name=args.volume_name,
                evidence_directory=args.evidence_directory,
                base_url=args.base_url,
                output=args.output,
                password_env=args.password_env,
            )
            print(f"plan={report['planId']} phases={len(report['phases'])}")
            for phase in PHASES:
                print(f"{phase}: {report['confirmations'][phase]}")
            return 0
        report = run_phase(
            plan_path=args.plan,
            phase=args.phase,
            confirmation=args.confirm,
            candidate_index=args.candidate_index,
            bundle_root=args.bundle_root,
            password_env=args.password_env,
        )
    except (OSError, ValueError, StorageProvisioningLabError) as exc:
        print(f"storage provisioning lab failed: {exc}", file=sys.stderr)
        return 1
    print(f"phase={report['phase']} plan={report['planId']} output={report['output']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
