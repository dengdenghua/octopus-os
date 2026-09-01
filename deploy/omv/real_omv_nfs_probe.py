#!/usr/bin/env python3
"""Exercise Echo's NFS control against one disposable real OMV 8 host.

This probe is intentionally restricted to the privileged GitHub Actions
systemd container used by ``verify-real-omv-x86-ci.sh``. It creates one sparse
file under /tmp, exposes it through a loop device, and asks OMV itself to create
and mount the ext4 filesystem. No host storage is mounted into the container.
"""

from __future__ import annotations

import argparse
import errno
import grp
import hashlib
import http.client
import ipaddress
import json
import os
import platform
import pwd
import re
import socket
import stat
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NoReturn

STATE_SCHEMA = "echo.real-omv-nfs-state.v2"
DESIRED_SCHEMA = "echo.omv.nfs-share-desired.v1"
PRIVILEGE_DESIRED_SCHEMA = "echo.omv.share-privilege-desired.v1"
SHARE_NAME = "echo-ci-nfs"
EXPORT_PATH = f"/export/{SHARE_NAME}"
REMOTE_PATH = f"/{SHARE_NAME}"
COMMENT_RW = "Echo real OMV x86 NFS read-write probe"
COMMENT_RO = "Echo real OMV x86 NFS read-only probe"
# This is a fixed, disposable CI-container namespace. Every reserved path below is
# created with O_EXCL and rejected when it is a symlink; host storage is never mounted.
TEMP_ROOT = Path("/tmp")  # nosec B108
IMAGE_PATH = TEMP_ROOT / "echo-omv-ci-volume.img"
STATE_PATH = TEMP_ROOT / "echo-real-omv-nfs-state.json"
PURGE_RESULT_PATH = TEMP_ROOT / "echo-real-omv-nfs-purge.json"
REINSTALL_RESULT_PATH = TEMP_ROOT / "echo-real-omv-nfs-reinstall.json"
SOCKET_PATH = Path("/run/echo-omv/omv.sock")
RW_MOUNT = Path("/mnt/echo-nfs-rw")
RO_MOUNT = Path("/mnt/echo-nfs-ro")
PAYLOAD_NAME = "echo-nfs-preserved.txt"
PAYLOAD_BYTES = b"Echo real OMV NFS data must survive plugin purge and reinstall.\n"
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
PLAN_PATTERN = re.compile(r"[0-9a-f]{64}")
LOOP_PATTERN = re.compile(r"/dev/loop[0-9]+")


class ProbeError(RuntimeError):
    """The disposable real-OMV NFS probe failed."""


def fail(message: str) -> NoReturn:
    raise ProbeError(message)


def require_environment() -> None:
    if os.environ.get("ECHO_REAL_OMV_CI") != "1" or os.environ.get("GITHUB_ACTIONS") != "true":
        fail("the NFS probe is restricted to GitHub Actions")
    if os.geteuid() != 0:
        fail("the NFS probe must run as root")
    if platform.machine() != "x86_64":
        fail("the NFS probe requires the native x86_64 runner")
    container = Path("/run/systemd/container")
    if not container.is_file() or container.read_text(encoding="utf-8").strip() != "docker":
        fail("the NFS probe must run inside the disposable systemd Docker container")


def run(argv: list[str], *, input_text: str | None = None) -> str:
    try:
        completed = subprocess.run(
            argv,
            input=input_text,
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = ""
        if isinstance(exc, subprocess.CalledProcessError):
            detail = (exc.stderr or exc.stdout or "").strip()[-2048:]
        raise ProbeError(f"command failed: {argv[0]}: {detail}") from exc
    return completed.stdout.strip()


def rpc(service: str, method: str, params: dict[str, Any]) -> Any:
    output = run(
        [
            "/usr/sbin/omv-rpc",
            "-u",
            "admin",
            service,
            method,
            json.dumps(params, separators=(",", ":"), sort_keys=True),
        ]
    )
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"OMV RPC {service}.{method} returned invalid JSON") from exc


def apply_module(module: str) -> None:
    result = rpc("Config", "applyChanges", {"modules": [module], "force": False})
    if not isinstance(result, list) or module not in result:
        fail(f"OMV did not deploy the dirty {module} module")


def wait_for_background(filename: Any) -> None:
    if (
        not isinstance(filename, str)
        or not filename.startswith(f"{TEMP_ROOT}/")
        or len(filename) > 255
    ):
        fail("OMV filesystem creation returned an invalid background status file")
    for _attempt in range(120):
        result = rpc("Exec", "getOutput", {"filename": filename, "pos": 0, "length": 65536})
        if not isinstance(result, dict) or not isinstance(result.get("running"), bool):
            fail("OMV background process status is invalid")
        if result["running"] is False:
            return
        time.sleep(1)
    fail("OMV filesystem creation did not finish within 120 seconds")


def require_uuid(value: Any, label: str) -> str:
    if not isinstance(value, str) or UUID_PATTERN.fullmatch(value.lower()) is None:
        fail(f"{label} is not an OMV UUID")
    return value.lower()


def strict_write_json(path: Path, payload: dict[str, Any]) -> None:
    if path not in {STATE_PATH, PURGE_RESULT_PATH, REINSTALL_RESULT_PATH}:
        fail("the probe output path is not reserved")
    if path.exists() or path.is_symlink():
        fail(f"reserved probe output already exists: {path}")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        written = 0
        while written < len(data):
            count = os.write(descriptor, data[written:])
            if count <= 0:
                fail(f"could not finish writing probe output: {path}")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def strict_read_state(path: Path) -> dict[str, Any]:
    if path != STATE_PATH or path.is_symlink():
        fail("the NFS state path must be the reserved non-symlink file")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 1 <= info.st_size <= 32 * 1024:
            fail("the NFS state file is empty, oversized, or not regular")
        raw = os.read(descriptor, 32 * 1024 + 1)
    finally:
        os.close(descriptor)
    try:
        state = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("the NFS state file is not valid JSON") from exc
    expected = {
        "schema",
        "clientCidr",
        "serverIp",
        "filesystemUuid",
        "mountPointUuid",
        "mountPoint",
        "sharedFolderUuid",
        "sharedFolderPlanId",
        "privilegePlanId",
        "shareUuid",
        "planId",
        "exportPath",
        "remotePath",
        "payloadSha256",
    }
    if not isinstance(state, dict) or set(state) != expected or state.get("schema") != STATE_SCHEMA:
        fail("the NFS state file has an unexpected schema")
    for key in ("filesystemUuid", "mountPointUuid", "sharedFolderUuid", "shareUuid"):
        require_uuid(state.get(key), key)
    for key in ("sharedFolderPlanId", "privilegePlanId", "planId"):
        if not isinstance(state.get(key), str) or PLAN_PATTERN.fullmatch(state[key]) is None:
            fail(f"the NFS state {key} is invalid")
    if (
        not isinstance(state.get("payloadSha256"), str)
        or SHA256_PATTERN.fullmatch(state["payloadSha256"]) is None
    ):
        fail("the NFS state payload digest is invalid")
    if state.get("exportPath") != EXPORT_PATH or state.get("remotePath") != REMOTE_PATH:
        fail("the NFS state export path is invalid")
    network = ipaddress.ip_network(str(state.get("clientCidr")), strict=True)
    private_networks = (
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
    )
    if network.version != 4 or not any(network.subnet_of(item) for item in private_networks):
        fail("the NFS state client network is not private IPv4")
    address = ipaddress.ip_address(str(state.get("serverIp")))
    if address.version != 4 or address not in network or not address.is_private:
        fail("the NFS state server address is outside its private client network")
    mount_point = Path(str(state.get("mountPoint")))
    if not mount_point.is_absolute() or not str(mount_point).startswith("/srv/dev-disk-by-uuid-"):
        fail("the OMV mount point is outside the expected managed path")
    return state


def verify_users_group_privilege(shared_folder_uuid: str) -> None:
    privileges = rpc("ShareMgmt", "getPrivileges", {"uuid": shared_folder_uuid})
    if not isinstance(privileges, list) or len(privileges) > 2048:
        fail("OMV shared folder privileges are not a bounded list")
    matches = [
        item
        for item in privileges
        if isinstance(item, dict) and item.get("type") == "group" and item.get("name") == "users"
    ]
    if len(matches) != 1 or matches[0].get("perms") != 7:
        fail("the OMV users group does not retain read-write shared-folder privilege")


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: Path) -> None:
        super().__init__("localhost", timeout=30)
        self.socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(str(self.socket_path))
        self.sock = sock


def bridge_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    if not SOCKET_PATH.exists() or not stat.S_ISSOCK(SOCKET_PATH.stat().st_mode):
        fail("the Echo OMV bridge socket is not active")
    body = None if payload is None else json.dumps(payload, separators=(",", ":"), sort_keys=True)
    headers = {} if body is None else {"Content-Type": "application/json"}
    connection = UnixHTTPConnection(SOCKET_PATH)
    try:
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except (OSError, http.client.HTTPException) as exc:
            raise ProbeError(f"Echo bridge {path} could not be reached safely") from exc
    finally:
        connection.close()
    if response.status != 200:
        fail(f"Echo bridge {path} failed with HTTP {response.status}: {raw[:512]!r}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Echo bridge {path} returned invalid JSON") from exc


def desired(shared_folder_uuid: str, *, read_only: bool) -> dict[str, Any]:
    return {
        "schema": DESIRED_SCHEMA,
        "sharedFolderRef": shared_folder_uuid,
        "clientCidr": _network_identity()[1],
        "readOnly": read_only,
        "comment": COMMENT_RO if read_only else COMMENT_RW,
    }


def _network_identity() -> tuple[str, str]:
    output = run(["/usr/bin/ip", "-4", "-o", "addr", "show", "scope", "global"])
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 4 or fields[1].rstrip(":") == "lo":
            continue
        try:
            interface = ipaddress.ip_interface(fields[3])
        except ValueError:
            continue
        private_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        if interface.version == 4 and any(interface.ip in item for item in private_networks):
            return str(interface.ip), interface.network.with_prefixlen
    fail("the disposable OMV host has no private global IPv4 network")


def verify_share(state: dict[str, Any], *, read_only: bool) -> None:
    share = rpc("NFS", "getShare", {"uuid": state["shareUuid"]})
    if not isinstance(share, dict):
        fail("OMV NFS share readback is not an object")
    expected = {
        "uuid": state["shareUuid"],
        "sharedfolderref": state["sharedFolderUuid"],
        "client": state["clientCidr"],
        "options": "ro" if read_only else "rw",
        "extraoptions": "sync,subtree_check,root_squash",
        "comment": COMMENT_RO if read_only else COMMENT_RW,
    }
    for key, value in expected.items():
        if share.get(key) != value:
            fail(f"OMV NFS share readback differs at {key}")


def verify_exports(state: dict[str, Any], *, read_only: bool) -> None:
    exports_path = Path("/etc/exports")
    if exports_path.is_symlink() or not exports_path.is_file():
        fail("OMV did not generate a regular /etc/exports file")
    access = "ro" if read_only else "rw"
    expected = (
        f"{EXPORT_PATH} {state['clientCidr']}"
        f"(fsid={state['shareUuid']},{access},sync,subtree_check,root_squash)"
    )
    lines = {line.strip() for line in exports_path.read_text(encoding="utf-8").splitlines()}
    if expected not in lines:
        fail("OMV /etc/exports does not contain the exact managed NFS rule")
    exported = run(["/usr/sbin/exportfs", "-v"])
    live_match = re.search(
        rf"{re.escape(EXPORT_PATH)}\s+{re.escape(state['clientCidr'])}\(([^)]*)\)",
        exported,
    )
    if live_match is None:
        fail("the live kernel export table is missing the managed NFS rule")
    live_options = set(live_match.group(1).split(","))
    for required in (access, "sync", "root_squash"):
        if required not in live_options:
            fail(f"the live kernel export table is missing {required}")
    opposite_access = "rw" if read_only else "ro"
    if opposite_access in live_options:
        fail("the live kernel export table contains contradictory access modes")
    run(["/usr/bin/systemctl", "is-active", "--quiet", "nfs-server.service"])
    listeners = run(["/usr/bin/ss", "-H", "-lnt", "sport", "=", ":2049"])
    if ":2049" not in listeners:
        fail("the real NFS server is not listening on TCP 2049")


def mount_share(state: dict[str, Any], target: Path) -> None:
    if target not in {RW_MOUNT, RO_MOUNT} or target.is_symlink():
        fail("the NFS client mount point is not one reserved non-symlink path")
    target.mkdir(mode=0o755, parents=True, exist_ok=True)
    if subprocess.run(["/usr/bin/mountpoint", "-q", str(target)], check=False).returncode == 0:
        fail("the reserved NFS client mount point is already mounted")
    source = f"{state['serverIp']}:{REMOTE_PATH}"
    run(
        [
            "/usr/bin/mount",
            "-t",
            "nfs4",
            "-o",
            "vers=4.2,proto=tcp,soft,timeo=50,retrans=2",
            source,
            str(target),
        ]
    )
    if subprocess.run(["/usr/bin/mountpoint", "-q", str(target)], check=False).returncode != 0:
        fail("the real NFSv4 client mount is not active")


def unmount_share(target: Path) -> None:
    if subprocess.run(["/usr/bin/mountpoint", "-q", str(target)], check=False).returncode == 0:
        run(["/usr/bin/umount", str(target)])


@contextmanager
def users_group_identity() -> Iterator[None]:
    """Exercise root-squashed NFS as a non-root member of OMV's users group."""
    original_gid = os.getegid()
    original_groups = os.getgroups()
    try:
        nobody_uid = pwd.getpwnam("nobody").pw_uid
        users_gid = grp.getgrnam("users").gr_gid
    except KeyError as exc:
        raise ProbeError("the real OMV host is missing nobody or users") from exc
    try:
        os.setgroups([users_gid])
        os.setegid(users_gid)
        os.seteuid(nobody_uid)
        yield
    finally:
        os.seteuid(0)
        os.setegid(original_gid)
        os.setgroups(original_groups)


def verify_payload_through_read_only_mount(state: dict[str, Any], target: Path) -> None:
    mount_share(state, target)
    try:
        payload = target / PAYLOAD_NAME
        digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        if digest != state["payloadSha256"]:
            fail("the preserved NFS payload digest changed")
        try:
            with users_group_identity(), payload.open("ab") as stream:
                stream.write(b"must not be writable\n")
        except OSError as exc:
            if exc.errno not in {errno.EROFS, errno.EACCES, errno.EPERM}:
                raise
        else:
            fail("the read-only NFS remount unexpectedly allowed a write")
    finally:
        unmount_share(target)


def create_fixture() -> None:
    for reserved in (IMAGE_PATH, STATE_PATH, PURGE_RESULT_PATH, REINSTALL_RESULT_PATH):
        if reserved.exists() or reserved.is_symlink():
            fail(f"reserved fixture path already exists: {reserved}")
    descriptor = os.open(IMAGE_PATH, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        os.ftruncate(descriptor, 1024 * 1024 * 1024)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    loop_device = run(["/usr/sbin/losetup", "--find", "--show", str(IMAGE_PATH)])
    if LOOP_PATTERN.fullmatch(loop_device) is None:
        fail("the disposable fixture did not receive one loop device")

    background = rpc("FileSystemMgmt", "create", {"devicefile": loop_device, "type": "ext4"})
    wait_for_background(background)
    filesystem_uuid = ""
    for _attempt in range(30):
        completed = subprocess.run(
            ["/usr/sbin/blkid", "-s", "UUID", "-o", "value", loop_device],
            check=False,
            capture_output=True,
            text=True,
        )
        filesystem_uuid = completed.stdout.strip() if completed.returncode == 0 else ""
        if UUID_PATTERN.fullmatch(filesystem_uuid.lower()):
            break
        time.sleep(1)
    filesystem_uuid = require_uuid(filesystem_uuid, "filesystem UUID")

    mount_point = rpc(
        "FileSystemMgmt",
        "setMountPoint",
        {"id": filesystem_uuid, "usagewarnthreshold": 85, "comment": "Echo real NFS CI volume"},
    )
    if not isinstance(mount_point, dict):
        fail("OMV setMountPoint did not return a mount configuration")
    mount_point_uuid = require_uuid(mount_point.get("uuid"), "mount point UUID")
    mount_path = str(mount_point.get("dir", ""))
    if not mount_path.startswith("/srv/dev-disk-by-uuid-"):
        fail("OMV created an unexpected mount path")
    apply_module("fstab")
    if subprocess.run(["/usr/bin/mountpoint", "-q", mount_path], check=False).returncode != 0:
        fail("OMV did not mount the disposable ext4 filesystem")

    folder_desired = {
        "schema": "echo.omv.shared-folder-desired.v1",
        "mountPointRef": mount_point_uuid,
        "name": SHARE_NAME,
        "comment": "Echo real OMV NFS CI shared folder",
    }
    folder_plan = bridge_request(
        "POST",
        "/v1/sharing/folders/plan",
        {"desired": folder_desired},
    )
    if (
        not isinstance(folder_plan, dict)
        or folder_plan.get("operation") != "create"
        or folder_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(folder_plan.get("planId", ""))) is None
        or folder_plan.get("safety")
        != {
            "filesystem": "existingMountedWritableOnly",
            "relativePath": "derivedFromPortableName",
            "directoryMode": "2770UsersGroup",
            "acl": "notManaged",
            "update": "notManaged",
            "delete": "notManaged",
        }
    ):
        fail("Echo bridge did not produce a safe approval-gated shared folder create plan")
    folder_applied = bridge_request(
        "POST",
        "/v1/sharing/folders/apply",
        {"desired": folder_desired, "planId": folder_plan["planId"]},
    )
    if (
        not isinstance(folder_applied, dict)
        or folder_applied.get("applied") is not True
        or folder_applied.get("verified") is not True
    ):
        fail("Echo bridge did not verify the real shared folder create")
    shared_folder_uuid = require_uuid(folder_applied.get("shareUuid"), "shared folder UUID")
    shared_directory = Path(mount_path) / SHARE_NAME
    try:
        directory_info = shared_directory.stat(follow_symlinks=False)
        users_gid = grp.getgrnam("users").gr_gid
    except (OSError, KeyError) as exc:
        raise ProbeError("the real shared folder directory cannot be inspected") from exc
    if (
        not stat.S_ISDIR(directory_info.st_mode)
        or stat.S_IMODE(directory_info.st_mode) != 0o2770
        or directory_info.st_gid != users_gid
    ):
        fail("the real shared folder is not one 2770 directory owned by the users group")

    privilege_desired = {
        "schema": PRIVILEGE_DESIRED_SCHEMA,
        "sharedFolderRef": shared_folder_uuid,
        "principalType": "group",
        "principalName": "users",
        "permission": "readWrite",
    }
    privilege_plan = bridge_request(
        "POST",
        "/v1/sharing/privileges/plan",
        {"desired": privilege_desired},
    )
    if (
        not isinstance(privilege_plan, dict)
        or privilege_plan.get("operation") != "update"
        or privilege_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(privilege_plan.get("planId", ""))) is None
        or privilege_plan.get("principal")
        != {
            "type": "group",
            "id": users_gid,
            "name": "users",
            "before": "inherit",
            "after": "readWrite",
        }
        or privilege_plan.get("safety")
        != {
            "scope": "sharedFolderConfigPrivilege",
            "principal": "existingOmvUserOrGroup",
            "filesystemAcl": "notModified",
            "recursive": "never",
            "serviceDeploy": "sambaAndRsyncdWhenDirty",
            "delete": "notManaged",
        }
    ):
        fail("Echo bridge did not produce a safe approval-gated users-group privilege plan")
    privilege_applied = bridge_request(
        "POST",
        "/v1/sharing/privileges/apply",
        {"desired": privilege_desired, "planId": privilege_plan["planId"]},
    )
    if (
        not isinstance(privilege_applied, dict)
        or privilege_applied.get("applied") is not True
        or privilege_applied.get("verified") is not True
        or not isinstance(privilege_applied.get("deployedServices"), list)
    ):
        fail("Echo bridge did not verify the real users-group shared-folder privilege")
    verify_users_group_privilege(shared_folder_uuid)

    rpc("NFS", "setSettings", {"enable": True, "versions": ["4", "4.1", "4.2"]})
    apply_module("nfs")
    server_ip, client_cidr = _network_identity()
    wanted_rw = desired(shared_folder_uuid, read_only=False)
    if wanted_rw["clientCidr"] != client_cidr:
        fail("private client network changed during NFS setup")
    plan = bridge_request("POST", "/v1/sharing/nfs/plan", {"desired": wanted_rw})
    if (
        not isinstance(plan, dict)
        or plan.get("operation") != "create"
        or plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(plan.get("planId", ""))) is None
    ):
        fail("Echo bridge did not produce an approval-gated NFS create plan")
    if plan.get("safety") != {
        "clientScope": "privateCidrOnly",
        "rootSquash": "required",
        "syncWrites": "required",
        "advancedOptions": "notManaged",
        "delete": "notManaged",
    }:
        fail("Echo bridge NFS plan safety contract is incomplete")
    applied = bridge_request(
        "POST",
        "/v1/sharing/nfs/apply",
        {"desired": wanted_rw, "planId": plan["planId"]},
    )
    if (
        not isinstance(applied, dict)
        or applied.get("applied") is not True
        or applied.get("verified") is not True
    ):
        fail("Echo bridge did not verify the real NFS create")
    share_uuid = require_uuid(applied.get("shareUuid"), "NFS share UUID")
    state = {
        "schema": STATE_SCHEMA,
        "clientCidr": client_cidr,
        "serverIp": server_ip,
        "filesystemUuid": filesystem_uuid,
        "mountPointUuid": mount_point_uuid,
        "mountPoint": mount_path,
        "sharedFolderUuid": shared_folder_uuid,
        "sharedFolderPlanId": folder_plan["planId"],
        "privilegePlanId": privilege_plan["planId"],
        "shareUuid": share_uuid,
        "planId": plan["planId"],
        "exportPath": EXPORT_PATH,
        "remotePath": REMOTE_PATH,
        "payloadSha256": hashlib.sha256(PAYLOAD_BYTES).hexdigest(),
    }
    verify_share(state, read_only=False)
    verify_exports(state, read_only=False)
    mount_share(state, RW_MOUNT)
    try:
        payload_path = RW_MOUNT / PAYLOAD_NAME
        with users_group_identity():
            payload_path.write_bytes(PAYLOAD_BYTES)
        if hashlib.sha256(payload_path.read_bytes()).hexdigest() != state["payloadSha256"]:
            fail("the NFS read-write mount changed the payload")
    finally:
        unmount_share(RW_MOUNT)

    wanted_ro = desired(shared_folder_uuid, read_only=True)
    update_plan = bridge_request("POST", "/v1/sharing/nfs/plan", {"desired": wanted_ro})
    if (
        not isinstance(update_plan, dict)
        or update_plan.get("operation") != "update"
        or update_plan.get("requiresApproval") is not True
    ):
        fail("Echo bridge did not produce an approval-gated read-only update")
    updated = bridge_request(
        "POST",
        "/v1/sharing/nfs/apply",
        {"desired": wanted_ro, "planId": update_plan.get("planId")},
    )
    if (
        not isinstance(updated, dict)
        or updated.get("verified") is not True
        or updated.get("shareUuid") != share_uuid
    ):
        fail("Echo bridge did not verify the real read-only NFS update")
    verify_share(state, read_only=True)
    verify_exports(state, read_only=True)
    verify_payload_through_read_only_mount(state, RO_MOUNT)
    strict_write_json(STATE_PATH, state)


def verify_purged() -> None:
    state = strict_read_state(STATE_PATH)
    if (
        subprocess.run(
            ["/usr/bin/dpkg-query", "-W", "openmediavault-echo-os"], check=False
        ).returncode
        == 0
    ):
        fail("the Echo OMV plugin is still installed during the purge check")
    if SOCKET_PATH.exists():
        fail("the Echo bridge socket survived plugin purge")
    verify_users_group_privilege(state["sharedFolderUuid"])
    verify_share(state, read_only=True)
    verify_exports(state, read_only=True)
    verify_payload_through_read_only_mount(state, RO_MOUNT)
    strict_write_json(
        PURGE_RESULT_PATH,
        {
            "purgePreservedShare": True,
            "purgePreservedPayload": True,
            "purgePreservedPrivilege": True,
        },
    )


def verify_reinstalled() -> None:
    state = strict_read_state(STATE_PATH)
    health = bridge_request("GET", "/health")
    if health != {"ok": True}:
        fail("the reinstalled Echo bridge health response is not exact")
    verify_users_group_privilege(state["sharedFolderUuid"])
    verify_share(state, read_only=True)
    verify_exports(state, read_only=True)
    verify_payload_through_read_only_mount(state, RO_MOUNT)
    wanted_ro = desired(state["sharedFolderUuid"], read_only=True)
    plan = bridge_request("POST", "/v1/sharing/nfs/plan", {"desired": wanted_ro})
    if (
        not isinstance(plan, dict)
        or plan.get("operation") != "none"
        or plan.get("requiresApproval") is not False
        or plan.get("shareUuid") != state["shareUuid"]
    ):
        fail("the reinstalled Echo bridge did not read back the preserved NFS state")
    overview = bridge_request("GET", "/v1/sharing")
    shares = overview.get("nfs", {}).get("shares", []) if isinstance(overview, dict) else []
    if not any(
        isinstance(item, dict)
        and item.get("uuid") == state["shareUuid"]
        and item.get("options") == "ro"
        and item.get("comment") == COMMENT_RO
        for item in shares
    ):
        fail("the reinstalled Echo bridge overview omitted the preserved NFS share")
    privilege_desired = {
        "schema": PRIVILEGE_DESIRED_SCHEMA,
        "sharedFolderRef": state["sharedFolderUuid"],
        "principalType": "group",
        "principalName": "users",
        "permission": "readWrite",
    }
    privilege_plan = bridge_request(
        "POST",
        "/v1/sharing/privileges/plan",
        {"desired": privilege_desired},
    )
    privilege_principal = (
        privilege_plan.get("principal") if isinstance(privilege_plan, dict) else None
    )
    if (
        not isinstance(privilege_plan, dict)
        or privilege_plan.get("operation") != "none"
        or privilege_plan.get("requiresApproval") is not False
        or not isinstance(privilege_principal, dict)
        or privilege_principal.get("before") != "readWrite"
        or privilege_principal.get("after") != "readWrite"
    ):
        fail("the reinstalled Echo bridge did not read back the preserved share privilege")
    strict_write_json(
        REINSTALL_RESULT_PATH,
        {
            "reinstallReadbackVerified": True,
            "reinstallPayloadVerified": True,
            "reinstallPrivilegeReadbackVerified": True,
            "reinstallPrivilegePlanNoop": True,
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("phase", choices=("create", "verify-purged", "verify-reinstalled"))
    return result


def main() -> int:
    require_environment()
    phase = parser().parse_args().phase
    try:
        if phase == "create":
            create_fixture()
        elif phase == "verify-purged":
            verify_purged()
        else:
            verify_reinstalled()
    except ProbeError as exc:
        print(f"Echo real OMV NFS probe failed: {exc}", file=os.sys.stderr)
        return 1
    print(f"ECHO_REAL_OMV_NFS_OK phase={phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
