#!/usr/bin/env python3
"""Fail-closed entrypoint for one system-managed backup remote mount."""

from __future__ import annotations

import os
import re
import stat
import sys
from pathlib import Path

REMOTE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
RCLONE = Path("/usr/bin/rclone")
MOUNT_ROOT = Path("/mnt/echo-backup-remotes")
MAX_CREDENTIAL_BYTES = 256 * 1024


class RemoteMountError(RuntimeError):
    """The service instance cannot safely start."""


def _remote_id(value: str) -> str:
    if REMOTE_ID.fullmatch(value) is None:
        raise RemoteMountError("invalid remote instance id")
    return value


def _safe_directory(path: Path, *, owner: int = 0) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022)
    ):
        raise RemoteMountError("remote mount directory is unsafe")


def _safe_credential(path: Path, *, owner: int = 0) -> None:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != owner
        or (os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o022)
        or not 1 <= metadata.st_size <= MAX_CREDENTIAL_BYTES
    ):
        raise RemoteMountError("remote mount credential is unsafe")


def _already_mounted(path: Path, mountinfo: Path = Path("/proc/self/mountinfo")) -> bool:
    target = str(path)
    try:
        lines = mountinfo.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RemoteMountError("mount table is unavailable") from exc
    for line in lines:
        fields = line.split()
        if len(fields) >= 5 and fields[4].replace("\\040", " ") == target:
            return True
    return False


def command(
    remote_id: str,
    *,
    environ: dict[str, str] | None = None,
    rclone_path: Path = RCLONE,
    mount_root: Path = MOUNT_ROOT,
    mountinfo: Path = Path("/proc/self/mountinfo"),
    credential_root_prefix: str = "/run/credentials/",
    trusted_uid: int = 0,
) -> list[str]:
    remote_id = _remote_id(remote_id)
    environment = os.environ if environ is None else environ
    credential_root = environment.get("CREDENTIALS_DIRECTORY", "")
    if not credential_root.startswith(credential_root_prefix):
        raise RemoteMountError("systemd credential directory is unavailable")
    credential = Path(credential_root) / "rclone.conf"
    mountpoint = mount_root / remote_id
    _safe_directory(mount_root, owner=trusted_uid)
    _safe_directory(mountpoint, owner=trusted_uid)
    _safe_credential(credential, owner=trusted_uid)
    rclone = rclone_path.lstat()
    if (
        not stat.S_ISREG(rclone.st_mode)
        or stat.S_ISLNK(rclone.st_mode)
        or rclone.st_uid != trusted_uid
        or (os.name == "posix" and not rclone.st_mode & 0o111)
        or (os.name == "posix" and stat.S_IMODE(rclone.st_mode) & 0o022)
    ):
        raise RemoteMountError("rclone runtime is unsafe")
    if _already_mounted(mountpoint, mountinfo):
        raise RemoteMountError("remote mountpoint is already active")
    return [
        str(rclone_path),
        "mount",
        "echo:",
        str(mountpoint),
        f"--config={credential}",
        "--vfs-cache-mode=off",
        "--dir-cache-time=5m",
        "--poll-interval=1m",
        "--umask=0077",
        "--log-level=NOTICE",
    ]


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    try:
        if len(arguments) != 1:
            raise RemoteMountError("exactly one remote instance id is required")
        os.execv(str(RCLONE), command(arguments[0]))
    except (OSError, RemoteMountError) as exc:
        print(f"Echo backup remote mount refused: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
