"""Approval-bound S3-compatible mounts for native NAS backups.

Remote credentials are encrypted by ``systemd-creds`` and consumed only by a
dedicated systemd instance.  The public registry contains a label and stable
identifier, never an endpoint, bucket, access key, secret, or remote path.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from deploy.appliance import external_storage
from deploy.appliance import nas_data_backup_schedule_runner as schedule_runner

DESIRED_SCHEMA = "echo.nas-backup-remote-desired.v1"
PLAN_SCHEMA = "echo.nas-backup-remote-plan.v1"
REGISTRY_SCHEMA = "echo.nas-backup-remotes.v1"
STATUS_SCHEMA = "echo.nas-backup-remote-status.v1"
REGISTRY_PATH = Path("/etc/echo-os/nas-backup-remotes.json")
CREDENTIAL_ROOT = Path("/etc/credstore.encrypted")
MOUNT_ROOT = Path("/mnt/echo-backup-remotes")
UNIT_PATH = Path("/etc/systemd/system/echo-rclone-backup@.service")
SYSTEMD_CREDS = Path("/usr/bin/systemd-creds")
SYSTEMCTL = Path("/usr/bin/systemctl")
RCLONE = Path("/usr/bin/rclone")
FUSERMOUNT3 = Path("/usr/bin/fusermount3")
MOUNTINFO = Path("/proc/self/mountinfo")
MAX_REGISTRY_BYTES = 128 * 1024
MAX_CREDENTIAL_BYTES = 256 * 1024
MAX_TOOL_BYTES = 128 * 1024 * 1024
MAX_REMOTES = 16
DEFAULT_BINDING_KEY = hashlib.sha256(b"echo-development-nas-backup-binding").digest()
_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_REGION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_LOCK = threading.RLock()


class NasBackupRemotePolicyError(ValueError):
    """The requested remote-mount state is unsafe or stale."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _safe_text(value: Any, label: str, *, minimum: int, maximum: int) -> str:
    if not isinstance(value, str):
        raise NasBackupRemotePolicyError(f"{label} is invalid")
    encoded = value.encode("utf-8")
    if not minimum <= len(encoded) <= maximum or any(
        token in value for token in ("\0", "\r", "\n")
    ):
        raise NasBackupRemotePolicyError(f"{label} is invalid")
    return value


def _remote_id(value: Any) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise NasBackupRemotePolicyError("remote id is invalid")
    return value


def _public_record(value: Mapping[str, Any]) -> dict[str, str]:
    if set(value) != {"id", "label", "kind"}:
        raise NasBackupRemotePolicyError("remote registry record is invalid")
    remote_id = _remote_id(value.get("id"))
    label = _safe_text(value.get("label"), "remote label", minimum=1, maximum=96)
    if value.get("kind") != "s3":
        raise NasBackupRemotePolicyError("remote registry kind is invalid")
    return {"id": remote_id, "label": label, "kind": "s3"}


def _strict_json(payload: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise NasBackupRemotePolicyError("remote registry has duplicate fields")
            result[key] = value
        return result

    try:
        return json.loads(payload.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise NasBackupRemotePolicyError("remote registry is invalid JSON") from exc


def _read_file(
    path: Path,
    *,
    trusted_uid: int,
    maximum: int,
    exact_mode: int | None = None,
) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    elif path.is_symlink():
        raise OSError("remote runtime file is unsafe")
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError("remote runtime file is unavailable") from exc
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != trusted_uid
            or not 1 <= before.st_size <= maximum
            or (os.name == "posix" and exact_mode is not None and mode != exact_mode)
            or (os.name == "posix" and exact_mode is None and mode & 0o022)
        ):
            raise OSError("remote runtime file is unsafe")
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
            raise OSError("remote runtime file changed while reading")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _read_registry(path: Path, *, trusted_uid: int) -> tuple[bytes | None, list[dict[str, str]]]:
    payload = _read_file(
        path,
        trusted_uid=trusted_uid,
        maximum=MAX_REGISTRY_BYTES,
        exact_mode=0o600,
    )
    if payload is None:
        return None, []
    value = _strict_json(payload)
    if not isinstance(value, dict) or set(value) != {"schema", "remotes"}:
        raise NasBackupRemotePolicyError("remote registry has an invalid shape")
    items = value.get("remotes")
    if value.get("schema") != REGISTRY_SCHEMA or not isinstance(items, list):
        raise NasBackupRemotePolicyError("remote registry has an invalid schema")
    if len(items) > MAX_REMOTES:
        raise NasBackupRemotePolicyError("remote registry is too large")
    remotes = [_public_record(item) for item in items if isinstance(item, dict)]
    if len(remotes) != len(items) or len({item["id"] for item in remotes}) != len(remotes):
        raise NasBackupRemotePolicyError("remote registry contains duplicate identities")
    if remotes != sorted(remotes, key=lambda item: item["id"]):
        raise NasBackupRemotePolicyError("remote registry is not canonical")
    return payload, remotes


def _endpoint(value: Any) -> str:
    endpoint = _safe_text(value, "S3 endpoint", minimum=8, maximum=2048)
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise NasBackupRemotePolicyError("S3 endpoint must be an HTTPS origin")
    try:
        port = parsed.port
    except ValueError as exc:
        raise NasBackupRemotePolicyError("S3 endpoint port is invalid") from exc
    if port is not None and not 1 <= port <= 65535:
        raise NasBackupRemotePolicyError("S3 endpoint port is invalid")
    return endpoint.rstrip("/")


def _bucket(value: Any) -> str:
    bucket = _safe_text(value, "S3 bucket", minimum=3, maximum=63)
    if (
        _BUCKET.fullmatch(bucket) is None
        or ".." in bucket
        or ".-" in bucket
        or "-." in bucket
        or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", bucket)
    ):
        raise NasBackupRemotePolicyError("S3 bucket is invalid")
    return bucket


def _prefix(value: Any) -> str:
    prefix = _safe_text(value, "S3 prefix", minimum=0, maximum=1024).strip("/")
    path = PurePosixPath(prefix)
    if "\\" in prefix or any(part in {"", ".", ".."} for part in path.parts):
        raise NasBackupRemotePolicyError("S3 prefix is invalid")
    return prefix


def _desired(value: Mapping[str, Any]) -> dict[str, Any]:
    operation = value.get("operation")
    if operation == "remove":
        if set(value) != {"schema", "operation", "remoteId"}:
            raise NasBackupRemotePolicyError("remove request has an invalid schema")
        if value.get("schema") != DESIRED_SCHEMA:
            raise NasBackupRemotePolicyError("remote request schema is unsupported")
        return {
            "schema": DESIRED_SCHEMA,
            "operation": "remove",
            "remoteId": _remote_id(value.get("remoteId")),
        }
    expected = {
        "schema",
        "operation",
        "remoteId",
        "label",
        "endpoint",
        "region",
        "bucket",
        "prefix",
        "accessKeyId",
        "secretAccessKey",
    }
    if operation != "create" or set(value) != expected or value.get("schema") != DESIRED_SCHEMA:
        raise NasBackupRemotePolicyError("create request has an invalid schema")
    region = _safe_text(value.get("region"), "S3 region", minimum=1, maximum=63)
    if _REGION.fullmatch(region) is None:
        raise NasBackupRemotePolicyError("S3 region is invalid")
    return {
        "schema": DESIRED_SCHEMA,
        "operation": "create",
        "remoteId": _remote_id(value.get("remoteId")),
        "label": _safe_text(value.get("label"), "remote label", minimum=1, maximum=96),
        "endpoint": _endpoint(value.get("endpoint")),
        "region": region,
        "bucket": _bucket(value.get("bucket")),
        "prefix": _prefix(value.get("prefix")),
        "accessKeyId": _safe_text(
            value.get("accessKeyId"), "S3 access key", minimum=3, maximum=256
        ),
        "secretAccessKey": _safe_text(
            value.get("secretAccessKey"), "S3 secret key", minimum=8, maximum=4096
        ),
    }


def _credential_path(root: Path, remote_id: str) -> Path:
    return root / f"echo-rclone-backup-{remote_id}.conf"


def _mountpoint(root: Path, remote_id: str) -> Path:
    return root / remote_id


def _unit(remote_id: str) -> str:
    return f"echo-rclone-backup@{remote_id}.service"


def _runtime_identity(path: Path, *, trusted_uid: int, executable: bool) -> dict[str, Any]:
    payload = _read_file(path, trusted_uid=trusted_uid, maximum=MAX_TOOL_BYTES)
    if payload is None:
        raise OSError("remote mount runtime is missing")
    metadata = path.stat()
    if executable and os.name == "posix" and not metadata.st_mode & 0o111:
        raise OSError("remote mount runtime is not executable")
    return {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "size": metadata.st_size,
    }


def _schedule_uses_remote(remote_id: str) -> bool:
    _configured, config = schedule_runner.read_config(schedule_runner.CONFIG_PATH, owner=0)
    return config.get("repositoryMount") == str(_mountpoint(MOUNT_ROOT, remote_id))


def _context(
    desired_state: Mapping[str, Any],
    *,
    registry_path: Path,
    credential_root: Path,
    mount_root: Path,
    unit_path: Path,
    systemd_creds: Path,
    systemctl: Path,
    rclone: Path,
    fusermount3: Path,
    trusted_uid: int,
    binding_key: bytes,
    in_use_reader: Callable[[str], bool],
) -> dict[str, Any]:
    desired = _desired(desired_state)
    raw_registry, remotes = _read_registry(registry_path, trusted_uid=trusted_uid)
    remote_id = desired["remoteId"]
    current = next((item for item in remotes if item["id"] == remote_id), None)
    credential = _credential_path(credential_root, remote_id)
    credential_payload = _read_file(
        credential,
        trusted_uid=trusted_uid,
        maximum=MAX_CREDENTIAL_BYTES,
        exact_mode=0o600,
    )
    if desired["operation"] == "create":
        if current is not None or credential_payload is not None:
            raise NasBackupRemotePolicyError("remote id is already configured")
        if len(remotes) >= MAX_REMOTES:
            raise NasBackupRemotePolicyError("too many backup remotes are configured")
    elif current is None or credential_payload is None:
        raise NasBackupRemotePolicyError("remote id is not configured")
    in_use = in_use_reader(remote_id)
    if desired["operation"] == "remove" and in_use:
        raise NasBackupRemotePolicyError("disable and clear the NAS backup schedule first")
    if len(binding_key) < 32:
        raise OSError("remote credential binding key is unavailable")
    runtime = {
        "unit": _runtime_identity(unit_path, trusted_uid=trusted_uid, executable=False),
        "systemdCreds": _runtime_identity(systemd_creds, trusted_uid=trusted_uid, executable=True),
        "systemctl": _runtime_identity(systemctl, trusted_uid=trusted_uid, executable=True),
        "rclone": _runtime_identity(rclone, trusted_uid=trusted_uid, executable=True),
        "fusermount3": _runtime_identity(fusermount3, trusted_uid=trusted_uid, executable=True),
    }
    public_desired: dict[str, Any] = {
        "operation": desired["operation"],
        "remoteId": remote_id,
        "kind": "s3",
    }
    secret_binding = None
    if desired["operation"] == "create":
        public_desired["label"] = desired["label"]
        secret_binding = hmac.new(
            binding_key,
            _canonical(
                {
                    key: desired[key]
                    for key in (
                        "endpoint",
                        "region",
                        "bucket",
                        "prefix",
                        "accessKeyId",
                        "secretAccessKey",
                    )
                }
            ),
            hashlib.sha256,
        ).hexdigest()
    binding = {
        "schema": PLAN_SCHEMA,
        "desired": public_desired,
        "current": current,
        "registrySha256": hashlib.sha256(raw_registry or b"").hexdigest(),
        "credentialSha256": hashlib.sha256(credential_payload or b"").hexdigest(),
        "runtime": runtime,
        "secretBinding": secret_binding,
        "inUse": in_use,
        "mountpoint": str(_mountpoint(mount_root, remote_id)),
    }
    return {
        **binding,
        "desiredPrivate": desired,
        "registry": remotes,
        "registryPayload": raw_registry,
        "planId": hashlib.sha256(_canonical(binding)).hexdigest(),
    }


def _public_plan(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": PLAN_SCHEMA,
        "planId": context["planId"],
        "operation": context["desired"]["operation"],
        "requiresApproval": True,
        "desired": context["desired"],
        "mountpoint": context["mountpoint"],
        "pathsRedacted": True,
        "secretsRedacted": True,
        "safety": {
            "systemdEncryptedCredential": True,
            "httpsEndpointRequired": True,
            "dedicatedFusedMount": True,
            "backupScheduleMustBeClearedBeforeRemoval": True,
        },
    }


def plan_remote(
    desired_state: Mapping[str, Any],
    *,
    registry_path: Path = REGISTRY_PATH,
    credential_root: Path = CREDENTIAL_ROOT,
    mount_root: Path = MOUNT_ROOT,
    unit_path: Path = UNIT_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    systemctl: Path = SYSTEMCTL,
    rclone: Path = RCLONE,
    fusermount3: Path = FUSERMOUNT3,
    trusted_uid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    in_use_reader: Callable[[str], bool] = _schedule_uses_remote,
) -> dict[str, Any]:
    return _public_plan(
        _context(
            desired_state,
            registry_path=registry_path,
            credential_root=credential_root,
            mount_root=mount_root,
            unit_path=unit_path,
            systemd_creds=systemd_creds,
            systemctl=systemctl,
            rclone=rclone,
            fusermount3=fusermount3,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            in_use_reader=in_use_reader,
        )
    )


def _assert_directory(path: Path, *, trusted_uid: int, exact_mode: int | None = None) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != trusted_uid
        or (os.name == "posix" and exact_mode is not None and mode != exact_mode)
        or (os.name == "posix" and exact_mode is None and mode & 0o022)
    ):
        raise OSError("remote mount directory is unsafe")


def _atomic_write(path: Path, payload: bytes, *, uid: int, gid: int) -> None:
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
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _credential_config(desired: Mapping[str, Any]) -> bytes:
    target = desired["bucket"]
    if desired["prefix"]:
        target += f"/{desired['prefix']}"
    lines = [
        "[source]",
        "type = s3",
        "provider = Other",
        "env_auth = false",
        f"access_key_id = {desired['accessKeyId']}",
        f"secret_access_key = {desired['secretAccessKey']}",
        f"endpoint = {desired['endpoint']}",
        f"region = {desired['region']}",
        "acl = private",
        "",
        "[echo]",
        "type = alias",
        f"remote = source:{target}",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _encrypt_credential(
    desired: Mapping[str, Any],
    target: Path,
    *,
    systemd_creds: Path,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    os.close(descriptor)
    temporary = Path(name)
    try:
        completed = runner(
            [str(systemd_creds), "encrypt", "--name=rclone.conf", "-", str(temporary)],
            input=_credential_config(desired),
            capture_output=True,
            timeout=30,
            check=False,
        )
        if completed.returncode != 0 or not temporary.is_file() or temporary.stat().st_size < 1:
            raise OSError("remote credential encryption failed")
        temporary.chmod(0o600)
        os.replace(temporary, target)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("remote credential encryption failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _service_action(
    action: str,
    remote_id: str,
    *,
    systemctl: Path = SYSTEMCTL,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    arguments = {
        "start": ("enable", "--now", _unit(remote_id)),
        "stop": ("disable", "--now", _unit(remote_id)),
    }.get(action)
    if arguments is None:
        raise OSError("remote service action is invalid")
    try:
        completed = runner(
            [str(systemctl), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            check=False,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise OSError("remote service action failed") from exc
    if completed.returncode != 0:
        raise OSError("remote service action failed")


def _verify_mount(path: Path) -> bool:
    try:
        external_storage.verify_external_storage(
            destination=path,
            mountpoint=path,
            deployment_root=schedule_runner.DEPLOYMENT_ROOT,
            appliance_env=schedule_runner.APPLIANCE_ENV,
            state_root_override=schedule_runner.STATE_ROOT,
            nas_root_override=schedule_runner.NAS_ROOT,
        )
    except (OSError, external_storage.ExternalStorageError):
        return False
    return _mount_active(path)


def _mount_active(path: Path) -> bool:
    return any(
        row["mountpoint"] == str(path)
        and row["filesystem"] == "fuse.rclone"
        and "rw" in row["options"]
        for row in external_storage._mount_rows(MOUNTINFO)
    )


def _wait_for_mount_state(
    path: Path,
    expected: bool,
    reader: Callable[[Path], bool],
    *,
    timeout: float = 30.0,
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if reader(path) is expected:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def apply_remote(
    desired_state: Mapping[str, Any],
    plan_id: str,
    *,
    registry_path: Path = REGISTRY_PATH,
    credential_root: Path = CREDENTIAL_ROOT,
    mount_root: Path = MOUNT_ROOT,
    unit_path: Path = UNIT_PATH,
    systemd_creds: Path = SYSTEMD_CREDS,
    systemctl: Path = SYSTEMCTL,
    rclone: Path = RCLONE,
    fusermount3: Path = FUSERMOUNT3,
    trusted_uid: int = 0,
    trusted_gid: int = 0,
    binding_key: bytes = DEFAULT_BINDING_KEY,
    in_use_reader: Callable[[str], bool] = _schedule_uses_remote,
    credential_writer: Callable[[Mapping[str, Any], Path], None] | None = None,
    service_action: Callable[[str, str], None] | None = None,
    mount_state_reader: Callable[[Path], bool] = _mount_active,
    mount_verifier: Callable[[Path], bool] = _verify_mount,
) -> dict[str, Any]:
    if os.name == "posix" and os.geteuid() != trusted_uid:
        raise OSError("remote mount update requires root")
    credential_writer = credential_writer or (
        lambda desired, target: _encrypt_credential(desired, target, systemd_creds=systemd_creds)
    )
    service_action = service_action or (
        lambda action, remote_id: _service_action(action, remote_id, systemctl=systemctl)
    )
    with _LOCK:
        context = _context(
            desired_state,
            registry_path=registry_path,
            credential_root=credential_root,
            mount_root=mount_root,
            unit_path=unit_path,
            systemd_creds=systemd_creds,
            systemctl=systemctl,
            rclone=rclone,
            fusermount3=fusermount3,
            trusted_uid=trusted_uid,
            binding_key=binding_key,
            in_use_reader=in_use_reader,
        )
        if context["planId"] != plan_id:
            raise NasBackupRemotePolicyError("remote mount plan is stale; preview again")
        _assert_directory(registry_path.parent, trusted_uid=trusted_uid)
        _assert_directory(credential_root, trusted_uid=trusted_uid, exact_mode=0o700)
        _assert_directory(mount_root, trusted_uid=trusted_uid)
        remote_id = context["desired"]["remoteId"]
        credential = _credential_path(credential_root, remote_id)
        mountpoint = _mountpoint(mount_root, remote_id)
        previous_registry = context["registryPayload"]
        registry_existed = registry_path.exists()
        previous_credential = _read_file(
            credential,
            trusted_uid=trusted_uid,
            maximum=MAX_CREDENTIAL_BYTES,
            exact_mode=0o600,
        )
        operation = context["desired"]["operation"]
        created_mountpoint = False
        service_changed = False
        try:
            if operation == "create":
                if mountpoint.exists():
                    _assert_directory(mountpoint, trusted_uid=trusted_uid)
                    if next(mountpoint.iterdir(), None) is not None or mount_state_reader(
                        mountpoint
                    ):
                        raise OSError("remote mountpoint is not empty")
                else:
                    mountpoint.mkdir(mode=0o755)
                    created_mountpoint = True
                credential_writer(context["desiredPrivate"], credential)
                if (
                    _read_file(
                        credential,
                        trusted_uid=trusted_uid,
                        maximum=MAX_CREDENTIAL_BYTES,
                        exact_mode=0o600,
                    )
                    is None
                ):
                    raise OSError("remote encrypted credential was not written")
                remotes = sorted(
                    [
                        *context["registry"],
                        {
                            "id": remote_id,
                            "label": context["desiredPrivate"]["label"],
                            "kind": "s3",
                        },
                    ],
                    key=lambda item: item["id"],
                )
                _atomic_write(
                    registry_path,
                    _canonical({"schema": REGISTRY_SCHEMA, "remotes": remotes}) + b"\n",
                    uid=trusted_uid,
                    gid=trusted_gid,
                )
                service_changed = True
                service_action("start", remote_id)
                if not _wait_for_mount_state(mountpoint, True, mount_state_reader):
                    raise OSError("remote mount did not become active")
                if not mount_verifier(mountpoint):
                    raise OSError("remote mount verification failed")
            else:
                service_changed = True
                service_action("stop", remote_id)
                if not _wait_for_mount_state(mountpoint, False, mount_state_reader):
                    raise OSError("remote mount remained active")
                remotes = [item for item in context["registry"] if item["id"] != remote_id]
                _atomic_write(
                    registry_path,
                    _canonical({"schema": REGISTRY_SCHEMA, "remotes": remotes}) + b"\n",
                    uid=trusted_uid,
                    gid=trusted_gid,
                )
                credential.unlink()
                mountpoint.rmdir()
        except OSError as exc:
            rollback_errors: list[str] = []
            try:
                if registry_existed and previous_registry is not None:
                    _atomic_write(
                        registry_path, previous_registry, uid=trusted_uid, gid=trusted_gid
                    )
                else:
                    registry_path.unlink(missing_ok=True)
            except OSError:
                rollback_errors.append("registry")
            try:
                if previous_credential is None:
                    credential.unlink(missing_ok=True)
                else:
                    _atomic_write(credential, previous_credential, uid=trusted_uid, gid=trusted_gid)
            except OSError:
                rollback_errors.append("credential")
            try:
                if service_changed:
                    service_action("stop" if operation == "create" else "start", remote_id)
            except OSError:
                rollback_errors.append("service")
            if created_mountpoint:
                try:
                    mountpoint.rmdir()
                except OSError:
                    rollback_errors.append("mountpoint")
            if rollback_errors:
                raise OSError("remote mount update failed and rollback was incomplete") from exc
            raise OSError("remote mount update failed and was rolled back") from exc
        return {
            **_public_plan(context),
            "applied": True,
            "verified": True,
            "mounted": operation == "create",
        }


def list_remotes(
    *,
    registry_path: Path = REGISTRY_PATH,
    trusted_uid: int = 0,
    mount_root: Path = MOUNT_ROOT,
    mount_state_reader: Callable[[Path], bool] = _mount_active,
) -> dict[str, Any]:
    _raw, remotes = _read_registry(registry_path, trusted_uid=trusted_uid)
    return {
        "schema": STATUS_SCHEMA,
        "remotes": [
            {**item, "mounted": mount_state_reader(_mountpoint(mount_root, item["id"]))}
            for item in remotes
        ],
        "count": len(remotes),
        "pathsRedacted": True,
        "secretsRedacted": True,
    }


__all__ = [
    "CREDENTIAL_ROOT",
    "DEFAULT_BINDING_KEY",
    "DESIRED_SCHEMA",
    "MOUNT_ROOT",
    "NasBackupRemotePolicyError",
    "PLAN_SCHEMA",
    "REGISTRY_PATH",
    "STATUS_SCHEMA",
    "apply_remote",
    "list_remotes",
    "plan_remote",
]
