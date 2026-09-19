"""Plan-bound EXT4 creation and persistent mount for Echo-managed md RAID1.

The authority boundary is intentionally narrow: only a healthy array present
in the Echo-owned mdadm config block, with no filesystem signature and no
mount, may be formatted.  The result is mounted below ``/data`` by filesystem
UUID through one Echo-owned block in ``fstab``.
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

from appliance.native_mdraid import managed_mdraid1_arrays
from appliance.omv_protocol import EXT4_VOLUME_PLAN_SCHEMA, validate_ext4_volume_desired

_FSTAB_PATH = Path("/etc/fstab")
_MOUNT_ROOT = Path("/data")
_LOCK_PATH = Path("/run/lock/echo-os-ext4.lock")
_THREAD_LOCK = threading.RLock()
_BEGIN = "# BEGIN ECHO OS MANAGED EXT4"
_END = "# END ECHO OS MANAGED EXT4"
_MAX_CONFIG_BYTES = 256 * 1024
_MAX_OUTPUT_BYTES = 256 * 1024
_FS_UUID_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_EXPORT_KEY_PATTERN = re.compile(r"[A-Z0-9_]+")


def _canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _require_tools() -> None:
    missing = [
        name
        for name in (
            "blkid",
            "findmnt",
            "mdadm",
            "mkfs.ext4",
            "mount",
            "systemctl",
            "umount",
            "wipefs",
        )
        if shutil.which(name) is None
    ]
    if missing:
        raise OSError(f"native EXT4 volume tools are unavailable: {', '.join(missing)}")


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


def _safe_directory(path: Path, *, description: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise OSError(f"{description} is unavailable: {path}") from exc
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise OSError(f"{description} is unsafe")
    if os.name == "posix" and (info.st_uid != 0 or info.st_mode & 0o022):
        raise OSError(f"{description} ownership or mode is unsafe")
    return info


def _read_fstab(path: Path = _FSTAB_PATH) -> bytes | None:
    _safe_directory(path.parent, description="fstab directory")
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
        raise OSError("fstab is unsafe")
    payload = path.read_bytes()
    if len(payload) > _MAX_CONFIG_BYTES or b"\x00" in payload:
        raise OSError("fstab is invalid")
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OSError("fstab must be UTF-8") from exc
    return payload


def _managed_entries(text: str) -> tuple[int | None, int | None, list[str]]:
    lines = text.splitlines()
    begins = [index for index, line in enumerate(lines) if line == _BEGIN]
    ends = [index for index, line in enumerate(lines) if line == _END]
    if not begins and not ends:
        return None, None, []
    if len(begins) != 1 or len(ends) != 1 or begins[0] >= ends[0]:
        raise OSError("fstab has an invalid Echo EXT4 managed block")
    entries = lines[begins[0] + 1 : ends[0]]
    pattern = re.compile(
        r"UUID=[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12} "
        r"/data/[a-z][a-z0-9_-]{0,15} ext4 "
        r"defaults,nofail,x-systemd\.device-timeout=30s 0 2"
    )
    if any(pattern.fullmatch(entry) is None for entry in entries):
        raise OSError("fstab Echo EXT4 managed block contains an unsupported entry")
    return begins[0], ends[0], entries


def _fstab_conflicts(text: str, *, source: str, mountpoint: str) -> bool:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            raise OSError("fstab contains an invalid entry")
        same_device_alias = (
            fields[0].startswith("/dev/")
            and source.startswith("/dev/")
            and os.path.realpath(fields[0]) == os.path.realpath(source)
        )
        if fields[0] == source or same_device_alias or fields[1] == mountpoint:
            return True
    return False


def _render_fstab(original: bytes | None, *, filesystem_uuid: str, mountpoint: str) -> bytes:
    text = original.decode("utf-8") if original is not None else ""
    begin, end, entries = _managed_entries(text)
    source = f"UUID={filesystem_uuid}"
    if _fstab_conflicts(text, source=source, mountpoint=mountpoint):
        raise OSError("filesystem UUID or mountpoint is already registered")
    entry = f"{source} {mountpoint} ext4 defaults,nofail,x-systemd.device-timeout=30s 0 2"
    block = [_BEGIN, *sorted([*entries, entry]), _END]
    lines = text.splitlines()
    if begin is None or end is None:
        if lines and lines[-1] != "":
            lines.append("")
        lines.extend(block)
    else:
        lines[begin : end + 1] = block
    rendered = ("\n".join(lines).rstrip("\n") + "\n").encode("utf-8")
    if len(rendered) > _MAX_CONFIG_BYTES:
        raise OSError("fstab would exceed its safety limit")
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


def _restore_fstab(path: Path, original: bytes | None) -> None:
    if original is None:
        if path.is_symlink():
            raise OSError("refusing to unlink a replaced fstab symlink")
        path.unlink(missing_ok=True)
    else:
        _atomic_write(path, original)


@contextmanager
def _volume_transaction() -> Iterator[None]:
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
                raise OSError("native EXT4 volume lock is not a regular file")
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


def _has_signatures(device: str) -> bool:
    return bool(_run_checked("wipefs", "--noheadings", "--output", "TYPE", device).strip())


def _is_mounted(*, source: str | None = None, mountpoint: str | None = None) -> bool:
    if (source is None) == (mountpoint is None):
        raise ValueError("exactly one mount lookup must be selected")
    args = ["findmnt", "--noheadings", "--raw"]
    args.extend(["--source", source] if source is not None else ["--mountpoint", mountpoint])
    completed = _run(*args)
    if completed.returncode == 0:
        return True
    if completed.returncode == 1 and not completed.stdout.strip():
        return False
    raise OSError("findmnt could not determine mount state")


def _mount_target_state(name: str, mount_root: Path) -> dict[str, str]:
    _safe_directory(mount_root, description="native volume mount root")
    target = mount_root / name
    try:
        info = target.lstat()
    except FileNotFoundError:
        return {"path": str(target), "kind": "absent"}
    return {
        "path": str(target),
        "kind": "directory" if stat.S_ISDIR(info.st_mode) and not target.is_symlink() else "other",
    }


def ext4_volume_candidates(*, mdadm_config_path: Path | None = None) -> list[dict[str, Any]]:
    _require_tools()
    kwargs = {} if mdadm_config_path is None else {"config_path": mdadm_config_path}
    candidates: list[dict[str, Any]] = []
    for array in managed_mdraid1_arrays(**kwargs):
        if _has_signatures(array["devicefile"]) or _is_mounted(source=array["devicefile"]):
            continue
        candidates.append(array)
    return candidates


def _build_plan(
    desired: dict[str, Any],
    *,
    fstab_path: Path,
    mount_root: Path,
    mdadm_config_path: Path | None,
) -> dict[str, Any]:
    _require_tools()
    candidates = ext4_volume_candidates(mdadm_config_path=mdadm_config_path)
    matches = [array for array in candidates if array["uuid"] == desired["arrayUuid"]]
    if len(matches) != 1:
        raise ValueError("managed md RAID1 is unavailable, not blank, or not healthy")
    array = matches[0]
    mount_target = _mount_target_state(desired["name"], mount_root)
    if mount_target["kind"] != "absent":
        raise ValueError("the derived EXT4 mountpoint already exists")
    fstab = _read_fstab(fstab_path)
    fstab_text = fstab.decode("utf-8") if fstab is not None else ""
    _managed_entries(fstab_text)
    if _fstab_conflicts(fstab_text, source=array["devicefile"], mountpoint=mount_target["path"]):
        raise ValueError("the array or derived mountpoint is already registered")
    fstab_sha256 = hashlib.sha256(fstab or b"").hexdigest()
    material = {
        "schema": EXT4_VOLUME_PLAN_SCHEMA,
        "operation": "createAndMount",
        "desired": desired,
        "array": array,
        "mountpoint": mount_target["path"],
        "fstabSha256": fstab_sha256,
        "fstabExists": fstab is not None,
        "baseRevision": _canonical_hash(
            {"array": array, "mountTarget": mount_target, "fstabSha256": fstab_sha256}
        ),
    }
    return {
        **material,
        "planId": _canonical_hash(material),
        "requiresApproval": True,
        "changes": [
            {"operation": "format", "resource": "ext4Filesystem"},
            {"operation": "persistMount", "resource": "fstab"},
            {"operation": "mount", "resource": "filesystem"},
        ],
        "safety": {
            "destructive": True,
            "dataLossConfirmed": True,
            "source": "healthyBlankEchoManagedMdRaid1Only",
            "filesystem": "ext4Only",
            "mountRoot": str(mount_root),
            "persistentIdentity": "filesystemUuid",
            "force": False,
        },
        "rollback": "unmountRestoreFstabAndClearNewFilesystemSignature",
        "source": "native",
    }


def plan_ext4_volume(
    desired_state: dict[str, Any],
    *,
    fstab_path: Path = _FSTAB_PATH,
    mount_root: Path = _MOUNT_ROOT,
    mdadm_config_path: Path | None = None,
) -> dict[str, Any]:
    desired = validate_ext4_volume_desired(dict(desired_state))
    with _volume_transaction():
        return _build_plan(
            desired,
            fstab_path=fstab_path,
            mount_root=mount_root,
            mdadm_config_path=mdadm_config_path,
        )


def _parse_blkid_export(output: str, *, expected_label: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in output.splitlines():
        if not raw_line:
            continue
        if "=" not in raw_line:
            raise OSError("blkid returned invalid export data")
        key, value = raw_line.split("=", 1)
        if _EXPORT_KEY_PATTERN.fullmatch(key) is None or not value or len(value) > 256:
            raise OSError("blkid returned invalid export data")
        if key in fields:
            raise OSError("blkid returned duplicate export fields")
        fields[key] = value
    filesystem_uuid = fields.get("UUID", "").lower()
    if (
        fields.get("TYPE") != "ext4"
        or fields.get("LABEL") != expected_label
        or _FS_UUID_PATTERN.fullmatch(filesystem_uuid) is None
    ):
        raise OSError("new EXT4 filesystem identity could not be verified")
    return {"uuid": filesystem_uuid, "label": expected_label, "type": "ext4"}


def _verify_mount(*, target: str, mountpoint: str, filesystem_uuid: str) -> None:
    output = _run_checked(
        "findmnt",
        "--noheadings",
        "--raw",
        "--output",
        "SOURCE,FSTYPE,OPTIONS",
        "--mountpoint",
        mountpoint,
    ).strip()
    parts = output.split(None, 2)
    if len(parts) != 3:
        raise OSError("mounted EXT4 filesystem did not return a stable identity")
    source, fstype, options = parts
    option_tokens = {item.strip() for item in options.split(",") if item.strip()}
    source_matches = source == f"UUID={filesystem_uuid}" or os.path.realpath(
        source
    ) == os.path.realpath(target)
    if not source_matches or fstype != "ext4" or "rw" not in option_tokens or "ro" in option_tokens:
        raise OSError("mounted EXT4 filesystem did not retain the planned writable topology")


def _clear_new_filesystem(device: str) -> bool:
    try:
        if _is_mounted(source=device):
            return False
        completed = _run("wipefs", "--all", device)
        return completed.returncode == 0 and not _has_signatures(device)
    except OSError:
        return False


def apply_ext4_volume(
    desired_state: dict[str, Any],
    plan_id: str,
    *,
    fstab_path: Path = _FSTAB_PATH,
    mount_root: Path = _MOUNT_ROOT,
    mdadm_config_path: Path | None = None,
) -> dict[str, Any]:
    desired = validate_ext4_volume_desired(dict(desired_state))
    with _volume_transaction():
        plan = _build_plan(
            desired,
            fstab_path=fstab_path,
            mount_root=mount_root,
            mdadm_config_path=mdadm_config_path,
        )
        if plan["planId"] != plan_id:
            raise ValueError("EXT4 volume plan is stale; preview the change again")
        original_fstab = _read_fstab(fstab_path)
        if (original_fstab is not None) != plan["fstabExists"] or hashlib.sha256(
            original_fstab or b""
        ).hexdigest() != plan["fstabSha256"]:
            raise ValueError("EXT4 volume plan is stale; fstab changed")
        target = plan["array"]["devicefile"]
        mountpoint = Path(plan["mountpoint"])
        mountpoint_created = False
        filesystem_started = False
        fstab_written = False
        mounted = False
        mount_attempted = False
        try:
            mountpoint.mkdir(mode=0o755)
            mountpoint_created = True
            os.chmod(mountpoint, 0o755)  # nosec B103 - mountpoint is chowned to root:root on the next line; it must stay traversable
            if os.name == "posix":
                os.chown(mountpoint, 0, 0)
            filesystem_started = True
            _run_mutating("mkfs.ext4", "-L", desired["name"], "-m", "0", target, timeout=900.0)
            filesystem = _parse_blkid_export(
                _run_checked("blkid", "--probe", "--output", "export", target),
                expected_label=desired["name"],
            )
            rendered = _render_fstab(
                original_fstab,
                filesystem_uuid=filesystem["uuid"],
                mountpoint=str(mountpoint),
            )
            fstab_written = True
            _atomic_write(fstab_path, rendered)
            _run_checked("findmnt", "--verify", "--tab-file", str(fstab_path))
            _run_mutating("systemctl", "daemon-reload")
            mount_attempted = True
            _run_mutating("mount", str(mountpoint))
            mounted = True
            _verify_mount(
                target=target,
                mountpoint=str(mountpoint),
                filesystem_uuid=filesystem["uuid"],
            )
            if _read_fstab(fstab_path) != rendered:
                raise OSError("fstab write-back verification failed")
        except (OSError, ValueError) as exc:
            rollback_errors: list[str] = []
            if mount_attempted:
                try:
                    mounted = mounted or _is_mounted(mountpoint=str(mountpoint))
                    if mounted:
                        _run_mutating("umount", str(mountpoint))
                        if _is_mounted(mountpoint=str(mountpoint)):
                            raise OSError("EXT4 rollback unmount was not verified")
                except OSError:
                    rollback_errors.append("mount")
            if fstab_written:
                try:
                    _restore_fstab(fstab_path, original_fstab)
                    _run_mutating("systemctl", "daemon-reload")
                except OSError:
                    rollback_errors.append("fstab")
            if filesystem_started and not rollback_errors and not _clear_new_filesystem(target):
                rollback_errors.append("filesystem")
            if mountpoint_created and not rollback_errors:
                try:
                    mountpoint.rmdir()
                except OSError:
                    rollback_errors.append("mountpoint")
            if rollback_errors:
                raise OSError(
                    "EXT4 volume creation failed and rollback was incomplete: "
                    + ", ".join(rollback_errors)
                ) from exc
            raise OSError("EXT4 volume creation failed; new filesystem state was removed") from exc
        return {
            **plan,
            "applied": True,
            "verified": True,
            "filesystem": {
                **filesystem,
                "devicefile": target,
                "mountpoint": str(mountpoint),
                "readOnly": False,
            },
        }


__all__ = ["apply_ext4_volume", "ext4_volume_candidates", "plan_ext4_volume"]
