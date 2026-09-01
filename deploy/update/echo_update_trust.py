#!/usr/bin/env python3
"""Create, verify and monotonically promote Echo OS update trust generations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

MAX_POLICY_BYTES = 64 * 1024
MAX_KEYRING_BYTES = 16 * 1024 * 1024
MAX_GENERATION = 2**31 - 1
FINGERPRINT = re.compile(r"^(?:[0-9A-F]{40}|[0-9A-F]{64})$")
POLICY_KEYS = {
    "schema",
    "generation",
    "keyring_sha256",
    "trusted_fingerprints",
    "retired_fingerprints",
}

SYSTEM_POLICY = Path("/usr/lib/echo-os/update-trust-policy.json")
SYSTEM_KEYRING = Path("/usr/lib/echo-os/update-keyring.gpg")
STATE_ROOT = Path("/var/lib/echo-os/update-trust")
KEYRING_VERIFIER = Path("/usr/lib/echo-os/verify-public-keyring.py")


class TrustError(RuntimeError):
    """Raised when an update trust transition is unsafe or inconsistent."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_regular(
    path: Path,
    maximum: int,
    label: str,
    *,
    expected_uid: int,
) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise TrustError(f"{label} is unavailable") from error
    try:
        before = os.fstat(descriptor)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != expected_uid
            or mode & 0o022
            or not 1 <= before.st_size <= maximum
        ):
            raise TrustError(f"{label} is empty, oversized, writable or unsafe")
        raw = bytearray()
        while len(raw) <= maximum:
            block = os.read(descriptor, min(1024 * 1024, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum
            or len(raw) != before.st_size
            or after.st_dev != before.st_dev
            or after.st_ino != before.st_ino
            or after.st_size != before.st_size
            or after.st_mtime_ns != before.st_mtime_ns
            or after.st_ctime_ns != before.st_ctime_ns
        ):
            raise TrustError(f"{label} changed while reading")
        return bytes(raw)
    finally:
        os.close(descriptor)


def load_verifier(path: Path) -> ModuleType:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise TrustError("public-keyring verifier must be an absolute regular file")
    specification = importlib.util.spec_from_file_location("echo_public_keyring", path)
    if specification is None or specification.loader is None:
        raise TrustError("cannot load the public-keyring verifier")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    if not hasattr(module, "verify_public_keyring_bytes"):
        raise TrustError("public-keyring verifier lacks its byte interface")
    return module


def verify_keyring_bytes(raw: bytes, verifier: ModuleType) -> None:
    if not 1 <= len(raw) <= MAX_KEYRING_BYTES:
        raise TrustError("update keyring is empty or oversized")
    try:
        verifier.verify_public_keyring_bytes(raw)
    except Exception as error:  # The verifier owns its concrete exception type.
        raise TrustError(f"update keyring is not a strict public-only keyring: {error}") from error


def canonical_policy(policy: dict[str, object]) -> bytes:
    return (json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()


def validate_fingerprint_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        if label == "retired fingerprints" and value == []:
            return []
        raise TrustError(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) or FINGERPRINT.fullmatch(item) is None for item in value):
        raise TrustError(f"{label} contains an invalid full fingerprint")
    normalized = sorted(set(value))
    if normalized != value:
        raise TrustError(f"{label} must be sorted and unique")
    return normalized


def parse_policy(raw: bytes) -> dict[str, object]:
    if not 1 <= len(raw) <= MAX_POLICY_BYTES:
        raise TrustError("update trust policy is empty or oversized")
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise TrustError("update trust policy is malformed") from error
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise TrustError("update trust policy has an invalid top-level contract")
    generation = policy.get("generation")
    digest = policy.get("keyring_sha256")
    if (
        policy.get("schema") != 1
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or not 1 <= generation <= MAX_GENERATION
        or not isinstance(digest, str)
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
    ):
        raise TrustError("update trust policy identity is invalid")
    trusted = validate_fingerprint_list(policy.get("trusted_fingerprints"), "trusted fingerprints")
    retired = validate_fingerprint_list(policy.get("retired_fingerprints"), "retired fingerprints")
    if set(trusted) & set(retired):
        raise TrustError("trusted and retired update fingerprints overlap")
    policy["trusted_fingerprints"] = trusted
    policy["retired_fingerprints"] = retired
    if raw != canonical_policy(policy):
        raise TrustError("update trust policy is not canonical JSON")
    return policy


def make_policy(
    keyring: bytes,
    generation: int,
    trusted: Sequence[str],
    retired: Sequence[str],
) -> dict[str, object]:
    policy: dict[str, object] = {
        "schema": 1,
        "generation": generation,
        "keyring_sha256": sha256(keyring),
        "trusted_fingerprints": sorted(set(trusted)),
        "retired_fingerprints": sorted(set(retired)),
    }
    return parse_policy(canonical_policy(policy))


def primary_fingerprints(gpg: Path, keyring: Path) -> list[str]:
    if not gpg.is_absolute() or gpg.is_symlink() or not gpg.is_file() or not os.access(gpg, os.X_OK):
        raise TrustError("gpg must be an absolute executable regular file")
    try:
        completed = subprocess.run(
            (
                str(gpg),
                "--batch",
                "--no-options",
                "--with-colons",
                "--show-keys",
                str(keyring),
            ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise TrustError("cannot inspect update-key fingerprints") from error
    if completed.returncode != 0 or len(completed.stdout) > 1024 * 1024:
        raise TrustError("gpg rejected the update public keyring")
    try:
        records = completed.stdout.decode("ascii").splitlines()
    except UnicodeError as error:
        raise TrustError("gpg returned non-ASCII key metadata") from error
    fingerprints: list[str] = []
    waiting_for_primary = False
    for record in records:
        fields = record.split(":")
        kind = fields[0] if fields else ""
        if kind == "pub":
            if len(fields) < 2 or fields[1] == "r":
                raise TrustError("revoked primary keys cannot remain in the active keyring")
            waiting_for_primary = True
        elif kind == "fpr" and waiting_for_primary:
            if len(fields) <= 9:
                raise TrustError("gpg omitted a primary-key fingerprint")
            fingerprint = fields[9].upper()
            if FINGERPRINT.fullmatch(fingerprint) is None:
                raise TrustError("gpg returned an invalid primary-key fingerprint")
            fingerprints.append(fingerprint)
            waiting_for_primary = False
    fingerprints = sorted(set(fingerprints))
    if waiting_for_primary or not fingerprints:
        raise TrustError("update keyring contains no complete primary public key")
    return fingerprints


def atomic_write(path: Path, raw: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise TrustError("cannot write update trust state")
            offset += written
        os.fsync(descriptor)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    os.replace(temporary, path)
    fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_pair(
    policy_path: Path,
    keyring_path: Path,
    verifier: ModuleType,
    *,
    expected_uid: int,
    label: str,
) -> tuple[dict[str, object], bytes]:
    policy = parse_policy(
        read_regular(policy_path, MAX_POLICY_BYTES, f"{label} policy", expected_uid=expected_uid)
    )
    keyring = read_regular(
        keyring_path, MAX_KEYRING_BYTES, f"{label} keyring", expected_uid=expected_uid
    )
    verify_keyring_bytes(keyring, verifier)
    if sha256(keyring) != policy["keyring_sha256"]:
        raise TrustError(f"{label} keyring does not match its policy")
    return policy, keyring


def validate_transition(previous: dict[str, object], candidate: dict[str, object]) -> None:
    previous_generation = int(previous["generation"])
    candidate_generation = int(candidate["generation"])
    if candidate_generation != previous_generation + 1:
        raise TrustError("update trust generations must advance exactly once")
    previous_trusted = set(previous["trusted_fingerprints"])
    previous_retired = set(previous["retired_fingerprints"])
    candidate_trusted = set(candidate["trusted_fingerprints"])
    candidate_retired = set(candidate["retired_fingerprints"])
    if not previous_retired <= candidate_retired:
        raise TrustError("a retired update fingerprint cannot become unretired")
    dropped = previous_trusted - candidate_trusted
    if not dropped <= candidate_retired:
        raise TrustError("removed update fingerprints must be explicitly retired")
    if not candidate_retired <= previous_retired | previous_trusted:
        raise TrustError("a trust transition retired a fingerprint it never trusted")


def require_private_directory(path: Path, expected_uid: int, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise TrustError(f"{label} is not a private directory")
    metadata = path.stat()
    if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise TrustError(f"{label} must be private and updater-owned")


def state_paths(root: Path) -> dict[str, Path]:
    return {
        "policy": root / "current-policy.json",
        "keyring": root / "update-keyring.gpg",
        "pending_policy": root / "pending-policy.json",
        "pending_keyring": root / "pending-keyring.gpg",
    }


def initialize_state_root(root: Path, expected_uid: int) -> None:
    if not root.is_absolute() or root.is_symlink():
        raise TrustError("managed update trust root must be an absolute non-symlink path")
    root.parent.mkdir(parents=True, exist_ok=True)
    parent_metadata = root.parent.stat()
    if root.parent.is_symlink() or parent_metadata.st_uid != expected_uid or stat.S_IMODE(
        parent_metadata.st_mode
    ) & 0o022:
        raise TrustError("managed update trust parent is writable or redirected")
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    require_private_directory(root, expected_uid, "managed update trust root")


@contextmanager
def promotion_lock(root: Path, expected_uid: int):  # noqa: ANN201
    lock_path = root / ".promotion.lock"
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise TrustError("cannot open the update trust promotion lock") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise TrustError("update trust promotion lock is unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise TrustError("another update trust promotion is running") from error
        yield
    finally:
        os.close(descriptor)


def clean_interrupted_temporaries(root: Path, expected_uid: int) -> None:
    prefixes = (".pending-keyring.gpg.", ".pending-policy.json.")
    removed = False
    for child in root.iterdir():
        if not child.name.startswith(prefixes):
            continue
        if child.is_symlink() or not child.is_file():
            raise TrustError("managed update trust contains an unsafe temporary entry")
        metadata = child.stat()
        if metadata.st_uid != expected_uid or stat.S_IMODE(metadata.st_mode) & 0o077:
            raise TrustError("managed update trust temporary is writable or foreign")
        child.unlink()
        removed = True
    if removed:
        fsync_directory(root)


def load_current(
    root: Path,
    verifier: ModuleType,
    *,
    expected_uid: int,
) -> tuple[dict[str, object], bytes] | None:
    paths = state_paths(root)
    present = (paths["policy"].exists(), paths["keyring"].exists())
    if present == (False, False):
        return None
    if present != (True, True):
        raise TrustError("managed update trust state is incomplete")
    return load_pair(
        paths["policy"],
        paths["keyring"],
        verifier,
        expected_uid=expected_uid,
        label="managed update trust",
    )


def recover_pending(root: Path, verifier: ModuleType, *, expected_uid: int) -> None:
    paths = state_paths(root)
    pending_policy = paths["pending_policy"].exists() or paths["pending_policy"].is_symlink()
    pending_keyring = paths["pending_keyring"].exists() or paths["pending_keyring"].is_symlink()
    if not pending_policy and not pending_keyring:
        return
    if pending_keyring and not pending_policy:
        if paths["pending_keyring"].is_symlink() or not paths["pending_keyring"].is_file():
            raise TrustError("managed update trust has an unsafe orphaned keyring")
        paths["pending_keyring"].unlink()
        fsync_directory(root)
        return
    candidate_policy = parse_policy(
        read_regular(
            paths["pending_policy"],
            MAX_POLICY_BYTES,
            "pending update trust policy",
            expected_uid=expected_uid,
        )
    )
    if pending_keyring:
        candidate_keyring = read_regular(
            paths["pending_keyring"],
            MAX_KEYRING_BYTES,
            "pending update keyring",
            expected_uid=expected_uid,
        )
        verify_keyring_bytes(candidate_keyring, verifier)
        if sha256(candidate_keyring) != candidate_policy["keyring_sha256"]:
            raise TrustError("pending update keyring does not match its policy")
        current = load_current(root, verifier, expected_uid=expected_uid)
        if current is not None:
            validate_transition(current[0], candidate_policy)
        os.replace(paths["pending_keyring"], paths["keyring"])
        fsync_directory(root)
    else:
        active_keyring = read_regular(
            paths["keyring"],
            MAX_KEYRING_BYTES,
            "managed update keyring",
            expected_uid=expected_uid,
        )
        verify_keyring_bytes(active_keyring, verifier)
        if sha256(active_keyring) != candidate_policy["keyring_sha256"]:
            raise TrustError("pending policy cannot recover the active keyring")
    os.replace(paths["pending_policy"], paths["policy"])
    fsync_directory(root)


def promote_locked(
    system_policy: dict[str, object],
    system_keyring: bytes,
    state_root: Path,
    verifier: ModuleType,
    expected_uid: int,
) -> tuple[str, dict[str, object]]:
    clean_interrupted_temporaries(state_root, expected_uid)
    recover_pending(state_root, verifier, expected_uid=expected_uid)
    current = load_current(state_root, verifier, expected_uid=expected_uid)
    if current is not None:
        current_policy, _current_keyring = current
        current_generation = int(current_policy["generation"])
        system_generation = int(system_policy["generation"])
        if system_generation < current_generation:
            return "retained", current_policy
        if system_generation == current_generation:
            if system_policy != current_policy:
                raise TrustError("one update trust generation has conflicting policies")
            return "current", current_policy
        validate_transition(current_policy, system_policy)
        source = "promoted"
    else:
        source = "bootstrap"

    paths = state_paths(state_root)
    atomic_write(paths["pending_keyring"], system_keyring, 0o400)
    atomic_write(paths["pending_policy"], canonical_policy(system_policy), 0o400)
    os.replace(paths["pending_keyring"], paths["keyring"])
    fsync_directory(state_root)
    os.replace(paths["pending_policy"], paths["policy"])
    fsync_directory(state_root)
    return source, system_policy


def promote(
    system_policy_path: Path,
    system_keyring_path: Path,
    state_root: Path,
    verifier_path: Path,
    *,
    expected_uid: int = 0,
) -> tuple[str, dict[str, object]]:
    verifier = load_verifier(verifier_path)
    system_policy, system_keyring = load_pair(
        system_policy_path,
        system_keyring_path,
        verifier,
        expected_uid=expected_uid,
        label="system update trust",
    )
    initialize_state_root(state_root, expected_uid)
    with promotion_lock(state_root, expected_uid):
        return promote_locked(
            system_policy,
            system_keyring,
            state_root,
            verifier,
            expected_uid,
        )


def select_keyring(
    system_policy_path: Path,
    system_keyring_path: Path,
    state_root: Path,
    verifier_path: Path,
    *,
    expected_uid: int = 0,
) -> tuple[str, dict[str, object], Path]:
    verifier = load_verifier(verifier_path)
    system_policy, _system_keyring = load_pair(
        system_policy_path,
        system_keyring_path,
        verifier,
        expected_uid=expected_uid,
        label="system update trust",
    )
    if not state_root.exists() and not state_root.is_symlink():
        return "system", system_policy, system_keyring_path
    require_private_directory(state_root, expected_uid, "managed update trust root")
    with promotion_lock(state_root, expected_uid):
        clean_interrupted_temporaries(state_root, expected_uid)
        recover_pending(state_root, verifier, expected_uid=expected_uid)
        current = load_current(state_root, verifier, expected_uid=expected_uid)
        if current is None:
            raise TrustError("managed update trust root has no complete current generation")
        current_policy, _current_keyring = current
        if (
            current_policy["generation"] == system_policy["generation"]
            and current_policy != system_policy
        ):
            raise TrustError("system and managed trust disagree at one generation")
        return "managed", current_policy, state_paths(state_root)["keyring"]


def add_runtime_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--system-policy", type=Path, default=SYSTEM_POLICY)
    parser.add_argument("--system-keyring", type=Path, default=SYSTEM_KEYRING)
    parser.add_argument("--state-root", type=Path, default=STATE_ROOT)
    parser.add_argument("--verifier", type=Path, default=KEYRING_VERIFIER)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create-policy")
    create.add_argument("--keyring", type=Path, required=True)
    create.add_argument("--generation", type=int, required=True)
    create.add_argument("--retired-fingerprint", action="append", default=[])
    create.add_argument("--gpg", type=Path, required=True)
    create.add_argument("--verifier", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify-system")
    add_runtime_arguments(verify)

    promote_parser = subparsers.add_parser("promote")
    add_runtime_arguments(promote_parser)

    select = subparsers.add_parser("select")
    add_runtime_arguments(select)
    select.add_argument("--machine", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    expected_uid = 0
    if os.environ.get("ECHO_UPDATE_TRUST_SOURCE_TEST") == "USE-SOURCE-RUNTIME":
        expected_uid = os.getuid()
    try:
        if args.command == "create-policy":
            verifier = load_verifier(args.verifier)
            keyring = read_regular(
                args.keyring,
                MAX_KEYRING_BYTES,
                "release update keyring",
                expected_uid=os.getuid(),
            )
            verify_keyring_bytes(keyring, verifier)
            trusted = primary_fingerprints(args.gpg, args.keyring)
            retired = [fingerprint.upper() for fingerprint in args.retired_fingerprint]
            policy = make_policy(keyring, args.generation, trusted, retired)
            if args.output.exists() or args.output.is_symlink():
                raise TrustError("update trust policy output already exists")
            atomic_write(args.output, canonical_policy(policy), 0o444)
            print(
                f"ECHO_UPDATE_TRUST_POLICY_CREATED generation={policy['generation']} "
                f"trusted={len(policy['trusted_fingerprints'])} "
                f"retired={len(policy['retired_fingerprints'])}"
            )
            return 0
        if args.command == "verify-system":
            verifier = load_verifier(args.verifier)
            policy, _keyring = load_pair(
                args.system_policy,
                args.system_keyring,
                verifier,
                expected_uid=expected_uid,
                label="system update trust",
            )
            print(
                f"ECHO_UPDATE_TRUST_SYSTEM_OK generation={policy['generation']} "
                f"keyring={policy['keyring_sha256']}"
            )
            return 0
        if args.command == "promote":
            source, policy = promote(
                args.system_policy,
                args.system_keyring,
                args.state_root,
                args.verifier,
                expected_uid=expected_uid,
            )
            print(
                f"ECHO_UPDATE_TRUST_READY generation={policy['generation']} "
                f"keyring={policy['keyring_sha256']} source={source} "
                f"trusted={len(policy['trusted_fingerprints'])} "
                f"retired={len(policy['retired_fingerprints'])}"
            )
            return 0
        source, policy, keyring = select_keyring(
            args.system_policy,
            args.system_keyring,
            args.state_root,
            args.verifier,
            expected_uid=expected_uid,
        )
        if args.machine:
            print(policy["generation"], keyring, policy["keyring_sha256"], source, sep="\t")
        else:
            print(
                f"ECHO_UPDATE_TRUST_SELECTED generation={policy['generation']} "
                f"keyring={policy['keyring_sha256']} source={source}"
            )
        return 0
    except (OSError, TrustError, ValueError) as error:
        print(f"Echo OS update trust failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
