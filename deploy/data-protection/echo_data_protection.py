#!/usr/bin/env python3
"""Enroll and verify per-device Echo OS LUKS2 data protection."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path


CRYPTSETUP = "/usr/sbin/cryptsetup"
CRYPTENROLL = "/usr/bin/systemd-cryptenroll"
FINDMNT = "/usr/bin/findmnt"
LSBLK = "/usr/bin/lsblk"
SWAPON = "/usr/sbin/swapon"
PARTITIONS = ("echo-var", "echo-swap", "echo-home")
RECOVERY_KEY = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{8}){7}$")
FACTORY_KEY_MIN_BYTES = 32
FACTORY_KEY_MAX_BYTES = 256
TPM2_DEVICE_KEY_MAX_BYTES = 64 * 1024
TPM2_PUBLIC_KEY_MAX_BYTES = 64 * 1024
TPM2_PCR_PUBLIC_KEY = Path("/usr/lib/systemd/tpm2-pcr-public-key.pem")
SIGNED_PCRS = "11"
OPENSSL = "/usr/bin/openssl"


class DataProtectionError(RuntimeError):
    """Raised when a device cannot meet the Echo data-protection contract."""


CommandRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]
DeviceResolver = Callable[[str], Path]


def run_command(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        check=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def command_succeeded(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode == 0


def require_command(result: subprocess.CompletedProcess[str], context: str) -> None:
    if command_succeeded(result):
        return
    detail = result.stderr.strip()
    if detail:
        raise DataProtectionError(f"{context}: {detail}")
    raise DataProtectionError(context)


def read_secret(path: Path, *, recovery: bool) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, OSError) as error:
        raise DataProtectionError(f"secret file is missing: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DataProtectionError(f"secret must be a regular file: {path}")
        if metadata.st_uid != os.geteuid():
            raise DataProtectionError(f"secret must be owned by the invoking user: {path}")
        if metadata.st_mode & 0o077:
            raise DataProtectionError(
                f"secret must not be accessible to group or other users: {path}"
            )
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = stream.read(FACTORY_KEY_MAX_BYTES + 1)
    finally:
        os.close(descriptor)
    if recovery:
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError as error:
            raise DataProtectionError("recovery key must be ASCII") from error
        if not RECOVERY_KEY.fullmatch(text):
            raise DataProtectionError("recovery key has an invalid Echo OS format")
    elif not FACTORY_KEY_MIN_BYTES <= len(value) <= FACTORY_KEY_MAX_BYTES:
        raise DataProtectionError(
            f"factory key must be {FACTORY_KEY_MIN_BYTES} to {FACTORY_KEY_MAX_BYTES} bytes"
        )
    if b"\x00" in value or b"\n" in value or b"\r" in value:
        raise DataProtectionError("secret files must contain one literal key without a terminator")
    return value


def generate_recovery_key(path: Path) -> None:
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    key_hex = secrets.token_hex(32)
    recovery_key = "-".join(key_hex[index : index + 8] for index in range(0, len(key_hex), 8))
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(recovery_key.encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def generate_factory_key(path: Path) -> None:
    parent = path.parent.resolve(strict=True)
    target = parent / path.name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(secrets.token_hex(32).encode("ascii"))
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            target.unlink()
        except FileNotFoundError:
            pass
        raise


def check_tpm2_device_key(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise DataProtectionError(f"TPM2 device public key is unavailable: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DataProtectionError("TPM2 device public key must be a regular file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise DataProtectionError(
                "TPM2 device public key must be owned by the invoking user "
                "and immutable to group/other"
            )
        if not 1 <= metadata.st_size <= TPM2_DEVICE_KEY_MAX_BYTES:
            raise DataProtectionError("TPM2 device public key size is outside the accepted range")
    finally:
        os.close(descriptor)


def check_tpm2_public_key(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise DataProtectionError(f"TPM2 PCR public key is unavailable: {path}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise DataProtectionError("TPM2 PCR public key must be a regular file")
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise DataProtectionError(
                "TPM2 PCR public key must be owned by the invoking user "
                "and immutable to group/other"
            )
        if not 1 <= metadata.st_size <= TPM2_PUBLIC_KEY_MAX_BYTES:
            raise DataProtectionError("TPM2 PCR public key size is outside the accepted range")
    finally:
        os.close(descriptor)
    require_command(
        run_command((OPENSSL, "rsa", "-pubin", "-in", str(path), "-noout")),
        "TPM2 PCR public key must be a PEM-encoded RSA public key",
    )


def resolver_for_disk(disk_input: Path) -> DeviceResolver:
    try:
        disk = disk_input.resolve(strict=True)
        metadata = disk.stat()
    except FileNotFoundError as error:
        raise DataProtectionError(f"target disk is missing: {disk_input}") from error
    if not stat.S_ISBLK(metadata.st_mode):
        raise DataProtectionError(f"target is not a whole block disk: {disk}")
    disk_type = run_command((LSBLK, "-dnro", "TYPE", str(disk)))
    require_command(disk_type, f"cannot read target type for {disk}")
    if disk_type.stdout.strip() != "disk":
        raise DataProtectionError(f"target is not a whole block disk: {disk}")
    children = run_command((LSBLK, "-nrpo", "PATH,TYPE,PARTLABEL", str(disk)))
    require_command(children, f"cannot enumerate partitions on {disk}")
    by_label: dict[str, list[Path]] = {label: [] for label in PARTITIONS}
    for line in children.stdout.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[1] == "part" and fields[2] in by_label:
            by_label[fields[2]].append(Path(fields[0]).resolve(strict=True))
    for label, devices in by_label.items():
        if len(devices) != 1:
            raise DataProtectionError(
                f"target disk must contain exactly one {label} partition, found {len(devices)}"
            )

    def resolve_partition(label: str) -> Path:
        try:
            return by_label[label][0]
        except (KeyError, IndexError) as error:
            raise DataProtectionError(f"partition is missing from target disk: {label}") from error

    return resolve_partition


class DataProtector:
    def __init__(
        self,
        *,
        runner: CommandRunner = run_command,
        resolver: DeviceResolver,
        tpm2_public_key: Path,
        tpm2_device_key: Path | None = None,
    ) -> None:
        self.runner = runner
        self.resolver = resolver
        self.tpm2_public_key = tpm2_public_key
        self.tpm2_device_key = tpm2_device_key

    def run(self, command: Sequence[str], context: str) -> None:
        require_command(self.runner(command), context)

    def succeeds(self, command: Sequence[str]) -> bool:
        return command_succeeded(self.runner(command))

    def resolve_devices(self) -> list[Path]:
        devices = [self.resolver(label) for label in PARTITIONS]
        if len({str(device) for device in devices}) != len(devices):
            raise DataProtectionError("data-protection partition devices are not unique")
        for label, device in zip(PARTITIONS, devices):
            self.run(
                (CRYPTSETUP, "isLuks", "--type", "luks2", str(device)),
                f"{label} is not a LUKS2 volume",
            )
            if self.succeeds((FINDMNT, "-rn", "--source", str(device))):
                raise DataProtectionError(f"refusing to enroll mounted partition: {label}")
            swap = self.runner((SWAPON, "--show=NAME", "--noheadings"))
            require_command(swap, "cannot inspect active swap")
            active_swap = {line.strip() for line in swap.stdout.splitlines() if line.strip()}
            if str(device) in active_swap:
                raise DataProtectionError(f"refusing to enroll active swap partition: {label}")
        return devices

    def key_works(self, device: Path, key_file: Path) -> bool:
        return self.succeeds(
            (
                CRYPTSETUP,
                "open",
                "--test-passphrase",
                "--key-file",
                str(key_file),
                str(device),
            )
        )

    def tpm_works(self, device: Path) -> bool:
        return self.succeeds(
            (
                CRYPTSETUP,
                "open",
                "--test-passphrase",
                "--token-only",
                "--token-type",
                "systemd-tpm2",
                str(device),
            )
        )

    def tpm_token_count(self, device: Path) -> int | None:
        result = self.runner((CRYPTSETUP, "luksDump", "--dump-json-metadata", str(device)))
        if not command_succeeded(result):
            return None
        try:
            metadata = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return None
        tokens = metadata.get("tokens") if isinstance(metadata, dict) else None
        if not isinstance(tokens, dict):
            return None
        tpm_tokens = [
            token
            for token in tokens.values()
            if isinstance(token, dict) and token.get("type") == "systemd-tpm2"
        ]
        if any(
            not isinstance(token.get("keyslots"), list) or not token["keyslots"]
            for token in tpm_tokens
        ):
            return None
        return len(tpm_tokens)

    def tpm_token_present(self, device: Path) -> bool:
        count = self.tpm_token_count(device)
        return count is not None and count > 0

    def tpm_ready(self, device: Path) -> bool:
        if self.tpm2_device_key is not None:
            return self.tpm_token_present(device)
        return self.tpm_works(device)

    def enroll_tpm(self, device: Path, unlock_key: Path, label: str) -> None:
        tpm_enrollment = [
            CRYPTENROLL,
            str(device),
            f"--unlock-key-file={unlock_key}",
            "--wipe-slot=tpm2",
        ]
        if self.tpm2_device_key is None:
            tpm_enrollment.append("--tpm2-device=auto")
        else:
            tpm_enrollment.append(f"--tpm2-device-key={self.tpm2_device_key}")
        tpm_enrollment.extend(
            (
                "--tpm2-pcrs=",
                f"--tpm2-public-key={self.tpm2_public_key}",
                f"--tpm2-public-key-pcrs={SIGNED_PCRS}",
            )
        )
        self.run(tpm_enrollment, f"cannot enroll TPM2 in {label}")
        if not self.tpm_ready(device):
            raise DataProtectionError(f"TPM2 enrollment verification failed for {label}")

    def verify(self, recovery_key: Path) -> None:
        read_secret(recovery_key, recovery=True)
        devices = self.resolve_devices()
        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, recovery_key):
                raise DataProtectionError(f"recovery key cannot unlock {label}")
            if not self.tpm_ready(device):
                raise DataProtectionError(f"TPM2 token cannot unlock {label}")

    def enroll_recovery(self, recovery_key: Path) -> None:
        read_secret(recovery_key, recovery=True)
        devices = self.resolve_devices()
        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, recovery_key):
                raise DataProtectionError(f"recovery key cannot unlock {label}")
        for label, device in zip(PARTITIONS, devices):
            self.enroll_tpm(device, recovery_key, label)
        self.verify(recovery_key)

    def rebind_tpm2(self, recovery_key: Path) -> None:
        read_secret(recovery_key, recovery=True)
        devices = self.resolve_devices()
        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, recovery_key):
                raise DataProtectionError(f"recovery key cannot unlock {label}")

        # systemd-cryptenroll de-duplicates TPM enrollments by policy hash. A
        # replacement TPM using the same signed PCR policy would therefore keep
        # the old sealed object if --wipe-slot=tpm2 were used only as part of the
        # new enrollment command. Explicitly remove stale TPM slots first. The
        # independent recovery key has already been verified on every volume, so
        # an interruption always leaves a usable unlock path and is retryable.
        for label, device in zip(PARTITIONS, devices):
            self.run(
                (CRYPTENROLL, str(device), "--wipe-slot=tpm2"),
                f"cannot remove stale TPM2 slots from {label}",
            )
            if self.tpm_token_count(device) != 0:
                raise DataProtectionError(f"stale TPM2 tokens remain in {label}")
            self.enroll_tpm(device, recovery_key, label)

        self.verify(recovery_key)

    def rotate_recovery(self, old_recovery_key: Path, new_recovery_key: Path) -> None:
        old_value = read_secret(old_recovery_key, recovery=True)
        new_value = read_secret(new_recovery_key, recovery=True)
        if old_value == new_value:
            raise DataProtectionError("old and new recovery keys must be independent")
        devices = self.resolve_devices()

        # Rotation is retryable across an interrupted removal phase. Every
        # partition must start with at least one of the explicitly supplied
        # recovery credentials, and every existing TPM token must remain in
        # place. We establish the new key everywhere before revoking the old
        # key anywhere, so loss of power cannot make a later volume depend on a
        # credential that was already removed from an earlier volume.
        for label, device in zip(PARTITIONS, devices):
            old_works = self.key_works(device, old_recovery_key)
            new_works = self.key_works(device, new_recovery_key)
            if not old_works and not new_works:
                raise DataProtectionError(f"neither supplied recovery key can unlock {label}")
            if self.tpm_token_count(device) != 1:
                raise DataProtectionError(
                    f"recovery rotation requires exactly one TPM2 token in {label}"
                )

        for label, device in zip(PARTITIONS, devices):
            if self.key_works(device, new_recovery_key):
                continue
            if not self.key_works(device, old_recovery_key):
                raise DataProtectionError(
                    f"old recovery key is unavailable while adding the new key to {label}"
                )
            self.run(
                (
                    CRYPTSETUP,
                    "luksAddKey",
                    "--batch-mode",
                    "--key-file",
                    str(old_recovery_key),
                    str(device),
                    str(new_recovery_key),
                ),
                f"cannot add the new recovery key to {label}",
            )
            if not self.key_works(device, new_recovery_key):
                raise DataProtectionError(
                    f"new recovery-key enrollment verification failed for {label}"
                )

        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, new_recovery_key):
                raise DataProtectionError(
                    f"new recovery key is not established on every volume: {label}"
                )

        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, old_recovery_key):
                continue
            self.run(
                (
                    CRYPTSETUP,
                    "luksRemoveKey",
                    "--batch-mode",
                    str(device),
                    str(old_recovery_key),
                ),
                f"cannot revoke the old recovery key from {label}",
            )
            if self.key_works(device, old_recovery_key):
                raise DataProtectionError(
                    f"old recovery key still unlocks {label} after revocation"
                )

        for label, device in zip(PARTITIONS, devices):
            if self.key_works(device, old_recovery_key):
                raise DataProtectionError(f"old recovery key still unlocks {label}")
            if not self.key_works(device, new_recovery_key):
                raise DataProtectionError(f"new recovery key cannot unlock {label}")
            if self.tpm_token_count(device) != 1:
                raise DataProtectionError(
                    f"TPM2 token count changed during recovery rotation in {label}"
                )

    def enroll(self, factory_key: Path, recovery_key: Path) -> None:
        factory_value = read_secret(factory_key, recovery=False)
        recovery_value = read_secret(recovery_key, recovery=True)
        if factory_value == recovery_value:
            raise DataProtectionError("factory and recovery keys must be independent")
        devices = self.resolve_devices()

        # The public factory key remains in every volume until all volumes have
        # both independent production unlock paths. This avoids deleting the
        # last known-good key from one partition after a failure on another.
        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, factory_key):
                raise DataProtectionError(f"factory key cannot unlock {label}")

        # Establish the human-held recovery path on every volume before any
        # TPM work. A TPM failure must never leave a later volume dependent on
        # the ephemeral factory key that an installer or reset process removes.
        for label, device in zip(PARTITIONS, devices):
            if not self.key_works(device, recovery_key):
                self.run(
                    (
                        CRYPTSETUP,
                        "luksAddKey",
                        "--batch-mode",
                        "--key-file",
                        str(factory_key),
                        str(device),
                        str(recovery_key),
                    ),
                    f"cannot enroll recovery key in {label}",
                )
            if not self.key_works(device, recovery_key):
                raise DataProtectionError(f"recovery enrollment verification failed for {label}")

        for label, device in zip(PARTITIONS, devices):
            self.enroll_tpm(device, factory_key, label)

        for label, device in zip(PARTITIONS, devices):
            self.run(
                (
                    CRYPTSETUP,
                    "luksRemoveKey",
                    "--batch-mode",
                    str(device),
                    str(factory_key),
                ),
                f"cannot remove factory key from {label}",
            )
            if self.key_works(device, factory_key):
                raise DataProtectionError(f"factory key still unlocks {label}")
            if not self.key_works(device, recovery_key) or not self.tpm_ready(device):
                raise DataProtectionError(
                    f"production unlock paths failed after factory-key removal for {label}"
                )


def require_root() -> None:
    if os.geteuid() != 0:
        raise DataProtectionError("data-protection enrollment requires root privileges")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="action", required=True)

    generate = subparsers.add_parser("generate-recovery-key")
    generate.add_argument("output", type=Path)

    generate_factory = subparsers.add_parser("generate-factory-key")
    generate_factory.add_argument("output", type=Path)

    check_factory = subparsers.add_parser("check-factory-key")
    check_factory.add_argument("factory_key", type=Path)

    check_recovery = subparsers.add_parser("check-recovery-key")
    check_recovery.add_argument("recovery_key", type=Path)

    check_public_key = subparsers.add_parser("check-tpm2-public-key")
    check_public_key.add_argument("public_key", type=Path)

    enroll = subparsers.add_parser("enroll")
    enroll.add_argument("--tpm2-device-key", type=Path)
    enroll.add_argument("--tpm2-public-key", type=Path, default=TPM2_PCR_PUBLIC_KEY)
    enroll.add_argument("whole_disk", type=Path)
    enroll.add_argument("factory_key", type=Path)
    enroll.add_argument("recovery_key", type=Path)

    enroll_recovery = subparsers.add_parser("enroll-recovery")
    enroll_recovery.add_argument("--tpm2-device-key", type=Path)
    enroll_recovery.add_argument("--tpm2-public-key", type=Path, default=TPM2_PCR_PUBLIC_KEY)
    enroll_recovery.add_argument("whole_disk", type=Path)
    enroll_recovery.add_argument("recovery_key", type=Path)

    rebind_tpm2 = subparsers.add_parser("rebind-tpm2")
    rebind_tpm2.add_argument("--tpm2-device-key", type=Path)
    rebind_tpm2.add_argument("--tpm2-public-key", type=Path, default=TPM2_PCR_PUBLIC_KEY)
    rebind_tpm2.add_argument("whole_disk", type=Path)
    rebind_tpm2.add_argument("recovery_key", type=Path)

    rotate_recovery = subparsers.add_parser("rotate-recovery")
    rotate_recovery.add_argument("whole_disk", type=Path)
    rotate_recovery.add_argument("old_recovery_key", type=Path)
    rotate_recovery.add_argument("new_recovery_key", type=Path)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--tpm2-device-key", type=Path)
    verify.add_argument("--tpm2-public-key", type=Path, default=TPM2_PCR_PUBLIC_KEY)
    verify.add_argument("whole_disk", type=Path)
    verify.add_argument("recovery_key", type=Path)

    args = parser.parse_args()
    try:
        if args.action == "generate-recovery-key":
            generate_recovery_key(args.output)
            print(f"Echo OS recovery key created securely at {args.output}")
            return 0
        if args.action == "generate-factory-key":
            generate_factory_key(args.output)
            print(f"Echo OS ephemeral factory key created securely at {args.output}")
            return 0
        if args.action == "check-factory-key":
            read_secret(args.factory_key, recovery=False)
            print("Echo OS factory data key is structurally valid")
            return 0
        if args.action == "check-recovery-key":
            read_secret(args.recovery_key, recovery=True)
            print("Echo OS recovery key is structurally valid")
            return 0
        if args.action == "check-tpm2-public-key":
            check_tpm2_public_key(args.public_key)
            print("Echo OS signed-PCR public key is structurally valid")
            return 0
        require_root()
        if args.action == "rotate-recovery":
            protector = DataProtector(
                resolver=resolver_for_disk(args.whole_disk),
                tpm2_public_key=TPM2_PCR_PUBLIC_KEY,
            )
            protector.rotate_recovery(args.old_recovery_key, args.new_recovery_key)
            print(
                "ECHO_DATA_RECOVERY_ROTATED "
                "volumes=var,swap,home old=revoked new=verified tpm2=preserved"
            )
            return 0
        check_tpm2_public_key(args.tpm2_public_key)
        if args.tpm2_device_key is not None:
            check_tpm2_device_key(args.tpm2_device_key)
        protector = DataProtector(
            resolver=resolver_for_disk(args.whole_disk),
            tpm2_public_key=args.tpm2_public_key,
            tpm2_device_key=args.tpm2_device_key,
        )
        if args.action == "enroll":
            protector.enroll(args.factory_key, args.recovery_key)
            mode = "offline-srk" if args.tpm2_device_key is not None else "live"
            print(
                f"ECHO_DATA_PROTECTION_ENROLLED volumes=var,swap,home tpm2=signed-pcr11 mode={mode}"
            )
        elif args.action == "enroll-recovery":
            protector.enroll_recovery(args.recovery_key)
            mode = "offline-srk" if args.tpm2_device_key is not None else "live"
            print(
                "ECHO_DATA_PROTECTION_RESET_ENROLLED "
                f"volumes=var,swap,home tpm2=signed-pcr11 mode={mode}"
            )
        elif args.action == "rebind-tpm2":
            protector.rebind_tpm2(args.recovery_key)
            mode = "offline-srk" if args.tpm2_device_key is not None else "live"
            print(f"ECHO_DATA_TPM2_REBOUND volumes=var,swap,home tpm2=signed-pcr11 mode={mode}")
        else:
            protector.verify(args.recovery_key)
            mode = "token-present" if args.tpm2_device_key is not None else "unsealed"
            print(
                f"ECHO_DATA_PROTECTION_READY volumes=var,swap,home tpm2=signed-pcr11 proof={mode}"
            )
    except (DataProtectionError, OSError) as error:
        print(f"Echo OS data protection failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
