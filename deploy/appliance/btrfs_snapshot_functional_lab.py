#!/usr/bin/env python3
"""Exercise the native Btrfs snapshot write plane on a disposable loop filesystem.

This lab is intentionally independent of the release operations bundle.  It is
for an isolated appliance VM: it creates one loop-backed Btrfs filesystem below
``/mnt``, drives the public HTTP plan/approval/apply contract, verifies the
result with Btrfs tooling, and restores the three global policy registries byte
for byte in a ``finally`` block.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import platform
import shutil
import stat
import subprocess  # nosec B404 - fixed tool paths and validated private paths
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows imports exercise pure helpers only
    fcntl = None  # type: ignore[assignment]

REGISTRY_PATH = Path("/var/lib/echo-os/native-shared-folders.json")
LOCK_POLICY_PATH = Path("/var/lib/echo-os/btrfs-snapshot-locks.json")
SCHEDULE_POLICY_PATH = Path("/etc/echo-os/btrfs-snapshot-schedule.json")
AUTH_PATH = Path("/data/appliance-auth.json")
LAB_LOCK_PATH = Path("/run/echo-btrfs-snapshot-functional-lab.lock")
MOUNT_ROOT = Path("/mnt")
IMAGE_ROOT = Path("/var/tmp")
IMAGE_BYTES = 512 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
SHA256_LENGTH = 64


class BtrfsSnapshotFunctionalLabError(RuntimeError):
    """The disposable functional lab was unsafe or did not verify."""


HttpCaller = Callable[
    [str, str, str, Mapping[str, Any] | None, str | None, Mapping[str, str] | None],
    tuple[int, Mapping[str, Any]],
]
CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class SavedFile:
    path: Path
    existed: bool
    payload: bytes | None
    mode: int | None


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _loopback_origin(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise BtrfsSnapshotFunctionalLabError("appliance URL is invalid") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise BtrfsSnapshotFunctionalLabError("appliance URL must be one loopback origin")
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
    connection = connection_type(parsed.hostname, parsed.port, timeout=120)
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        status_code = response.status
    finally:
        connection.close()
    if len(raw) > MAX_RESPONSE_BYTES:
        raise BtrfsSnapshotFunctionalLabError("appliance response exceeds the safety bound")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BtrfsSnapshotFunctionalLabError("appliance returned a non-JSON response") from exc
    if not isinstance(value, dict):
        raise BtrfsSnapshotFunctionalLabError("appliance returned a non-object response")
    return status_code, value


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
    status_code, value = caller(base_url, method, path, payload, token, headers)
    if status_code != expected:
        detail = value.get("detail")
        suffix = f": {detail}" if isinstance(detail, str) and len(detail) <= 256 else ""
        raise BtrfsSnapshotFunctionalLabError(
            f"appliance {method} {path} returned HTTP {status_code}, expected {expected}{suffix}"
        )
    return dict(value)


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
        raise BtrfsSnapshotFunctionalLabError("appliance returned an invalid approval")
    return approval


def _planned_apply(
    caller: HttpCaller,
    base_url: str,
    token: str,
    password: str,
    *,
    desired: Mapping[str, Any],
    plan_path: str,
    apply_path: str,
    action: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _request(caller, base_url, "POST", plan_path, expected=200, payload=desired, token=token)
    plan_id = plan.get("planId")
    if (
        not isinstance(plan_id, str)
        or len(plan_id) != SHA256_LENGTH
        or any(character not in "0123456789abcdef" for character in plan_id)
        or plan.get("requiresApproval") is not True
    ):
        raise BtrfsSnapshotFunctionalLabError("appliance returned an invalid mutation plan")
    approval = _approve(caller, base_url, token, password, action, plan_id)
    result = _request(
        caller,
        base_url,
        "POST",
        apply_path,
        expected=200,
        payload={"desired": dict(desired), "planId": plan_id},
        token=token,
        headers={"X-Echo-Approval": approval},
    )
    if result.get("verified") is not True or result.get("planId") != plan_id:
        raise BtrfsSnapshotFunctionalLabError("appliance mutation was not verified")
    return plan, result


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        list(command),
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        env={
            **os.environ,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        },
    )


def _checked(command: Sequence[str], runner: CommandRunner, label: str) -> str:
    completed = runner(command)
    if (
        completed.returncode != 0
        or len(completed.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES
        or len(completed.stderr.encode("utf-8")) > MAX_RESPONSE_BYTES
    ):
        raise BtrfsSnapshotFunctionalLabError(f"{label} failed")
    return completed.stdout


def _save_file(path: Path) -> SavedFile:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return SavedFile(path=path, existed=False, payload=None, mode=None)
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise BtrfsSnapshotFunctionalLabError(f"state file {path.name} is unsafe")
    if metadata.st_size > MAX_RESPONSE_BYTES:
        raise BtrfsSnapshotFunctionalLabError(f"state file {path.name} is too large")
    return SavedFile(
        path=path,
        existed=True,
        payload=path.read_bytes(),
        mode=stat.S_IMODE(metadata.st_mode),
    )


def _atomic_restore(saved: SavedFile) -> None:
    path = saved.path
    if not saved.existed:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise BtrfsSnapshotFunctionalLabError(f"cannot remove unsafe state file {path.name}")
        path.unlink()
        return
    assert saved.payload is not None and saved.mode is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.lab.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(saved.payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, saved.mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write(path: Path, payload: bytes, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.lab.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _set_test_password(
    saved: SavedFile,
    password: str,
    *,
    hasher: Callable[[str], str] | None = None,
) -> None:
    if not saved.existed or saved.payload is None:
        raise BtrfsSnapshotFunctionalLabError("appliance auth state is unavailable")
    try:
        auth = json.loads(saved.payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BtrfsSnapshotFunctionalLabError("appliance auth state is invalid") from exc
    accounts = auth.get("accounts") if isinstance(auth, dict) else None
    admin = accounts.get("admin") if isinstance(accounts, dict) else None
    if not isinstance(auth, dict) or not isinstance(admin, dict):
        raise BtrfsSnapshotFunctionalLabError("appliance auth state has no admin account")
    if hasher is None:
        from runtime.adapters.integrations.local_auth.config import hash_password

        hasher = hash_password
    password_hash = hasher(password)
    if not isinstance(password_hash, str) or not password_hash:
        raise BtrfsSnapshotFunctionalLabError("appliance password hashing failed")
    auth["password_hash"] = password_hash
    admin["password_hash"] = password_hash
    _atomic_write(saved.path, _canonical(auth), mode=0o600)


def _admin_token(
    saved: SavedFile,
    *,
    timestamp: int | None = None,
    encoder: Callable[[Mapping[str, Any], str], str] | None = None,
) -> str:
    """Mint a short-lived local operator token from the VM's existing secret.

    The Agent may own ``/api/auth/local/login`` from its YAML configuration,
    while the appliance approval plane deliberately owns ``appliance-auth``.
    A root-only lab uses the latter signing key directly so it never rewrites
    the Agent configuration merely to exercise appliance routes.
    """
    if not saved.existed or saved.payload is None:
        raise BtrfsSnapshotFunctionalLabError("appliance auth state is unavailable")
    try:
        auth = json.loads(saved.payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise BtrfsSnapshotFunctionalLabError("appliance auth state is invalid") from exc
    secret = auth.get("jwt_secret") if isinstance(auth, dict) else None
    if not isinstance(secret, str) or len(secret) < 32:
        raise BtrfsSnapshotFunctionalLabError("appliance auth state has no signing secret")
    issued_at = int(time.time()) if timestamp is None else timestamp
    claims = {
        "sub": "local:admin",
        "iat": issued_at,
        "exp": issued_at + 600,
        "iss": "echo-agent",
        "provider": "local",
        "username": "admin",
    }
    if encoder is None:
        from runtime.safety.auth.identity import encode_jwt_hs256

        def encoder(payload: Mapping[str, Any], key: str) -> str:
            return encode_jwt_hs256(payload, secret=key)

    token = encoder(claims, secret)
    if not isinstance(token, str) or not token:
        raise BtrfsSnapshotFunctionalLabError("appliance operator token could not be minted")
    return token


def _wait_health(caller: HttpCaller, base_url: str) -> None:
    for _attempt in range(30):
        try:
            status_code, value = caller(base_url, "GET", "/api/health", None, None, None)
            if status_code == 200 and isinstance(value, Mapping):
                return
        except OSError:
            pass
        time.sleep(2)
    raise BtrfsSnapshotFunctionalLabError("appliance health did not recover after restart")


def _safe_lab_paths(run_id: str) -> tuple[Path, Path]:
    if not run_id or any(character not in "0123456789abcdef" for character in run_id):
        raise BtrfsSnapshotFunctionalLabError("lab run id is invalid")
    image = IMAGE_ROOT / f"echo-btrfs-snapshot-lab-{run_id}.img"
    mountpoint = MOUNT_ROOT / f"echo-btrfs-snapshot-lab-{run_id}"
    if image.parent != IMAGE_ROOT or mountpoint.parent != MOUNT_ROOT:
        raise BtrfsSnapshotFunctionalLabError("lab paths escaped their private roots")
    if image.exists() or image.is_symlink() or mountpoint.exists() or mountpoint.is_symlink():
        raise BtrfsSnapshotFunctionalLabError("unique lab paths already exist")
    return image, mountpoint


def _target_for_mount(inventory: Mapping[str, Any], mountpoint: Path) -> str:
    targets = inventory.get("sharedFolderTargets")
    if not isinstance(targets, list):
        raise BtrfsSnapshotFunctionalLabError("shared-folder target inventory is invalid")
    matches = [
        target
        for target in targets
        if isinstance(target, dict)
        and target.get("label") == mountpoint.name
        and str(target.get("type", "")).casefold() == "btrfs"
        and target.get("readOnly") is False
    ]
    if len(matches) != 1:
        raise BtrfsSnapshotFunctionalLabError("disposable Btrfs target was not uniquely visible")
    reference = matches[0].get("mountPointRef")
    try:
        return str(uuid.UUID(str(reference)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BtrfsSnapshotFunctionalLabError("disposable target has no opaque UUID") from exc


def _snapshot(inventory: Mapping[str, Any], name: str, *, locked: bool) -> dict[str, Any]:
    snapshots = inventory.get("snapshots")
    if not isinstance(snapshots, list):
        raise BtrfsSnapshotFunctionalLabError("snapshot inventory is invalid")
    matches = [item for item in snapshots if isinstance(item, dict) and item.get("name") == name]
    if len(matches) != 1:
        raise BtrfsSnapshotFunctionalLabError(f"snapshot {name} was not uniquely inventoried")
    selected = matches[0]
    if selected.get("readOnly") is not True or selected.get("locked") is not locked:
        raise BtrfsSnapshotFunctionalLabError(f"snapshot {name} flags are invalid")
    try:
        uuid.UUID(str(selected.get("snapshotId")))
        uuid.UUID(str(selected.get("subvolumeUuid")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise BtrfsSnapshotFunctionalLabError(f"snapshot {name} identity is invalid") from exc
    return dict(selected)


def _assert_private_response(value: Mapping[str, Any], image: Path, mountpoint: Path) -> None:
    private = (str(image), str(mountpoint))

    def contains(candidate: Any) -> bool:
        if isinstance(candidate, str):
            return any(path in candidate for path in private)
        if isinstance(candidate, Mapping):
            return any(contains(key) or contains(item) for key, item in candidate.items())
        if isinstance(candidate, list):
            return any(contains(item) for item in candidate)
        return False

    if contains(value):
        raise BtrfsSnapshotFunctionalLabError("public API leaked a private host path")


def _delete_subvolume(path: Path, runner: CommandRunner) -> None:
    if path.is_symlink():
        raise BtrfsSnapshotFunctionalLabError("cleanup encountered a symlink")
    if path.exists():
        _checked(["btrfs", "subvolume", "delete", str(path)], runner, "subvolume cleanup")


def _cleanup(
    *,
    image: Path,
    mountpoint: Path,
    share_ref: str | None,
    source_name: str,
    recovered_name: str,
    snapshot_names: Sequence[str],
    mounted: bool,
    saved_files: Sequence[SavedFile],
    runner: CommandRunner,
) -> None:
    errors: list[Exception] = []
    if mounted and mountpoint.is_dir() and not mountpoint.is_symlink():
        candidates: list[Path] = []
        if share_ref:
            snapshot_root = mountpoint / ".echo-snapshots" / share_ref
            candidates.extend(snapshot_root / name for name in snapshot_names)
        candidates.extend((mountpoint / recovered_name, mountpoint / source_name))
        for candidate in candidates:
            try:
                _delete_subvolume(candidate, runner)
            except Exception as exc:
                errors.append(exc)
        for directory in (
            mountpoint / ".echo-snapshots" / str(share_ref or "missing"),
            mountpoint / ".echo-snapshots",
        ):
            try:
                if directory.is_dir() and not directory.is_symlink():
                    directory.rmdir()
            except OSError as exc:
                errors.append(exc)
        try:
            _checked(["umount", str(mountpoint)], runner, "lab unmount")
        except Exception as exc:
            errors.append(exc)
    for saved in reversed(saved_files):
        try:
            _atomic_restore(saved)
        except Exception as exc:
            errors.append(exc)
    try:
        _checked(
            ["systemctl", "restart", "echo-appliance.service"],
            runner,
            "appliance service restoration",
        )
    except Exception as exc:
        errors.append(exc)
    try:
        if mountpoint.exists() and not mountpoint.is_symlink():
            mountpoint.rmdir()
        if image.exists() and not image.is_symlink():
            image.unlink()
    except OSError as exc:
        errors.append(exc)
    if errors:
        raise BtrfsSnapshotFunctionalLabError("functional lab cleanup was incomplete") from errors[
            0
        ]


def _preflight(runner: CommandRunner) -> dict[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0:
        raise BtrfsSnapshotFunctionalLabError("functional lab requires Linux root")
    if not MOUNT_ROOT.is_dir() or MOUNT_ROOT.is_symlink():
        raise BtrfsSnapshotFunctionalLabError("/mnt is not a safe lab root")
    if not IMAGE_ROOT.is_dir() or IMAGE_ROOT.is_symlink():
        raise BtrfsSnapshotFunctionalLabError("/var/tmp is not a safe lab root")
    for tool in ("btrfs", "mkfs.btrfs", "mount", "truncate", "umount"):
        if shutil.which(tool) is None:
            raise BtrfsSnapshotFunctionalLabError(f"required tool {tool} is unavailable")
    version = _checked(["btrfs", "version"], runner, "Btrfs version probe").strip()
    return {"system": platform.system(), "kernel": platform.release(), "btrfs": version}


def run_lab(
    *,
    base_url: str,
    password: str,
    caller: HttpCaller = _http_call,
    runner: CommandRunner = _run,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one destructive-but-contained snapshot workflow and return public evidence."""
    base_url = _loopback_origin(base_url)
    if not password:
        raise BtrfsSnapshotFunctionalLabError("ECHO_ADMIN_PASSWORD must be set")
    host = _preflight(runner)
    run_id = uuid.uuid4().hex[:12]
    image, mountpoint = _safe_lab_paths(run_id)
    source_name = f"snapshot_lab_{run_id}"
    recovered_name = f"snapshot_restore_{run_id}"
    manual_name = f"manual_{run_id}"
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    old_time = timestamp - timedelta(days=2)
    old_auto_name = old_time.strftime("auto-%Y%m%dt%H%M%Sz")
    current_auto_name = timestamp.strftime("auto-%Y%m%dt%H%M%Sz")
    later_auto_name = (timestamp + timedelta(seconds=1)).strftime("auto-%Y%m%dt%H%M%Sz")
    snapshot_names = (manual_name, old_auto_name, current_auto_name, later_auto_name)
    saved_files: list[SavedFile] = []
    mounted = False
    share_ref: str | None = None
    LAB_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_descriptor = os.open(LAB_LOCK_PATH, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        try:
            if fcntl is None:
                raise BtrfsSnapshotFunctionalLabError("functional lab requires POSIX locking")
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BtrfsSnapshotFunctionalLabError(
                "another snapshot functional lab is active"
            ) from exc
        for path in (AUTH_PATH, REGISTRY_PATH, LOCK_POLICY_PATH, SCHEDULE_POLICY_PATH):
            saved_files.append(_save_file(path))
        _set_test_password(saved_files[0], password)
        _checked(
            ["systemctl", "restart", "echo-appliance.service"],
            runner,
            "appliance service restart",
        )
        _wait_health(caller, base_url)
        _checked(["truncate", "-s", str(IMAGE_BYTES), str(image)], runner, "image creation")
        _checked(["mkfs.btrfs", "-f", str(image)], runner, "Btrfs formatting")
        mountpoint.mkdir(mode=0o700)
        _checked(["mount", "-o", "loop", str(image), str(mountpoint)], runner, "Btrfs mount")
        mounted = True

        token = _admin_token(saved_files[0])
        sharing = _request(
            caller,
            base_url,
            "GET",
            "/api/appliance/omv/sharing",
            expected=200,
            token=token,
        )
        mount_ref = _target_for_mount(sharing, mountpoint)
        _assert_private_response(sharing, image, mountpoint)
        folder_desired = {
            "schema": "echo.omv.shared-folder-desired.v1",
            "mountPointRef": mount_ref,
            "name": source_name,
            "comment": "Disposable Btrfs snapshot functional lab",
        }
        folder_plan, folder_result = _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=folder_desired,
            plan_path="/api/appliance/omv/sharing/folders/plan",
            apply_path="/api/appliance/omv/sharing/folders/apply",
            action="omv.shared-folder.create",
        )
        share = folder_result.get("sharedFolder")
        if (
            not isinstance(share, dict)
            or share.get("snapshotCapable") is not True
            or not isinstance(share.get("uuid"), str)
        ):
            raise BtrfsSnapshotFunctionalLabError("created share was not snapshot capable")
        share_ref = str(uuid.UUID(share["uuid"]))
        source = mountpoint / source_name
        source_identity = _checked(
            ["btrfs", "subvolume", "show", str(source)], runner, "source subvolume probe"
        )
        marker = source / "snapshot-lab-marker.txt"
        marker.write_text("version-1\n", encoding="utf-8")

        manual_desired = {
            "schema": "echo.omv.btrfs-snapshot-desired.v1",
            "sharedFolderRef": share_ref,
            "name": manual_name,
        }
        try:
            _manual_plan, manual_result = _planned_apply(
                caller,
                base_url,
                token,
                password,
                desired=manual_desired,
                plan_path="/api/appliance/omv/sharing/snapshots/plan",
                apply_path="/api/appliance/omv/sharing/snapshots/apply",
                action="omv.btrfs-snapshot.create",
            )
        except BtrfsSnapshotFunctionalLabError as http_error:
            # The public route intentionally redacts storage OSError details.
            # Re-run the same guarded implementation in this root-only,
            # disposable process so VM evidence reports an actionable cause.
            from appliance import native_btrfs_snapshot

            try:
                diagnostic_plan = native_btrfs_snapshot.plan_snapshot(manual_desired)
                native_btrfs_snapshot.apply_snapshot(manual_desired, str(diagnostic_plan["planId"]))
            except Exception as direct_error:
                raise BtrfsSnapshotFunctionalLabError(
                    "manual snapshot HTTP apply failed; direct diagnostic: "
                    f"{type(direct_error).__name__}: {direct_error}"
                ) from http_error
            raise BtrfsSnapshotFunctionalLabError(
                "manual snapshot HTTP apply failed although the direct diagnostic succeeded"
            ) from http_error
        _assert_private_response(manual_result, image, mountpoint)
        inventory_path = f"/api/appliance/omv/sharing/{share_ref}/snapshots"
        inventory = _request(caller, base_url, "GET", inventory_path, expected=200, token=token)
        manual = _snapshot(inventory, manual_name, locked=False)
        if manual.get("kind") != "manual":
            raise BtrfsSnapshotFunctionalLabError("manual snapshot kind is invalid")
        snapshot_path = mountpoint / ".echo-snapshots" / share_ref / manual_name
        read_only = _checked(
            ["btrfs", "property", "get", "-ts", str(snapshot_path), "ro"],
            runner,
            "snapshot read-only probe",
        ).strip()
        if read_only != "ro=true":
            raise BtrfsSnapshotFunctionalLabError("manual snapshot is not read-only")

        marker.write_text("version-2\n", encoding="utf-8")
        restore_desired = {
            "schema": "echo.omv.btrfs-snapshot-restore-copy-desired.v1",
            "sharedFolderRef": share_ref,
            "snapshotId": manual["snapshotId"],
            "name": recovered_name,
        }
        _restore_plan, restore_result = _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=restore_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/restore-copy/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/restore-copy/apply",
            action="omv.btrfs-snapshot.restore-copy",
        )
        recovered = mountpoint / recovered_name
        recovered_marker = recovered / marker.name
        if marker.read_text(encoding="utf-8") != "version-2\n":
            raise BtrfsSnapshotFunctionalLabError("restore-copy changed the live source")
        if recovered_marker.read_text(encoding="utf-8") != "version-1\n":
            raise BtrfsSnapshotFunctionalLabError("restore-copy did not recover snapshot data")
        recovered_marker.write_text("writable-copy\n", encoding="utf-8")
        _assert_private_response(restore_result, image, mountpoint)

        lock_desired = {
            "schema": "echo.btrfs-snapshot-lock-desired.v1",
            "sharedFolderRef": share_ref,
            "snapshotId": manual["snapshotId"],
            "locked": True,
        }
        _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=lock_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/lock/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/lock/apply",
            action="omv.btrfs-snapshot.lock",
        )
        locked_inventory = _request(
            caller, base_url, "GET", inventory_path, expected=200, token=token
        )
        _snapshot(locked_inventory, manual_name, locked=True)
        delete_desired = {
            "schema": "echo.omv.btrfs-snapshot-delete-desired.v1",
            "sharedFolderRef": share_ref,
            "snapshotId": manual["snapshotId"],
        }
        refusal_status, _refusal = caller(
            base_url,
            "POST",
            "/api/appliance/omv/sharing/snapshots/delete/plan",
            delete_desired,
            token,
            None,
        )
        if refusal_status != 422:
            raise BtrfsSnapshotFunctionalLabError("locked snapshot deletion was not refused")
        unlock_desired = {**lock_desired, "locked": False}
        _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=unlock_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/lock/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/lock/apply",
            action="omv.btrfs-snapshot.lock",
        )
        _delete_plan, delete_result = _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=delete_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/delete/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/delete/apply",
            action="omv.btrfs-snapshot.delete",
        )
        if delete_result.get("snapshotDeleted") is not True or snapshot_path.exists():
            raise BtrfsSnapshotFunctionalLabError("manual snapshot deletion was not verified")

        schedule_desired = {
            "schema": "echo.btrfs-snapshot-schedule-desired.v2",
            "sharedFolderRef": share_ref,
            "enabled": True,
            "retention": {"mode": "days", "value": 1},
        }
        _schedule_plan, schedule_result = _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=schedule_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/schedule/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/schedule/apply",
            action="storage.btrfs.snapshot.schedule",
        )
        schedule_status = _request(
            caller,
            base_url,
            "GET",
            f"/api/appliance/omv/sharing/{share_ref}/snapshots/schedule",
            expected=200,
            token=token,
        )
        if (
            schedule_result.get("desired") != schedule_desired
            or schedule_status.get("enabled") is not True
            or schedule_status.get("retention") != {"mode": "days", "value": 1}
        ):
            raise BtrfsSnapshotFunctionalLabError("age retention policy was not applied")

        from deploy.appliance.btrfs_snapshot_schedule_runner import run_schedule

        old_run = run_schedule(now=old_time)
        if old_run != {"outcome": "completed", "created": 1, "pruned": 0, "errors": 0}:
            raise BtrfsSnapshotFunctionalLabError("old automatic snapshot run did not verify")
        old_inventory = _request(caller, base_url, "GET", inventory_path, expected=200, token=token)
        old_snapshot = _snapshot(old_inventory, old_auto_name, locked=False)
        if old_snapshot.get("kind") != "automatic":
            raise BtrfsSnapshotFunctionalLabError("scheduled snapshot kind is invalid")
        auto_lock_desired = {
            "schema": "echo.btrfs-snapshot-lock-desired.v1",
            "sharedFolderRef": share_ref,
            "snapshotId": old_snapshot["snapshotId"],
            "locked": True,
        }
        _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired=auto_lock_desired,
            plan_path="/api/appliance/omv/sharing/snapshots/lock/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/lock/apply",
            action="omv.btrfs-snapshot.lock",
        )
        protected_run = run_schedule(now=timestamp)
        if protected_run != {
            "outcome": "completed",
            "created": 1,
            "pruned": 0,
            "errors": 0,
        }:
            raise BtrfsSnapshotFunctionalLabError("locked retention run did not verify")
        protected_inventory = _request(
            caller, base_url, "GET", inventory_path, expected=200, token=token
        )
        _snapshot(protected_inventory, old_auto_name, locked=True)
        _snapshot(protected_inventory, current_auto_name, locked=False)
        _planned_apply(
            caller,
            base_url,
            token,
            password,
            desired={**auto_lock_desired, "locked": False},
            plan_path="/api/appliance/omv/sharing/snapshots/lock/plan",
            apply_path="/api/appliance/omv/sharing/snapshots/lock/apply",
            action="omv.btrfs-snapshot.lock",
        )
        prune_run = run_schedule(now=timestamp + timedelta(seconds=1))
        if prune_run != {"outcome": "completed", "created": 1, "pruned": 1, "errors": 0}:
            raise BtrfsSnapshotFunctionalLabError("age retention prune did not verify")
        final_inventory = _request(
            caller, base_url, "GET", inventory_path, expected=200, token=token
        )
        final_names = {
            item.get("name")
            for item in final_inventory.get("snapshots", [])
            if isinstance(item, dict)
        }
        if old_auto_name in final_names or not {current_auto_name, later_auto_name} <= final_names:
            raise BtrfsSnapshotFunctionalLabError("age retention host readback is invalid")
        _assert_private_response(final_inventory, image, mountpoint)

        filesystem_uuid = _checked(
            ["findmnt", "-n", "-o", "UUID", "-T", str(mountpoint)],
            runner,
            "filesystem UUID probe",
        ).strip()
        return {
            "schemaVersion": 1,
            "kind": "echo-btrfs-snapshot-functional-result",
            "outcome": "verified",
            "host": host,
            "filesystem": {"type": "btrfs", "uuid": filesystem_uuid, "imageBytes": IMAGE_BYTES},
            "http": {
                "planApprovalApply": True,
                "privatePathsHidden": True,
                "lockedDeleteRefused": True,
            },
            "snapshot": {
                "manualReadOnly": True,
                "sourceIdentityObserved": bool(source_identity.strip()),
                "restoreCopyPreservedPointInTime": True,
                "restoreCopyWritable": True,
                "manualDeleteVerified": True,
            },
            "retention": {
                "mode": "days",
                "value": 1,
                "lockedOldSnapshotPreserved": True,
                "unlockedOldSnapshotPruned": True,
            },
        }
    finally:
        cleanup_error: Exception | None = None
        try:
            _cleanup(
                image=image,
                mountpoint=mountpoint,
                share_ref=share_ref,
                source_name=source_name,
                recovered_name=recovered_name,
                snapshot_names=snapshot_names,
                mounted=mounted,
                saved_files=saved_files,
                runner=runner,
            )
        except Exception as exc:
            cleanup_error = exc
        try:
            os.close(lock_descriptor)
        finally:
            if cleanup_error is not None:
                raise cleanup_error


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)
    try:
        result = run_lab(
            base_url=args.base_url,
            password=os.environ.get("ECHO_ADMIN_PASSWORD", ""),
        )
    except BtrfsSnapshotFunctionalLabError as exc:
        print(f"Btrfs snapshot functional lab failed: {exc}", file=sys.stderr)
        return 2
    print(_canonical(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
