#!/usr/bin/env python3
"""Exercise Echo family-account creation against disposable real OMV 8.

The probe is restricted to the privileged GitHub Actions systemd container
created by ``verify-real-omv-x86-ci.sh``. The fixed password is a disposable
canary: it is sent through Echo's Unix-socket bridge and later to ``smbclient``
over an inherited one-shot pipe selected by ``PASSWD_FD``. It must never appear
in an environment value, process argument, response, log, file or evidence
artifact.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import http.client
import json
import os
import platform
import pwd
import re
import socket
import stat

# Fixed executable paths are used in a disposable CI container only.
import subprocess  # nosec B404
import time
from pathlib import Path
from typing import Any, NoReturn

SCHEMA = "echo.real-omv-account-state.v3"
GROUP_NAME = "familyci"
GROUP_COMMENT = "Echo disposable family group"
USER_NAME = "motherci"
USER_DISPLAY_NAME = "Echo disposable family member"
USER_CI_CANARY = "Disposable-Family-CI-2026!"
USER_REPLACEMENT_CI_CANARY = "Replacement-Family-CI-2026!"
SECRET_CANARIES = (USER_CI_CANARY, USER_REPLACEMENT_CI_CANARY)
HMAC_SAFETY_CONTRACT = "hmacBoundNeverReturnedOrAudited"
SMB_SHARE_NAME = "echo-ci-nfs"
SMB_PAYLOAD_NAME = "echo-family-smb-ci.txt"
SMB_PAYLOAD = b"Echo family SMB authentication and read-write verification.\n"
GROUP_DESIRED = {
    "schema": "echo.omv.group-desired.v1",
    "name": GROUP_NAME,
    "comment": GROUP_COMMENT,
}
USER_DESIRED = {
    "schema": "echo.omv.user-desired.v1",
    "name": USER_NAME,
    "displayName": USER_DISPLAY_NAME,
    "password": USER_CI_CANARY,
    "groups": [GROUP_NAME],
}
USER_PASSWORD_DESIRED = {
    "schema": "echo.omv.user-password-desired.v1",
    "name": USER_NAME,
    "password": USER_REPLACEMENT_CI_CANARY,
}
SOCKET_PATH = Path("/run/echo-omv/omv.sock")
STATE_PATH = Path("/tmp/echo-real-omv-account-state.json")  # nosec B108
PURGE_PATH = Path("/tmp/echo-real-omv-account-purge.json")  # nosec B108
REINSTALL_PATH = Path("/tmp/echo-real-omv-account-reinstall.json")  # nosec B108
PLAN_PATTERN = re.compile(r"[0-9a-f]{64}")
UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
NFS_STATE_PATH = Path("/tmp/echo-real-omv-nfs-state.json")  # nosec B108
SMB_INPUT_PATH = Path("/tmp/echo-real-omv-smb-input.txt")  # nosec B108
SMB_OUTPUT_PATH = Path("/tmp/echo-real-omv-smb-output.txt")  # nosec B108


class ProbeError(RuntimeError):
    """The disposable real-OMV account probe failed."""


def fail(message: str) -> NoReturn:
    raise ProbeError(message)


def require_environment() -> None:
    if os.environ.get("ECHO_REAL_OMV_CI") != "1" or os.environ.get("GITHUB_ACTIONS") != "true":
        fail("the account probe is restricted to GitHub Actions")
    if os.geteuid() != 0:
        fail("the account probe must run as root")
    if platform.machine() != "x86_64":
        fail("the account probe requires the native x86_64 runner")
    container = Path("/run/systemd/container")
    if not container.is_file() or container.read_text(encoding="utf-8").strip() != "docker":
        fail("the account probe must run inside the disposable systemd Docker container")


def run(argv: list[str]) -> str:
    try:
        # Every caller supplies a fixed command and argument shape from this file.
        completed = subprocess.run(  # nosec B603
            argv,
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"fixed account verification command failed: {argv[0]}") from exc
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


def run_smbclient(
    command: str,
    *,
    password: str = USER_REPLACEMENT_CI_CANARY,
    expect_success: bool = True,
) -> str:
    read_descriptor, write_descriptor = os.pipe()
    if password not in SECRET_CANARIES:
        fail("the SMB password is not a reserved disposable canary")
    secret = bytearray(password.encode())
    try:
        offset = 0
        while offset < len(secret):
            written = os.write(write_descriptor, secret[offset:])
            if written <= 0:
                fail("the one-shot SMB password pipe could not be written completely")
            offset += written
        os.close(write_descriptor)
        write_descriptor = -1
        environment = os.environ.copy()
        environment.pop("PASSWD", None)
        environment.pop("PASSWD_FILE", None)
        environment["PASSWD_FD"] = str(read_descriptor)
        try:
            # The executable, share, identity and command strings are fixed by this probe.
            completed = subprocess.run(  # nosec B603
                [
                    "/usr/bin/smbclient",
                    f"//127.0.0.1/{SMB_SHARE_NAME}",
                    "-U",
                    USER_NAME,
                    "-m",
                    "SMB3",
                    "-c",
                    command,
                ],
                env=environment,
                pass_fds=(read_descriptor,),
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProbeError("fixed SMB authentication verification failed") from exc
    finally:
        if write_descriptor >= 0:
            os.close(write_descriptor)
        os.close(read_descriptor)
        secret[:] = b"\x00" * len(secret)
    combined = f"{completed.stdout}\n{completed.stderr}"
    if any(canary in combined for canary in SECRET_CANARIES):
        fail("smbclient unexpectedly returned the password canary")
    if (completed.returncode == 0) is not expect_success:
        fail(
            "fixed SMB authentication unexpectedly succeeded"
            if not expect_success
            else "fixed SMB authentication verification failed"
        )
    if not expect_success and "NT_STATUS_LOGON_FAILURE" not in combined:
        fail("old SMB password was not rejected with an authentication failure")
    return completed.stdout.strip()


def nfs_shared_folder_uuid() -> str:
    if NFS_STATE_PATH.is_symlink() or not NFS_STATE_PATH.is_file():
        fail("the disposable NFS state is unavailable to the account probe")
    raw = NFS_STATE_PATH.read_bytes()
    if not 1 <= len(raw) <= 16 * 1024 or any(canary.encode() in raw for canary in SECRET_CANARIES):
        fail("the disposable NFS state is invalid or contains the password canary")
    try:
        state = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError("the disposable NFS state is invalid JSON") from exc
    shared_folder_uuid = state.get("sharedFolderUuid") if isinstance(state, dict) else None
    if (
        not isinstance(shared_folder_uuid, str)
        or UUID_PATTERN.fullmatch(shared_folder_uuid) is None
    ):
        fail("the disposable NFS state has an invalid shared folder UUID")
    return shared_folder_uuid


def smb_desired(shared_folder_uuid: str) -> dict[str, Any]:
    if UUID_PATTERN.fullmatch(shared_folder_uuid) is None:
        fail("the SMB fixture shared folder UUID is invalid")
    return {
        "schema": "echo.omv.smb-share-desired.v1",
        "sharedFolderRef": shared_folder_uuid,
        "enabled": True,
        "readOnly": False,
        "browseable": True,
        "recycleBin": False,
        "comment": "Echo disposable family SMB authentication",
    }


def enable_smb_service() -> None:
    result = rpc(
        "SMB",
        "setSettings",
        {
            "enable": True,
            "workgroup": "WORKGROUP",
            "serverstring": "Echo disposable SMB CI",
            "loglevel": 0,
            "usesendfile": True,
            "aio": True,
            "timeserver": False,
            "winssupport": False,
            "winsserver": "",
            "homesenable": False,
            "homesbrowseable": True,
            "extraoptions": "",
        },
    )
    if not isinstance(result, dict) or result.get("enable") is not True:
        fail("OMV did not enable the disposable SMB service")
    apply_module("samba")
    for _attempt in range(30):
        try:
            with socket.create_connection(("127.0.0.1", 445), timeout=1):
                return
        except OSError:
            time.sleep(1)
    fail("the disposable SMB service did not listen on TCP 445")


def verify_smb_payload(
    *,
    upload: bool,
    password: str = USER_REPLACEMENT_CI_CANARY,
) -> str:
    expected_digest = hashlib.sha256(SMB_PAYLOAD).hexdigest()
    if upload:
        if SMB_INPUT_PATH.exists() or SMB_INPUT_PATH.is_symlink():
            fail("the reserved SMB input fixture already exists")
        descriptor = os.open(
            SMB_INPUT_PATH,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
            0o600,
        )
        try:
            offset = 0
            while offset < len(SMB_PAYLOAD):
                written = os.write(descriptor, SMB_PAYLOAD[offset:])
                if written <= 0:
                    fail("the SMB input fixture could not be written completely")
                offset += written
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        run_smbclient(
            f'put "{SMB_INPUT_PATH}" "{SMB_PAYLOAD_NAME}"',
            password=password,
        )
    if SMB_OUTPUT_PATH.exists() or SMB_OUTPUT_PATH.is_symlink():
        fail("the reserved SMB output fixture already exists")
    run_smbclient(
        f'get "{SMB_PAYLOAD_NAME}" "{SMB_OUTPUT_PATH}"',
        password=password,
    )
    try:
        output = SMB_OUTPUT_PATH.read_bytes()
    except OSError as exc:
        raise ProbeError("the SMB round-trip output could not be read") from exc
    if hashlib.sha256(output).hexdigest() != expected_digest:
        fail("the authenticated SMB round trip changed the payload")
    SMB_OUTPUT_PATH.unlink()
    return expected_digest


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self) -> None:
        super().__init__("localhost", timeout=30)

    def connect(self) -> None:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(self.timeout)
        connection.connect(str(SOCKET_PATH))
        self.sock = connection


def bridge_request(
    path: str,
    payload: dict[str, Any],
    *,
    expected_status: int = 200,
) -> Any:
    if not SOCKET_PATH.exists() or not stat.S_ISSOCK(SOCKET_PATH.stat().st_mode):
        fail("the Echo OMV bridge socket is not active")
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    connection = UnixHTTPConnection()
    try:
        try:
            connection.request(
                "POST",
                path,
                body=encoded,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
        except (OSError, http.client.HTTPException) as exc:
            raise ProbeError(f"Echo account bridge {path} could not be reached") from exc
    finally:
        connection.close()
    if response.status != expected_status:
        # Never include a secret-bearing remote body in logs or exceptions.
        fail(f"Echo account bridge {path} returned HTTP {response.status}")
    if len(raw) > 1024 * 1024 or any(canary.encode() in raw for canary in SECRET_CANARIES):
        fail("Echo account bridge returned an oversized response or the password canary")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Echo account bridge {path} returned invalid JSON") from exc


def strict_write(path: Path, payload: dict[str, Any]) -> None:
    if path not in {STATE_PATH, PURGE_PATH, REINSTALL_PATH}:
        fail("the account probe output path is not reserved")
    if path.exists() or path.is_symlink():
        fail(f"reserved account probe output already exists: {path}")
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    if any(canary.encode() in encoded for canary in SECRET_CANARIES):
        fail("the account evidence contains the password canary")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    try:
        offset = 0
        while offset < len(encoded):
            written = os.write(descriptor, encoded[offset:])
            if written <= 0:
                fail("the account evidence could not be written completely")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def strict_state() -> dict[str, Any]:
    if STATE_PATH.is_symlink() or not STATE_PATH.is_file():
        fail("the account state must be a regular non-symlink file")
    raw = STATE_PATH.read_bytes()
    if not 1 <= len(raw) <= 16 * 1024 or any(canary.encode() in raw for canary in SECRET_CANARIES):
        fail("the account state is empty, oversized, or contains the password canary")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError("the account state is invalid JSON") from exc
    expected = {
        "schema",
        "groupName",
        "groupGid",
        "groupPlanId",
        "userName",
        "userUid",
        "userGid",
        "userPlanId",
        "passwordResetPlanId",
        "smbShareUuid",
        "smbShareName",
        "smbPlanId",
        "smbProtocol",
        "smbPayloadSha256",
        "passwordNeverReturned",
        "nologinVerified",
        "noSshKeysVerified",
        "selfModificationDisabled",
        "sambaAccountVerified",
        "smbAuthenticationVerified",
        "smbReadWriteVerified",
        "oldPasswordRejected",
        "replacementPasswordAuthenticationVerified",
        "accountFieldsPreservedAfterPasswordReset",
    }
    if not isinstance(value, dict) or set(value) != expected or value.get("schema") != SCHEMA:
        fail("the account state has an unexpected schema")
    for key in ("groupPlanId", "userPlanId", "passwordResetPlanId", "smbPlanId"):
        if not isinstance(value.get(key), str) or PLAN_PATTERN.fullmatch(value[key]) is None:
            fail(f"the account state {key} is invalid")
    if value.get("groupName") != GROUP_NAME or value.get("userName") != USER_NAME:
        fail("the account state names are not the reserved disposable identities")
    if (
        not isinstance(value.get("smbShareUuid"), str)
        or UUID_PATTERN.fullmatch(value["smbShareUuid"]) is None
        or value.get("smbShareName") != SMB_SHARE_NAME
        or value.get("smbProtocol") != "SMB3"
        or not isinstance(value.get("smbPayloadSha256"), str)
        or PLAN_PATTERN.fullmatch(value["smbPayloadSha256"]) is None
    ):
        fail("the account state SMB identifiers are invalid")
    for key in ("groupGid", "userUid", "userGid"):
        if isinstance(value.get(key), bool) or not isinstance(value.get(key), int):
            fail(f"the account state {key} is invalid")
    for key in expected - {
        "schema",
        "groupName",
        "groupGid",
        "groupPlanId",
        "userName",
        "userUid",
        "userGid",
        "userPlanId",
        "passwordResetPlanId",
        "smbShareUuid",
        "smbShareName",
        "smbPlanId",
        "smbProtocol",
        "smbPayloadSha256",
    }:
        if value.get(key) is not True:
            fail(f"the account state did not verify {key}")
    return value


def verify_accounts() -> tuple[int, int, int]:
    groups = rpc("UserMgmt", "enumerateGroups", {})
    users = rpc("UserMgmt", "enumerateUsers", {"detail": "basic"})
    group_matches = (
        [item for item in groups if isinstance(item, dict) and item.get("name") == GROUP_NAME]
        if isinstance(groups, list)
        else []
    )
    user_matches = (
        [item for item in users if isinstance(item, dict) and item.get("name") == USER_NAME]
        if isinstance(users, list)
        else []
    )
    if len(group_matches) != 1 or len(user_matches) != 1:
        fail("OMV did not enumerate exactly one disposable group and user")
    group = group_matches[0]
    user = user_matches[0]
    group_detail = rpc("UserMgmt", "getGroup", {"name": GROUP_NAME})
    if (
        not isinstance(group_detail, dict)
        or group_detail.get("comment") != GROUP_COMMENT
        or group.get("members") not in ([], [USER_NAME])
        or user.get("comment") != USER_DISPLAY_NAME
        or user.get("shell") != "/usr/sbin/nologin"
        or sorted(item for item in user.get("groups", []) if item != "users") != [GROUP_NAME]
    ):
        fail("OMV account enumeration differs from the constrained desired state")
    detail = rpc("UserMgmt", "getUser", {"name": USER_NAME})
    if (
        not isinstance(detail, dict)
        or detail.get("email") != ""
        or detail.get("sshpubkeys") != []
        or detail.get("disallowusermod") is not True
    ):
        fail("OMV user detail did not preserve the no-email/no-SSH/self-modification policy")
    try:
        passwd_entry = pwd.getpwnam(USER_NAME)
        group_entry = grp.getgrnam(GROUP_NAME)
    except KeyError as exc:
        raise ProbeError("the disposable account is missing from the operating system") from exc
    if passwd_entry.pw_shell != "/usr/sbin/nologin" or USER_NAME not in group_entry.gr_mem:
        fail("the operating-system account or group membership differs from OMV")
    pdb_output = run(["/usr/bin/pdbedit", "-L", "-u", USER_NAME])
    if not pdb_output.startswith(f"{USER_NAME}:"):
        fail("Samba did not create the disposable NAS account")
    return group_entry.gr_gid, passwd_entry.pw_uid, passwd_entry.pw_gid


def create_accounts() -> None:
    for path in (STATE_PATH, PURGE_PATH, REINSTALL_PATH):
        if path.exists() or path.is_symlink():
            fail(f"reserved account fixture path already exists: {path}")
    settings = rpc("UserMgmt", "getSettings", {})
    if not isinstance(settings, dict) or settings.get("enable") is not False:
        fail("the disposable OMV host must have automatic user homes disabled")

    group_plan = bridge_request("/v1/accounts/groups/plan", {"desired": GROUP_DESIRED})
    if (
        not isinstance(group_plan, dict)
        or group_plan.get("operation") != "create"
        or group_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(group_plan.get("planId", ""))) is None
        or group_plan.get("safety")
        != {
            "scope": "newNormalOmvGroup",
            "initialMembers": "empty",
            "systemGroups": "never",
            "update": "notManaged",
            "delete": "rollbackOnlyBeforeUse",
        }
    ):
        fail("Echo bridge did not produce a constrained real group plan")
    group_applied = bridge_request(
        "/v1/accounts/groups/apply",
        {"desired": GROUP_DESIRED, "planId": group_plan["planId"]},
    )
    if group_applied.get("applied") is not True or group_applied.get("verified") is not True:
        fail("Echo bridge did not verify the real group creation")

    user_plan = bridge_request("/v1/accounts/users/plan", {"desired": USER_DESIRED})
    if (
        not isinstance(user_plan, dict)
        or user_plan.get("operation") != "create"
        or user_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(user_plan.get("planId", ""))) is None
        or user_plan.get("desired")
        != {
            "schema": USER_DESIRED["schema"],
            "name": USER_NAME,
            "displayName": USER_DISPLAY_NAME,
            "groups": [GROUP_NAME],
            "passwordBound": True,
        }
        or user_plan.get("safety")
        != {
            "scope": "newNormalOmvUser",
            "password": HMAC_SAFETY_CONTRACT,
            "loginShell": "nologin",
            "sshKeys": "none",
            "homeDirectory": "automaticHomesMustBeDisabled",
            "systemGroups": "notEnumeratedNotSelectable",
            "update": "notManaged",
            "delete": "rollbackOnlyBeforeUse",
        }
    ):
        fail("Echo bridge did not produce a password-safe constrained real user plan")
    user_applied = bridge_request(
        "/v1/accounts/users/apply",
        {"desired": USER_DESIRED, "planId": user_plan["planId"]},
    )
    if user_applied.get("applied") is not True or user_applied.get("verified") is not True:
        fail("Echo bridge did not verify the real user creation")

    group_gid, user_uid, user_gid = verify_accounts()
    shared_folder_uuid = nfs_shared_folder_uuid()
    enable_smb_service()
    wanted_smb = smb_desired(shared_folder_uuid)
    smb_plan = bridge_request("/v1/sharing/smb/plan", {"desired": wanted_smb})
    if (
        not isinstance(smb_plan, dict)
        or smb_plan.get("operation") != "create"
        or smb_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(smb_plan.get("planId", ""))) is None
        or smb_plan.get("safety")
        != {
            "guestAccess": "disabled",
            "advancedOptions": "notManaged",
            "acl": "notManaged",
        }
    ):
        fail("Echo bridge did not produce a safe private SMB plan for the family account")
    smb_applied = bridge_request(
        "/v1/sharing/smb/apply",
        {"desired": wanted_smb, "planId": smb_plan["planId"]},
    )
    smb_share_uuid = smb_applied.get("shareUuid") if isinstance(smb_applied, dict) else None
    if (
        not isinstance(smb_applied, dict)
        or smb_applied.get("applied") is not True
        or smb_applied.get("verified") is not True
        or not isinstance(smb_share_uuid, str)
        or UUID_PATTERN.fullmatch(smb_share_uuid) is None
    ):
        fail("Echo bridge did not create and verify the private SMB family share")
    smb_payload_sha256 = verify_smb_payload(upload=True, password=USER_CI_CANARY)
    password_plan = bridge_request(
        "/v1/accounts/users/password/plan",
        {"desired": USER_PASSWORD_DESIRED},
    )
    if (
        not isinstance(password_plan, dict)
        or password_plan.get("operation") != "resetPassword"
        or password_plan.get("requiresApproval") is not True
        or PLAN_PATTERN.fullmatch(str(password_plan.get("planId", ""))) is None
        or password_plan.get("desired")
        != {
            "schema": USER_PASSWORD_DESIRED["schema"],
            "name": USER_NAME,
            "passwordBound": True,
        }
        or password_plan.get("safety")
        != {
            "scope": "existingConstrainedNormalOmvUser",
            "password": HMAC_SAFETY_CONTRACT,
            "accountFields": "preservedAndVerified",
            "loginShell": "nologin",
            "sshKeys": "none",
            "rollback": "notAvailableAfterAcceptedSecretRpc",
        }
    ):
        fail("Echo bridge did not produce a password-safe reset plan")
    password_applied = bridge_request(
        "/v1/accounts/users/password/apply",
        {"desired": USER_PASSWORD_DESIRED, "planId": password_plan["planId"]},
    )
    if (
        not isinstance(password_applied, dict)
        or password_applied.get("applied") is not True
        or password_applied.get("verified") is not True
    ):
        fail("Echo bridge did not verify the real family password reset")
    verify_accounts()
    run_smbclient("ls", password=USER_CI_CANARY, expect_success=False)
    replacement_digest = verify_smb_payload(
        upload=False,
        password=USER_REPLACEMENT_CI_CANARY,
    )
    if replacement_digest != smb_payload_sha256:
        fail("password reset changed the authenticated SMB payload")
    strict_write(
        STATE_PATH,
        {
            "schema": SCHEMA,
            "groupName": GROUP_NAME,
            "groupGid": group_gid,
            "groupPlanId": group_plan["planId"],
            "userName": USER_NAME,
            "userUid": user_uid,
            "userGid": user_gid,
            "userPlanId": user_plan["planId"],
            "passwordResetPlanId": password_plan["planId"],
            "smbShareUuid": smb_share_uuid,
            "smbShareName": SMB_SHARE_NAME,
            "smbPlanId": smb_plan["planId"],
            "smbProtocol": "SMB3",
            "smbPayloadSha256": smb_payload_sha256,
            "passwordNeverReturned": True,
            "nologinVerified": True,
            "noSshKeysVerified": True,
            "selfModificationDisabled": True,
            "sambaAccountVerified": True,
            "smbAuthenticationVerified": True,
            "smbReadWriteVerified": True,
            "oldPasswordRejected": True,
            "replacementPasswordAuthenticationVerified": True,
            "accountFieldsPreservedAfterPasswordReset": True,
        },
    )


def verify_purged() -> None:
    state = strict_state()
    group_gid, user_uid, user_gid = verify_accounts()
    if (group_gid, user_uid, user_gid) != (
        state["groupGid"],
        state["userUid"],
        state["userGid"],
    ):
        fail("plugin purge changed the disposable account identities")
    purge_smb_payload_sha256 = verify_smb_payload(upload=False)
    if purge_smb_payload_sha256 != state["smbPayloadSha256"]:
        fail("plugin purge changed the authenticated SMB payload")
    strict_write(
        PURGE_PATH,
        {
            "purgePreservedGroup": True,
            "purgePreservedUser": True,
            "purgePreservedSambaAccount": True,
            "purgePreservedSmbAuthentication": True,
            "purgePreservedSmbPayload": True,
            "purgeSmbPayloadSha256": purge_smb_payload_sha256,
        },
    )


def verify_reinstalled() -> None:
    state = strict_state()
    group_gid, user_uid, user_gid = verify_accounts()
    if (group_gid, user_uid, user_gid) != (
        state["groupGid"],
        state["userUid"],
        state["userGid"],
    ):
        fail("plugin reinstall changed the disposable account identities")
    reinstall_smb_payload_sha256 = verify_smb_payload(upload=False)
    if reinstall_smb_payload_sha256 != state["smbPayloadSha256"]:
        fail("plugin reinstall changed the authenticated SMB payload")
    bridge_request(
        "/v1/accounts/groups/plan",
        {"desired": GROUP_DESIRED},
        expected_status=409,
    )
    bridge_request(
        "/v1/accounts/users/plan",
        {"desired": USER_DESIRED},
        expected_status=409,
    )
    preserved_smb = smb_desired(nfs_shared_folder_uuid())
    smb_plan = bridge_request("/v1/sharing/smb/plan", {"desired": preserved_smb})
    if (
        not isinstance(smb_plan, dict)
        or smb_plan.get("operation") != "none"
        or smb_plan.get("requiresApproval") is not False
        or smb_plan.get("shareUuid") != state["smbShareUuid"]
        or PLAN_PATTERN.fullmatch(str(smb_plan.get("planId", ""))) is None
    ):
        fail("the reinstalled bridge did not read back the preserved private SMB share")
    strict_write(
        REINSTALL_PATH,
        {
            "reinstallReadbackVerified": True,
            "existingGroupCreateRejected": True,
            "existingUserCreateRejected": True,
            "passwordNeverReturned": True,
            "reinstallSmbAuthenticationVerified": True,
            "reinstallSmbPayloadVerified": True,
            "reinstallSmbPayloadSha256": reinstall_smb_payload_sha256,
            "reinstallSmbPlanNoop": True,
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
            create_accounts()
        elif phase == "verify-purged":
            verify_purged()
        else:
            verify_reinstalled()
    except ProbeError as exc:
        print(f"Echo real OMV account probe failed: {exc}", file=os.sys.stderr)
        return 1
    print(f"ECHO_REAL_OMV_ACCOUNT_OK phase={phase}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
