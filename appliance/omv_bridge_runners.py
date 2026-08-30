"""Least-privilege command and socket runners for the host OMV bridge."""

from __future__ import annotations

import json
import socket
import stat
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

from appliance.omv_bridge_contract import (
    _ALLOWED_RPC_CALLS,
    _DEVICEFILE_PATTERN,
    _NFS_SHARE_FIELDS,
    _OMV_UUID_PATTERN,
    _SHARE_LIST_PARAMS,
    _SMB_SHARE_FIELDS,
    DEFAULT_OMV_ENGINE_SOCKET,
    DEFAULT_RPC_TIMEOUT_SECONDS,
    MAX_DEVICEFILE_LENGTH,
    MAX_MDSTAT_BYTES,
    MAX_QUOTA_BYTES,
    MAX_RPC_OUTPUT_BYTES,
    MAX_SECRET_RPC_RESPONSE_BYTES,
    OMV_CONFIGOBJECT_NEW_UUID,
    USER_DESIRED_SCHEMA,
    _account_name,
    _strict_text,
    _validated_nfs_share,
    _validated_shared_folder_config,
    _validated_smb_share,
    _validated_user_desired,
)
from appliance.omv_bridge_errors import OmvBridgeError, OmvBridgeValidationError


class OmvCommandRunner:
    """Execute the local OMV CLI with a fixed argv shape and no shell."""

    def __init__(
        self,
        executable: Path | str = "/usr/sbin/omv-rpc",
        *,
        timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> None:
        self.executable = Path(executable)
        self.timeout_seconds = timeout_seconds

    def __call__(self, service: str, method: str, params: dict[str, Any]) -> Any:
        if (service, method) not in _ALLOWED_RPC_CALLS:
            raise OmvBridgeError("OMV RPC method is not in the fixed allowlist")
        if (service, method) == ("FileSystemMgmt", "enumerateMountedFilesystems"):
            if params != {"includeroot": False}:
                raise OmvBridgeError("OMV filesystem parameters are not allowed")
        elif (service, method) in {
            ("NFS", "getSettings"),
            ("ShareMgmt", "getCandidates"),
            ("ShareMgmt", "enumerateSharedFolders"),
            ("SMB", "getSettings"),
            ("UserMgmt", "enumerateGroups"),
            ("UserMgmt", "getSettings"),
        }:
            if params:
                raise OmvBridgeError("OMV inventory parameters are not allowed")
        elif (service, method) in {
            ("NFS", "getShareList"),
            ("SMB", "getShareList"),
        }:
            if params != _SHARE_LIST_PARAMS:
                raise OmvBridgeError("OMV share list parameters are not allowed")
        elif (service, method) == ("UserMgmt", "enumerateUsers"):
            if params != {"detail": "basic"}:
                raise OmvBridgeError("OMV user enumeration parameters are not allowed")
        elif (service, method) in {
            ("UserMgmt", "getGroup"),
            ("UserMgmt", "getUser"),
            ("UserMgmt", "deleteGroup"),
            ("UserMgmt", "deleteUser"),
        }:
            name = params.get("name") if len(params) == 1 else None
            try:
                _account_name(name, "account")
            except OmvBridgeValidationError as exc:
                raise OmvBridgeError("OMV account parameters are not allowed") from exc
        elif (service, method) == ("UserMgmt", "setGroup"):
            if set(params) != {"name", "comment", "members"}:
                raise OmvBridgeError("OMV group creation fields are not allowed")
            try:
                _account_name(params.get("name"), "group")
                _strict_text(params.get("comment"), maximum=65)
            except OmvBridgeValidationError as exc:
                raise OmvBridgeError("OMV group creation values are not allowed") from exc
            if params.get("members") != []:
                raise OmvBridgeError("OMV group creation must start with no members")
        elif (service, method) in {
            ("FsTab", "get"),
            ("ShareMgmt", "get"),
            ("ShareMgmt", "getPrivileges"),
        }:
            share_uuid = params.get("uuid") if len(params) == 1 else None
            if not isinstance(share_uuid, str) or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None:
                raise OmvBridgeError("OMV object query parameters are not allowed")
        elif (service, method) == ("ShareMgmt", "set"):
            if set(params) != {"uuid", "name", "reldirpath", "comment", "mntentref", "mode"}:
                raise OmvBridgeError("OMV shared folder fields are not allowed")
            _validated_shared_folder_config(params)
            if params["uuid"] != OMV_CONFIGOBJECT_NEW_UUID or params["mode"] != "770":
                raise OmvBridgeError("OMV shared folder creation parameters are not allowed")
            if params["reldirpath"] != params["name"]:
                raise OmvBridgeError("OMV shared folder path must be derived from its name")
        elif (service, method) == ("ShareMgmt", "delete"):
            share_uuid = params.get("uuid") if set(params) == {"uuid", "recursive"} else None
            if (
                not isinstance(share_uuid, str)
                or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None
                or params.get("recursive") is not False
            ):
                raise OmvBridgeError("OMV shared folder rollback parameters are not allowed")
        elif (service, method) == ("ShareMgmt", "setPrivileges"):
            share_uuid = params.get("uuid") if set(params) == {"uuid", "privileges"} else None
            privileges = params.get("privileges")
            if (
                not isinstance(share_uuid, str)
                or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None
                or not isinstance(privileges, list)
                or len(privileges) > 2048
            ):
                raise OmvBridgeError("OMV privilege mutation parameters are not allowed")
            identities: list[tuple[str, str]] = []
            for item in privileges:
                if not isinstance(item, dict) or set(item) != {"type", "name", "perms"}:
                    raise OmvBridgeError("OMV privilege mutation fields are not allowed")
                role_type = item.get("type")
                name = item.get("name")
                perms = item.get("perms")
                if (
                    role_type not in {"user", "group"}
                    or not isinstance(name, str)
                    or not name
                    or name != name.strip()
                    or len(name) > 255
                    or any(character < " " for character in name)
                    or isinstance(perms, bool)
                    or perms not in {0, 5, 7}
                ):
                    raise OmvBridgeError("OMV privilege mutation values are not allowed")
                identities.append((role_type, name))
            canonical_identities = sorted(
                identities,
                key=lambda item: (0 if item[0] == "user" else 1, item[1]),
            )
            if identities != canonical_identities or len(identities) != len(set(identities)):
                raise OmvBridgeError("OMV privilege mutation list is not canonical")
        elif (service, method) == ("Config", "isDirty"):
            if params not in (
                {"modules": ["samba"]},
                {"modules": ["nfs"]},
                {"modules": ["quota"]},
                {"modules": ["rsyncd"]},
            ):
                raise OmvBridgeError("OMV dirty-state parameters are not allowed")
        elif (service, method) == ("Config", "applyChanges"):
            if params not in (
                {"modules": ["samba"], "force": False},
                {"modules": ["nfs"], "force": False},
                {"modules": ["quota"], "force": False},
                {"modules": ["rsyncd"], "force": False},
            ):
                raise OmvBridgeError("OMV apply parameters are not allowed")
        elif (service, method) == ("Quota", "get"):
            filesystem_uuid = params.get("uuid") if len(params) == 1 else None
            if (
                not isinstance(filesystem_uuid, str)
                or _OMV_UUID_PATTERN.fullmatch(filesystem_uuid) is None
            ):
                raise OmvBridgeError("OMV quota query parameters are not allowed")
        elif (service, method) == ("Quota", "setByTypeName"):
            if set(params) != {"uuid", "type", "name", "bhardlimit", "bunit"}:
                raise OmvBridgeError("OMV quota mutation fields are not allowed")
            filesystem_uuid = params.get("uuid")
            subject_type = params.get("type")
            subject_name = params.get("name")
            hard_limit = params.get("bhardlimit")
            if (
                not isinstance(filesystem_uuid, str)
                or _OMV_UUID_PATTERN.fullmatch(filesystem_uuid) is None
                or subject_type not in {"user", "group"}
                or not isinstance(subject_name, str)
                or not subject_name
                or len(subject_name) > 255
                or any(character < " " for character in subject_name)
                or isinstance(hard_limit, bool)
                or not isinstance(hard_limit, int)
                or not 0 <= hard_limit <= MAX_QUOTA_BYTES // 1024
                or params.get("bunit") != "KiB"
            ):
                raise OmvBridgeError("OMV quota mutation parameters are not allowed")
        elif (service, method) in {("SMB", "getShare"), ("SMB", "deleteShare")}:
            share_uuid = params.get("uuid") if len(params) == 1 else None
            if not isinstance(share_uuid, str) or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None:
                raise OmvBridgeError("OMV SMB share parameters are not allowed")
        elif (service, method) == ("SMB", "setShare"):
            if set(params) != _SMB_SHARE_FIELDS:
                raise OmvBridgeError("OMV SMB share fields are not allowed")
            _validated_smb_share(params)
        elif (service, method) in {("NFS", "getShare"), ("NFS", "deleteShare")}:
            share_uuid = params.get("uuid") if len(params) == 1 else None
            if not isinstance(share_uuid, str) or _OMV_UUID_PATTERN.fullmatch(share_uuid) is None:
                raise OmvBridgeError("OMV NFS share parameters are not allowed")
        elif (service, method) == ("NFS", "setShare"):
            if set(params) != _NFS_SHARE_FIELDS:
                raise OmvBridgeError("OMV NFS share fields are not allowed")
            _validated_nfs_share(params)
        elif (service, method) == ("Smart", "enumerateDevices"):
            if params:
                raise OmvBridgeError("OMV SMART enumeration parameters are not allowed")
        else:
            devicefile = params.get("devicefile") if len(params) == 1 else None
            if (
                not isinstance(devicefile, str)
                or len(devicefile) > MAX_DEVICEFILE_LENGTH
                or _DEVICEFILE_PATTERN.fullmatch(devicefile) is None
            ):
                raise OmvBridgeError("OMV SMART parameters are not allowed")
        if (
            not self.executable.is_absolute()
            or self.executable.is_symlink()
            or not self.executable.is_file()
        ):
            raise OmvBridgeError("the OMV RPC executable is unavailable")
        encoded = json.dumps(params, sort_keys=True, separators=(",", ":"))
        command = [
            str(self.executable),
            "-u",
            "admin",
            service,
            method,
            encoded,
        ]
        try:
            # service/method are internal constants and params are one JSON argv;
            # no shell or caller-controlled executable is involved.
            result = subprocess.run(  # nosec B603
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=False,
                timeout=self.timeout_seconds,
                check=False,
                cwd="/",
                env={
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OmvBridgeError("OMV RPC execution failed") from exc
        if result.returncode != 0:
            raise OmvBridgeError("OMV RPC rejected the fixed request")
        if len(result.stdout) > MAX_RPC_OUTPUT_BYTES:
            raise OmvBridgeError("OMV RPC response exceeded the safety limit")
        try:
            return json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OmvBridgeError("OMV RPC returned invalid JSON") from exc


class OmvEngineSecretRunner:
    """Call one fixed secret-bearing OMV account method over its root socket.

    Unlike the public omv-rpc CLI, this keeps the account password out of
    process arguments. Responses and remote error traces are intentionally
    discarded because OMV's setUser response contains the submitted password.
    """

    def __init__(
        self,
        socket_path: Path | str = DEFAULT_OMV_ENGINE_SOCKET,
        *,
        timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.timeout_seconds = timeout_seconds

    def __call__(self, params: dict[str, Any]) -> None:
        desired = _validated_user_desired(
            {
                "schema": USER_DESIRED_SCHEMA,
                "name": params.get("name"),
                "displayName": params.get("comment"),
                "password": params.get("password"),
                "groups": params.get("groups"),
            }
        )
        expected = {
            "name",
            "groups",
            "shell",
            "password",
            "email",
            "comment",
            "disallowusermod",
            "sshpubkeys",
        }
        if (
            set(params) != expected
            or params.get("shell") != "/usr/sbin/nologin"
            or params.get("email") != ""
            or params.get("disallowusermod") is not True
            or params.get("sshpubkeys") != []
            or params.get("name") != desired["name"]
            or params.get("comment") != desired["displayName"]
            or params.get("groups") != desired["groups"]
        ):
            raise OmvBridgeError("OMV secret user parameters are not allowed")
        path = self.socket_path
        if not path.is_absolute() or path.is_symlink():
            raise OmvBridgeError("OMV engine socket path is unsafe")
        try:
            info = path.stat()
        except OSError as exc:
            raise OmvBridgeError("OMV engine socket is unavailable") from exc
        if not stat.S_ISSOCK(info.st_mode) or info.st_mode & stat.S_IWOTH:
            raise OmvBridgeError("OMV engine socket is unsafe")
        request = bytearray(
            json.dumps(
                {
                    "service": "UserMgmt",
                    "method": "setUser",
                    "params": params,
                    "context": {"username": "admin", "role": 1},
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\0"
        )
        response = bytearray()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(path))
                connection.sendall(request)
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        raise OmvBridgeError("OMV secret RPC connection closed early")
                    response.extend(chunk)
                    if len(response) > MAX_SECRET_RPC_RESPONSE_BYTES:
                        raise OmvBridgeError("OMV secret RPC response exceeded the safety limit")
                    if response.endswith(b"\0"):
                        break
        except (OSError, TimeoutError) as exc:
            raise OmvBridgeError("OMV secret RPC failed") from exc
        finally:
            for index in range(len(request)):
                request[index] = 0
        try:
            result = json.loads(bytes(response[:-1]))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OmvBridgeError("OMV secret RPC returned invalid JSON") from exc
        finally:
            for index in range(len(response)):
                response[index] = 0
        if (
            not isinstance(result, dict)
            or set(result) != {"response", "error"}
            or result.get("error") is not None
        ):
            raise OmvBridgeError("OMV secret RPC rejected the fixed user request")
        result["response"] = None


class LsblkTopologyRunner:
    """Read block-device relationships with one fixed, non-mutating command."""

    def __init__(
        self,
        executable: Path | str = "/usr/bin/lsblk",
        *,
        timeout_seconds: int = DEFAULT_RPC_TIMEOUT_SECONDS,
    ) -> None:
        self.executable = Path(executable)
        self.timeout_seconds = timeout_seconds

    def __call__(self) -> Any:
        if (
            not self.executable.is_absolute()
            or self.executable.is_symlink()
            or not self.executable.is_file()
        ):
            raise OmvBridgeError("the lsblk executable is unavailable")
        command = [
            str(self.executable),
            "--json",
            "--bytes",
            "--paths",
            "--tree",
            "--output",
            "NAME,TYPE,SIZE,FSTYPE,ROTA",
        ]
        try:
            # The executable and every argument are internal constants. The
            # selected columns deliberately exclude UUID, serial and WWN.
            result = subprocess.run(  # nosec B603
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=False,
                timeout=self.timeout_seconds,
                check=False,
                cwd="/",
                env={
                    "LANG": "C.UTF-8",
                    "LC_ALL": "C.UTF-8",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                },
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise OmvBridgeError("block topology discovery failed") from exc
        if result.returncode != 0:
            raise OmvBridgeError("block topology discovery was rejected")
        if len(result.stdout) > MAX_RPC_OUTPUT_BYTES:
            raise OmvBridgeError("block topology exceeded the safety limit")
        try:
            return json.loads(result.stdout)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise OmvBridgeError("block topology returned invalid JSON") from exc


class ProcMdstatReader:
    """Read the kernel's fixed software-RAID status file with a hard limit."""

    def __call__(self) -> str:
        path = Path("/proc/mdstat")
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                content = stream.read(MAX_MDSTAT_BYTES + 1)
        except OSError as exc:
            raise OmvBridgeError("software RAID status is unavailable") from exc
        if len(content) > MAX_MDSTAT_BYTES:
            raise OmvBridgeError("software RAID status exceeded the safety limit")
        return content


__all__ = [
    "LsblkTopologyRunner",
    "OmvCommandRunner",
    "OmvEngineSecretRunner",
    "ProcMdstatReader",
]
