"""Encrypted, offline backup for Echo appliance state (never NAS user data).

The format is an authenticated AES-256-GCM envelope around a gzip tar archive.
Its key is derived with scrypt from ``ECHO_BACKUP_PASSPHRASE``. Export requires
the same exclusive state lock held by the running appliance, so two writers or
an online snapshot can never overlap.
Restore only creates a previously non-existent directory and therefore cannot
silently merge stale state into a live device.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import datetime as dt
import io
import json
import os
import re
import secrets
import struct
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from appliance.state_lock import LOCK_FILENAME, StateDirectoryLock, StateLockError
from appliance.state_schema import (
    CURRENT_SCHEMA_VERSION,
    LEGACY_SCHEMA_VERSION,
    inspect_state_schema,
)

MAGIC = b"ECHO-STATE-BACKUP-v1\n"
TAG_BYTES = 16
CHUNK_BYTES = 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_ARCHIVE_MEMBERS = 1_000_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024**3
# This is the environment-variable key, never the credential value itself.
PASSPHRASE_ENV = "ECHO_BACKUP_PASSPHRASE"  # nosec B105
MANIFEST_NAME = "echo-backup-manifest.json"
FORMAT_VERSION = 1
BACKUP_PREFIX_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class BackupError(RuntimeError):
    pass


def _passphrase(environment_name: str = PASSPHRASE_ENV) -> bytes:
    value = os.environ.get(environment_name, "")
    if len(value) < 12:
        raise BackupError(f"{environment_name} must contain at least 12 characters")
    encoded = value.encode("utf-8")
    if len(encoded) > 1024:
        raise BackupError(f"{environment_name} is unreasonably long")
    return encoded


def _backup_secret(value: bytes | None) -> bytes:
    if value is None:
        return _passphrase()
    if not 12 <= len(value) <= 4096:
        raise BackupError("backup passphrase must contain between 12 and 4096 bytes")
    return value


def _derived_key(passphrase: bytes, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase)


def _is_relative_to(path: Path, parent: Path) -> bool:
    with contextlib.suppress(ValueError):
        path.relative_to(parent)
        return True
    return False


def _portable_symlink(member_path: PurePosixPath, link_name: str) -> bool:
    link = PurePosixPath(link_name)
    if link.is_absolute() or "\\" in link_name:
        return False
    depth = 0
    for part in (*member_path.parent.parts, *link.parts):
        if part in {"", "."}:
            continue
        if part == "..":
            depth -= 1
            if depth < 0:
                return False
        else:
            depth += 1
    return True


def _validate_member(member: tarfile.TarInfo) -> None:
    name = member.name
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise BackupError(f"unsafe archive path: {name!r}")
    if not (member.isfile() or member.isdir() or member.issym()):
        raise BackupError(f"unsupported archive entry: {name!r}")
    if member.issym() and not _portable_symlink(path, member.linkname):
        raise BackupError(f"unsafe archive symlink: {name!r}")


def _archive_manifest(state_schema: dict[str, Any]) -> bytes:
    return json.dumps(
        {
            "format": FORMAT_VERSION,
            "kind": "echo-appliance-state",
            "createdAt": dt.datetime.now(dt.UTC).isoformat(),
            "nasUserDataIncluded": False,
            "stateMinimumCompatibleVersion": state_schema["minimumCompatibleVersion"],
            "stateSchemaVersion": state_schema["version"],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _tar_filter(
    state_root: Path,
    nas_root: Path | None,
    info: tarfile.TarInfo,
) -> tarfile.TarInfo | None:
    relative = PurePosixPath(info.name)
    candidate = state_root.joinpath(*relative.parts).resolve(strict=False)
    if nas_root is not None and (candidate == nas_root or _is_relative_to(candidate, nas_root)):
        return None
    if relative.name == LOCK_FILENAME:
        return None
    _validate_member(info)
    # Backups restore as the receiving appliance user; never preserve a source
    # host's numeric root ownership or setuid/setgid/sticky privilege bits.
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mode &= 0o777
    return info


def _create_archive(state_dir: Path, nas_root: Path | None, output: Path) -> dict[str, Any]:
    state_schema = inspect_state_schema(state_dir, require_compatible=False)
    manifest = _archive_manifest(state_schema)
    with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT, dereference=False) as archive:
        manifest_info = tarfile.TarInfo(MANIFEST_NAME)
        manifest_info.size = len(manifest)
        manifest_info.mode = 0o600
        manifest_info.mtime = 0
        archive.addfile(manifest_info, io.BytesIO(manifest))
        for entry in sorted(state_dir.iterdir(), key=lambda item: item.name):
            if entry.name == LOCK_FILENAME:
                continue
            resolved = entry.resolve(strict=False)
            if nas_root is not None and (
                resolved == nas_root or _is_relative_to(resolved, nas_root)
            ):
                continue
            archive.add(
                entry,
                arcname=entry.name,
                recursive=True,
                filter=lambda info: _tar_filter(state_dir, nas_root, info),
            )
    return _inspect_archive(output)


def _inspect_archive(path: Path) -> dict[str, Any]:
    seen: set[str] = set()
    symlinks: set[str] = set()
    member_count = 0
    unpacked_bytes = 0
    manifest: dict[str, Any] | None = None
    try:
        with tarfile.open(path, "r:gz") as archive:
            for member in archive:
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise BackupError("backup archive contains too many entries")
                _validate_member(member)
                if member.name in seen:
                    raise BackupError(f"duplicate archive path: {member.name!r}")
                seen.add(member.name)
                if member.issym():
                    symlinks.add(member.name)
                unpacked_bytes += max(0, int(member.size))
                if unpacked_bytes > MAX_UNCOMPRESSED_BYTES:
                    raise BackupError("backup archive expands beyond the safety limit")
                if member.name == MANIFEST_NAME:
                    extracted = archive.extractfile(member)
                    if extracted is None or member.size > MAX_HEADER_BYTES:
                        raise BackupError("backup manifest is unreadable")
                    payload = json.loads(extracted.read())
                    if not isinstance(payload, dict):
                        raise BackupError("backup manifest must be an object")
                    manifest = payload
    except (OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        raise BackupError("backup archive is corrupt") from exc
    if manifest is None:
        raise BackupError("backup manifest is missing")
    for name in seen:
        parent = PurePosixPath(name).parent
        while parent != PurePosixPath("."):
            if parent.as_posix() in symlinks:
                raise BackupError(f"archive entry traverses a symlink: {name!r}")
            parent = parent.parent
    if (
        manifest.get("format") != FORMAT_VERSION
        or manifest.get("kind") != "echo-appliance-state"
        or manifest.get("nasUserDataIncluded") is not False
    ):
        raise BackupError("backup manifest is incompatible")
    schema_version = manifest.get("stateSchemaVersion", LEGACY_SCHEMA_VERSION)
    schema_minimum = manifest.get("stateMinimumCompatibleVersion", LEGACY_SCHEMA_VERSION)
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < LEGACY_SCHEMA_VERSION
        or isinstance(schema_minimum, bool)
        or not isinstance(schema_minimum, int)
        or schema_minimum < LEGACY_SCHEMA_VERSION
        or schema_minimum > schema_version
    ):
        raise BackupError("backup state schema version is invalid")
    return {
        "format": FORMAT_VERSION,
        "members": member_count,
        "unpackedBytes": unpacked_bytes,
        "createdAt": manifest.get("createdAt"),
        "nasUserDataIncluded": False,
        "stateSchemaVersion": schema_version,
        "stateCompatible": (
            schema_version <= CURRENT_SCHEMA_VERSION and schema_minimum <= CURRENT_SCHEMA_VERSION
        ),
    }


def _envelope_header(*, salt: bytes, nonce: bytes, payload_bytes: int) -> bytes:
    return json.dumps(
        {
            "format": FORMAT_VERSION,
            "cipher": "AES-256-GCM",
            "kdf": "scrypt",
            "scryptN": 2**15,
            "scryptR": 8,
            "scryptP": 1,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "payloadBytes": payload_bytes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _write_encrypted(archive_path: Path, destination: Path, passphrase: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(12)
    header = _envelope_header(
        salt=salt,
        nonce=nonce,
        payload_bytes=archive_path.stat().st_size,
    )
    if len(header) > MAX_HEADER_BYTES:
        raise BackupError("backup envelope header is too large")
    prefix = MAGIC + struct.pack(">I", len(header)) + header
    key = _derived_key(passphrase, salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise BackupError(f"backup destination already exists: {destination}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output, archive_path.open("rb") as source:
            output.write(prefix)
            while block := source.read(CHUNK_BYTES):
                output.write(encryptor.update(block))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        with contextlib.suppress(OSError):
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _read_exact(source: BinaryIO, count: int) -> bytes:
    value = source.read(count)
    if len(value) != count:
        raise BackupError("backup envelope is truncated")
    return value


def _read_header(source: BinaryIO, total_size: int) -> tuple[bytes, dict[str, Any], bytes, bytes]:
    magic = _read_exact(source, len(MAGIC))
    if magic != MAGIC:
        raise BackupError("not an Echo appliance backup")
    header_size = struct.unpack(">I", _read_exact(source, 4))[0]
    if not 1 <= header_size <= MAX_HEADER_BYTES:
        raise BackupError("backup envelope header is invalid")
    header_bytes = _read_exact(source, header_size)
    try:
        header = json.loads(header_bytes)
        if not isinstance(header, dict):
            raise TypeError("header must be an object")
        salt = base64.b64decode(header["salt"], validate=True)
        nonce = base64.b64decode(header["nonce"], validate=True)
        payload_bytes = int(header["payloadBytes"])
    except (binascii.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("backup envelope header is invalid") from exc
    if (
        header.get("format") != FORMAT_VERSION
        or header.get("cipher") != "AES-256-GCM"
        or header.get("kdf") != "scrypt"
        or header.get("scryptN") != 2**15
        or header.get("scryptR") != 8
        or header.get("scryptP") != 1
        or len(salt) != 16
        or len(nonce) != 12
        or payload_bytes < 1
    ):
        raise BackupError("backup envelope parameters are unsupported")
    prefix = MAGIC + struct.pack(">I", header_size) + header_bytes
    expected = len(prefix) + payload_bytes + TAG_BYTES
    if total_size != expected:
        raise BackupError("backup envelope size does not match its header")
    return prefix, header, salt, nonce


def _decrypt_to(backup_path: Path, output_path: Path, passphrase: bytes) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if backup_path.is_symlink() or not backup_path.is_file():
        raise BackupError(f"backup file is missing or unsafe: {backup_path}")
    total_size = backup_path.stat().st_size
    try:
        with backup_path.open("rb") as source:
            prefix, header, salt, nonce = _read_header(source, total_size)
            payload_bytes = int(header["payloadBytes"])
            source.seek(total_size - TAG_BYTES)
            tag = _read_exact(source, TAG_BYTES)
            source.seek(len(prefix))
            decryptor = Cipher(
                algorithms.AES(_derived_key(passphrase, salt)),
                modes.GCM(nonce, tag),
            ).decryptor()
            decryptor.authenticate_additional_data(prefix)
            remaining = payload_bytes
            with output_path.open("wb") as output:
                while remaining:
                    block = source.read(min(CHUNK_BYTES, remaining))
                    if not block:
                        raise BackupError("backup ciphertext is truncated")
                    remaining -= len(block)
                    output.write(decryptor.update(block))
                output.write(decryptor.finalize())
    except InvalidTag as exc:
        raise BackupError("backup authentication failed (wrong passphrase or tampering)") from exc
    except OSError as exc:
        raise BackupError("backup could not be decrypted") from exc


def export_backup(
    state_dir: Path | str,
    destination: Path | str,
    *,
    nas_root: Path | str | None = None,
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    state = Path(state_dir).resolve()
    target = Path(destination).resolve(strict=False)
    nas = Path(nas_root).resolve(strict=False) if nas_root else None
    if not state.is_dir():
        raise BackupError(f"state directory does not exist: {state}")
    if target == state or _is_relative_to(target, state):
        raise BackupError("backup destination must be outside the state directory")
    if nas is not None and (target == nas or _is_relative_to(target, nas)):
        raise BackupError("backup destination must be outside NAS user data")
    secret = _backup_secret(passphrase)
    try:
        with (
            StateDirectoryLock.acquire(state, exclusive=True),
            tempfile.TemporaryDirectory(prefix="echo-state-backup-") as temporary,
        ):
            archive_path = Path(temporary) / "state.tar.gz"
            report = _create_archive(state, nas, archive_path)
            _write_encrypted(archive_path, target, secret)
    except StateLockError as exc:
        raise BackupError(str(exc)) from exc
    return {**report, "encrypted": True, "outputBytes": target.stat().st_size}


def verify_backup(
    backup_path: Path | str,
    *,
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    source = Path(backup_path)
    secret = _backup_secret(passphrase)
    with tempfile.TemporaryDirectory(prefix="echo-state-verify-") as temporary:
        archive_path = Path(temporary) / "state.tar.gz"
        _decrypt_to(source, archive_path, secret)
        return {**_inspect_archive(archive_path), "encrypted": True}


def _extract_archive(archive_path: Path, destination: Path) -> None:
    """Extract already-validated regular files/directories, then safe symlinks."""

    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                _validate_member(member)
                if member.name == MANIFEST_NAME or member.issym():
                    continue
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError(f"backup file is unreadable: {member.name!r}")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(target, flags, member.mode & 0o777)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        while block := source.read(CHUNK_BYTES):
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    source.close()
                target.chmod(member.mode & 0o777)
            for member in members:
                if not member.issym():
                    continue
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(member.linkname, target)
            directories = sorted(
                (member for member in members if member.isdir()),
                key=lambda member: len(PurePosixPath(member.name).parts),
                reverse=True,
            )
            for member in directories:
                destination.joinpath(*PurePosixPath(member.name).parts).chmod(member.mode & 0o777)
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("backup archive could not be restored safely") from exc


def restore_backup(
    backup_path: Path | str,
    target_dir: Path | str,
    *,
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    source = Path(backup_path)
    target = Path(target_dir).resolve(strict=False)
    if target.exists() or target.is_symlink():
        raise BackupError("restore target must not already exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    secret = _backup_secret(passphrase)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}.restore-", dir=target.parent))
    try:
        archive_path = staging / ".echo-restore-payload.tar.gz"
        try:
            _decrypt_to(source, archive_path, secret)
            report = _inspect_archive(archive_path)
            _extract_archive(archive_path, staging)
        finally:
            with contextlib.suppress(FileNotFoundError):
                archive_path.unlink()
        os.replace(staging, target)
        with contextlib.suppress(OSError):
            directory = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        import shutil

        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {**report, "encrypted": True, "restoredTo": str(target)}


def prune_backup_set(
    directory: Path | str,
    *,
    keep: int = 7,
    prefix: str = "echo-state",
    passphrase: bytes | None = None,
) -> dict[str, Any]:
    """Keep the newest verified rotation set and remove only managed backups."""

    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise BackupError(f"backup directory is missing or unsafe: {root}")
    if not 2 <= keep <= 10_000:
        raise BackupError("backup retention must keep between 2 and 10000 files")
    if BACKUP_PREFIX_PATTERN.fullmatch(prefix) is None:
        raise BackupError("backup prefix contains unsafe characters")
    filename_pattern = re.compile(rf"{re.escape(prefix)}-\d{{8}}T\d{{6}}Z\.echo-backup")
    candidates = sorted(
        (
            entry
            for entry in root.iterdir()
            if filename_pattern.fullmatch(entry.name) and not entry.is_symlink() and entry.is_file()
        ),
        key=lambda entry: entry.name,
    )
    if len(candidates) <= keep:
        return {
            "matched": len(candidates),
            "keep": keep,
            "deleted": [],
            "verifiedNewest": None,
        }

    # Never age out an older generation until the newest retained backup can be
    # fully authenticated, decrypted and structurally inspected.
    newest = candidates[-1]
    verify_backup(newest, passphrase=_backup_secret(passphrase))
    removed: list[str] = []
    for entry in candidates[:-keep]:
        if entry.is_symlink() or not entry.is_file():
            raise BackupError(f"backup changed during retention: {entry.name}")
        try:
            entry.unlink()
        except OSError as exc:
            raise BackupError(f"could not retire backup: {entry.name}") from exc
        removed.append(entry.name)
    with contextlib.suppress(OSError):
        descriptor = os.open(root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {
        "matched": len(candidates),
        "keep": keep,
        "deleted": removed,
        "verifiedNewest": newest.name,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Encrypted Echo appliance state backup")
    parser.add_argument(
        "--passphrase-env",
        default=PASSPHRASE_ENV,
        help="environment variable containing the backup passphrase",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export")
    export.add_argument("backup", type=Path)
    export.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("ECHO_DATA_DIR") or "/data"),
    )
    export.add_argument(
        "--nas-root",
        type=Path,
        default=Path(os.environ.get("ECHO_NAS_ROOT") or "/data/nas"),
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("backup", type=Path)
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("target", type=Path)
    prune = subparsers.add_parser("prune")
    prune.add_argument("directory", type=Path)
    prune.add_argument("--keep", type=int, default=7)
    prune.add_argument("--prefix", default="echo-state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        secret = _passphrase(args.passphrase_env)
        if args.command == "export":
            report = export_backup(
                args.state_dir,
                args.backup,
                nas_root=args.nas_root,
                passphrase=secret,
            )
        elif args.command == "verify":
            report = verify_backup(args.backup, passphrase=secret)
        elif args.command == "restore":
            report = restore_backup(args.backup, args.target, passphrase=secret)
        else:
            report = prune_backup_set(
                args.directory,
                keep=args.keep,
                prefix=args.prefix,
                passphrase=secret,
            )
    except BackupError as exc:
        print(f"Echo state backup failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BackupError",
    "export_backup",
    "main",
    "prune_backup_set",
    "restore_backup",
    "verify_backup",
]
