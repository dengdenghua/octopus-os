"""One-time, approval-bound provisioning for the NAS backup credential."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import stat
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from appliance import nas_backup_schedule_policy as schedule_policy
from deploy.appliance import nas_data_backup
from deploy.appliance import nas_data_backup_schedule_runner as schedule_runner

DESIRED_SCHEMA = "echo.nas-data-backup-credential-desired.v1"
PLAN_SCHEMA = "echo.nas-data-backup-credential-plan.v1"
ROTATION_DESIRED_SCHEMA = "echo.nas-data-backup-credential-rotation-desired.v1"
ROTATION_PLAN_SCHEMA = "echo.nas-data-backup-credential-rotation-plan.v1"
CREDENTIAL_NAME = "echo-nas-backup-password"
CREDENTIAL_PATH = schedule_policy.CREDENTIAL_PATH
SYSTEMD_CREDS = Path("/usr/bin/systemd-creds")
CREDENTIAL_LOCK_FILE = Path("/run/echo-os/nas-backup-credential.lock")
ROTATION_RECEIPT_PATH = Path("/etc/credstore.encrypted/echo-nas-backup-password.rotation")
ROTATION_RECEIPT_SCHEMA = "echo.nas-data-backup-credential-rotation-receipt.v1"
MAX_PASSWORD_BYTES = nas_data_backup.MAX_PASSWORD_BYTES
MAX_CREDENTIAL_BYTES = schedule_policy.MAX_CREDENTIAL_BYTES
MAX_ROTATION_RECEIPT_BYTES = 128 * 1024
MAX_TOOL_BYTES = 64 * 1024 * 1024
MAX_REPOSITORY_KEYS = 128
DEFAULT_BINDING_KEY = hashlib.sha256(b"echo-development-nas-backup-binding").digest()
_LOCK = threading.RLock()


class NasBackupCredentialPolicyError(ValueError):
    """The requested credential provisioning state is invalid."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_password(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise NasBackupCredentialPolicyError(f"{label} is invalid")
    encoded = value.encode("utf-8")
    if not 12 <= len(encoded) <= MAX_PASSWORD_BYTES or any(
        token in encoded for token in (b"\0", b"\r", b"\n")
    ):
        raise NasBackupCredentialPolicyError(f"{label} must contain 12 to 4096 safe UTF-8 bytes")
    return value


def _validate_desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value)
        != {
            "schema",
            "mode",
            "repository",
            "repositoryMount",
            "password",
        }
        or value.get("schema") != DESIRED_SCHEMA
    ):
        raise NasBackupCredentialPolicyError("NAS backup credential request has an invalid schema")
    mode = value.get("mode")
    if mode not in {"initialize", "connect"}:
        raise NasBackupCredentialPolicyError("NAS backup repository mode is invalid")
    try:
        config = schedule_runner.validate_config(
            {
                "schema": schedule_runner.CONFIG_SCHEMA,
                "enabled": True,
                "repository": value.get("repository"),
                "repositoryMount": value.get("repositoryMount"),
            }
        )
    except schedule_runner.NasDataBackupScheduleError as exc:
        raise NasBackupCredentialPolicyError(str(exc)) from exc
    password = _validate_password(value.get("password"), label="NAS backup password")
    return {
        "schema": DESIRED_SCHEMA,
        "mode": mode,
        "repository": config["repository"],
        "repositoryMount": config["repositoryMount"],
        "password": password,
    }


def _validate_rotation_desired(value: Mapping[str, Any]) -> dict[str, Any]:
    if (
        set(value)
        != {
            "schema",
            "repository",
            "repositoryMount",
            "currentPassword",
            "newPassword",
        }
        or value.get("schema") != ROTATION_DESIRED_SCHEMA
    ):
        raise NasBackupCredentialPolicyError(
            "NAS backup credential rotation request has an invalid schema"
        )
    try:
        config = schedule_runner.validate_config(
            {
                "schema": schedule_runner.CONFIG_SCHEMA,
                "enabled": True,
                "repository": value.get("repository"),
                "repositoryMount": value.get("repositoryMount"),
            }
        )
    except schedule_runner.NasDataBackupScheduleError as exc:
        raise NasBackupCredentialPolicyError(str(exc)) from exc
    current_password = _validate_password(
        value.get("currentPassword"), label="current NAS backup password"
    )
    new_password = _validate_password(value.get("newPassword"), label="new NAS backup password")
    if hmac.compare_digest(current_password.encode("utf-8"), new_password.encode("utf-8")):
        raise NasBackupCredentialPolicyError(
            "new NAS backup password must differ from the current password"
        )
    return {
        "schema": ROTATION_DESIRED_SCHEMA,
        "repository": config["repository"],
        "repositoryMount": config["repositoryMount"],
        "currentPassword": current_password,
        "newPassword": new_password,
    }


def _tool_identity(path: Path, *, trusted_uid: int) -> dict[str, int]:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or metadata.st_size < 1
        or metadata.st_size > MAX_TOOL_BYTES
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022)
    ):
        raise OSError("NAS backup credential runtime is unsafe")
    return {
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
        "mtimeNs": metadata.st_mtime_ns,
    }


def _repository_binding(desired: Mapping[str, Any]) -> dict[str, Any]:
    repository, _nas = nas_data_backup._context(
        repository=Path(str(desired["repository"])),
        repository_mount=Path(str(desired["repositoryMount"])),
        deployment_root=schedule_runner.DEPLOYMENT_ROOT,
        appliance_env=schedule_runner.APPLIANCE_ENV,
        state_root_override=schedule_runner.STATE_ROOT,
        nas_root_override=schedule_runner.NAS_ROOT,
    )
    empty = next(repository.iterdir(), None) is None
    if desired["mode"] == "initialize" and not empty:
        raise NasBackupCredentialPolicyError("new NAS backup repository directory must be empty")
    if desired["mode"] == "connect" and empty:
        raise NasBackupCredentialPolicyError("existing NAS backup repository directory is empty")
    mount = nas_data_backup._mount_record(repository)
    metadata = repository.stat()
    return {
        "filesystem": mount["filesystem"],
        "sourceSha256": hashlib.sha256(mount["source"].encode("utf-8")).hexdigest(),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "empty": empty,
    }


def _context(
    desired_state: Mapping[str, Any],
    *,
    credential_path: Path,
    systemd_creds: Path,
    trusted_uid: int,
    binding_key: bytes,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    desired = _validate_desired(desired_state)
    credential_identity = schedule_policy._safe_file_identity(
        credential_path,
        trusted_uid=trusted_uid,
        maximum=MAX_CREDENTIAL_BYTES,
        exact_mode=0o600,
    )
    if credential_identity is not None:
        raise NasBackupCredentialPolicyError(
            "NAS backup credential is already configured; blind rotation is not allowed"
        )
    runtime = {
        "systemdCreds": _tool_identity(systemd_creds, trusted_uid=trusted_uid),
        "restic": _tool_identity(nas_data_backup.RESTIC, trusted_uid=trusted_uid),
    }
    repository_binding = repository_binding_reader(desired)
    if len(binding_key) < 32:
        raise OSError("NAS backup credential binding key is unavailable")
    password_binding = hmac.new(
        binding_key, desired["password"].encode("utf-8"), hashlib.sha256
    ).hexdigest()
    binding = {
        "schema": PLAN_SCHEMA,
        "mode": desired["mode"],
        "repository": {
            "pathSha256": hashlib.sha256(desired["repository"].encode()).hexdigest(),
            "mountPathSha256": hashlib.sha256(desired["repositoryMount"].encode()).hexdigest(),
            "identity": repository_binding,
        },
        "credentialPathIdentity": None,
        "runtime": runtime,
        "passwordBinding": password_binding,
    }
    return {
        **binding,
        "desired": desired,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
    }


def _public_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "planId": context["planId"],
        "operation": (
            "initializeCredential" if context["mode"] == "initialize" else "connectCredential"
        ),
        "requiresApproval": True,
        "desired": {
            "mode": context["mode"],
            "repositoryConfigured": True,
            "passwordBound": True,
        },
        "pathsRedacted": True,
        "safety": {
            "systemdEncryptedCredential": True,
            "externalMountedRepositoryRequired": True,
            "existingCredentialMustBeAbsent": True,
            "blindRotationAllowed": False,
        },
    }


def plan_credential(
    desired_state: Mapping[str, Any],
    *,
    credential_path: Path = CREDENTIAL_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    trusted_uid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
) -> dict[str, Any]:
    return _public_plan(
        _context(
            desired_state,
            credential_path=credential_path,
            systemd_creds=systemd_creds,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            repository_binding_reader=repository_binding_reader,
        )
    )


def _rotation_context(
    desired_state: Mapping[str, Any],
    *,
    credential_path: Path,
    systemd_creds: Path,
    trusted_uid: int,
    binding_key: bytes,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    desired = _validate_rotation_desired(desired_state)
    credential_identity = schedule_policy._safe_file_identity(
        credential_path,
        trusted_uid=trusted_uid,
        maximum=MAX_CREDENTIAL_BYTES,
        exact_mode=0o600,
    )
    if credential_identity is None:
        raise NasBackupCredentialPolicyError("NAS backup credential is not configured")
    if len(binding_key) < 32:
        raise OSError("NAS backup credential binding key is unavailable")
    repository_binding = repository_binding_reader(
        {
            **desired,
            "mode": "connect",
        }
    )
    current_binding = hmac.new(
        binding_key,
        b"current\0" + desired["currentPassword"].encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    new_binding = hmac.new(
        binding_key,
        b"new\0" + desired["newPassword"].encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    binding = {
        "schema": ROTATION_PLAN_SCHEMA,
        "repository": {
            "pathSha256": hashlib.sha256(desired["repository"].encode()).hexdigest(),
            "mountPathSha256": hashlib.sha256(desired["repositoryMount"].encode()).hexdigest(),
            "identity": repository_binding,
        },
        "credentialPathIdentity": credential_identity,
        "runtime": {
            "systemdCreds": _tool_identity(systemd_creds, trusted_uid=trusted_uid),
            "restic": _tool_identity(nas_data_backup.RESTIC, trusted_uid=trusted_uid),
        },
        "currentPasswordBinding": current_binding,
        "newPasswordBinding": new_binding,
    }
    return {
        **binding,
        "desired": desired,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
    }


def _public_rotation_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": ROTATION_PLAN_SCHEMA,
        "planId": context["planId"],
        "operation": "rotateCredential",
        "requiresApproval": True,
        "desired": {
            "repositoryConfigured": True,
            "currentPasswordBound": True,
            "newPasswordBound": True,
        },
        "pathsRedacted": True,
        "safety": {
            "systemdEncryptedCredential": True,
            "newRepositoryKeyVerifiedBeforeSwitch": True,
            "oldPasswordKeysRevokedAfterSwitch": True,
            "rollbackPreservesRepositoryAccess": True,
        },
    }


def plan_rotation(
    desired_state: Mapping[str, Any],
    *,
    credential_path: Path = CREDENTIAL_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    trusted_uid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
) -> dict[str, Any]:
    return _public_rotation_plan(
        _rotation_context(
            desired_state,
            credential_path=credential_path,
            systemd_creds=systemd_creds,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            repository_binding_reader=repository_binding_reader,
        )
    )


def _run_systemd_creds(
    arguments: list[str],
    payload: bytes,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    systemd_creds: Path,
) -> bytes:
    try:
        completed = runner(
            [str(systemd_creds), *arguments],
            input=payload,
            capture_output=True,
            timeout=30,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("NAS backup credential encryption tool failed") from exc
    if (
        completed.returncode != 0
        or not 1 <= len(completed.stdout) <= MAX_CREDENTIAL_BYTES
        or len(completed.stderr) > MAX_CREDENTIAL_BYTES
    ):
        raise OSError("NAS backup credential encryption tool failed")
    return completed.stdout


def _encrypt_and_verify(
    password: bytes,
    *,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
    systemd_creds: Path,
) -> bytes:
    encrypted = _run_systemd_creds(
        ["encrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
        password,
        runner=runner,
        systemd_creds=systemd_creds,
    )
    decrypted = _run_systemd_creds(
        ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
        encrypted,
        runner=runner,
        systemd_creds=systemd_creds,
    )
    if not hmac.compare_digest(decrypted, password):
        raise OSError("NAS backup credential encryption verification failed")
    return encrypted


@contextmanager
def _credential_lock(path: Path = CREDENTIAL_LOCK_FILE, *, trusted_uid: int = 0):
    if os.name != "posix":
        yield
        return
    import fcntl

    nas_data_backup._ensure_lock_directory(path.parent, trusted_uid=trusted_uid)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != trusted_uid:
            raise OSError("NAS backup credential lock is unsafe")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise OSError("another NAS backup credential operation is active") from exc
        yield
    finally:
        os.close(descriptor)


def _atomic_write_new(path: Path, payload: bytes, *, uid: int, gid: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise NasBackupCredentialPolicyError(
                "NAS backup credential was configured concurrently"
            ) from exc
        temporary.unlink()
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _read_private_file(
    path: Path,
    *,
    trusted_uid: int,
    maximum: int,
    label: str,
) -> tuple[bytes, dict[str, Any]]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or not 1 <= before.st_size <= maximum
            or (os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600)
        ):
            raise OSError(f"{label} is unsafe")
        payload = bytearray()
        while len(payload) <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
        after = os.fstat(descriptor)
        if len(payload) != before.st_size or (
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
            raise OSError(f"{label} changed while reading")
    finally:
        os.close(descriptor)
    raw = bytes(payload)
    return raw, {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "device": before.st_dev,
        "inode": before.st_ino,
        "size": before.st_size,
        "mtimeNs": before.st_mtime_ns,
    }


def _read_private_credential(path: Path, *, trusted_uid: int) -> tuple[bytes, dict[str, Any]]:
    return _read_private_file(
        path,
        trusted_uid=trusted_uid,
        maximum=MAX_CREDENTIAL_BYTES,
        label="NAS backup credential",
    )


def _atomic_replace_expected(
    path: Path,
    payload: bytes,
    expected_identity: Mapping[str, Any],
    *,
    uid: int,
    gid: int,
) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if hasattr(os, "fchown"):
            os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        current = schedule_policy._safe_file_identity(
            path,
            trusted_uid=uid,
            maximum=MAX_CREDENTIAL_BYTES,
            exact_mode=0o600,
        )
        if current != dict(expected_identity):
            raise NasBackupCredentialPolicyError("NAS backup credential changed concurrently")
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _invoke_restic_key(
    command: list[str],
    password_fds: tuple[int, ...],
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> subprocess.CompletedProcess[str]:
    try:
        for descriptor in password_fds:
            os.lseek(descriptor, 0, os.SEEK_SET)
        completed = runner(
            command,
            pass_fds=password_fds,
            env=nas_data_backup._fixed_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("NAS backup repository key command failed") from exc
    if (
        len(completed.stdout.encode("utf-8", "replace")) > nas_data_backup.MAX_OUTPUT_BYTES
        or len(completed.stderr.encode("utf-8", "replace")) > nas_data_backup.MAX_OUTPUT_BYTES
    ):
        raise OSError("NAS backup repository key command output is too large")
    return completed


def _repository_path(desired: Mapping[str, Any]) -> Path:
    repository, _nas = nas_data_backup._context(
        repository=Path(str(desired["repository"])),
        repository_mount=Path(str(desired["repositoryMount"])),
        deployment_root=schedule_runner.DEPLOYMENT_ROOT,
        appliance_env=schedule_runner.APPLIANCE_ENV,
        state_root_override=schedule_runner.STATE_ROOT,
        nas_root_override=schedule_runner.NAS_ROOT,
    )
    return repository


def _repository_keys(
    repository: Path,
    password: bytes,
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> list[dict[str, Any]] | None:
    with nas_data_backup._password_memfd(password) as descriptor:
        command = [
            *nas_data_backup._restic_base(repository, descriptor),
            "--json",
            "key",
            "list",
        ]
        completed = _invoke_restic_key(command, (descriptor,), runner=runner)
    if completed.returncode == 12:
        return None
    if completed.returncode != 0:
        raise OSError("NAS backup repository key list failed")
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise OSError("NAS backup repository key list is malformed") from exc
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_REPOSITORY_KEYS:
        raise OSError("NAS backup repository key list is invalid")
    keys: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise OSError("NAS backup repository key list is invalid")
        key_id = item.get("id")
        current = item.get("current")
        if (
            not isinstance(key_id, str)
            or nas_data_backup.SNAPSHOT.fullmatch(key_id) is None
            or not isinstance(current, bool)
        ):
            raise OSError("NAS backup repository key list is invalid")
        keys.append({"id": key_id, "current": current})
    if sum(item["current"] for item in keys) != 1:
        raise OSError("NAS backup repository current key is ambiguous")
    return keys


def _current_key_id(keys: list[dict[str, Any]]) -> str:
    return next(str(item["id"]) for item in keys if item["current"])


def _add_repository_key(
    desired: Mapping[str, Any],
    current_password: bytes,
    new_password: bytes,
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> dict[str, Any]:
    repository = _repository_path(desired)
    before = _repository_keys(repository, current_password, runner=runner)
    if before is None:
        raise NasBackupCredentialPolicyError(
            "current NAS backup password cannot unlock the repository"
        )
    if len(before) >= MAX_REPOSITORY_KEYS:
        raise OSError("NAS backup repository has too many keys to rotate safely")
    old_key_id = _current_key_id(before)
    before_ids = {str(item["id"]) for item in before}
    with (
        nas_data_backup._password_memfd(current_password) as current_descriptor,
        nas_data_backup._password_memfd(new_password) as new_descriptor,
    ):
        command = [
            *nas_data_backup._restic_base(repository, current_descriptor),
            "key",
            "add",
            "--new-password-file",
            f"/proc/self/fd/{new_descriptor}",
            "--user",
            "echo-os",
            "--host",
            "echo-os",
        ]
        completed = _invoke_restic_key(
            command,
            (current_descriptor, new_descriptor),
            runner=runner,
        )
    if completed.returncode != 0:
        raise OSError("NAS backup repository key add failed")
    try:
        after = _repository_keys(repository, new_password, runner=runner)
        if after is None:
            raise OSError("new NAS backup repository key could not be verified")
        new_key_id = _current_key_id(after)
        after_ids = {str(item["id"]) for item in after}
        if (
            new_key_id in before_ids
            or old_key_id not in after_ids
            or after_ids != before_ids | {new_key_id}
        ):
            raise OSError("NAS backup repository key transition is invalid")
    except Exception as exc:
        try:
            live = _repository_keys(repository, current_password, runner=runner)
            added_ids = (
                []
                if live is None
                else [str(item["id"]) for item in live if item["id"] not in before_ids]
            )
            if len(added_ids) != 1:
                raise OSError("new NAS backup repository key is ambiguous")
            _remove_repository_key(
                repository,
                current_password,
                added_ids[0],
                runner=runner,
            )
        except Exception as cleanup_exc:
            raise OSError(
                "NAS backup repository key verification failed; cleanup is required"
            ) from cleanup_exc
        raise exc
    return {
        "repository": repository,
        "oldKeyId": old_key_id,
        "newKeyId": new_key_id,
    }


def _remove_repository_key(
    repository: Path,
    authentication_password: bytes,
    key_id: str,
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> None:
    with nas_data_backup._password_memfd(authentication_password) as descriptor:
        completed = _invoke_restic_key(
            [
                *nas_data_backup._restic_base(repository, descriptor),
                "key",
                "remove",
                key_id,
            ],
            (descriptor,),
            runner=runner,
        )
    if completed.returncode != 0:
        raise OSError("NAS backup repository key removal failed")


def _verify_repository_password(
    desired: Mapping[str, Any],
    password: bytes,
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> dict[str, Any]:
    repository = _repository_path(desired)
    with nas_data_backup._password_memfd(password) as descriptor:
        repository_id = nas_data_backup._repository_id(repository, descriptor, runner)
        nas_data_backup._restic(
            [*nas_data_backup._restic_base(repository, descriptor), "check"],
            descriptor,
            runner,
            phase="credential_rotation",
        )
    return {"repositoryId": repository_id, "repositoryVerified": True}


def _revoke_repository_password(
    desired: Mapping[str, Any],
    authentication_password: bytes,
    rejected_password: bytes,
    preserve_key_id: str,
    *,
    runner: nas_data_backup.Runner = nas_data_backup._run,
) -> int:
    repository = _repository_path(desired)
    removed = 0
    while removed < MAX_REPOSITORY_KEYS:
        keys = _repository_keys(repository, rejected_password, runner=runner)
        if keys is None:
            return removed
        current = _current_key_id(keys)
        if current == preserve_key_id:
            raise OSError("new NAS backup repository key matched the old password")
        _remove_repository_key(
            repository,
            authentication_password,
            current,
            runner=runner,
        )
        removed += 1
    raise OSError("too many NAS backup repository keys use the old password")


def _validate_rotation_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "planId",
        "repository",
        "repositoryMount",
        "repositoryId",
        "oldEncrypted",
        "oldCredentialSha256",
        "newCredentialSha256",
        "beforeKeyIds",
    }:
        raise OSError("NAS backup credential rotation receipt is invalid")
    if value.get("schema") != ROTATION_RECEIPT_SCHEMA:
        raise OSError("NAS backup credential rotation receipt is invalid")
    digests = (
        value.get("planId"),
        value.get("repositoryId"),
        value.get("oldCredentialSha256"),
        value.get("newCredentialSha256"),
    )
    if any(
        not isinstance(item, str) or nas_data_backup.SNAPSHOT.fullmatch(item) is None
        for item in digests
    ):
        raise OSError("NAS backup credential rotation receipt is invalid")
    try:
        config = schedule_runner.validate_config(
            {
                "schema": schedule_runner.CONFIG_SCHEMA,
                "enabled": True,
                "repository": value.get("repository"),
                "repositoryMount": value.get("repositoryMount"),
            }
        )
    except schedule_runner.NasDataBackupScheduleError as exc:
        raise OSError("NAS backup credential rotation receipt is invalid") from exc
    encoded = value.get("oldEncrypted")
    if not isinstance(encoded, str) or not encoded:
        raise OSError("NAS backup credential rotation receipt is invalid")
    try:
        old_encrypted = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise OSError("NAS backup credential rotation receipt is invalid") from exc
    if (
        not 1 <= len(old_encrypted) <= MAX_CREDENTIAL_BYTES
        or hashlib.sha256(old_encrypted).hexdigest() != value["oldCredentialSha256"]
        or value["oldCredentialSha256"] == value["newCredentialSha256"]
    ):
        raise OSError("NAS backup credential rotation receipt is invalid")
    raw_key_ids = value.get("beforeKeyIds")
    if (
        not isinstance(raw_key_ids, list)
        or not 1 <= len(raw_key_ids) < MAX_REPOSITORY_KEYS
        or len(set(raw_key_ids)) != len(raw_key_ids)
        or any(
            not isinstance(item, str) or nas_data_backup.SNAPSHOT.fullmatch(item) is None
            for item in raw_key_ids
        )
    ):
        raise OSError("NAS backup credential rotation receipt is invalid")
    return {
        "schema": ROTATION_RECEIPT_SCHEMA,
        "planId": value["planId"],
        "repository": config["repository"],
        "repositoryMount": config["repositoryMount"],
        "repositoryId": value["repositoryId"],
        "oldEncrypted": old_encrypted,
        "oldCredentialSha256": value["oldCredentialSha256"],
        "newCredentialSha256": value["newCredentialSha256"],
        "beforeKeyIds": list(raw_key_ids),
    }


def _load_rotation_receipt(
    path: Path, *, trusted_uid: int
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    identity = schedule_policy._safe_file_identity(
        path,
        trusted_uid=trusted_uid,
        maximum=MAX_ROTATION_RECEIPT_BYTES,
        exact_mode=0o600,
    )
    if identity is None:
        return None
    raw, read_identity = _read_private_file(
        path,
        trusted_uid=trusted_uid,
        maximum=MAX_ROTATION_RECEIPT_BYTES,
        label="NAS backup credential rotation receipt",
    )
    if identity != read_identity:
        raise OSError("NAS backup credential rotation receipt changed while reading")
    try:
        value = nas_data_backup._strict_json(raw, "NAS backup credential rotation receipt")
    except nas_data_backup.NasDataBackupError as exc:
        raise OSError("NAS backup credential rotation receipt is invalid") from exc
    return _validate_rotation_receipt(value), read_identity


def _rotation_receipt_payload(
    *,
    context: Mapping[str, Any],
    repository_id: str,
    old_encrypted: bytes,
    new_encrypted: bytes,
    before_keys: list[dict[str, Any]],
) -> bytes:
    value = {
        "schema": ROTATION_RECEIPT_SCHEMA,
        "planId": context["planId"],
        "repository": context["desired"]["repository"],
        "repositoryMount": context["desired"]["repositoryMount"],
        "repositoryId": repository_id,
        "oldEncrypted": base64.b64encode(old_encrypted).decode("ascii"),
        "oldCredentialSha256": hashlib.sha256(old_encrypted).hexdigest(),
        "newCredentialSha256": hashlib.sha256(new_encrypted).hexdigest(),
        "beforeKeyIds": sorted(str(item["id"]) for item in before_keys),
    }
    payload = _canonical(value)
    if len(payload) > MAX_ROTATION_RECEIPT_BYTES:
        raise OSError("NAS backup credential rotation receipt is too large")
    _validate_rotation_receipt(value)
    return payload


def _remove_rotation_receipt(
    path: Path,
    expected_identity: Mapping[str, Any],
    *,
    trusted_uid: int,
) -> None:
    live = schedule_policy._safe_file_identity(
        path,
        trusted_uid=trusted_uid,
        maximum=MAX_ROTATION_RECEIPT_BYTES,
        exact_mode=0o600,
    )
    if live != dict(expected_identity):
        raise OSError("NAS backup credential rotation receipt changed concurrently")
    path.unlink()
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def rotation_recovery_pending(
    *,
    receipt_path: Path = ROTATION_RECEIPT_PATH,
    trusted_uid: int = 0,
) -> bool:
    return (
        schedule_policy._safe_file_identity(
            receipt_path,
            trusted_uid=trusted_uid,
            maximum=MAX_ROTATION_RECEIPT_BYTES,
            exact_mode=0o600,
        )
        is not None
    )


def _recover_rotation_locked(
    receipt: Mapping[str, Any],
    receipt_identity: Mapping[str, Any],
    *,
    receipt_path: Path,
    credential_path: Path,
    systemd_creds: Path,
    trusted_uid: int,
    credential_runner: Callable[..., subprocess.CompletedProcess[bytes]],
    repository_path_resolver: Callable[[Mapping[str, Any]], Path],
    repository_key_reader: Callable[[Path, bytes], list[dict[str, Any]] | None],
    repository_password_verifier: Callable[[Mapping[str, Any], bytes], dict[str, Any]],
    repository_password_revoker: Callable[[Mapping[str, Any], bytes, bytes, str], int],
    repository_key_remover: Callable[[Path, bytes, str], None],
) -> dict[str, Any]:
    live_encrypted, live_identity = _read_private_credential(
        credential_path, trusted_uid=trusted_uid
    )
    old_password = _run_systemd_creds(
        ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
        bytes(receipt["oldEncrypted"]),
        runner=credential_runner,
        systemd_creds=systemd_creds,
    )
    desired = {
        "schema": ROTATION_DESIRED_SCHEMA,
        "repository": receipt["repository"],
        "repositoryMount": receipt["repositoryMount"],
        "currentPassword": "receipt-redacted-placeholder",
        "newPassword": "receipt-redacted-placeholder-2",
    }
    repository = repository_path_resolver(desired)
    before_ids = set(receipt["beforeKeyIds"])
    live_digest = live_identity["sha256"]
    if live_digest == receipt["oldCredentialSha256"]:
        active_password = _run_systemd_creds(
            ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
            live_encrypted,
            runner=credential_runner,
            systemd_creds=systemd_creds,
        )
        if not hmac.compare_digest(active_password, old_password):
            raise OSError("NAS backup credential rotation receipt does not match")
        verification = repository_password_verifier(desired, active_password)
        if verification.get("repositoryId") != receipt["repositoryId"]:
            raise OSError("NAS backup repository identity changed during recovery")
        keys = repository_key_reader(repository, active_password)
        if keys is None:
            raise OSError("NAS backup repository cannot be unlocked during recovery")
        live_ids = {str(item["id"]) for item in keys}
        added_ids = live_ids - before_ids
        if not before_ids.issubset(live_ids) or len(added_ids) > 1:
            raise OSError("NAS backup repository keys changed during recovery")
        for key_id in added_ids:
            repository_key_remover(repository, active_password, key_id)
        after = repository_key_reader(repository, active_password)
        if after is None or {str(item["id"]) for item in after} != before_ids:
            raise OSError("NAS backup credential rotation rollback is incomplete")
        removed = len(added_ids)
        direction = "rollback"
    elif live_digest == receipt["newCredentialSha256"]:
        active_password = _run_systemd_creds(
            ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
            live_encrypted,
            runner=credential_runner,
            systemd_creds=systemd_creds,
        )
        verification = repository_password_verifier(desired, active_password)
        if verification.get("repositoryId") != receipt["repositoryId"]:
            raise OSError("NAS backup repository identity changed during recovery")
        old_keys = repository_key_reader(repository, old_password)
        if old_keys is None:
            removed = 0
        else:
            keys = repository_key_reader(repository, active_password)
            if keys is None:
                raise OSError("NAS backup repository cannot be unlocked during recovery")
            live_ids = {str(item["id"]) for item in keys}
            added_ids = live_ids - before_ids
            if not before_ids.issubset(live_ids) or len(added_ids) != 1:
                raise OSError("NAS backup repository keys changed during recovery")
            preserve_key_id = next(iter(added_ids))
            if _current_key_id(keys) != preserve_key_id:
                raise OSError("NAS backup repository recovery key is ambiguous")
            removed = repository_password_revoker(
                desired,
                active_password,
                old_password,
                preserve_key_id,
            )
            if repository_key_reader(repository, old_password) is not None:
                raise OSError("old NAS backup password remains active after recovery")
        final = repository_password_verifier(desired, active_password)
        if final.get("repositoryId") != receipt["repositoryId"]:
            raise OSError("NAS backup repository identity changed during recovery")
        direction = "forward"
    else:
        raise OSError("NAS backup credential rotation receipt does not match")
    _remove_rotation_receipt(
        receipt_path,
        receipt_identity,
        trusted_uid=trusted_uid,
    )
    return {
        "schema": ROTATION_RECEIPT_SCHEMA,
        "planId": receipt["planId"],
        "recovered": True,
        "direction": direction,
        "removedKeyCount": removed,
        "pathsRedacted": True,
    }


def recover_rotation(
    *,
    receipt_path: Path = ROTATION_RECEIPT_PATH,
    credential_path: Path = CREDENTIAL_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    trusted_uid: int = 0,
    credential_lock_path: Path = CREDENTIAL_LOCK_FILE,
    credential_runner: Callable[..., subprocess.CompletedProcess[bytes]] = (subprocess.run),
    operation_lock: Callable[[], Any] = nas_data_backup._operation_lock,
    repository_path_resolver: Callable[[Mapping[str, Any]], Path] = _repository_path,
    repository_key_reader: Callable[[Path, bytes], list[dict[str, Any]] | None] = _repository_keys,
    repository_password_verifier: Callable[
        [Mapping[str, Any], bytes], dict[str, Any]
    ] = _verify_repository_password,
    repository_password_revoker: Callable[
        [Mapping[str, Any], bytes, bytes, str], int
    ] = _revoke_repository_password,
    repository_key_remover: Callable[[Path, bytes, str], None] = (_remove_repository_key),
) -> dict[str, Any]:
    with _LOCK, _credential_lock(credential_lock_path, trusted_uid=trusted_uid):
        loaded = _load_rotation_receipt(receipt_path, trusted_uid=trusted_uid)
        if loaded is None:
            return {
                "schema": ROTATION_RECEIPT_SCHEMA,
                "recovered": False,
                "pathsRedacted": True,
            }
        if os.name == "posix" and os.geteuid() != trusted_uid:
            raise OSError("NAS backup credential rotation recovery requires root")
        receipt, receipt_identity = loaded
        with operation_lock():
            return _recover_rotation_locked(
                receipt,
                receipt_identity,
                receipt_path=receipt_path,
                credential_path=credential_path,
                systemd_creds=systemd_creds,
                trusted_uid=trusted_uid,
                credential_runner=credential_runner,
                repository_path_resolver=repository_path_resolver,
                repository_key_reader=repository_key_reader,
                repository_password_verifier=repository_password_verifier,
                repository_password_revoker=repository_password_revoker,
                repository_key_remover=repository_key_remover,
            )


def _connect_repository(desired: Mapping[str, Any], password: bytes) -> dict[str, Any]:
    repository, _nas = nas_data_backup._context(
        repository=Path(str(desired["repository"])),
        repository_mount=Path(str(desired["repositoryMount"])),
        deployment_root=schedule_runner.DEPLOYMENT_ROOT,
        appliance_env=schedule_runner.APPLIANCE_ENV,
        state_root_override=schedule_runner.STATE_ROOT,
        nas_root_override=schedule_runner.NAS_ROOT,
    )
    with nas_data_backup._operation_lock(), nas_data_backup._password_memfd(password) as descriptor:
        repository_id = nas_data_backup._repository_id(repository, descriptor, nas_data_backup._run)
        nas_data_backup._restic(
            [
                *nas_data_backup._restic_base(repository, descriptor),
                "check",
            ],
            descriptor,
            nas_data_backup._run,
        )
    return {
        "repositoryId": repository_id,
        "encrypted": True,
        "repositoryVerified": True,
        "fullReadVerified": False,
    }


def _prepare_repository(desired: Mapping[str, Any], password: bytes) -> dict[str, Any]:
    if desired["mode"] == "connect":
        return _connect_repository(desired, password)
    return nas_data_backup.init_repository(
        repository=Path(str(desired["repository"])),
        repository_mount=Path(str(desired["repositoryMount"])),
        deployment_root=schedule_runner.DEPLOYMENT_ROOT,
        appliance_env=schedule_runner.APPLIANCE_ENV,
        state_root_override=schedule_runner.STATE_ROOT,
        nas_root_override=schedule_runner.NAS_ROOT,
        password=password,
    )


def apply_credential(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    credential_path: Path = CREDENTIAL_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    credential_lock_path: Path = CREDENTIAL_LOCK_FILE,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
    repository_preparer: Callable[[Mapping[str, Any], bytes], dict[str, Any]] = (
        _prepare_repository
    ),
    credential_runner: Callable[..., subprocess.CompletedProcess[bytes]] = (subprocess.run),
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("NAS backup credential provisioning requires root")
    with _LOCK, _credential_lock(credential_lock_path, trusted_uid=trusted_uid):
        context = _context(
            desired_state,
            credential_path=credential_path,
            systemd_creds=systemd_creds,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            repository_binding_reader=repository_binding_reader,
        )
        if context["planId"] != plan_id:
            raise NasBackupCredentialPolicyError(
                "NAS backup credential plan is stale; preview again"
            )
        desired = context["desired"]
        password = desired["password"].encode("utf-8")
        schedule_policy._assert_parent(credential_path, trusted_uid=trusted_uid)
        encrypted = _encrypt_and_verify(
            password, runner=credential_runner, systemd_creds=systemd_creds
        )
        repository_result = repository_preparer(desired, password)
        _atomic_write_new(credential_path, encrypted, uid=trusted_uid, gid=trusted_gid)
        identity = schedule_policy._safe_file_identity(
            credential_path,
            trusted_uid=trusted_uid,
            maximum=MAX_CREDENTIAL_BYTES,
            exact_mode=0o600,
        )
        if identity is None or identity["sha256"] != hashlib.sha256(encrypted).hexdigest():
            raise OSError("NAS backup credential could not be verified")
        return {
            **_public_plan(context),
            "applied": True,
            "verified": True,
            "repositoryId": repository_result["repositoryId"],
            "repositoryVerified": repository_result.get("repositoryVerified", True),
            "fullReadVerified": repository_result.get("fullReadVerified", False),
        }


def apply_rotation(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    credential_path: Path = CREDENTIAL_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    credential_lock_path: Path = CREDENTIAL_LOCK_FILE,
    receipt_path: Path = ROTATION_RECEIPT_PATH,
    repository_binding_reader: Callable[[Mapping[str, Any]], dict[str, Any]] = (
        _repository_binding
    ),
    credential_runner: Callable[..., subprocess.CompletedProcess[bytes]] = (subprocess.run),
    operation_lock: Callable[[], Any] = nas_data_backup._operation_lock,
    repository_path_resolver: Callable[[Mapping[str, Any]], Path] = _repository_path,
    repository_key_reader: Callable[[Path, bytes], list[dict[str, Any]] | None] = _repository_keys,
    repository_key_adder: Callable[
        [Mapping[str, Any], bytes, bytes], dict[str, Any]
    ] = _add_repository_key,
    repository_password_verifier: Callable[
        [Mapping[str, Any], bytes], dict[str, Any]
    ] = _verify_repository_password,
    repository_password_revoker: Callable[
        [Mapping[str, Any], bytes, bytes, str], int
    ] = _revoke_repository_password,
    repository_key_remover: Callable[[Path, bytes, str], None] = (_remove_repository_key),
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("NAS backup credential rotation requires root")
    with _LOCK, _credential_lock(credential_lock_path, trusted_uid=trusted_uid):
        pending = _load_rotation_receipt(receipt_path, trusted_uid=trusted_uid)
        if pending is not None:
            receipt, receipt_identity = pending
            with operation_lock():
                _recover_rotation_locked(
                    receipt,
                    receipt_identity,
                    receipt_path=receipt_path,
                    credential_path=credential_path,
                    systemd_creds=systemd_creds,
                    trusted_uid=trusted_uid,
                    credential_runner=credential_runner,
                    repository_path_resolver=repository_path_resolver,
                    repository_key_reader=repository_key_reader,
                    repository_password_verifier=repository_password_verifier,
                    repository_password_revoker=repository_password_revoker,
                    repository_key_remover=repository_key_remover,
                )
        context = _rotation_context(
            desired_state,
            credential_path=credential_path,
            systemd_creds=systemd_creds,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            repository_binding_reader=repository_binding_reader,
        )
        if context["planId"] != plan_id:
            raise NasBackupCredentialPolicyError(
                "NAS backup credential rotation plan is stale; preview again"
            )
        desired = context["desired"]
        current_password = desired["currentPassword"].encode("utf-8")
        new_password = desired["newPassword"].encode("utf-8")
        old_encrypted, old_identity = _read_private_credential(
            credential_path, trusted_uid=trusted_uid
        )
        if old_identity != context["credentialPathIdentity"]:
            raise NasBackupCredentialPolicyError(
                "NAS backup credential rotation plan is stale; preview again"
            )
        stored_password = _run_systemd_creds(
            ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
            old_encrypted,
            runner=credential_runner,
            systemd_creds=systemd_creds,
        )
        if not hmac.compare_digest(stored_password, current_password):
            raise NasBackupCredentialPolicyError(
                "current NAS backup password does not match the configured credential"
            )
        schedule_policy._assert_parent(credential_path, trusted_uid=trusted_uid)
        schedule_policy._assert_parent(receipt_path, trusted_uid=trusted_uid)
        new_encrypted = _encrypt_and_verify(
            new_password, runner=credential_runner, systemd_creds=systemd_creds
        )
        transition: dict[str, Any] | None = None
        switched = False
        with operation_lock():
            current_repository = repository_password_verifier(desired, current_password)
            repository = repository_path_resolver(desired)
            before_keys = repository_key_reader(repository, current_password)
            if (
                before_keys is None
                or not 1 <= len(before_keys) < MAX_REPOSITORY_KEYS
                or sum(item["current"] for item in before_keys) != 1
            ):
                raise OSError("NAS backup repository keys cannot be bound for rotation")
            receipt_payload = _rotation_receipt_payload(
                context=context,
                repository_id=str(current_repository["repositoryId"]),
                old_encrypted=old_encrypted,
                new_encrypted=new_encrypted,
                before_keys=before_keys,
            )
            _atomic_write_new(
                receipt_path,
                receipt_payload,
                uid=trusted_uid,
                gid=trusted_gid,
            )
            loaded_receipt = _load_rotation_receipt(receipt_path, trusted_uid=trusted_uid)
            if loaded_receipt is None:
                raise OSError("NAS backup credential rotation receipt was not written")
            _receipt, written_receipt_identity = loaded_receipt
            try:
                transition = repository_key_adder(desired, current_password, new_password)
                new_repository = repository_password_verifier(desired, new_password)
                if current_repository.get("repositoryId") != new_repository.get("repositoryId"):
                    raise OSError("NAS backup repository identity changed during rotation")
                try:
                    _atomic_replace_expected(
                        credential_path,
                        new_encrypted,
                        old_identity,
                        uid=trusted_uid,
                        gid=trusted_gid,
                    )
                    switched = True
                except Exception:
                    live = schedule_policy._safe_file_identity(
                        credential_path,
                        trusted_uid=trusted_uid,
                        maximum=MAX_CREDENTIAL_BYTES,
                        exact_mode=0o600,
                    )
                    switched = bool(
                        live and live.get("sha256") == hashlib.sha256(new_encrypted).hexdigest()
                    )
                    raise
                removed = repository_password_revoker(
                    desired,
                    new_password,
                    current_password,
                    str(transition["newKeyId"]),
                )
                _remove_rotation_receipt(
                    receipt_path,
                    written_receipt_identity,
                    trusted_uid=trusted_uid,
                )
            except Exception as exc:
                cleanup_error: Exception | None = None
                if transition is not None and not switched:
                    try:
                        repository_key_remover(
                            Path(transition["repository"]),
                            current_password,
                            str(transition["newKeyId"]),
                        )
                    except Exception as cleanup_exc:
                        cleanup_error = cleanup_exc
                    if cleanup_error is None:
                        try:
                            _remove_rotation_receipt(
                                receipt_path,
                                written_receipt_identity,
                                trusted_uid=trusted_uid,
                            )
                        except Exception as cleanup_exc:
                            cleanup_error = cleanup_exc
                if cleanup_error is not None:
                    raise OSError(
                        "NAS backup credential rotation failed; repository key cleanup is required"
                    ) from cleanup_error
                raise exc
        live_payload, live_identity = _read_private_credential(
            credential_path, trusted_uid=trusted_uid
        )
        if live_identity["sha256"] != hashlib.sha256(
            new_encrypted
        ).hexdigest() or not hmac.compare_digest(live_payload, new_encrypted):
            raise OSError("rotated NAS backup credential could not be verified")
        decrypted = _run_systemd_creds(
            ["decrypt", f"--name={CREDENTIAL_NAME}", "-", "-"],
            live_payload,
            runner=credential_runner,
            systemd_creds=systemd_creds,
        )
        if not hmac.compare_digest(decrypted, new_password):
            raise OSError("rotated NAS backup credential could not be decrypted")
        return {
            **_public_rotation_plan(context),
            "applied": True,
            "verified": True,
            "repositoryId": current_repository["repositoryId"],
            "repositoryVerified": True,
            "oldPasswordRevoked": True,
            "removedKeyCount": removed,
            "recoveryReceiptCleared": True,
        }


__all__ = [
    "CREDENTIAL_NAME",
    "CREDENTIAL_LOCK_FILE",
    "CREDENTIAL_PATH",
    "DEFAULT_BINDING_KEY",
    "DESIRED_SCHEMA",
    "NasBackupCredentialPolicyError",
    "PLAN_SCHEMA",
    "ROTATION_DESIRED_SCHEMA",
    "ROTATION_PLAN_SCHEMA",
    "ROTATION_RECEIPT_PATH",
    "ROTATION_RECEIPT_SCHEMA",
    "SYSTEMD_CREDS",
    "apply_credential",
    "apply_rotation",
    "plan_credential",
    "plan_rotation",
    "recover_rotation",
    "rotation_recovery_pending",
]
