#!/usr/bin/env python3
"""Atomically publish one authenticated Echo OS bundle to the stable channel."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
VERIFY_BUNDLE_PATH = HERE / "verify-update-bundle.py"
VERIFY_KEYRING_PATH = HERE.parent / "installer" / "verify_public_keyring.py"
GPGV_PATH = Path("/usr/bin/gpgv")
ARCHITECTURE = "x86-64"
CHANNEL_NAME = "stable"
SEQUENCE_MAX = 2**63 - 1
COPY_BLOCK_SIZE = 1024 * 1024
RELEASE_NAME = re.compile(r"^(?P<sequence>[0-9]{20})-(?P<version>[0-9A-Za-z][0-9A-Za-z.+:~_-]*)$")


class PublishError(ValueError):
    """Raised when a release cannot be published without weakening the channel."""


def load_module(path: Path, name: str) -> ModuleType:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise PublishError(f"{name} must be an absolute regular file")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PublishError(f"cannot load {name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def require_owned_directory(path: Path, label: str, *, create: bool = False) -> Path:
    if create:
        with contextlib.suppress(FileExistsError):
            path.mkdir(mode=0o755)
    if path.is_symlink() or not path.is_dir():
        raise PublishError(f"{label} must be a regular directory")
    metadata = path.stat()
    if metadata.st_uid != os.geteuid():
        raise PublishError(f"{label} must be owned by the publisher user")
    if stat.S_IMODE(metadata.st_mode) & 0o022:
        raise PublishError(f"{label} must not be writable by group or other")
    return path.resolve(strict=True)


def read_regular(path: Path, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise PublishError(f"{label} must be a bounded regular file")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(COPY_BLOCK_SIZE, remaining))
            if not chunk:
                raise PublishError(f"{label} was truncated while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise PublishError(f"{label} grew while being read")
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise PublishError(f"{label} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def release_name(sequence: int, version: str) -> str:
    if isinstance(sequence, bool) or not 1 <= sequence <= SEQUENCE_MAX:
        raise PublishError("publication sequence must be a positive 63-bit integer")
    name = f"{sequence:020d}-{version}"
    if RELEASE_NAME.fullmatch(name) is None or len(os.fsencode(name)) > 255:
        raise PublishError("release version cannot form one safe repository directory")
    return name


def expected_channel_target(name: str) -> str:
    return f"../releases/{ARCHITECTURE}/{name}"


def open_publish_lock(releases_root: Path) -> int:
    path = releases_root / ".publish.lock"
    descriptor = os.open(
        path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
        os.close(descriptor)
        raise PublishError("repository publication lock is unsafe")
    os.fchmod(descriptor, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise PublishError("another repository publication is already running") from error
    return descriptor


def prepare_repository(repository_root: Path) -> tuple[Path, Path, Path, int]:
    if not repository_root.is_absolute():
        raise PublishError("repository root must be an absolute path")
    root = require_owned_directory(repository_root, "repository root")
    releases = require_owned_directory(root / "releases", "releases root", create=True)
    lock = open_publish_lock(releases)
    try:
        release_arch = require_owned_directory(
            releases / ARCHITECTURE,
            "architecture release root",
            create=True,
        )
        stable = require_owned_directory(root / CHANNEL_NAME, "stable channel root", create=True)
    except Exception:
        os.close(lock)
        raise
    return root, release_arch, stable / ARCHITECTURE, lock


def parse_current_channel(
    channel: Path,
    release_root: Path,
) -> tuple[int, str, Path] | None:
    if not channel.exists() and not channel.is_symlink():
        return None
    if not channel.is_symlink():
        raise PublishError("stable channel must be one atomic relative symlink")
    target = os.readlink(channel)
    match = re.fullmatch(
        rf"\.\./releases/{re.escape(ARCHITECTURE)}/(?P<name>[^/]+)",
        target,
    )
    if match is None:
        raise PublishError("stable channel symlink has an unexpected target")
    name = match.group("name")
    release_match = RELEASE_NAME.fullmatch(name)
    if release_match is None or target != expected_channel_target(name):
        raise PublishError("stable channel release identity is malformed")
    sequence = int(release_match.group("sequence"), 10)
    version = release_match.group("version")
    if release_name(sequence, version) != name:
        raise PublishError("stable channel publication sequence is not canonical")
    release = release_root / name
    if release.is_symlink() or not release.is_dir():
        raise PublishError("stable channel points to a missing or unsafe release")
    if release.resolve(strict=True).parent != release_root.resolve(strict=True):
        raise PublishError("stable channel escapes its architecture release root")
    return sequence, version, release


def cleanup_abandoned_staging(release_root: Path, channel: Path) -> None:
    for child in release_root.iterdir():
        if not child.name.startswith(".incoming-"):
            continue
        if child.is_symlink() or not child.is_dir():
            raise PublishError("unsafe abandoned release staging entry")
        remove_private_tree(child)
    channel_parent = channel.parent
    if not channel_parent.exists():
        return
    prefix = f".{ARCHITECTURE}.incoming-"
    for child in channel_parent.iterdir():
        if not child.name.startswith(prefix):
            continue
        if not child.is_symlink():
            raise PublishError("unsafe abandoned channel staging entry")
        child.unlink()
    fsync_directory(release_root)
    fsync_directory(channel_parent)


def remove_private_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise PublishError("publisher cleanup target is not a private directory")
    os.chmod(path, 0o700)
    for child in path.iterdir():
        if child.is_symlink() or not child.is_file():
            raise PublishError("publisher staging contains an unsafe entry")
        child.unlink()
    path.rmdir()


def source_layout(
    bundle: Path,
    verifier: ModuleType,
) -> tuple[Path, dict[str, int], str]:
    if bundle.is_symlink() or not bundle.is_dir():
        raise PublishError("source bundle must be a regular directory")
    source = bundle.resolve(strict=True)
    manifest = read_regular(
        source / "SHA256SUMS",
        verifier.MAX_MANIFEST_SIZE,
        "source SHA256SUMS",
    )
    entries, version = verifier.parse_manifest(manifest)
    limits = {
        "SHA256SUMS": verifier.MAX_MANIFEST_SIZE,
        "SHA256SUMS.gpg": verifier.MAX_SIGNATURE_SIZE,
    }
    for kind, (name, _digest) in entries.items():
        limits[name] = verifier.PAYLOAD_LIMITS[kind]
    actual = {entry.name for entry in source.iterdir()}
    if actual != set(limits):
        raise PublishError("source bundle contains missing or unsigned extra entries")
    release_name(1, version)
    return source, limits, version


def copy_regular(source: Path, destination: Path, maximum: int) -> str:
    source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    destination_fd = -1
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode) or not 1 <= before.st_size <= maximum:
            raise PublishError(f"source artifact is outside its bound: {source.name}")
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o400,
        )
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(source_fd, min(COPY_BLOCK_SIZE, remaining))
            if not chunk:
                raise PublishError(f"source artifact was truncated: {source.name}")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    raise PublishError(f"cannot stage source artifact: {source.name}")
                view = view[written:]
            remaining -= len(chunk)
        if os.read(source_fd, 1):
            raise PublishError(f"source artifact grew while being staged: {source.name}")
        after = os.fstat(source_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise PublishError(f"source artifact changed while being staged: {source.name}")
        os.fsync(destination_fd)
        os.fchmod(destination_fd, 0o444)
        return digest.hexdigest()
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)


def bundle_digests(bundle: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for entry in sorted(bundle.iterdir(), key=lambda path: path.name):
        if entry.is_symlink() or not entry.is_file():
            raise PublishError("published release contains a non-regular entry")
        digest = hashlib.sha256()
        with entry.open("rb") as stream:
            for block in iter(lambda: stream.read(COPY_BLOCK_SIZE), b""):
                digest.update(block)
        result[entry.name] = digest.hexdigest()
    return result


def default_signature_verifier(bundle: Path, keyring: Path) -> None:
    if not GPGV_PATH.is_file() or GPGV_PATH.is_symlink() or not os.access(GPGV_PATH, os.X_OK):
        raise PublishError("/usr/bin/gpgv is required to publish an update")
    result = subprocess.run(
        [
            str(GPGV_PATH),
            "--keyring",
            str(keyring),
            str(bundle / "SHA256SUMS.gpg"),
            str(bundle / "SHA256SUMS"),
        ],
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if result.returncode != 0:
        raise PublishError("update manifest signature is not trusted by the release keyring")


def snapshot_keyring(keyring: Path, keyring_module: ModuleType) -> tempfile.TemporaryDirectory[str]:
    data = read_regular(
        keyring,
        keyring_module.MAX_KEYRING_SIZE,
        "release public keyring",
    )
    keyring_module.verify_public_keyring_bytes(data)
    temporary = tempfile.TemporaryDirectory(prefix="echo-update-publisher-keyring-")
    snapshot = Path(temporary.name) / "release-keyring.gpg"
    descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PublishError("cannot snapshot the release public keyring")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return temporary


def verify_release(
    release: Path,
    keyring_snapshot: Path,
    verifier: ModuleType,
    signature_verifier: Callable[[Path, Path], None],
) -> dict[str, object]:
    signature_verifier(release, keyring_snapshot)
    identity = verifier.verify_bundle_identity(release)
    for entry in release.iterdir():
        if stat.S_IMODE(entry.stat().st_mode) != 0o444:
            raise PublishError("published release files must be mode 0444")
    if stat.S_IMODE(release.stat().st_mode) != 0o555:
        raise PublishError("published release directory must be mode 0555")
    return identity


def stage_release(
    source: Path,
    limits: dict[str, int],
    release_root: Path,
    verifier: ModuleType,
    keyring_snapshot: Path,
    signature_verifier: Callable[[Path, Path], None],
) -> tuple[Path, dict[str, object]]:
    staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=release_root))
    os.chmod(staging, 0o700)
    try:
        for name in sorted(limits):
            copy_regular(source / name, staging / name, limits[name])
        fsync_directory(staging)
        signature_verifier(staging, keyring_snapshot)
        identity = verifier.verify_bundle_identity(staging)
        os.chmod(staging, 0o555)
        fsync_directory(staging)
        return staging, identity
    except Exception:
        remove_private_tree(staging)
        raise


def atomic_switch_channel(channel: Path, release: Path) -> None:
    target = expected_channel_target(release.name)
    temporary = channel.parent / f".{ARCHITECTURE}.incoming-{secrets.token_hex(12)}"
    os.symlink(target, temporary)
    try:
        os.replace(temporary, channel)
        fsync_directory(channel.parent)
    finally:
        if temporary.is_symlink():
            temporary.unlink()


def publish_repository(
    bundle: Path,
    keyring: Path,
    repository_root: Path,
    sequence: int,
    *,
    signature_verifier: Callable[[Path, Path], None] = default_signature_verifier,
) -> dict[str, object]:
    verifier = load_module(VERIFY_BUNDLE_PATH, "echo_update_bundle_verifier")
    keyring_module = load_module(VERIFY_KEYRING_PATH, "echo_update_keyring_verifier")
    source, limits, source_version = source_layout(bundle, verifier)
    name = release_name(sequence, source_version)
    _root, release_root, channel, lock = prepare_repository(repository_root)
    staging: Path | None = None
    keyring_temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        cleanup_abandoned_staging(release_root, channel)
        current = parse_current_channel(channel, release_root)
        if current is None:
            if sequence != 1:
                raise PublishError("the first stable publication must use sequence 1")
        else:
            current_sequence, current_version, _current_release = current
            if sequence == current_sequence:
                if source_version != current_version:
                    raise PublishError("one publication sequence cannot identify two versions")
            elif sequence != current_sequence + 1:
                raise PublishError("stable publication must advance by exactly one sequence")
            elif source_version == current_version:
                raise PublishError("a new publication sequence cannot replace the same version")

        keyring_temporary = snapshot_keyring(keyring, keyring_module)
        keyring_snapshot = Path(keyring_temporary.name) / "release-keyring.gpg"
        staging, identity = stage_release(
            source,
            limits,
            release_root,
            verifier,
            keyring_snapshot,
            signature_verifier,
        )
        if identity["version"] != source_version:
            raise PublishError("staged release changed version during verification")

        destination = release_root / name
        if destination.exists() or destination.is_symlink():
            if destination.is_symlink() or not destination.is_dir():
                raise PublishError("immutable release destination is unsafe")
            verify_release(
                destination,
                keyring_snapshot,
                verifier,
                signature_verifier,
            )
            if bundle_digests(destination) != bundle_digests(staging):
                raise PublishError("immutable release sequence/version already has different bytes")
            remove_private_tree(staging)
            staging = None
        else:
            os.replace(staging, destination)
            staging = None
            fsync_directory(release_root)

        current = parse_current_channel(channel, release_root)
        if current is None or current[2] != destination:
            atomic_switch_channel(channel, destination)
        verified = verify_release(
            destination,
            keyring_snapshot,
            verifier,
            signature_verifier,
        )
        source_identity = verified["source"]
        return {
            "sequence": sequence,
            "version": source_version,
            "release": str(destination),
            "channel": f"{CHANNEL_NAME}/{ARCHITECTURE}",
            "source_commit": source_identity["commit"],
        }
    finally:
        if staging is not None and staging.exists():
            remove_private_tree(staging)
        if keyring_temporary is not None:
            keyring_temporary.cleanup()
        os.close(lock)


def verify_current_repository(
    keyring: Path,
    repository_root: Path,
    *,
    signature_verifier: Callable[[Path, Path], None] = default_signature_verifier,
) -> dict[str, object]:
    verifier = load_module(VERIFY_BUNDLE_PATH, "echo_update_bundle_verifier")
    keyring_module = load_module(VERIFY_KEYRING_PATH, "echo_update_keyring_verifier")
    _root, release_root, channel, lock = prepare_repository(repository_root)
    keyring_temporary: tempfile.TemporaryDirectory[str] | None = None
    try:
        current = parse_current_channel(channel, release_root)
        if current is None:
            raise PublishError("stable update channel is unpublished")
        sequence, version, release = current
        keyring_temporary = snapshot_keyring(keyring, keyring_module)
        keyring_snapshot = Path(keyring_temporary.name) / "release-keyring.gpg"
        identity = verify_release(
            release,
            keyring_snapshot,
            verifier,
            signature_verifier,
        )
        if identity["version"] != version:
            raise PublishError("stable channel target and signed bundle version differ")
        return {
            "sequence": sequence,
            "version": version,
            "release": str(release),
            "channel": f"{CHANNEL_NAME}/{ARCHITECTURE}",
            "source_commit": identity["source"]["commit"],
        }
    finally:
        if keyring_temporary is not None:
            keyring_temporary.cleanup()
        os.close(lock)


def print_machine(result: dict[str, object]) -> None:
    print(
        result["sequence"],
        result["version"],
        result["source_commit"],
        result["channel"],
        result["release"],
        sep="\t",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--bundle", type=Path, required=True)
    publish.add_argument("--keyring", type=Path, required=True)
    publish.add_argument("--repository-root", type=Path, required=True)
    publish.add_argument("--sequence", type=int, required=True)
    publish.add_argument("--machine", action="store_true")
    verify = subparsers.add_parser("verify-current")
    verify.add_argument("--keyring", type=Path, required=True)
    verify.add_argument("--repository-root", type=Path, required=True)
    verify.add_argument("--machine", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "publish":
            result = publish_repository(
                args.bundle,
                args.keyring,
                args.repository_root,
                args.sequence,
            )
        else:
            result = verify_current_repository(args.keyring, args.repository_root)
    except (
        OSError,
        PublishError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(f"Echo OS update repository rejected: {error}", file=sys.stderr)
        return 1
    if args.machine:
        print_machine(result)
    else:
        print(
            (
                "ECHO_UPDATE_REPOSITORY_PUBLISHED"
                if args.command == "publish"
                else "ECHO_UPDATE_REPOSITORY_VERIFIED"
            ),
            f"sequence={result['sequence']}",
            f"version={result['version']}",
            f"source_commit={result['source_commit']}",
            f"channel={result['channel']}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
